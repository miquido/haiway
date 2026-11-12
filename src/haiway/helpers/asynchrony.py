from asyncio import AbstractEventLoop, get_running_loop
from collections.abc import Callable, Coroutine
from concurrent.futures import Executor
from contextvars import Context, copy_context
from functools import partial, update_wrapper
from inspect import iscoroutinefunction
from typing import overload

from haiway.types.missing import Missing

__all__ = ("asynchronous",)


@overload
def asynchronous[**Args, Result]() -> Callable[
    [Callable[Args, Result]],
    Callable[Args, Coroutine[None, None, Result]],
]: ...


@overload
def asynchronous[**Args, Result](
    *,
    loop: AbstractEventLoop | None = None,
    executor: Executor | Missing = Missing,
) -> Callable[
    [Callable[Args, Result]],
    Callable[Args, Coroutine[None, None, Result]],
]: ...


@overload
def asynchronous[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Coroutine[None, None, Result]]: ...


def asynchronous[**Args, Result](
    function: Callable[Args, Result] | None = None,
    /,
    *,
    loop: AbstractEventLoop | None = None,
    executor: Executor | Missing = Missing,
) -> (
    Callable[
        [Callable[Args, Result]],
        Callable[Args, Coroutine[None, None, Result]],
    ]
    | Callable[Args, Coroutine[None, None, Result]]
):
    """
    Convert a synchronous function to an asynchronous one that runs in an executor.

    This decorator transforms synchronous, potentially blocking functions into
    asynchronous coroutines that execute in an event loop's executor, allowing
    them to be used with async/await syntax without blocking the event loop.

    Can be used as a simple decorator (@asynchronous) or with configuration
    parameters (@asynchronous(executor=my_executor)).

    Parameters
    ----------
    function: Callable[Args, Result] | None
        The synchronous function to be wrapped. When used as a simple decorator,
        this parameter is provided automatically.
    loop: AbstractEventLoop | None
        The event loop to run the function in. When None is provided, the currently
        running loop while executing the function will be used. Default is None.
    executor: Executor | Missing
        The executor used to run the function. When not provided, the default loop
        executor will be used. Useful for CPU-bound tasks or operations that would
        otherwise block the event loop.

    Returns
    -------
    Callable
        When used as @asynchronous: Returns the wrapped function that can be awaited.
        When used as @asynchronous(...): Returns a decorator that can be applied to a function.

    Notes
    -----
    The function preserves the original function's signature, docstring, and other attributes.
    Context variables from the calling context are preserved when executing in the executor.

    Examples
    --------
    Basic usage:

    >>> @asynchronous
    ... def cpu_intensive_task(data):
    ...     # This runs in the default executor
    ...     return process_data(data)
    ...
    >>> await cpu_intensive_task(my_data)  # Non-blocking

    With custom executor:

    >>> @asynchronous(executor=process_pool)
    ... def cpu_intensive_task(data):
    ...     return process_data(data)
    """

    def wrap(
        wrapped: Callable[Args, Result],
    ) -> Callable[Args, Coroutine[None, None, Result]]:
        assert not iscoroutinefunction(wrapped), "Cannot wrap async function in executor"  # nosec: B101

        async def asynchronous(
            *args: Args.args,
            **kwargs: Args.kwargs,
        ) -> Result:
            context: Context = copy_context()
            # submitted from an empty context - a thread the executor starts for it would
            # otherwise inherit the context of the caller (the default without the GIL),
            # keeping its state alive for as long as the thread lives. the call itself runs
            # within the copy of the caller's context, as ever
            return await Context().run(
                (loop or get_running_loop()).run_in_executor,
                None if executor is Missing else executor,
                context.run,
                partial(wrapped, *args, **kwargs),
            )

        # the attributes of the function, its annotations evaluated only once read - they may
        # name what is declared after it - and `__wrapped__`, which its signature is read from
        update_wrapper(asynchronous, wrapped)
        return asynchronous

    if function is not None:
        return wrap(wrapped=function)

    else:
        return wrap
