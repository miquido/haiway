from asyncio import CancelledError, iscoroutinefunction, sleep
from collections.abc import Callable, Coroutine
from time import sleep as sleep_sync
from typing import Any, cast, overload

from haiway.context import ctx
from haiway.utils import mimic_function

__all__ = ("retry",)


@overload
def retry[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Result]:
    """\
    Function wrapper retrying the wrapped function again on fail. \
    Works for both sync and async functions. \
    It is not allowed to be used on class methods. \
    This wrapper is not thread safe.

    Parameters
    ----------
    function: Callable[_Args_T, _Result_T]
        function to wrap in auto retry, either sync or async.

    Returns
    -------
    Callable[_Args_T, _Result_T]
        provided function wrapped in auto retry with default configuration.
    """


@overload
def retry[**Args, Result](
    *,
    limit: int = 1,
    delay: Callable[[int, Exception], float] | float | None = None,
    catching: set[type[Exception]] | tuple[type[Exception], ...] | type[Exception] = Exception,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]]:
    """\
    Function wrapper retrying the wrapped function again on fail. \
    Works for both sync and async functions. \
    It is not allowed to be used on class methods. \
    This wrapper is not thread safe.

    Parameters
    ----------
    limit: int
        limit of retries, default is 1
    delay: Callable[[int, Exception], float] | float | None
        retry delay time in seconds, either concrete value or a function producing it, \
        default is None (no delay)
    catching: set[type[Exception]] | type[Exception] | None
        Exception types that are triggering auto retry. Retry will trigger only when \
        exceptions of matching types (including subclasses) will occur. CancelledError \
        will be always propagated even if specified explicitly.
        Default is Exception - all subclasses of Exception will be handled.

    Returns
    -------
    Callable[[Callable[_Args_T, _Result_T]], Callable[_Args_T, _Result_T]]
        function wrapper for adding auto retry
    """


def retry[**Args, Result](
    function: Callable[Args, Result] | None = None,
    *,
    limit: int = 1,
    delay: Callable[[int, Exception], float] | float | None = None,
    catching: set[type[Exception]] | tuple[type[Exception], ...] | type[Exception] = Exception,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]] | Callable[Args, Result]:
    """
    Automatically retry a function on failure.

    This decorator attempts to execute a function and, if it fails with a specified
    exception type, retries the execution up to a configurable number of times,
    with an optional delay between attempts.

    Can be used as a simple decorator (@retry) or with configuration
    parameters (@retry(limit=3, delay=1.0)).

    Parameters
    ----------
    function: Callable[Args, Result] | None
        The function to wrap with retry logic. When used as a simple decorator,
        this parameter is provided automatically.
    limit: int
        Maximum number of retry attempts. Default is 1, meaning the function
        will be called at most twice (initial attempt + 1 retry).
    delay: Callable[[int, Exception], float] | float | None
        Delay between retry attempts in seconds. Can be:
          - None: No delay between retries (default)
          - float: Fixed delay in seconds
          - Callable: A function that calculates delay based on attempt number
            and the caught exception, allowing for backoff strategies
    catching: set[type[Exception]] | tuple[type[Exception], ...] | type[Exception]
        Exception types that should trigger retry. Can be a single exception type,
        a set, or a tuple of exception types. Default is Exception (all exception
        types except for CancelledError, which is always propagated).

    Returns
    -------
    Callable
        When used as @retry: Returns the wrapped function with retry logic.
        When used as @retry(...): Returns a decorator that can be applied to a function.

    Notes
    -----
    - Works with both synchronous and asynchronous functions.
    - Not thread-safe; concurrent invocations are not coordinated.
    - Cannot be used on class methods.
    - Always propagates asyncio.CancelledError regardless of catching parameter.
    - The function preserves the original function's signature, docstring, and other attributes.

    Examples
    --------
    Basic usage:

    >>> @retry
    ... def fetch_data():
    ...     # Will retry once if any exception occurs
    ...     return external_api.fetch()

    With configuration:

    >>> @retry(limit=3, delay=2.0, catching=ConnectionError)
    ... async def connect():
    ...     # Will retry up to 3 times with 2 second delays on ConnectionError
    ...     return await establish_connection()

    With exponential backoff:

    >>> def backoff(attempt, exception):
    ...     return 0.5 * (2 ** attempt)  # 1s, 2s, 4s, ...
    ...
    >>> @retry(limit=5, delay=backoff)
    ... def unreliable_operation():
    ...     return perform_operation()
    """

    def _wrap(
        function: Callable[Args, Result],
        /,
    ) -> Callable[Args, Result]:
        if iscoroutinefunction(function):
            return cast(
                Callable[Args, Result],
                _wrap_async(
                    function,
                    limit=limit,
                    delay=delay,
                    catching=catching if isinstance(catching, set | tuple) else {catching},
                ),
            )

        else:
            return _wrap_sync(
                function,
                limit=limit,
                delay=delay,
                catching=catching if isinstance(catching, set | tuple) else {catching},
            )

    if function := function:
        return _wrap(function)
    else:
        return _wrap


def _wrap_sync[**Args, Result](
    function: Callable[Args, Result],
    *,
    limit: int,
    delay: Callable[[int, Exception], float] | float | None,
    catching: set[type[Exception]] | tuple[type[Exception], ...],
) -> Callable[Args, Result]:
    assert limit > 0, "Limit has to be greater than zero"  # nosec: B101

    @mimic_function(function)
    def wrapped(
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        attempt: int = 0
        while True:
            try:
                return function(*args, **kwargs)
            except CancelledError as exc:
                raise exc

            except Exception as exc:
                if attempt < limit and any(isinstance(exc, exception) for exception in catching):
                    attempt += 1
                    ctx.log_error(
                        "Attempting to retry %s which failed due to an error: %s",
                        function.__name__,
                        exc,
                    )

                    match delay:
                        case None:
                            continue

                        case float(strict):
                            sleep_sync(strict)

                        case make_delay:
                            sleep_sync(make_delay(attempt, exc))  # pyright: ignore[reportCallIssue, reportUnknownArgumentType]

                else:
                    raise exc

    return wrapped


def _wrap_async[**Args, Result](
    function: Callable[Args, Coroutine[Any, Any, Result]],
    *,
    limit: int,
    delay: Callable[[int, Exception], float] | float | None,
    catching: set[type[Exception]] | tuple[type[Exception], ...],
) -> Callable[Args, Coroutine[Any, Any, Result]]:
    assert limit > 0, "Limit has to be greater than zero"  # nosec: B101

    @mimic_function(function)
    async def wrapped(
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        attempt: int = 0
        while True:
            try:
                return await function(*args, **kwargs)
            except CancelledError as exc:
                raise exc

            except Exception as exc:
                if attempt < limit and any(isinstance(exc, exception) for exception in catching):
                    attempt += 1
                    ctx.log_error(
                        "Attempting to retry %s which failed due to an error",
                        function.__name__,
                        exception=exc,
                    )

                    match delay:
                        case None:
                            continue

                        case float(strict):
                            await sleep(strict)

                        case make_delay:
                            await sleep(make_delay(attempt, exc))  # pyright: ignore[reportCallIssue, reportUnknownArgumentType]

                else:
                    raise exc

    return wrapped
