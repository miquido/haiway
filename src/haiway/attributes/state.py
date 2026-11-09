import base64
import inspect
import json
import os
import threading
import types
import typing
from collections.abc import (
    Generator,
    Iterable,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
    Set,
)
from contextlib import suppress
from copy import deepcopy
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from datetime import date, datetime, time, timedelta
from functools import partial
from pathlib import Path
from types import GenericAlias, MemberDescriptorType
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    Generic,
    Literal,
    NoReturn,
    ParamSpec,
    Self,
    TypeVar,
    TypeVarTuple,
    cast,
    dataclass_transform,
    final,
    get_args,
    get_origin,
    overload,
)
from uuid import UUID, SafeUUID

from haiway._native import nstruct
from haiway.attributes import native
from haiway.attributes.annotations import (
    ObjectAttribute,
    attribute_redaction,
    resolve_self_attribute,
)
from haiway.attributes.attribute import Attribute
from haiway.attributes.generics import (
    Variance,
    argument_variances,
    bind_type_arguments,
    is_open_type_argument,
    unspecialized_defaults,
)
from haiway.attributes.path import AttributePath
from haiway.attributes.specification import object_specification
from haiway.attributes.validation import ValidationError
from haiway.types import (
    BasicObject,
    DefaultValue,
    Map,
    Meta,
    Missing,
    TypeSpecification,
)
from haiway.types.basic import basic_key, basic_value, parse_duration
from haiway.types.immutable import attribute_names, namespace_annotations

__all__ = ("State",)


