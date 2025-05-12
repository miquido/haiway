from collections.abc import Callable, Coroutine
from typing import Any

__all__ = (
    "always",
    "async_always",
)


def always[Value](
    value: Value,
    /,
) -> Callable[..., Value]:
    """
    Factory method creating functions returning always the same value.

    Parameters
    ----------
    value: Value
        value to be always returned from prepared function

    Returns
    -------
    Callable[..., Value]
        function ignoring arguments and always returning the provided value.
    """

    def always_value(
        *args: Any,
        **kwargs: Any,
    ) -> Value:
        return value

    return always_value


def async_always[Value](
    value: Value,
    /,
) -> Callable[..., Coroutine[Any, Any, Value]]:
    """
    Factory method creating async functions returning always the same value.

    Parameters
    ----------
    value: Value
        value to be always returned from prepared function

    Returns
    -------
    Callable[..., Coroutine[Any, Any, Value]]
        async function ignoring arguments and always returning the provided value.
    """

    async def always_value(
        *args: Any,
        **kwargs: Any,
    ) -> Value:
        return value

    return always_value
