import inspect
from types import EllipsisType
from typing import Any, ClassVar, Self, dataclass_transform, final, get_origin, get_type_hints

from haiway.types.default import DefaultValue

__all__ = ("Immutable",)


@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
    field_specifiers=(),
)
class ImmutableMeta(type):
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
) -> dict[str, DefaultValue[Any] | None]:
    attributes: dict[str, DefaultValue[Any] | None] = {}
    for key, annotation in get_type_hints(cls, localns={cls.__name__: cls}).items():
        # do not include ClassVars
        if (get_origin(annotation) or annotation) is ClassVar:
            continue

        field_value: Any = getattr(cls, key, inspect.Parameter.empty)

        # Create a Field instance with the default value
        if field_value is inspect.Parameter.empty:
            attributes[key] = None

        elif isinstance(field_value, DefaultValue):
            attributes[key] = field_value

        else:
            attributes[key] = DefaultValue(field_value)

    return attributes


class Immutable(metaclass=ImmutableMeta):
    __IMMUTABLE__: ClassVar[EllipsisType] = ...
    __ATTRIBUTES__: ClassVar[dict[str, DefaultValue | None]]

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
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    def __str__(self) -> str:
        attributes: str = ", ".join([f"{key}: {value}" for key, value in vars(self).items()])
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
