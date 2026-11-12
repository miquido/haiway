from asyncio import timeout as async_timeout
from collections.abc import Callable, Coroutine
from functools import wraps

__all__ = ("timeout",)


def timeout[**Args, Result](
    timeout: float,
    /,
) -> Callable[
    [Callable[Args, Coroutine[None, None, Result]]],
    Callable[Args, Coroutine[None, None, Result]],
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
    Callable[[Callable[Args, Coroutine[None, None, Result]]], Callable[Args, Coroutine[None, None, Result]]]
        A decorator that can be applied to an async function to add timeout behavior

    Notes
    -----
    - Works only with asynchronous functions.
    - The wrapped function will be properly cancelled when the timeout occurs.
      TimeoutError is raised only after the cancellation has fully unwound, so
      the caller can rely on all resources being released once it observes it.
    - The wrapped function runs within the caller's task, its context is shared.
    - Holds no state of its own, so it is safe to use from any event loop or thread.
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
        function: Callable[Args, Coroutine[None, None, Result]],
    ) -> Callable[Args, Coroutine[None, None, Result]]:
        # a plain coroutine function - it stays recognizable as one, so it can be wrapped
        # further (throttle, cache), and binds as a method like the function it wraps
        @wraps(function)
        async def timed(
            *args: Args.args,
            **kwargs: Args.kwargs,
        ) -> Result:
            # asyncio.timeout awaits the cancellation it triggers before raising,
            # and takes care of distinguishing it from cancellation coming from outside
            async with async_timeout(timeout):
                return await function(
                    *args,
                    **kwargs,
                )

        return timed

    return _wrap