# Default is deliberately not declared as a field specifier: type checkers
# match specifier arguments by keyword only, so `Default(value)` would read as
# supplying no default and every construction would be reported as missing it.
# Left out, the annotated assignment is checked as an ordinary one, which keeps
# the type of the default verified and accepts every calling form.
@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
)
class StateMeta(nstruct.StructMeta):  # the classes of the native storage - their plan is kept there
    """
    Metaclass for State classes that manages attribute definitions and validation.

    This metaclass is responsible for:
    - Processing attribute annotations and defaults
    - Building ``Attribute`` entries from resolved ``AttributeAnnotation`` metadata
    - Setting up validation for attributes
    - Managing generic type parameters and specialization
    - Creating immutable class instances

    The dataclass_transform decorator allows State classes to be treated
    like dataclasses by static type checkers while using custom initialization
    and validation logic.
    """

    __SELF_ATTRIBUTE__: ObjectAttribute
    __TYPE_PARAMETERS__: Mapping[str, Any] | None
    __SPECIALIZED__: bool
    __SPECIFICATION__: TypeSpecification
    __FIELDS__: Sequence[Attribute]
    __ALLOWED_FIELDS__: Set[str]
    __ALIASES__: Mapping[str, str]
    __SERIALIZABLE__: bool
    __DEFAULTED__: Set[str]
    __ANNOTATED__: Mapping[str, Any]
    __slots__: tuple[str, ...]
    __match_args__: tuple[str, ...]
    # declared by a specialization alone - `__SPECIALIZED__` tells whether the
    # class itself has them, as opposed to inheriting them from one
    __origin__: type[Any]
    __args__: tuple[Any, ...]

    def __new__(
        mcs,
        /,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        type_parameters: dict[str, Any] | None = None,
        serializable: bool = False,
        **kwargs: Any,
    ) -> Any:
        # a class which declares attributes is final. the only thing which
        # re-declares them is a specialization, and one is created here rather
        # than by deriving from a class - `type_parameters` tells them apart.
        # a subclass would have to keep the slots, defaults, annotations and type
        # arguments of everything above it consistent with its own, and each of
        # those is resolved once, when the class is created - a base changing
        # afterwards is not seen. the nominal identity that equality, the
        # instance checks and the schema are built on stops holding along with it
        if not type_parameters:
            _refuse_inheriting(name, bases)

        # the names are resolved first, so the declared defaults can leave the
        # namespace before the annotations are resolved against it. resolving them
        # before the class exists matters for the same reason: either a default or
        # the slot descriptor replacing it would shadow a same named type used as
        # an annotation, as in `date: date`.
        # only the names of this first resolution can be trusted - a class body
        # annotation resolves through the namespace it is declared in, so a
        # declared default still standing there answers for the type of the same
        # name, as in `str: str = "text"` resolving to `"text"`. the keys do not
        # depend on that, which is why the annotations are resolved again below
        names: Sequence[str] = attribute_names(namespace_annotations(namespace))
        # a specialized generic re-collects the fields of its origin, whose
        # defaults are no longer class attributes - they are carried over from
        # the fields the origin already resolved them into. a specialization is
        # the only class with a base declaring fields, and it has exactly one
        defaults: MutableMapping[str, Any] = {
            field.name: field.default for base in bases for field in getattr(base, "__FIELDS__", ())
        }
        # the declared defaults have to leave the namespace - `type.__new__`
        # refuses a slot shadowed by a class variable of the same name, and a
        # surviving one would win over the slot descriptor
        declared_defaults: Mapping[str, Any] = {
            key: namespace.pop(key) for key in names if key in namespace
        }
        defaults.update(declared_defaults)
        # resolved again, now that no declared default can answer for a type of
        # the same name - see the note on the names above
        declared: Mapping[str, Any] | None = namespace_annotations(namespace)
        # slots are only ever created by `type.__new__`, so they have to be
        # declared within the namespace handed to it - assigning `__slots__` to a
        # finished class defines no descriptors and leaves the instances with a
        # `__dict__`, which is what the whole slot declaration exists to avoid
        owned_names: tuple[str, ...] = _declared_slots(
            name,
            names=names,
            bases=bases,
        )
        # natively stored fields live inline in the instance, behind descriptors
        # installed below - there are no slots to declare, and no `__dict__` either
        namespace["__slots__"] = ()

        cls = type.__new__(
            mcs,
            name,
            bases,
            namespace,
            **kwargs,
        )

        # with the defaults gone from the namespace and a slot descriptor in place
        # of each of them, having a default can no longer be told by looking the
        # name up on the class - it is recorded here and inherited explicitly
        defaulted: Set[str] = frozenset(declared_defaults.keys()).union(
            *(getattr(base, "__DEFAULTED__", ()) for base in bases)
        )
        cls.__DEFAULTED__ = defaulted  # pyright: ignore[reportConstantRedefinition]

        # a specialized generic declares nothing of its own, so it resolves the
        # annotations of its origin - which were resolved there before the slots
        # of that class could shadow them
        annotations: MutableMapping[str, Any] = {}
        for base in reversed(bases):
            annotations.update(getattr(base, "__ANNOTATED__", {}))

        if declared is not None:
            annotations.update(declared)

        cls.__ANNOTATED__ = annotations  # pyright: ignore[reportConstantRedefinition]

        # a specialization is the only class binding type parameters, and the
        # only one whose annotations are written in terms of them - one can't be
        # inherited from, and an attribute-less base has no annotation to carry
        parameters: Mapping[str, Any] | None = type_parameters or None

        # the annotations are always resolved here, so the namespace is not
        # handed over - it would only be used to resolve them in place of it
        self_attribute: ObjectAttribute = resolve_self_attribute(
            cls,
            # a generic which is not specialized stands for its defaults (PEP 696), and
            # for anything where a parameter declares none
            parameters=parameters or unspecialized_defaults(cls),
            defaults=defaulted,
            annotations=annotations,
        )

        cls.__SELF_ATTRIBUTE__ = self_attribute  # pyright: ignore[reportConstantRedefinition]
        cls.__TYPE_PARAMETERS__ = parameters  # pyright: ignore[reportConstantRedefinition]
        # only a class created as a specialization checks its type arguments -
        # `__origin__` and `__args__` reach a subclass of one through inheritance,
        # which would otherwise make it interchangeable with each of its siblings
        cls.__SPECIALIZED__ = bool(type_parameters)  # pyright: ignore[reportConstantRedefinition]
        cls._ = AttributePath(cls, attribute=cls)  # pyright: ignore[reportCallIssue, reportUnknownMemberType, reportAttributeAccessIssue]

        if not any(isinstance(base, StateMeta) for base in bases):
            # the base class - no fields specified
            assert not type_parameters  # nosec: B101
            cls.__SPECIFICATION__ = {  # pyright: ignore[reportConstantRedefinition]
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
            cls.__FIELDS__ = ()  # pyright: ignore[reportAttributeAccessIssue, reportConstantRedefinition]
            native.install(cls, (), ())

            cls.__ALLOWED_FIELDS__ = frozenset()  # pyright: ignore[reportConstantRedefinition]
            cls.__ALIASES__ = {}  # pyright: ignore[reportConstantRedefinition]
            cls.__SERIALIZABLE__ = True  # pyright: ignore[reportConstantRedefinition]
            cls.__match_args__ = ()  # pyright: ignore[reportAttributeAccessIssue]

            return cls  # early exit - base class

        fields: Sequence[Attribute] = tuple(
            Attribute(
                name=key,
                annotation=attribute,
                default=_resolve_default(defaults.get(key, Missing)),
                redaction=attribute_redaction(attribute),
                spec=native.compile(attribute),
            )
            for key, attribute in self_attribute.attributes.items()
        )
        # every key an attribute can be provided under, refusing a collision
        allowed_fields: Mapping[str, str] = _allowed_fields(
            name,
            fields=fields,
        )

        # resolved in one call rather than attribute by attribute - the class is
        # entered into the recursion guard of that call, which is the only way an
        # attribute referring back to it can resolve to a reference instead of to
        # the specification of the class, which is not there yet.
        # an attribute which can't be represented leaves the class without a
        # specification at all, whether or not it is required - a schema which
        # silently omits it would describe an object refusing the very attribute
        # this class accepts, `additionalProperties` being off
        specification: TypeSpecification | None = object_specification(
            self_attribute,
            attributes=tuple((field.key, field.annotation, field.required) for field in fields),
        )

        if specification is not None:  # it is technically not serializable otherwise
            cls.__SPECIFICATION__ = specification  # pyright: ignore[reportAttributeAccessIssue, reportConstantRedefinition]
            cls.__SERIALIZABLE__ = True  # pyright: ignore[reportConstantRedefinition]

        elif serializable:
            raise TypeError(f"{cls.__name__} requires serialization but cannot produce json schema")

        else:  # no specification
            cls.__SERIALIZABLE__ = False  # pyright: ignore[reportConstantRedefinition]
            cls.__SPECIFICATION__ = _no_specification  # pyright: ignore[reportAttributeAccessIssue, reportConstantRedefinition]

        cls.__FIELDS__ = fields  # pyright: ignore[reportConstantRedefinition]
        native.install(cls, fields, owned_names)

        cls.__ALLOWED_FIELDS__ = frozenset(allowed_fields)  # pyright: ignore[reportConstantRedefinition]
        # resolved here so that updates can be normalized to canonical names
        # without rebuilding this mapping of the class on each of them
        cls.__ALIASES__ = {  # pyright: ignore[reportConstantRedefinition]
            field.alias: field.name for field in fields if field.alias is not None
        }
        # every field, unlike `__slots__` which holds the names declared here - a
        # specialized generic declares none of them and matches all of them
        cls.__match_args__ = tuple(  # pyright: ignore[reportAttributeAccessIssue]
            field.name for field in fields
        )

        return cls

    @property
    def __signature__(cls) -> inspect.Signature:  # pyright: ignore[reportIncompatibleVariableOverride]
        # what `inspect.signature` reads - the constructor itself takes `**kwargs`,
        # which tells a tool nothing about the attributes it accepts
        return inspect.Signature(
            parameters=[
                inspect.Parameter(
                    field.name,
                    inspect.Parameter.KEYWORD_ONLY,
                    annotation=cls.__ANNOTATED__.get(field.name, Any),
                    default=inspect.Parameter.empty if field.required else ...,
                )
                for field in cls.__FIELDS__
            ],
            return_annotation=None,
        )

    def __instancecheck__(
        self,
        instance: Any,
    ) -> bool:
        # only a specialization has type arguments to check the values against,
        # and it is the only reason to look at them at all
        if not self.__SPECIALIZED__:
            return type.__instancecheck__(self, instance)

        # constructed as this very specialization, an instance has had each of its
        # attributes validated against the arguments, and they can't change after -
        # there is nothing left to check it against
        if type(instance) is cast(Any, self):
            return True

        if not self.__subclasscheck__(type(instance)):  # pyright: ignore[reportUnknownArgumentType]
            return False

        return all(
            nstruct.check(field.spec, getattr(instance, field.name)) for field in self.__FIELDS__
        )

    def __subclasscheck__(
        self,
        subclass: type[Any],
    ) -> bool:
        if self is subclass:
            return True

        # a class which is not a specialization is a nominal type of its own,
        # even when it inherits the `__origin__` and `__args__` of one - comparing
        # those instead would make every subclass of a single specialization a
        # subclass of each of its siblings
        if not self.__SPECIALIZED__:
            return type.__subclasscheck__(self, subclass)

        self_origin: type[Any] = self.__origin__
        if getattr(subclass, "__SPECIALIZED__", False):
            # both are specializations - only the arguments of the same generic
            # can be compared, they are positional
            if subclass.__origin__ is not self_origin:  # pyright: ignore[reportAttributeAccessIssue]
                return False

        # otherwise it matches whatever the origin matches, narrowed down by the
        # arguments it carries over from the specialization it derives from
        elif not type.__subclasscheck__(self_origin, subclass):
            return False

        return self._check_type_parameters(subclass)

    def _check_type_parameters(
        self,
        subclass: type[Any],
    ) -> bool:
        # only a specialization reaches here, and one always carries arguments
        self_args: Sequence[Any] = self.__args__
        subclass_args: Sequence[Any] | None = getattr(
            subclass,
            "__args__",
            None,
        )

        # nothing to narrow down when the subclass carries no arguments - it
        # matches whatever its unparametrized origin matches
        if subclass_args is None:
            return True

        # arguments of a differently parameterized generic - reached through a
        # class deriving from a specialization of one - do not map onto ours
        if len(self_args) != len(subclass_args):
            return False

        # each argument against its parameter - covariant when it is only ever read,
        # which is all a `State` does with one, unless the parameter is used the other
        # way around or declares otherwise
        for self_arg, subclass_arg, variance in zip(
            self_args,
            subclass_args,
            argument_variances(self.__origin__, len(self_args)),
            strict=True,
        ):
            if not _compatible(self_arg, subclass_arg, variance):
                return False

        return True


def _check_keys(
    cls: Any,
    kwargs: Mapping[str, Any],
    /,
) -> None:
    # what the constructor and `__replace__` refuse before looking at any value -
    # the native code hands over the moment it finds a key which is not right
    if unexpected := kwargs.keys() - cls.__ALLOWED_FIELDS__:
        raise _unexpected_attributes(cls, unexpected)

    for alias, name in cls.__ALIASES__.items():
        if alias in kwargs and name in kwargs:
            raise _duplicate_attribute(cls, name, alias)


# a variant is made once - two threads asking for the same one at the same time would
# otherwise each make their own, and the plan of a class is given once
_SPECIALIZING: Final[threading.RLock] = threading.RLock()


def _specialize(
    cls: Any,
    type_arguments: tuple[Any, ...],
    /,
) -> Any:
    with _SPECIALIZING:
        return _specialize_locked(cls, type_arguments)


def _specialize_locked(
    cls: Any,
    type_arguments: tuple[Any, ...],
    /,
) -> Any:
    # held by the class which is specialized rather than by a registry of the
    # module - a registry would keep every class created at runtime, together
    # with all of its variants, alive for as long as the process runs. here
    # the variants reference their origin and are referenced by it, a cycle
    # which is collected along with it
    specializations: MutableMapping[tuple[Any, ...], Any] | None = cls.__dict__.get(
        "__SPECIALIZATIONS__"
    )
    if specializations is None:
        specializations = {}
        cls.__SPECIALIZATIONS__ = specializations

    # the arguments as they bind - the defaults are given, the arguments of a
    # variadic parameter are its own, a list is a tuple. a variant is the same
    # whichever way it was spelled out
    bindings, bound_arguments = bind_type_arguments(cls, type_arguments)
    cacheable: bool = True
    cached: Any | None = None
    try:
        cached = specializations.get(bound_arguments)

    except TypeError:
        cacheable = False  # i.e. an annotation carrying an unhashable value

    # the lookup comes first - the arguments of a variant which exists have
    # been checked when it was created
    if cached is not None:
        if type_arguments != bound_arguments:
            with suppress(TypeError):  # spelled with an unhashable argument
                specializations[type_arguments] = cached

        return cached

    _check_type_arguments(cls, bindings)

    parameter_names: str = ",".join(
        getattr(argument, "__name__", str(argument)) for argument in bound_arguments
    )
    parametrized_type: Any = StateMeta.__new__(
        cls.__class__,
        name=f"{cls.__name__}[{parameter_names}]",
        bases=(cls,),
        # the origin and the arguments the subclass checks are made of are
        # declared within the namespace rather than assigned afterwards -
        # the class is asked about them as soon as it exists
        namespace={
            "__module__": cls.__module__,
            "__origin__": cls,
            "__args__": bound_arguments,
        },
        type_parameters={
            parameter.__name__: _bound_argument(parameter, argument)
            for parameter, argument in bindings
        },
    )
    if cacheable:
        specializations[bound_arguments] = parametrized_type
        if type_arguments != bound_arguments:
            with suppress(TypeError):  # spelled with an unhashable argument
                specializations[type_arguments] = parametrized_type

    return parametrized_type


def _known_specialization(
    cls: Any,
    type_argument: Any,
    /,
) -> Any:
    known: MutableMapping[tuple[Any, ...], Any] | None = cls.__dict__.get("__SPECIALIZATIONS__")
    if not known:
        return None

    try:
        key: tuple[Any, ...] = (
            cast(tuple[Any, ...], type_argument)
            if isinstance(type_argument, tuple)
            else (type_argument,)
        )
        return known.get(key)

    except TypeError:
        return None  # an unhashable argument is never cached


def _refuse_inheriting(
    name: str,
    bases: tuple[type, ...],
    /,
) -> None:
    states: tuple[type, ...] = tuple(base for base in bases if isinstance(base, StateMeta))
    if not states:
        return  # the root State itself

    # a State carries state - it is not a building block of objects. it has a single
    # base, the State it refines, and `Generic` when it declares type parameters
    if len(states) > 1 or any(base is not Generic for base in bases if base not in states):
        raise TypeError(
            f"{name} can't inherit from {', '.join(base.__name__ for base in bases)} - a"
            " State inherits from a single State, and Generic alone when it declares type"
            " parameters. Hold instances as attributes to compose them, or share behavior"
            " through an attribute-less State base"
        )

    for base in states:
        if getattr(base, "__FIELDS__", ()):
            raise TypeError(
                f"{name} can't inherit from {base.__name__} - a State declaring"
                " attributes is final. Parametrize a generic State to type a"
                " variant of it, inherit from an attribute-less base to share"
                " behavior, or hold an instance as an attribute to reuse its"
                " attributes"
            )


def _declared_slots(
    name: str,
    /,
    names: Sequence[str],
    bases: tuple[type, ...],
) -> tuple[str, ...]:
    slots: MutableSequence[str] = []
    for key in names:
        inherited: Any = Missing
        for base in bases:
            inherited = getattr(base, key, Missing)
            if inherited is not Missing:
                break

        if inherited is Missing:
            slots.append(key)  # nothing above declares it, this class owns the slot
            continue

        if isinstance(inherited, MemberDescriptorType):
            continue  # a name a base already declared keeps using the slot defined there

        # anything else inherited under that name is not a slot to reuse -
        # declaring one here would shadow it, while leaving it out makes the
        # attribute unassignable, failing only when the first instance is built
        raise TypeError(
            f"{name} declares attribute '{key}' shadowing the inherited"
            f" {type(inherited).__name__} of the same name"
        )

    return tuple(slots)


def _allowed_fields(
    name: str,
    /,
    fields: Sequence[Attribute],
) -> Mapping[str, str]:
    """Keys the attributes of a class can be provided under, by attribute name.

    A name and an alias share a single namespace - the constructor, ``validate``
    and ``to_mapping`` all key an attribute by either of them - so a collision
    would silently make one attribute answer for the value given to another.
    """
    allowed: MutableMapping[str, str] = {}
    for field in fields:
        alias: str | None = field.alias
        # an alias equal to the name declares the same key twice - `validate`
        # would read a single occurrence of it as the attribute provided under
        # both of its keys, refusing every input naming the attribute at all
        if alias == field.name:
            raise TypeError(f"{name} declares '{field.name}' aliased as its own name")

        for key in (field.name, alias):
            if key is None:
                continue  # no alias declared

            if (claiming := allowed.get(key)) is not None:
                raise TypeError(
                    f"{name} allows '{key}' for both '{claiming}' and '{field.name}'"
                    " - an attribute name and an alias can't collide within a class"
                )

            allowed[key] = field.name

    return allowed


def _resolve_default(
    value: DefaultValue | Any | Missing,
) -> DefaultValue:
    if isinstance(value, DefaultValue):
        return value

    return DefaultValue(default=value)


def _compatible(
    self_arg: Any,
    subclass_arg: Any,
    variance: Variance,
    /,
) -> bool:
    if self_arg is Any or subclass_arg is Any or self_arg == subclass_arg:
        return True

    # the signature of a `ParamSpec` - its types are compared position by position
    if isinstance(self_arg, tuple) and isinstance(subclass_arg, tuple):
        return _compatible_signatures(
            cast(tuple[Any, ...], self_arg),
            cast(tuple[Any, ...], subclass_arg),
            variance,
        )

    # the relation of two classes is all there is to decide it on - an argument
    # which is not one, a parameterized generic, a union or a literal, only ever
    # matches the equal argument admitted above
    try:
        match variance:
            case "co":
                return issubclass(subclass_arg, cast(Any, self_arg))

            case "contra":
                return issubclass(cast(Any, self_arg), subclass_arg)

            case "inv":
                return False

    except TypeError:
        return False


def _compatible_signatures(
    own: tuple[Any, ...],
    other: tuple[Any, ...],
    variance: Variance,
    /,
) -> bool:
    return len(own) == len(other) and all(
        _compatible(own_type, other_type, variance)
        for own_type, other_type in zip(own, other, strict=True)
    )


def _bound_argument(
    parameter: Any,
    argument: Any,
    /,
) -> Any:
    # what a variadic parameter stands for within an annotation is the tuple of
    # its arguments, which is spelled out as one
    if isinstance(parameter, TypeVarTuple):
        return tuple[*argument]

    return argument


def _check_type_arguments(
    cls: type[Any],
    bindings: Sequence[tuple[Any, Any]],
    /,
) -> None:
    for parameter, argument in bindings:
        if isinstance(parameter, TypeVarTuple):
            for element in argument:
                _check_type_argument(cls, parameter, element)

        elif isinstance(parameter, ParamSpec):
            _check_parameter_specification(cls, parameter, argument)

        else:
            _check_type_argument(cls, parameter, argument)


def _check_parameter_specification(
    cls: type[Any],
    parameter: Any,
    argument: Any,
    /,
) -> None:
    # the signature a callable is parametrized by - the types of its arguments,
    # an ellipsis for any of them, or the parameter of another generic
    if (
        argument is Ellipsis
        or isinstance(argument, tuple | ParamSpec)
        or get_origin(argument) is typing.Concatenate
    ):
        return

    raise TypeError(
        f"{cls.__name__} can't be specialized with '{_type_name(argument)}' -"
        f" '{parameter.__name__}' is a ParamSpec taking a list of types, `...` or"
        " a ParamSpec"
    )


def _check_type_argument(
    cls: type[Any],
    parameter: Any,
    argument: Any,
    /,
) -> None:
    # a variant is distinguished by its arguments, so one which can never hold a
    # value of the declared type is refused where it is spelled out, rather than
    # surfacing as a validation failure of every value it is ever given
    bound: Any = getattr(parameter, "__bound__", None)
    constraints: Sequence[Any] = getattr(parameter, "__constraints__", ())
    if bound is not None and not _satisfies(argument, bound):
        raise TypeError(
            f"{cls.__name__} can't be specialized with '{_type_name(argument)}' -"
            f" '{parameter.__name__}' is bound to '{_type_name(bound)}'"
        )

    if constraints and not any(_satisfies(argument, constraint) for constraint in constraints):
        raise TypeError(
            f"{cls.__name__} can't be specialized with '{_type_name(argument)}' -"
            f" '{parameter.__name__}' is constrained to"
            f" {', '.join(repr(_type_name(constraint)) for constraint in constraints)}"
        )


def _type_name(
    annotation: Any,
    /,
) -> str:
    return getattr(annotation, "__name__", None) or str(annotation)


def _satisfies(
    argument: Any,
    bound: Any,
    /,
) -> bool:
    if argument is Any or bound is Any or isinstance(argument, str | TypeVar):
        return True  # nothing to decide against - a forward reference, an open parameter

    argument = type(None) if argument is None else argument
    bound = type(None) if bound is None else bound

    # every member of a union has to satisfy it, a literal is checked by its values
    match get_origin(argument):
        case typing.Union | types.UnionType:
            return all(_satisfies(member, bound) for member in get_args(argument))

        case typing.Literal:
            return all(_satisfies(type(member), bound) for member in get_args(argument))

        case _:
            pass

    match get_origin(bound):
        case typing.Union | types.UnionType:
            return any(_satisfies(argument, member) for member in get_args(bound))

        case _:
            pass

    target: Any = get_origin(argument) or argument
    limit: Any = get_origin(bound) or bound
    try:
        return issubclass(target, limit)

    except TypeError:
        return True  # not a relation which can be decided on classes alone


def _duplicate_attribute(
    cls: type[Any],
    /,
    name: str,
    alias: str,
) -> TypeError:
    return TypeError(f"Duplicate attribute '{name}' with alias '{alias}' for {cls.__name__}")


def _unexpected_attributes(
    cls: type[Any],
    /,
    keys: Set[str],
) -> TypeError:
    return TypeError(
        f"{cls.__name__} has no attribute {', '.join(repr(key) for key in sorted(keys))}"
    )


@final
class _NoSpecification:
    __slots__ = ()

    def __get__(
        self,
        instance: object,
        owner: type[object],
    ) -> NoReturn:
        raise TypeError(f"{owner.__name__} cannot be represented using json schema")


_no_specification: _NoSpecification = _NoSpecification()


class State(nstruct.Validated, metaclass=StateMeta):
    """
    Base class for immutable data structures.

    State provides a framework for creating immutable, type-safe data classes
    with validation. It's designed to represent application state that can be
    safely shared and updated in a predictable manner.

    Key features:
    - Immutable: Instances cannot be modified after creation
    - Type-safe: Attributes are validated based on type annotations
    - Generic: Can be parameterized with type variables
    - Declarative: Uses a class-based declaration syntax similar to dataclasses
    - Validated: Custom validation rules can be applied to attributes (sequences and
      sets and mappings are coerced to immutable containers)

    State classes can be created by subclassing State and declaring attributes:

    ```python
    class User(State):
        name: str
        age: int
        email: str | None = None
    ```

    Instances are created using standard constructor syntax:

    ```python
    user = User(name="Alice", age=30)
    ```

    New instances with updated values can be created from existing ones:

    ```python
    updated_user = user.updating(age=31)
    ```

    A class which declares attributes is final - it can't be inherited from:

    ```python
    class Admin(User):  # TypeError
        privileges: Sequence[str]
    ```

    A generic ``State`` is parametrized instead of derived from, an
    attribute-less class can still be inherited to share behavior the way
    ``Configuration`` does, and attributes are reused by holding an instance
    rather than by extending its class:

    ```python
    class Box[T](State):
        value: T

    IntBox = Box[int]  # a typed variant of the same class

    class Admin(State):
        user: User  # reused by composition
        privileges: Sequence[str]
    ```

    Notes
    -----
    Instances can't be pickled - serialize them with ``to_json`` or ``to_basic_object``.
    Instances are not weak referenceable - attributes are held inline in the
    instance, with neither a ``__dict__`` nor a ``__weakref__``. A ``State``
    can't be held by ``weakref.ref`` or a ``WeakValueDictionary`` because of
    that, and its attributes can't be rebound by any means.
    """

    _: ClassVar[Self]

    if TYPE_CHECKING:
        # what a type checker reads the constructor as - the native base binds the
        # attributes, and a subclass with a constructor of its own delegates to it
        def __init__(self, **kwargs: Any) -> None: ...

    @classmethod
    def __class_getitem__(
        cls,
        type_argument: tuple[type[Any], ...] | type[Any],
    ) -> type[Self]:
        """
        Create a specialized version of a generic State class.

        This method enables the generic type syntax Class[TypeArg] for State classes.

        Parameters
        ----------
        type_argument : tuple[type[Any], ...] | type[Any]
            The type arguments to specialize the class with

        Returns
        -------
        type[Self]
            A specialized version of the class

        Raises
        ------
        TypeError
            If an argument does not satisfy the bound or the constraints of its
            type parameter.
        """
        # a variant which exists is looked up as the arguments were written - it was
        # checked when it was created, and only a class which can be specialized
        # ever holds any. what is not found goes through the checks below
        if (variant := _known_specialization(cls, type_argument)) is not None:
            return variant

        # the parameters bound by a specialization above are inherited, so only
        # being a specialization rules out specializing this class again
        if cls.__SPECIALIZED__:
            raise TypeError(f"Can't specialize already specialized type {cls.__name__}")

        if Generic not in cls.__bases__:
            raise TypeError(f"Can't specialize non generic type {cls.__name__}")

        type_arguments: tuple[type[Any], ...]
        match type_argument:
            case [*arguments]:
                type_arguments = tuple(arguments)

            case argument:
                type_arguments = (argument,)

        if any(is_open_type_argument(argument) for argument in type_arguments):
            # if we got unfinished type treat it as an alias instead of resolving
            return cast(type[Self], GenericAlias(cls, type_arguments))

        return cast(type[Self], _specialize(cls, type_arguments))

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        /,
    ) -> Self:
        """
        Build an instance from a mapping of attribute values.

        Parameters
        ----------
        value : Mapping[str, Any]
            Mapping containing attribute names or aliases and their values.

        Returns
        -------
        Self
            New instance constructed from the provided mapping.
        """
        return cls.validate(value)

    @overload
    @classmethod
    def json_schema(
        cls,
        *,
        indent: int | None = None,
        required: Literal[True] = True,
    ) -> str: ...

    @overload
    @classmethod
    def json_schema(
        cls,
        *,
        indent: int | None = None,
        required: Literal[False],
    ) -> str | None: ...

    @classmethod
    def json_schema(
        cls,
        *,
        indent: int | None = None,
        required: bool = True,
    ) -> str | None:
        """
        Render this State's JSON Schema definition.

        Parameters
        ----------
        indent : int | None, optional
            Indentation passed to ``json.dumps`` for pretty-printing.
        required : bool, default=True
            When ``True``, raises if the class has no specification.

        Returns
        -------
        str | None
            JSON Schema string when available; ``None`` if no schema is defined
            and ``required`` is ``False``.

        Raises
        ------
        TypeError
            If ``required`` is ``True`` but the class does not declare a schema.
        """
        if cls.__SERIALIZABLE__:
            return json.dumps(
                cls.__SPECIFICATION__,
                indent=indent,
            )

        elif required:
            raise TypeError(f"{cls.__name__} cannot be represented using json schema")

        return None

    @classmethod
    def from_json(
        cls,
        value: str | bytes,
        /,
        decoder: type[json.JSONDecoder] = json.JSONDecoder,
    ) -> Self:
        """
        Deserialize an instance from a JSON object payload.

        Parameters
        ----------
        value : str | bytes
            JSON payload representing a single instance.
        decoder : type[json.JSONDecoder], default=json.JSONDecoder
            Decoder class used by ``json.loads``.

        Returns
        -------
        Self
            Instance built from the decoded payload.

        Raises
        ------
        ValueError
            If the payload cannot be decoded or fails State validation.
        """
        try:
            if decoder is json.JSONDecoder:
                # read natively when it is the usual document - anything else is read
                # below, which is also what says what is wrong with it
                decoded: Any = nstruct.from_json(cls, value, False)
                if decoded is not None:
                    return decoded

            return cls.validate(
                json.loads(
                    value,
                    cls=decoder,
                )
            )

        except Exception as exc:
            raise ValueError(f"Failed to decode {cls.__name__} from json: {exc}") from exc

    @classmethod
    def from_json_array(
        cls,
        value: str | bytes,
        /,
        decoder: type[json.JSONDecoder] = json.JSONDecoder,
    ) -> Sequence[Self]:
        """
        Deserialize a sequence of instances from a JSON array payload.

        Parameters
        ----------
        value : str | bytes
            JSON payload representing an array of objects.
        decoder : type[json.JSONDecoder], default=json.JSONDecoder
            Decoder class used by ``json.loads``.

        Returns
        -------
        Sequence[Self]
            Tuple of instances decoded from the array payload.

        Raises
        ------
        ValueError
            If decoding fails or the payload is not an array of valid objects.
        """
        payload: Any
        try:
            if decoder is json.JSONDecoder:
                decoded: Any = nstruct.from_json(cls, value, True)
                if decoded is not None:
                    return decoded

            payload = json.loads(
                value,
                cls=decoder,
            )

        except Exception as exc:
            raise ValueError(f"Failed to decode {cls.__name__} from json: {exc}") from exc

        match payload:
            case [*elements]:
                try:
                    return tuple(cls.validate(element) for element in elements)

                except Exception as exc:
                    raise ValueError(
                        f"Failed to decode {cls.__name__} from json array: {exc}"
                    ) from exc

            case _:
                raise ValueError("Provided json is not an array!")

    def to_json(
        self,
        indent: int | None = None,
        encoder_class: type[json.JSONEncoder] = json.JSONEncoder,
    ) -> str:
        """
        Serialize this instance to a JSON string.

        Parameters
        ----------
        indent : int | None, optional
            Indentation passed to ``json.dumps`` for pretty-printing.
        encoder_class : type[json.JSONEncoder], default=JSONEncoder
            Encoder class responsible for encoding custom types.

        Returns
        -------
        str
            JSON representation of this instance.

        Raises
        ------
        ValueError
            If encoding fails. The message names the type only - the values are
            left to the cause, so a payload which failed to encode cannot carry
            a redacted attribute into a log through the error path.
        """
        try:
            if indent is None and encoder_class is json.JSONEncoder:
                # written natively when every value has a spelling it knows - anything
                # else is written below, where the encoder of the caller is
                written: str | None = nstruct.to_json(self)
                if written is not None:
                    return written

            # non-strict - `encoder_class` is the extension point for the types
            # which have no basic spelling, so those have to reach it unchanged
            return json.dumps(
                _basic_object(self, strict=False),
                indent=indent,
                cls=encoder_class,
            )

        except Exception as exc:
            # `to_mapping` returns the actual values, redaction included, so the
            # mapping must not reach the message - the encoder already names the
            # type it could not serialize
            raise ValueError(f"Failed to encode {self.__class__.__name__} to json") from exc

    def to_str(self) -> str:
        """
        Convert this instance to a string representation.

        Returns
        -------
        str
            A string representation of this instance
        """
        return str(self)

    def to_mapping(
        self,
        recursive: bool = True,
    ) -> Mapping[str, Any]:
        """
        Convert this instance to a mapping of exported attribute values.

        Parameters
        ----------
        recursive : bool, default=True
            If True, nested ``State`` objects and collection elements are
            converted recursively.

        Returns
        -------
        Mapping[str, Any]
            A mapping keyed by attribute aliases when present, otherwise by
            canonical field names. Values equal to ``Missing`` are omitted.
        """
        return nstruct.to_mapping(self, recursive)

    def to_basic_object(self) -> BasicObject:
        """
        Convert this instance to a mapping of basic - JSON compatible - values.

        Where ``to_mapping`` keeps the live values, this converts every leaf and
        every key to the spelling the attribute validation reads back, so the
        result round trips through ``from_mapping``:

        ===========================  =====================
        Type                         Basic value
        ===========================  =====================
        str/int/float/bool/None      unchanged
        StrEnum/IntEnum              unchanged
        UUID                         ``str(value)``
        datetime/date/time           ``value.isoformat()``
        timedelta                    ISO 8601 duration, ``P1DT2H3M4.5S``
        Path                         ``value.as_posix()``
        bytes-like                   base64 encoded str
        ===========================  =====================

        Mapping keys go through the same table and are then spelled the way json
        spells them - ``int`` and ``float`` become ``str(key)``, ``bool`` and
        ``None`` become ``"true"``/``"false"``/``"null"``.

        Nested ``State`` instances, dataclasses and collections are converted as
        the value graph is traversed, in a single pass - a basic object is basic
        all the way down, there is nothing left for a non-recursive form to hold.

        Returns
        -------
        BasicObject
            A mapping keyed by attribute aliases when present, otherwise by
            canonical field names, holding only basic values.

        Raises
        ------
        TypeError
            If any attribute holds a value with no basic representation, i.e. a
            plain ``Enum`` or a ``Decimal``. The message names the path and the
            type only, never the value.

        Examples
        --------
        >>> class User(State):
        ...     identifier: UUID
        ...     created: datetime
        >>> User(identifier=UUID(int=0), created=datetime(2026, 1, 1)).to_basic_object()
        {'identifier': '00000000-0000-0000-0000-000000000000', 'created': '2026-01-01T00:00:00'}
        """
        return _basic_object(self, strict=True)

    def __str__(self) -> str:
        """
        Get a string representation of this instance.

        Returns
        -------
        str
            A string representation in the format "ClassName(attr1: value1, attr2: value2)"
        """
        attributes: str = ", ".join(
            f"{field.key}:"
            f" {getattr(self, field.name) if field.redaction is None else field.redaction}"
            for field in self.__FIELDS__
        )
        return f"{self.__class__.__name__}({attributes})"

    def __repr__(self) -> str:
        """
        Return the canonical representation of this instance.

        Returns
        -------
        str
            ``repr`` of the class and its attributes, in the format of ``__str__``
            but with every value spelled by its own ``repr`` - so ``"1"`` and ``1``
            can be told apart. Redacted attributes still render their redaction.
        """
        attributes: str = ", ".join(
            f"{field.key}:"
            f" {repr(getattr(self, field.name)) if field.redaction is None else field.redaction}"
            for field in self.__FIELDS__
        )
        return f"{self.__class__.__name__}({attributes})"

    def __reduce_ex__(
        self,
        protocol: Any,
    ) -> NoReturn:
        """
        Refuse pickling.

        Raises
        ------
        TypeError
            Always - a ``State`` is not picklable. Rebuilding one would have to
            skip validation, and a specialized variant has no importable name.
            Use ``to_json`` or ``to_basic_object`` to serialize it.
        """
        raise TypeError(
            f"{self.__class__.__name__} can't be pickled - serialize it with"
            " to_json or to_basic_object and rebuild it with from_json or from_mapping"
        )

    def __copy__(self) -> Self:
        """
        Create a shallow copy of this instance.

        Since State is immutable, this returns the instance itself.

        Returns
        -------
        Self
            This instance
        """
        return self  # State is immutable, no need to provide an actual copy


