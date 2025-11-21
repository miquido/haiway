"""Utilities for working with immutable mapping-like objects."""

import json
from collections.abc import Mapping
from typing import Any, Self, final

__all__ = ("Map",)


@final
class Map[Key, Element](dict[Key, Element]):
    """An immutable ``dict`` wrapper with convenience conversion helpers."""

    __slots__ = ()

    @classmethod
    def from_json(
        cls,
        value: str | bytes,
        /,
    ) -> Self:
        """Deserialize a JSON object into a ``Map`` instance."""
        match json.loads(value):
            case {**values}:
                return cls(values)

            case other:
                raise ValueError(f"Invalid json: {other}")

    def to_str(self) -> str:
        """Return the string representation of the map."""
        return self.__str__()

    def to_json(
        self,
    ) -> str:
        """Serialize the map into a JSON object string."""
        return json.dumps(self)

    def __setattr__(
        self,
        name: str,
        value: object,
    ) -> None:
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

    def __setitem__(
        self,
        key: Key,
        value: Element,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" item - '{key}' cannot be modified"
        )

    def __delitem__(
        self,
        key: Key,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" item - '{key}' cannot be deleted"
        )

    def clear(self) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, clear is not supported"
        )

    def pop(
        self,
        key: Key,
        default: Any | None = None,
        /,
    ) -> Element:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, pop is not supported"
        )

    def popitem(self) -> tuple[Key, Element]:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, popitem is not supported"
        )

    def setdefault(
        self,
        key: Key,
        default: Element | None = None,
        /,
    ) -> Element:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, setdefault is not supported"
        )

    def update(
        self,
        *updates: object,
        **kwargs: Element,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, update is not supported"
        )

    def __or__(
        self,
        other: Any,
    ) -> Any:
        if not isinstance(other, Mapping):
            raise NotImplementedError()

        return self.__class__({**self, **other})  # pyright: ignore[reportUnknownArgumentType]

    def __ror__(
        self,
        other: Any,
    ) -> Any:
        if not isinstance(other, Mapping):
            raise NotImplementedError()

        return self.__class__({**other, **self})  # pyright: ignore[reportUnknownArgumentType]

    def __ior__(
        self,
        other: object,
    ) -> Self:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, |= is not supported"
        )

    def copy(self) -> Self:
        return self  # Map is immutable, no need to provide an actual copy

    def __copy__(self) -> Self:
        return self  # Map is immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        return self  # Map is immutable, no need to provide an actual copy
