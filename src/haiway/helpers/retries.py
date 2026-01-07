from asyncio import CancelledError, sleep
from collections.abc import Callable, Coroutine
from functools import wraps
from inspect import iscoroutinefunction
from time import sleep as sleep_sync
from typing import Any, cast, overload

from haiway.context import ctx

__all__ = ("retry",)


@overload
def retry[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Result]:
    """
    Retry the supplied function with the default configuration.

    Parameters
    ----------
    function: Callable[Args, Result]
        Function to wrap. When used as ``@retry`` the value is provided
        automatically.

    Returns
    -------
    Callable[Args, Result]
        The wrapped function. It will retry once on handled exceptions,
        propagating ``CancelledError`` immediately.
    """


@overload
def retry[**Args, Result](
    *,
    limit: int = 1,
    delay: Callable[[int, Exception], float] | float | int | None = None,
    catching: Callable[[Exception], bool] | type[Exception] = Exception,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]]:
    """
    Configure the retry decorator.

    Parameters
    ----------
    limit: int, default=1
        Maximum number of retry attempts after the initial call. Must be greater than
        zero.
    delay: Callable[[int, Exception], float] | float | int | None, default=None
        Delay between attempts in seconds. ``Callable`` receives the retry attempt
        number (starting at 1) and the raised exception.
    catching: Callable[[Exception], bool] | type[Exception], default=Exception
        Predicate or exception type deciding whether a raised exception should trigger
        another attempt. ``CancelledError`` is always propagated.

    Returns
    -------
    Callable[[Callable[Args, Result]], Callable[Args, Result]]
        Decorator that applies retry logic to the target function.
    """


def retry[**Args, Result](
    function: Callable[Args, Result] | None = None,
    *,
    limit: int = 1,
    delay: Callable[[int, Exception], float] | float | int | None = None,
    catching: Callable[[Exception], bool] | type[Exception] = Exception,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]] | Callable[Args, Result]:
    """
    Automatically retry a function when it raises handled exceptions.

    The decorator can be used without arguments (``@retry``) or configured with keyword
    arguments (``@retry(limit=3, delay=1.0)``). It supports both synchronous and
    asynchronous callables and preserves the wrapped function's metadata.

    Parameters
    ----------
    function: Callable[Args, Result] | None
        The function to wrap. When used as ``@retry`` this argument is injected
        automatically.
    limit: int, default=1
        Maximum number of retry attempts after the initial call. The function can be
        executed at most ``limit + 1`` times.
    delay: Callable[[int, Exception], float] | float | int | None, default=None
        Delay between retry attempts in seconds. May be:
          - ``None``: retries occur immediately.
          - ``float`` or ``int``: fixed delay applied before every retry.
          - ``Callable``: invoked with the retry attempt number (starting at 1) and the
            most recent exception to compute a delay.
    catching: Callable[[Exception], bool] | type[Exception]
        Predicate or exception type that determines whether the raised exception should
        trigger another attempt. ``CancelledError`` is always propagated, even when the
        predicate returns ``True`` or the type matches.

    Returns
    -------
    Callable
        ``@retry`` returns the wrapped function. ``@retry(...)`` returns a decorator
        that can be applied to a function.

    Notes
    -----
    - Works with both synchronous and asynchronous functions.
    - Always propagates ``asyncio.CancelledError`` regardless of the ``catching`` value.
    - Preserves the wrapped function's signature, docstring, and other attributes.

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

    catch_check: Callable[[Exception], bool]
    if isinstance(catching, type):

        def check(exc: Exception) -> bool:
            return isinstance(exc, catching)

        catch_check = check

    else:
        catch_check = catching

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
                    catching=catch_check,
                ),
            )

        else:
            return _wrap_sync(
                function,
                limit=limit,
                delay=delay,
                catching=catch_check,
            )

    if function is not None:
        return _wrap(function)
    else:
        return _wrap


def _wrap_sync[**Args, Result](
    function: Callable[Args, Result],
    *,
    limit: int,
    delay: Callable[[int, Exception], float] | float | int | None,
    catching: Callable[[Exception], bool],
) -> Callable[Args, Result]:
    assert limit > 0, "Limit has to be greater than zero"  # nosec: B101

    @wraps(function)
    def wrapped(
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        attempt: int = 0
        while True:
            try:
                return function(*args, **kwargs)
            except CancelledError:
                raise

            except Exception as exc:
                if attempt < limit and catching(exc):
                    attempt += 1
                    ctx.log_error(
                        "Attempting to retry %s which failed due to an error: %s",
                        function.__name__,
                        exc,
                    )

                    match delay:
                        case None:
                            continue

                        case float(strict) | int(strict):
                            sleep_sync(float(strict))

                        case make_delay:
                            sleep_sync(make_delay(attempt, exc))  # pyright: ignore[reportCallIssue, reportUnknownArgumentType]

                else:
                    raise

    return wrapped


def _wrap_async[**Args, Result](
    function: Callable[Args, Coroutine[Any, Any, Result]],
    *,
    limit: int,
    delay: Callable[[int, Exception], float] | float | int | None,
    catching: Callable[[Exception], bool],
) -> Callable[Args, Coroutine[Any, Any, Result]]:
    assert limit > 0, "Limit has to be greater than zero"  # nosec: B101

    @wraps(function)
    async def wrapped(
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        attempt: int = 0
        while True:
            try:
                return await function(*args, **kwargs)
            except CancelledError:
                raise

            except Exception as exc:
                if attempt < limit and catching(exc):
                    attempt += 1
                    ctx.log_error(
                        "Attempting to retry %s which failed due to an error",
                        function.__name__,
                        exception=exc,
                    )

                    match delay:
                        case None:
                            continue

                        case float(strict) | int(strict):
                            await sleep(float(strict))

                        case make_delay:
                            await sleep(make_delay(attempt, exc))  # pyright: ignore[reportCallIssue, reportUnknownArgumentType]

                else:
                    raise

    return wrapped