def _basic_object(
    instance: State,
    /,
    strict: bool,
    path: tuple[str, ...] = (),
) -> BasicObject:
    return nstruct.basic_object(instance, strict, path)


# The basic conversion of a value graph, next to `_recursive_mapping` and
# structured the same way - it converts as it traverses instead of copying the
# live values first. The leaves, the keys and the plain collections are resolved
# by `haiway.types.basic`, which can't reach `State` from within `types`, so the
# structured branches are spelled out here.
def _basic_mapping(  # noqa: PLR0911
    value: Any,
    /,
    strict: bool,
    path: tuple[str, ...],
) -> Any:
    # `str` and the bytes-like types are iterable, so the leaf conversion has to
    # win over the container branches - every other leaf is unambiguous and
    # reaches it through the fallthrough below
    if isinstance(value, str | bytes | bytearray | memoryview):
        return basic_value(value, strict=strict, path=path)

    elif isinstance(value, State):
        return _basic_object(value, strict=strict, path=path)

    elif is_dataclass(value) and not isinstance(value, type):
        return Map(_basic_fields(value, strict=strict, path=path))

    elif isinstance(value, Mapping):
        return Map(_basic_items(value, strict=strict, path=path))  # pyright: ignore[reportUnknownArgumentType]

    elif isinstance(value, Iterable):
        return tuple(_basic_elements(value, strict=strict, path=path))  # pyright: ignore[reportUnknownArgumentType]

    else:
        # the duck-typed `to_mapping` `_recursive_mapping` honors - only in the
        # non-strict traversal, where an unsupported leaf is left to the caller
        # anyway, so a value which knows how to spell itself as a mapping is
        # converted through it instead. Under `strict` it stays rejected below -
        # the mapping it returns is not the spelling the validation of the
        # annotation it came from would read back.
        if not strict:
            to_mapping: Any = getattr(value, "to_mapping", None)
            if callable(to_mapping):
                converted: Any = to_mapping()
                if isinstance(converted, Mapping):
                    return Map(
                        _basic_items(
                            converted,  # pyright: ignore[reportUnknownArgumentType]
                            strict=strict,
                            path=path,
                        )
                    )

        # the leaf table - raising under `strict` on a value it has no spelling
        # for, where `_recursive_mapping` would have made a copy of it
        return basic_value(value, strict=strict, path=path)


