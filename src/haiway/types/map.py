"""Utilities for working with immutable mapping-like objects."""

import json
from collections.abc import Iterable, Iterator, Mapping
from types import EllipsisType
from typing import Any, ClassVar, Self, final

__all__ = ("Map",)


@final
class Map[Key, Element](Mapping[Key, Element]):
    """An immutable ``Mapping`` wrapper with convenience conversion helpers."""

    __IMMUTABLE__: ClassVar[EllipsisType] = ...

    __slots__ = ("_elements",)

    def __init__(
        self,
        values: Mapping[Key, Element] | Iterable[tuple[Key, Element]],
        /,
    ):
        """Create a ``Map`` from a mapping or key/value iterable."""
        self._elements: Mapping[Key, Element]
        match values:
            case {**elements}:
                object.__setattr__(
                    self,
                    "_elements",
                    elements,
                )

            case items:
                object.__setattr__(
                    self,
                    "_elements",
                    dict(items),
                )

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
        return json.dumps(self._elements)

    def __bool__(self) -> bool:
        """Return ``True`` when the map contains at least one item."""
        return bool(self._elements)

    def __contains__(
        self,
        element: Any,
    ) -> bool:
        """Return ``True`` when ``element`` exists as a key in the map."""
        return self._elements.__contains__(element)

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
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
    ) -> Any:
        """Prevent item mutation, enforcing immutability."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" item - '{key}' cannot be modified"
        )

    def __delitem__(
        self,
        key: Key,
    ) -> Element:
        """Prevent item deletion, enforcing immutability."""
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" item - '{key}' cannot be deleted"
        )

    def __getitem__(
        self,
        key: Key,
    ) -> Element:
        """Return the value stored for ``key``."""
        return self._elements[key]

    def __iter__(self) -> Iterator[Key]:
        """Iterate over stored keys in insertion order."""
        return iter(self._elements)

    def __len__(self) -> int:
        """Return the number of stored key/value pairs."""
        return len(self._elements)

    def __copy__(self) -> Self:
        """Return ``self`` because the structure is immutable."""
        return self  # Map is immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        """Return ``self`` because the structure is immutable."""
        return self  # Map is immutable, no need to provide an actual copy
