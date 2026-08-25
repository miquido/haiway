import annotationlib
import inspect
from collections.abc import Mapping, MutableMapping, Sequence
from typing import (
    Any,
    ClassVar,
    NoReturn,
    Self,
    dataclass_transform,
    get_origin,
    get_type_hints,
)

from haiway.types.default import DefaultValue

__all__ = ("Immutable",)


# see StateMeta for why Default is not declared as a field specifier here
@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
)
class ImmutableMeta(type):
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
        # slots are only ever created by `type.__new__`, so they have to be
        # declared within the namespace handed to it - assigning `__slots__` to a
        # finished class defines no descriptors and leaves the instances with a
        # `__dict__`, which is what the whole slot declaration exists to avoid
        namespace["__slots__"] = tuple(
            key for key in names if not any(hasattr(base, key) for base in bases)
        )

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

        return state_type


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


class Immutable(metaclass=ImmutableMeta):
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

    def __init__(
        self,
        **kwargs: Any,
    ) -> None:
        for name, default in self.__ATTRIBUTES__.items():
            if name in kwargs:
                object.__setattr__(
                    self,
                    name,
                    kwargs[name],
                )

            elif default is not None:
                object.__setattr__(
                    self,
                    name,
                    default(),
                )

            else:
                raise AttributeError(
                    f"Missing required attribute: {name}@{self.__class__.__qualname__}"
                )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be deleted"
        )

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
