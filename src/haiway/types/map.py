"""Utilities for working with immutable mapping-like objects."""

import json
from collections.abc import Mapping
from typing import Any, NoReturn, Self, final

__all__ = ("Map",)


@final
class Map[Key, Element](dict[Key, Element]):
    """
    Immutable ``dict`` subclass with JSON helpers and persistent-style merges.

    ``Map`` behaves like a normal mapping for reads, but all mutating
    operations raise ``AttributeError``. Merge operators create new ``Map``
    instances instead of changing the original object.
    """

    __slots__ = ()

    @classmethod
    def from_json(
        cls,
        value: str | bytes,
        /,
    ) -> Self:
        """
        Deserialize a JSON object into a ``Map`` instance.

        Parameters
        ----------
        value : str | bytes
            JSON payload expected to decode to an object.

        Returns
        -------
        Self
            Immutable mapping containing the decoded key-value pairs.

        Raises
        ------
        ValueError
            If the payload does not decode to a JSON object.
        """
        match json.loads(value):
            case {**values}:
                return cls(values)

            case other:
                raise ValueError(f"Invalid json: {other}")

    def to_str(self) -> str:
        """
        Return the string representation of the map.

        Returns
        -------
        str
            Human-readable representation identical to ``str(self)``.
        """
        return self.__str__()

    def to_json(
        self,
    ) -> str:
        """
        Serialize the map into a JSON object string.

        Returns
        -------
        str
            JSON encoding of the mapping contents.
        """
        return json.dumps(self)

    def __setattr__(
        self,
        name: str,
        value: object,
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

    def __setitem__(
        self,
        key: Key,
        value: Element,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" item - '{key}' cannot be modified"
        )

    def __delitem__(
        self,
        key: Key,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__} item - '{key}' cannot be deleted"
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
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, pop is not supported"
        )

    def popitem(self) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, popitem is not supported"
        )

    def setdefault(
        self,
        key: Key,
        default: Element | None = None,
        /,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}, setdefault is not supported"
        )

    def update(
        self,
        *updates: object,
        **kwargs: Element,
    ) -> NoReturn:
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
    ) -> NoReturn:
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
