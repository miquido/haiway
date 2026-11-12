from asyncio import sleep
from collections import deque
from collections.abc import Callable, Coroutine
from contextlib import suppress
from datetime import timedelta
from functools import wraps
from inspect import iscoroutinefunction
from time import monotonic
from typing import overload

__all__ = ("throttle",)


@overload
def throttle[**Args, Result](
    function: Callable[Args, Coroutine[None, None, Result]],
    /,
) -> Callable[Args, Coroutine[None, None, Result]]: ...


@overload
def throttle[**Args, Result](
    *,
    limit: int = 1,
    period: timedelta | float = 1,
) -> Callable[
    [Callable[Args, Coroutine[None, None, Result]]], Callable[Args, Coroutine[None, None, Result]]
]: ...


def throttle[**Args, Result](
    function: Callable[Args, Coroutine[None, None, Result]] | None = None,
    *,
    limit: int = 1,
    period: timedelta | float = 1,
) -> (
    Callable[
        [Callable[Args, Coroutine[None, None, Result]]],
        Callable[Args, Coroutine[None, None, Result]],
    ]
    | Callable[Args, Coroutine[None, None, Result]]
):
    """
    Rate-limit asynchronous function calls.

    This decorator restricts the frequency of function calls by enforcing a maximum
    number of executions within a specified time period. When the limit is reached,
    subsequent calls will wait until they can be executed without exceeding the limit.

    Can be used as a simple decorator (@throttle) or with configuration
    parameters (@throttle(limit=5, period=60)).

    Parameters
    ----------
    function: Callable[Args, Coroutine[None, None, Result]] | None
        The async function to throttle. When used as a simple decorator,
        this parameter is provided automatically.
    limit: int
        Maximum number of executions allowed within the specified period.
        Default is 1, meaning only one call is allowed per period.
    period: timedelta | float
        Time window in which the limit applies. Can be specified as a timedelta
        object or as a float (seconds). Default is 1 second.

    Returns
    -------
    Callable
        When used as @throttle: Returns the wrapped function that enforces the rate limit.
        When used as @throttle(...): Returns a decorator that can be applied to a function.

    Notes
    -----
    - Works only with asynchronous functions.
    - Cannot be used on class or instance methods.
    - Not thread-safe, should only be used within a single event loop.
    - The function preserves the original function's signature, docstring, and other attributes.

    Examples
    --------
    Basic usage to limit to 1 call per second:

    >>> @throttle
    ... async def api_call(data):
    ...     return await external_api.send(data)

    Limit to 5 calls per minute:

    >>> @throttle(limit=5, period=60)
    ... async def api_call(data):
    ...     return await external_api.send(data)
    """

    def _wrap(
        function: Callable[Args, Coroutine[None, None, Result]],
    ) -> Callable[Args, Coroutine[None, None, Result]]:
        assert iscoroutinefunction(function)  # nosec: B101
        assert limit > 0, "Throttle limit has to be greater than 0"  # nosec: B101
        # reserved starts, in order - the ones which can't limit a call anymore are
        # dropped as calls come, so a waiting call can give its reservation back
        starts: deque[float] = deque()
        throttle_period: float
        match period:
            case timedelta() as delta:
                throttle_period = delta.total_seconds()

            case period_seconds:
                throttle_period = period_seconds

        assert throttle_period >= 0, "Throttle period can't be negative"  # nosec: B101

        @wraps(function)
        async def throttle(
            *args: Args.args,
            **kwargs: Args.kwargs,
        ) -> Result:
            # reserve the slot without awaiting, so no other call can interleave
            time_now: float = monotonic()
            while starts and starts[0] + throttle_period <= time_now:
                starts.popleft()

            start_at: float = time_now
            if len(starts) >= limit:  # a period after the last `limit` reserved starts
                start_at = max(time_now, starts[-limit] + throttle_period)

            starts.append(start_at)
            if start_at > time_now:
                try:
                    await sleep(start_at - time_now)

                except BaseException:
                    # a call which never started does not count - any subset of the
                    # reservations still keeps every period within the limit
                    with suppress(ValueError):
                        starts.remove(start_at)

                    raise

            return await function(*args, **kwargs)

        return throttle

    if function is not None:
        return _wrap(function)

    else:
        return _wrap