def _basic_fields(
    value: Any,
    /,
    strict: bool,
    path: tuple[str, ...],
) -> Generator[Any]:
    for field in dataclass_fields(value):
        yield (
            field.name,
            _basic_mapping(
                getattr(value, field.name),
                strict=strict,
                path=(*path, f'["{field.name}"]'),
            ),
        )


def _basic_items(
    mapping: Mapping[Any, Any],
    /,
    strict: bool,
    path: tuple[str, ...],
) -> Generator[Any]:
    for key, element in mapping.items():
        key_basic: Any = basic_key(key, strict=strict, path=path)
        yield (
            key_basic,
            _basic_mapping(
                element,
                strict=strict,
                # the converted key - in strict mode always a `str`, so the path
                # can't render a key through its `repr`
                path=(*path, f'["{key_basic}"]'),
            ),
        )


def _basic_elements(
    value: Iterable[Any],
    /,
    strict: bool,
    path: tuple[str, ...],
) -> Generator[Any]:
    for index, element in enumerate(value):
        yield _basic_mapping(
            element,
            strict=strict,
            path=(*path, f"[{index}]"),
        )


def _is_dataclass_instance(value: Any, /) -> bool:
    return is_dataclass(value) and not isinstance(value, type)


def _recursive_mapping(  # noqa: PLR0911
    value: Any,
) -> Any:
    if isinstance(value, str | bytes | float | int | bool | None):
        return value

    elif isinstance(value, State):
        return value.to_mapping(recursive=True)

    elif _is_dataclass_instance(value):
        return {
            field.name: _recursive_mapping(getattr(value, field.name))
            for field in dataclass_fields(value)
        }

    elif isinstance(value, Mapping):
        return {key: _recursive_mapping(element) for key, element in value.items()}  # pyright: ignore[reportUnknownVariableType]

    elif isinstance(value, Iterable):
        return [_recursive_mapping(element) for element in value]  # pyright: ignore[reportUnknownVariableType]

    elif hasattr(value, "to_mapping") and callable(value.to_mapping):
        return value.to_mapping()

    else:
        return deepcopy(value)


