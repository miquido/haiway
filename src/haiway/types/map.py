"""Utilities for working with immutable mapping-like objects."""

import json
from collections.abc import Mapping
from types import EllipsisType
from typing import Any, ClassVar, Self, final

__all__ = ("Map",)


@final
class Map[Key, Element](dict[Key, Element]):
    """An immutable ``dict`` wrapper with convenience conversion helpers."""

    __IMMUTABLE__: ClassVar[EllipsisType] = ...

    __slots__ = ()

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[Key, Element],
        /,
    ) -> Self:
        """Build a ``Map`` directly from an existing mapping instance."""
        return cls(mapping)

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

    def to_mapping(
        self,
    ) -> Mapping[Key, Element]:
        """Expose as mapping."""
        return self

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
        """Prevent attribute mutation, enforcing immutability."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        """Prevent attribute deletion, enforcing immutability."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    def __setitem__(
        self,
        key: Key,
        value: Element,
    ) -> None:
        """Prevent item mutation, enforcing immutability."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" item - '{key}' cannot be modified"
        )

    def __delitem__(
        self,
        key: Key,
    ) -> None:
        """Prevent item deletion, enforcing immutability."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" item - '{key}' cannot be deleted"
        )

    def clear(self) -> None:
        """Prevent removing all elements via ``clear``."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, clear is not supported"
        )

    def pop(
        self,
        key: Key,
        default: Any | None = None,
        /,
    ) -> Element:
        """Prevent removing elements via ``pop``."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, pop is not supported"
        )

    def popitem(self) -> tuple[Key, Element]:
        """Prevent removing elements via ``popitem``."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, popitem is not supported"
        )

    def setdefault(
        self,
        key: Key,
        default: Element | None = None,
        /,
    ) -> Element:
        """Prevent mutation via ``setdefault``."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, setdefault is not supported"
        )

    def update(
        self,
        *updates: object,
        **kwargs: Element,
    ) -> None:
        """Prevent mutation via ``update``."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, update is not supported"
        )

    def __ior__(
        self,
        other: object,
    ) -> Self:
        """Prevent in-place union operations from mutating the map."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, |= is not supported"
        )

    def copy(self) -> Self:
        """Return ``self`` instead of a shallow copy since the map is immutable."""
        return self

    def __copy__(self) -> Self:
        """Return ``self`` because the structure is immutable."""
        return self  # Map is immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        """Return ``self`` because the structure is immutable."""
        return self  # Map is immutable, no need to provide an actual copy
