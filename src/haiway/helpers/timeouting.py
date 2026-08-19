from asyncio import timeout as async_timeout
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any

__all__ = ("timeout",)


def timeout[**Args, Result](
    timeout: float,
    /,
) -> Callable[
    [Callable[Args, Coroutine[Any, Any, Result]]],
    Callable[Args, Coroutine[Any, Any, Result]],
]:
    """
    Add a timeout to an asynchronous function.

    This decorator enforces a maximum execution time for the decorated function.
    If the function does not complete within the specified timeout period, it
    will be cancelled and a TimeoutError will be raised.

    Parameters
    ----------
    timeout: float
        Maximum execution time in seconds allowed for the function

    Returns
    -------
    Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Callable[Args, Coroutine[Any, Any, Result]]]
        A decorator that can be applied to an async function to add timeout behavior

    Notes
    -----
    - Works only with asynchronous functions.
    - The wrapped function will be properly cancelled when the timeout occurs.
      TimeoutError is raised only after the cancellation has fully unwound, so
      the caller can rely on all resources being released once it observes it.
    - The wrapped function runs within the caller's task, its context is shared.
    - Not thread-safe, should only be used within a single event loop.
    - The original function should handle cancellation properly to ensure
      resources are released when timeout occurs.

    Examples
    --------
    >>> @timeout(5.0)
    ... async def fetch_data(url):
    ...     # Will raise TimeoutError if it takes more than 5 seconds
    ...     return await http_client.get(url)
    """  # noqa: E501

    def _wrap(
        function: Callable[Args, Coroutine[Any, Any, Result]],
    ) -> Callable[Args, Coroutine[Any, Any, Result]]:
        return wraps(function)(
            _AsyncTimeout(
                function,
                timeout=timeout,
            )
        )

    return _wrap


class _AsyncTimeout[**Args, Result]:
    __slots__ = (
        "__annotations__",
        "__defaults__",
        "__dict__",
        "__doc__",
        "__globals__",
        "__kwdefaults__",
        "__name__",
        "__qualname__",
        "__wrapped__",
        "_function",
        "_timeout",
    )

    def __init__(
        self,
        function: Callable[Args, Coroutine[Any, Any, Result]],
        /,
        timeout: float,
    ) -> None:
        self._function: Callable[Args, Coroutine[Any, Any, Result]] = function
        self._timeout: float = timeout

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        # asyncio.timeout awaits the cancellation it triggers before raising,
        # and takes care of distinguishing it from cancellation coming from outside
        async with async_timeout(self._timeout):
            return await self._function(
                *args,
                **kwargs,
            )