def _unexpected_key(cls: Any, key: Any) -> NoReturn:
    raise TypeError(f"Unexpected attribute '{key}' for {cls.__name__}")


def _basic_fallback(value: Any, strict: bool, path: tuple[str, ...]) -> Any:
    return _basic_mapping(value, strict=strict, path=path)


def _basic_key_fallback(key: Any, strict: bool, path: tuple[str, ...]) -> Any:
    return basic_key(key, strict=strict, path=path)


def _b64decode_strict(value: str, /) -> bytes:
    return base64.b64decode(value, validate=True)


# what the extension calls back - handed over once, when the module is loaded
native.configure(
    native.ValidationHooks(
        missing=Missing,
        check=_check_keys,
        report=ValidationError.report,
        unexpected=_unexpected_key,
        describe=native.describe,
        accepts=native.accepts,
        enum_values=native.enum_values,
        literal_values=native.literal_values,
        b64decode=_b64decode_strict,
        parse_duration=parse_duration,
        exception_group=ExceptionGroup,
    ),
    native.ConversionHooks(
        map=Map,
        basic_value=_basic_fallback,
        basic_key=_basic_key_fallback,
        recursive=_recursive_mapping,
    ),
    native.TypeHooks(
        mapping=Mapping,
        sequence=Sequence,
        set=Set,
        iterable=Iterable,
        uuid=UUID,
        safe_uuid=SafeUUID,
        datetime=datetime,
        date=date,
        time=time,
        timedelta=timedelta,
        path=Path,
        pathlike=os.PathLike,
        meta=Meta,
        partial=partial,
    ),
)
