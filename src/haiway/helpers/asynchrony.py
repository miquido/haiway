from asyncio import AbstractEventLoop, get_running_loop, iscoroutinefunction
from collections.abc import Callable, Coroutine
from concurrent.futures import Executor
from contextvars import Context, copy_context
from functools import partial
from typing import Any, cast, overload

from haiway.types.missing import MISSING, Missing

__all__ = ("asynchronous",)


@overload
def asynchronous[**Args, Result]() -> Callable[
    [Callable[Args, Result]],
    Callable[Args, Coroutine[Any, Any, Result]],
]: ...


@overload
def asynchronous[**Args, Result](
    *,
    loop: AbstractEventLoop | None = None,
    executor: Executor,
) -> Callable[
    [Callable[Args, Result]],
    Callable[Args, Coroutine[Any, Any, Result]],
]: ...


@overload
def asynchronous[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Coroutine[Any, Any, Result]]: ...


def asynchronous[**Args, Result](
    function: Callable[Args, Result] | None = None,
    /,
    *,
    loop: AbstractEventLoop | None = None,
    executor: Executor | Missing = MISSING,
) -> (
    Callable[
        [Callable[Args, Result]],
        Callable[Args, Coroutine[Any, Any, Result]],
    ]
    | Callable[Args, Coroutine[Any, Any, Result]]
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
    ) -> Callable[Args, Coroutine[Any, Any, Result]]:
        assert not iscoroutinefunction(wrapped), "Cannot wrap async function in executor"  # nosec: B101

        async def asynchronous(
            *args: Args.args,
            **kwargs: Args.kwargs,
        ) -> Result:
            context: Context = copy_context()
            return await (loop or get_running_loop()).run_in_executor(
                cast(Executor | None, None if executor is MISSING else executor),
                context.run,
                partial(wrapped, *args, **kwargs),
            )

        return _mimic_async(wrapped, within=asynchronous)

    if function := function:
        return wrap(wrapped=function)

    else:
        return wrap


def _mimic_async[**Args, Result](
    function: Callable[Args, Result],
    /,
    within: Callable[..., Coroutine[Any, Any, Result]],
) -> Callable[Args, Coroutine[Any, Any, Result]]:
    try:
        annotations: Any = getattr(  # noqa: B009
            function,
            "__annotations__",
        )
        object.__setattr__(
            within,
            "__annotations__",
            {
                **annotations,
                "return": Coroutine[Any, Any, annotations.get("return", Any)],
            },
        )

    except AttributeError:
        pass

    for attribute in (
        "__module__",
        "__name__",
        "__qualname__",
        "__doc__",
        "__type_params__",
        "__defaults__",
        "__kwdefaults__",
        "__globals__",
    ):
        try:
            object.__setattr__(
                within,
                attribute,
                getattr(
                    function,
                    attribute,
                ),
            )

        except AttributeError:
            pass
    try:
        within.__dict__.update(function.__dict__)

    except AttributeError:
        pass

    object.__setattr__(  # mimic functools.wraps behavior for correct signature checks
        within,
        "__wrapped__",
        function,
    )

    return cast(
        Callable[Args, Coroutine[Any, Any, Result]],
        within,
    )
