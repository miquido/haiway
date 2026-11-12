from typing import Any, NoReturn

__all__ = (
    "refuse_assignment",
    "refuse_deletion",
)


def refuse_assignment(
    self: object,
    name: str,
    value: Any,
    /,
) -> NoReturn:
    """Guard making an instance refuse to rebind its attributes.

    Assigned as ``__setattr__`` of a final, slotted class which binds its values once,
    from ``__init__``, through ``object.__setattr__``.

    Raises
    ------
    AttributeError
        Always.
    """
    raise AttributeError(
        f"Can't modify immutable {type(self).__name__}, attribute - '{name}' cannot be modified"
    )


def refuse_deletion(
    self: object,
    name: str,
    /,
) -> NoReturn:
    """Guard making an instance refuse to delete its attributes.

    Assigned as ``__delattr__`` of a final, slotted class which binds its values once.

    Raises
    ------
    AttributeError
        Always.
    """
    raise AttributeError(
        f"Can't modify immutable {type(self).__name__}, attribute - '{name}' cannot be deleted"
    )
