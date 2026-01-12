import inspect
from collections.abc import Mapping, MutableMapping
from typing import (
    Any,
    ClassVar,
    NoReturn,
    Self,
    dataclass_transform,
    final,
    get_origin,
    get_type_hints,
)

from haiway.types.default import Default, DefaultValue

__all__ = ("Immutable",)


@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
    field_specifiers=(Default,),
)
class ImmutableMeta(type):
    __slots__: tuple[str, ...]

    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        **kwargs: Any,
    ) -> type:
        state_type = type.__new__(
            mcs,
            name,
            bases,
            namespace,
            **kwargs,
        )

        state_type.__ATTRIBUTES__ = _collect_attributes(state_type)  # pyright: ignore[reportAttributeAccessIssue]
        state_type.__slots__ = tuple(state_type.__ATTRIBUTES__.keys())  # pyright: ignore[reportAttributeAccessIssue]
        state_type.__match_args__ = state_type.__slots__  # pyright: ignore[reportAttributeAccessIssue]

        # Only mark subclasses as final (not the base Immutable class itself)
        if name != "Immutable":
            state_type = final(state_type)

        return state_type


def _collect_attributes(
    cls: type[Any],
) -> Mapping[str, DefaultValue | None]:
    attributes: MutableMapping[str, DefaultValue | None] = {}
    for key, annotation in get_type_hints(cls, localns={cls.__name__: cls}).items():
        if key.startswith("__"):
            continue  # do not dunder specials

        if get_origin(annotation) is ClassVar:
            continue  # do not include ClassVars

        default_value: Any = getattr(cls, key, inspect.Parameter.empty)

        # Create an instance of the default value if any
        if default_value is inspect.Parameter.empty:
            attributes[key] = None

        elif isinstance(default_value, DefaultValue):
            attributes[key] = default_value

        else:
            attributes[key] = DefaultValue(default=default_value)

    return attributes


class Immutable(metaclass=ImmutableMeta):
    __ATTRIBUTES__: ClassVar[Mapping[str, DefaultValue | None]]

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
        attributes: str = ", ".join(f"{name}: {getattr(self, name)}" for name in self.__slots__)
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
