import annotationlib
import inspect
import sys
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from typing import (
    TYPE_CHECKING,
    Any,
    ClassVar,
    Final,
    Self,
    dataclass_transform,
    get_origin,
    get_type_hints,
)

from haiway._native import nstruct
from haiway.types.default import DefaultValue

__all__ = ("Immutable",)


# see StateMeta for why Default is not declared as a field specifier here
@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
)
class ImmutableMeta(nstruct.StructMeta):  # a class of the native storage - its plan is kept there
    __ATTRIBUTES__: Mapping[str, DefaultValue | None]
    __slots__: tuple[str, ...]
    __match_args__: tuple[str, ...]

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> type:
        if any(isinstance(base, ImmutableMeta) and base.__name__ != "Immutable" for base in bases):
            raise TypeError(
                "Immutable subclasses cannot be inherited; inherit directly from Immutable"
            )

        # the names are resolved first, so the declared defaults can leave the
        # namespace before the annotations are resolved against it - an attribute
        # named after the type annotating it would otherwise resolve to its own
        # default rather than to that type. only the names of this first
        # resolution are used for that reason, the annotations being resolved
        # again below once the defaults are gone
        names: Sequence[str] = attribute_names(namespace_annotations(namespace))
        # the defaults are collected into `__ATTRIBUTES__` and have to leave the
        # namespace either way - `type.__new__` refuses a slot shadowed by a class
        # variable of the same name, and a surviving one would win over the slot
        defaults: Mapping[str, Any] = {key: namespace.pop(key) for key in names if key in namespace}
        # resolved again, now that no declared default can answer for a type of
        # the same name - see the note on the names above
        annotations: Mapping[str, Any] | None = namespace_annotations(namespace)
        # the attributes live inline in the instance, behind descriptors installed
        # below - there are no slots to declare, and no `__dict__` either, which is
        # what an empty declaration here keeps out
        slots: tuple[str, ...] = tuple(
            key for key in names if not any(hasattr(base, key) for base in bases)
        )
        namespace["__slots__"] = ()

        state_type = type.__new__(
            mcs,
            name,
            bases,
            namespace,
            **kwargs,
        )

        state_type.__ATTRIBUTES__ = _collect_attributes(  # pyright: ignore[reportConstantRedefinition]
            state_type,
            annotations=annotations,
            defaults=defaults,
        )
        state_type.__match_args__ = tuple(state_type.__ATTRIBUTES__.keys())  # pyright: ignore[reportAttributeAccessIssue]
        # the names this class stores - kept for what reads them, the values
        # themselves live inline in the instance, not in slots
        state_type.__slots__ = slots  # pyright: ignore[reportConstantRedefinition]
        _install(state_type, attributes=state_type.__ATTRIBUTES__, owned=slots)

        return state_type


def _required(
    cls: type[Any],
    name: str,
) -> Callable[[], Any]:
    # the default of an attribute which has none - asking for it is the error
    def missing() -> Any:
        raise AttributeError(f"Missing required attribute: {name}@{cls.__qualname__}")

    return missing


def _install(
    cls: type[Any],
    /,
    attributes: Mapping[str, DefaultValue | None],
    owned: Sequence[str],
) -> None:
    # the plan of a plain record, as the extension reads it - an entry for each attribute,
    # (name, alias, spec, default, constant), accepting anything, with no alias, and the
    # failures raised as they are. the names are interned, as the names of the keyword
    # arguments of a call are, which lets the native code tell them apart by identity
    nstruct.compile_plan(
        cls,
        tuple(
            (
                sys.intern(name),
                None,
                _ANY_SPEC,
                default if default is not None else _required(cls, name),
                default is not None and default.constant,
            )
            for name, default in attributes.items()
        ),
        tuple(sys.intern(name) for name in owned),
        False,
    )


# the description of an annotation accepting anything - compiled once, shared by every
# attribute of every plain record
_ANY_SPEC: Final[Any] = nstruct.compile_spec((nstruct.SPEC_ANY, None, None, None, None, None, ()))


