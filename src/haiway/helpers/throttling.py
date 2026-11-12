from asyncio import sleep
from bisect import bisect_right, insort
from collections.abc import Callable, Coroutine, Sequence
from contextlib import suppress
from datetime import timedelta
from functools import wraps
from inspect import iscoroutinefunction
from itertools import chain
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
        # reserved starts, kept sorted - the ones which can't limit a call anymore are
        # dropped as calls come, so a waiting call can give its reservation back
        starts: list[float] = []
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
            del starts[: bisect_right(starts, time_now - throttle_period)]
            start_at: float = _earliest_start(
                starts,
                time_now=time_now,
                limit=limit,
                period=throttle_period,
            )
            # in order - a slot given back may put this one ahead of later ones
            insort(starts, start_at)
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


def _earliest_start(
    starts: Sequence[float],
    /,
    *,
    time_now: float,
    limit: int,
    period: float,
) -> float:
    # the earliest start keeping every period within the limit - now, or a period
    # after one of the reservations, a slot given back by a cancelled call included.
    # A period after the last reservation always fits, which ends the search
    for candidate in chain((time_now,), (start + period for start in starts)):
        if candidate >= time_now and _fits(starts, candidate, limit=limit, period=period):
            return candidate

    raise AssertionError("A period after the last reservation always fits")


def _fits(
    starts: Sequence[float],
    candidate: float,
    /,
    *,
    limit: int,
    period: float,
) -> bool:
    # within a period at most `limit` starts - with the candidate among the starts,
    # each `limit + 1` consecutive ones including it span a whole period at least
    position: int = bisect_right(starts, candidate)

    def at(index: int) -> float:  # within the starts as if the candidate was among them
        if index == position:
            return candidate

        return starts[index] if index < position else starts[index - 1]

    for first in range(max(0, position - limit), min(position, len(starts) - limit) + 1):
        if at(first + limit) - at(first) < period:
            return False

    return True
