from typing import Any, final

__all__ = ("ImmutableDict",)


@final
class ImmutableDict[Key, Value](dict[Key, Value]):
    def __setitem__(
        self,
        key: Key,
        value: Value,
        /,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" key - '{key}' cannot be modified"
        )

    def __delitem__(
        self,
        key: Key,
        /,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" key - '{key}' cannot be deleted"
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
