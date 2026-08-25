import json
from collections.abc import (
    Iterable,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
    Set,
)
from copy import deepcopy
from dataclasses import fields as dataclass_fields
from dataclasses import is_dataclass
from types import GenericAlias, MemberDescriptorType
from typing import (
    Any,
    ClassVar,
    Generic,
    Literal,
    NoReturn,
    Self,
    TypeVar,
    cast,
    dataclass_transform,
    final,
    overload,
)

from haiway.attributes.annotations import (
    ObjectAttribute,
    attribute_redaction,
    resolve_self_attribute,
)
from haiway.attributes.attribute import Attribute
from haiway.attributes.coding import AttributesJSONEncoder
from haiway.attributes.path import AttributePath
from haiway.attributes.specification import object_specification
from haiway.attributes.validation import ValidationError
from haiway.types import (
    MISSING,
    DefaultValue,
    Missing,
    TypeSpecification,
    not_missing,
)
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
class StateMeta(type):
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
        if type_parameters is None:
            for base in bases:
                if getattr(base, "__FIELDS__", ()):
                    raise TypeError(
                        f"{name} can't inherit from {base.__name__} - a State declaring"
                        " attributes is final. Parametrize a generic State to type a"
                        " variant of it, inherit from an attribute-less base to share"
                        " behavior, or hold an instance as an attribute to reuse its"
                        " attributes"
                    )

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
        namespace["__slots__"] = _declared_slots(
            name,
            names=names,
            bases=bases,
        )

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
            parameters=parameters or {},
            defaults=defaulted,
            annotations=annotations,
        )

        cls.__SELF_ATTRIBUTE__ = self_attribute  # pyright: ignore[reportConstantRedefinition]
        cls.__TYPE_PARAMETERS__ = parameters  # pyright: ignore[reportConstantRedefinition]
        # only a class created as a specialization checks its type arguments -
        # `__origin__` and `__args__` reach a subclass of one through inheritance,
        # which would otherwise make it interchangeable with each of its siblings
        cls.__SPECIALIZED__ = type_parameters is not None  # pyright: ignore[reportConstantRedefinition]
        cls._ = AttributePath(cls, attribute=cls)  # pyright: ignore[reportCallIssue, reportUnknownMemberType, reportAttributeAccessIssue]

        if not bases:  # handle base class - no fields specified
            assert not type_parameters  # nosec: B101
            cls.__SPECIFICATION__ = {  # pyright: ignore[reportConstantRedefinition]
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
            cls.__FIELDS__ = ()  # pyright: ignore[reportAttributeAccessIssue, reportConstantRedefinition]
            cls.__ALLOWED_FIELDS__ = frozenset()  # pyright: ignore[reportConstantRedefinition]
            cls.__ALIASES__ = {}  # pyright: ignore[reportConstantRedefinition]
            cls.__SERIALIZABLE__ = True  # pyright: ignore[reportConstantRedefinition]
            cls.__match_args__ = ()  # pyright: ignore[reportAttributeAccessIssue]

            return cls  # early exit - base class

        fields: Sequence[Attribute] = tuple(
            Attribute(
                name=key,
                annotation=attribute,
                default=_resolve_default(defaults.get(key, MISSING)),
                redaction=attribute_redaction(attribute),
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

    def __instancecheck__(
        self,
        instance: Any,
    ) -> bool:
        # only a specialization has type arguments to check the values against,
        # and it is the only reason to look at them at all
        if not self.__SPECIALIZED__:
            return type.__instancecheck__(self, instance)

        if not self.__subclasscheck__(type(instance)):  # pyright: ignore[reportUnknownArgumentType]
            return False

        return all(
            field.annotation.check(getattr(instance, field.name)) for field in self.__FIELDS__
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

        # Check if the type parameters are compatible (covariant)
        for self_arg, subclass_arg in zip(
            self_args,
            subclass_args,
            strict=True,
        ):
            if self_arg is Any or subclass_arg is Any or self_arg == subclass_arg:
                continue

            # For covariance: GenericState[Child] should be subclass of GenericState[Parent]
            # This means subclass_param should be a subclass of self_param
            try:
                if not issubclass(subclass_arg, self_arg):
                    return False

            except TypeError:
                # an argument which is not a class - a parameterized generic, a
                # union, a literal - has no subclass relation to resolve, so it
                # only ever matches the equal argument admitted above
                return False

        return True


def _declared_slots(
    name: str,
    /,
    names: Sequence[str],
    bases: tuple[type, ...],
) -> tuple[str, ...]:
    slots: MutableSequence[str] = []
    for key in names:
        inherited: Any = MISSING
        for base in bases:
            inherited = getattr(base, key, MISSING)
            if inherited is not MISSING:
                break

        if inherited is MISSING:
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

_types_cache: MutableMapping[
    tuple[
        Any,
        tuple[Any, ...],
    ],
    Any,
] = {}


class State(metaclass=StateMeta):
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
      sets are coerced to immutable containers; mappings remain regular dicts)

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
    Instances are not weak referenceable - attributes are held in slots and no
    slot list declares ``__weakref__``, keeping instances as small as they can
    be. A ``State`` can't be held by ``weakref.ref`` or a ``WeakValueDictionary``
    because of that.
    """

    _: ClassVar[Self]

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
        """
        # the parameters bound by a specialization above are inherited, so only
        # being a specialization rules out specializing this class again
        assert not cls.__SPECIALIZED__, "Can't specialize already specialized type!"  # nosec: B101
        assert Generic in cls.__bases__, "Can't specialize non generic type!"  # nosec: B101

        type_arguments: tuple[type[Any], ...]
        match type_argument:
            case [*arguments]:
                type_arguments = tuple(arguments)

            case argument:
                type_arguments = (argument,)

        if any(isinstance(argument, TypeVar) for argument in type_arguments):  # pyright: ignore[reportUnnecessaryIsInstance]
            # if we got unfinished type treat it as an alias instead of resolving
            return cast(type[Self], GenericAlias(cls, type_arguments))

        assert len(type_arguments) == len(  # nosec: B101
            cls.__type_params__
        ), "Type arguments count has to match type parameters count"

        if (cached := _types_cache.get((cls, type_arguments))) is not None:
            return cached

        type_parameters: dict[str, Any] = {
            parameter.__name__: argument
            for (parameter, argument) in zip(
                cls.__type_params__,
                type_arguments,
                strict=True,
            )
        }

        parameter_names: str = ",".join(
            getattr(
                argument,
                "__name__",
                str(argument),
            )
            for argument in type_arguments
        )
        parametrized_type: type[Self] = StateMeta.__new__(
            cls.__class__,
            name=f"{cls.__name__}[{parameter_names}]",
            bases=(cls,),
            # the origin and the arguments the subclass checks are made of are
            # declared within the namespace rather than assigned afterwards -
            # the class is asked about them as soon as it exists
            namespace={
                "__module__": cls.__module__,
                "__origin__": cls,
                "__args__": type_arguments,
            },
            type_parameters=type_parameters,
        )
        _types_cache[(cls, type_arguments)] = parametrized_type
        return parametrized_type

    @classmethod
    def validate(
        cls,
        value: Any,
    ) -> Self:
        """
        Validate and convert a value to an instance of this class.

        Parameters
        ----------
        value : Any
            The value to validate and convert

        Returns
        -------
        Self
            An instance of this class

        Raises
        ------
        TypeError
            If the value cannot be converted to an instance of this class
        """
        if isinstance(value, cls):
            return value

        elif isinstance(value, Mapping):
            for key in cast(Mapping[Any, Any], value.keys()):
                if key not in cls.__ALLOWED_FIELDS__:
                    raise TypeError(f"Unexpected attribute '{key}' for {cls.__name__}")

            # only an aliased attribute can be provided twice, under both of its
            # keys - the mapping of aliases is empty for most of the classes
            for alias, field_name in cls.__ALIASES__.items():
                if alias in value and field_name in value:
                    raise TypeError(
                        f"Duplicate attribute '{field_name}'"
                        f" with alias '{alias}' for {cls.__name__}"
                    )

            return cls(**value)

        else:
            raise TypeError(f"'{type(value).__name__}' is not matching expected type of '{cls}'")

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
        encoder_class: type[json.JSONEncoder] = AttributesJSONEncoder,
    ) -> str:
        """
        Serialize this instance to a JSON string.

        Parameters
        ----------
        indent : int | None, optional
            Indentation passed to ``json.dumps`` for pretty-printing.
        encoder_class : type[json.JSONEncoder], default=AttributesJSONEncoder
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
        mapping: Mapping[str, Any] = self.to_mapping(recursive=True)
        try:
            return json.dumps(
                mapping,
                indent=indent,
                cls=encoder_class,
            )

        except Exception as exc:
            # `to_mapping` returns the actual values, redaction included, so the
            # mapping must not reach the message - the encoder already names the
            # type it could not serialize
            raise ValueError(f"Failed to encode {self.__class__.__name__} to json") from exc

    def __init__(
        self,
        **kwargs: Any,
    ) -> None:
        """
        Initialize a new State instance.

        Creates a new instance with the provided attribute values.
        Attributes not specified will use their default values.
        All attributes are validated according to their type annotations.

        Parameters
        ----------
        **kwargs : Any
            Attribute values for the new instance. Both canonical field names
            and aliases are accepted.

        Raises
        ------
        TypeError
            If any key does not match a field name or alias
        Exception
            If validation fails for any attribute
        """
        # an unmatched key is a mistake rather than an extra - accepting it would
        # construct a plausible instance out of a misspelled attribute, which the
        # defaults then hide. `validate` refuses the same input for a mapping
        if unexpected := kwargs.keys() - self.__ALLOWED_FIELDS__:
            raise _unexpected_attributes(self.__class__, unexpected)

        for field in self.__FIELDS__:
            validated: Any
            try:
                validated = field.validate_from(kwargs)

            # the position of the failure is spelled out here rather than
            # established before validating - a validation which succeeds, by far
            # the common case, is left with nothing to pay for it
            except Exception as exc:
                ValidationError.report(f".{field.name}", exc)

            object.__setattr__(
                self,  # pyright: ignore[reportUnknownArgumentType]
                field.name,
                validated,
            )

    def updating(
        self,
        **kwargs: Any,
    ) -> Self:
        """
        Create a new instance with updated attribute values.

        This method creates a new instance with the same attribute values as this
        instance, but with any provided values updated.

        Parameters
        ----------
        **kwargs : Any
            New values for attributes to update

        Returns
        -------
        Self
            A new instance with updated values

        Raises
        ------
        TypeError
            If any key does not match a field name or alias
        """
        return self.__replace__(**kwargs)

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
            canonical field names. Values equal to ``MISSING`` are omitted.
        """
        dict_result: MutableMapping[str, Any] = {}
        for field in self.__FIELDS__:
            value: Any | Missing = getattr(self, field.name, MISSING)
            if not_missing(value):
                dict_result[field.key] = _recursive_mapping(value) if recursive else value

        return dict_result

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
            ``repr`` string mirroring ``__str__`` for readability.
        """
        return str(self)

    def __eq__(
        self,
        other: Any,
    ) -> bool:
        """
        Check if this instance is equal to another object.

        Two State instances are considered equal only when they have the same
        concrete class and equal values for all declared attributes.

        Parameters
        ----------
        other : Any
            The object to compare with

        Returns
        -------
        bool
            True if the objects are equal, False otherwise
        """
        if other.__class__ is not self.__class__:
            return False

        return all(
            getattr(self, field.name, MISSING) == getattr(other, field.name, MISSING)
            for field in self.__FIELDS__
        )

    def __hash__(self) -> int:
        """
        Compute a hash value for this immutable instance.

        Returns
        -------
        int
            Hash derived from non-missing attribute values.
        """
        hash_values: MutableSequence[int] = []
        for field in self.__FIELDS__:
            value: Any = getattr(self, field.name, MISSING)

            # Skip MISSING values to ensure consistent hashing
            if value is MISSING:
                continue

            # Convert to hashable representation
            try:
                hash_values.append(hash(value))

            except TypeError:
                continue  # skip unhashable

        return hash((self.__class__, tuple(hash_values)))

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> NoReturn:
        """
        Disallow attribute assignment to preserve immutability.

        Parameters
        ----------
        name : str
            Attribute name being set.
        value : Any
            Incoming value (unused).

        Raises
        ------
        AttributeError
            Always raised to signal immutability.
        """
        raise AttributeError(
            f"Can't modify immutable state {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        """
        Disallow attribute deletion to preserve immutability.

        Parameters
        ----------
        name : str
            Attribute name being deleted.

        Raises
        ------
        AttributeError
            Always raised to signal immutability.
        """
        raise AttributeError(
            f"Can't modify immutable state {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
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

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        """
        Create a deep copy of this instance.

        Unlike ``__copy__``, this creates a new instance so nested mutable
        values are deep-copied consistently.

        Parameters
        ----------
        memo : dict[int, Any] | None
            Memoization dictionary for already copied objects

        Returns
        -------
        Self
            Copy of this instance
        """
        if memo is None:
            memo = {}

        deep_copy: Self = object.__new__(self.__class__)
        memo[id(self)] = deep_copy
        for field in self.__FIELDS__:
            object.__setattr__(
                deep_copy,
                field.name,
                deepcopy(
                    getattr(self, field.name),
                    memo=memo,
                ),
            )

        return deep_copy

    def __replace__(
        self,
        **kwargs: Any,
    ) -> Self:
        """
        Create a new instance with replaced attribute values.

        This internal method is used by updating() to create a new instance
        with updated values.

        Parameters
        ----------
        **kwargs : Any
            New values for attributes to replace. Both canonical field names and
            aliases are accepted; aliases are normalized to canonical names
            before validation.

        Returns
        -------
        Self
            A new instance with replaced values

        Raises
        ------
        TypeError
            If any key does not match a field name or alias
        """
        if not kwargs:
            return self  # do not make a copy when nothing will be updated

        if unexpected := kwargs.keys() - self.__ALLOWED_FIELDS__:
            raise _unexpected_attributes(self.__class__, unexpected)

        # both names and aliases are accepted, the aliases are the only keys
        # which have to be normalized - and only when the class declares any
        aliases: Mapping[str, str] = self.__ALIASES__
        canonical_updates: Mapping[str, Any] = (
            {aliases.get(key, key): value for key, value in kwargs.items()} if aliases else kwargs
        )

        updated: Self = object.__new__(self.__class__)
        for field in self.__FIELDS__:
            # an absent key is what carries an attribute over, rather than a
            # `MISSING` value standing for one - `MISSING` is the value of an
            # attribute typed `| Missing`, and passing it has to clear that
            # attribute the same way constructing the instance with it does
            if field.name not in canonical_updates:
                object.__setattr__(
                    updated,
                    field.name,
                    getattr(self, field.name),
                )
                continue

            validated: Any
            try:
                # validated through the annotation rather than the field, the
                # way a supplied attribute is on construction - the default of
                # the field answers for an attribute which was not supplied
                validated = field.annotation.validate(canonical_updates[field.name])

            except Exception as exc:
                ValidationError.report(f".{field.name}", exc)

            object.__setattr__(
                updated,
                field.name,
                validated,
            )

        return updated


def _recursive_mapping(  # noqa: PLR0911
    value: Any,
) -> Any:
    if isinstance(value, str | bytes | float | int | bool | None):
        return value

    elif isinstance(value, State):
        return value.to_mapping(recursive=True)

    elif is_dataclass(value):
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