def namespace_annotations(
    namespace: Mapping[str, Any],
    /,
) -> Mapping[str, Any] | None:
    """Resolve the annotations of a class body before the class is created.

    Parameters
    ----------
    namespace : Mapping[str, Any]
        Class body namespace, as handed to a metaclass.

    Returns
    -------
    Mapping[str, Any] | None
        Annotations declared within the body, or ``None`` when it declares none.

    Notes
    -----
    The annotations of a class resolve names against its namespace, which holds
    a slot descriptor for every attribute once the class exists - an attribute
    named after the type annotating it, such as ``date: date``, would resolve to
    that descriptor instead of the type. Resolving before the class is created
    leaves the module scope to answer, which is where the type comes from.

    The FORWARDREF format is used, so a name which cannot be resolved yet - the
    class annotating itself above all - is carried as a ``ForwardRef`` instead
    of failing here.
    """
    annotate = annotationlib.get_annotate_from_class_namespace(namespace)
    if annotate is None:
        return None  # nothing annotated within the class body

    return annotationlib.call_annotate_function(
        annotate,
        annotationlib.Format.FORWARDREF,
    )


def attribute_names(
    annotations: Mapping[str, Any] | None,
    /,
) -> Sequence[str]:
    """Names of the attributes declared by the given annotations.

    Parameters
    ----------
    annotations : Mapping[str, Any] | None
        Annotations of a class body, as resolved by ``namespace_annotations``.

    Returns
    -------
    Sequence[str]
        Declared attribute names, without dunder specials and class variables.
    """
    if annotations is None:
        return ()

    return tuple(
        key
        for key, annotation in annotations.items()
        if not key.startswith("__")  # do not include dunder specials
        and get_origin(annotation) is not ClassVar  # nor class variables
    )


def _collect_attributes(
    cls: type[Any],
    *,
    annotations: Mapping[str, Any] | None,
    defaults: Mapping[str, Any],
) -> Mapping[str, DefaultValue | None]:
    attributes: MutableMapping[str, DefaultValue | None] = {}
    resolved: Mapping[str, Any]
    if annotations is not None:
        resolved = annotations

    else:  # nothing declared within the body - the inherited hints, if any
        resolved = get_type_hints(
            cls,
            localns={cls.__name__: cls},
        )

    for key, annotation in resolved.items():
        if key.startswith("__"):
            continue  # do not dunder specials

        if get_origin(annotation) is ClassVar:
            continue  # do not include ClassVars

        # the declared default was taken out of the namespace to make room for
        # the slot, so it is provided by the caller instead of read off the class
        default_value: Any = defaults.get(key, inspect.Parameter.empty)

        # Create an instance of the default value if any
        if default_value is inspect.Parameter.empty:
            attributes[key] = None

        elif isinstance(default_value, DefaultValue):
            attributes[key] = default_value

        else:
            attributes[key] = DefaultValue(default=default_value)

    return attributes


class Immutable(nstruct.Struct, metaclass=ImmutableMeta):
    """
    Base class for frozen, slot-based Haiway value objects.

    Subclasses declare typed attributes as class annotations. The metaclass
    collects those annotations, derives ``__slots__`` and ``__match_args__``,
    and resolves literal defaults or ``Default(...)`` field markers during
    instance construction.

    Parameters
    ----------
    **kwargs : Any
        Values for declared attributes. Required attributes must be provided;
        optional attributes use their configured defaults.

    Raises
    ------
    AttributeError
        If a required attribute is missing or any mutation is attempted after
        initialization.
    """

    # construction, and the refusal to be modified, are the native storage's - what it
    # raises is the `AttributeError` this class has always raised. `object.__setattr__`
    # remains the one way around it, as it has always been for a plain record

    if TYPE_CHECKING:
        # what a type checker reads the constructor as - the native base binds the
        # attributes, and a subclass with a constructor of its own delegates to it
        def __init__(self, **kwargs: Any) -> None: ...

    # a record is the one it is, not its values - both operators are declared, as the
    # native storage defines each of them to compare the values
    def __eq__(self, other: object) -> bool:
        return object.__eq__(self, other)

    def __ne__(self, other: object) -> bool:
        return object.__ne__(self, other)

    def __hash__(self) -> int:
        return object.__hash__(self)

    def __str__(self) -> str:
        # `__slots__` holds the names declared by this class alone, the attributes
        # are what the instance actually carries
        attributes: str = ", ".join(
            f"{name}: {getattr(self, name)}" for name in self.__ATTRIBUTES__
        )
        return f"{self.__class__.__name__}({attributes})"

    def __repr__(self) -> str:
        return str(self)

    def __copy__(self) -> Self:
        return self  # Immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        return self  # Immutable, no need to provide an actual copy
