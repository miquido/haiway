from asyncio import AbstractEventLoop, get_running_loop, iscoroutinefunction
from collections.abc import Callable, Coroutine
from concurrent.futures import Executor
from contextvars import Context, copy_context
from functools import partial
from typing import Any, cast, overload

from haiway.types.missing import MISSING, Missing

__all__ = [
    "asynchronous",
    "wrap_async",
]


def wrap_async[**Args, Result](
    function: Callable[Args, Coroutine[Any, Any, Result]] | Callable[Args, Result],
    /,
) -> Callable[Args, Coroutine[Any, Any, Result]]:
    if iscoroutinefunction(function):
        return function

    else:

        async def async_function(*args: Args.args, **kwargs: Args.kwargs) -> Result:
            return cast(Callable[Args, Result], function)(*args, **kwargs)

        _mimic_async(function, within=async_function)
        return async_function


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
    loop: AbstractEventLoop | None = None,
    executor: Executor | Missing = MISSING,
) -> (
    Callable[
        [Callable[Args, Result]],
        Callable[Args, Coroutine[Any, Any, Result]],
    ]
    | Callable[Args, Coroutine[Any, Any, Result]]
):
    """\
    Wrapper for a sync function to convert it to an async function. \
    When specified an executor, it can be used to wrap long running or blocking synchronous \
    operations within coroutines system.

    Parameters
    ----------
    function: Callable[Args, Result]
        function to be wrapped as running in loop executor.
    loop: AbstractEventLoop | None
        loop used to call the function. When None was provided the loop currently running while \
        executing the function will be used. Default is None.
    executor: Executor | Missing
        executor used to run the function. When not provided (Missing) default loop executor\
         will be used.

    Returns
    -------
    Callable[_Args, _Result]
        function wrapped to async using loop executor.
    """

    def wrap(
        wrapped: Callable[Args, Result],
    ) -> Callable[Args, Coroutine[Any, Any, Result]]:
        assert not iscoroutinefunction(wrapped), "Cannot wrap async function in executor"  # nosec: B101

        return _ExecutorWrapper(
            wrapped,
            loop=loop,
            executor=cast(Executor | None, None if executor is MISSING else executor),
        )

    if function := function:
        return wrap(wrapped=function)

    else:
        return wrap


class _ExecutorWrapper[**Args, Result]:
    __slots__ = (
        "__annotations__",
        "__defaults__",
        "__doc__",
        "__globals__",
        "__kwdefaults__",
        "__name__",
        "__qualname__",
        "__wrapped__",
        "_executor",
        "_function",
        "_loop",
    )

    def __init__(
        self,
        function: Callable[Args, Result],
        /,
        loop: AbstractEventLoop | None,
        executor: Executor | None,
    ) -> None:
        self._function: Callable[Args, Result] = function
        self._loop: AbstractEventLoop | None = loop
        self._executor: Executor | None = executor

        # mimic function attributes if able
        _mimic_async(function, within=self)

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        context: Context = copy_context()
        return await (self._loop or get_running_loop()).run_in_executor(
            self._executor,
            context.run,
            partial(self._function, *args, **kwargs),
        )

    def __get__(
        self,
        instance: object,
        owner: type | None = None,
        /,
    ) -> Callable[Args, Coroutine[Any, Any, Result]]:
        if owner is None:
            return self

        else:
            return _mimic_async(
                self._function,
                within=partial(
                    self.__method_call__,
                    instance,
                ),
            )

    async def __method_call__(
        self,
        __method_self: object,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        return await (self._loop or get_running_loop()).run_in_executor(
            self._executor,
            partial(self._function, __method_self, *args, **kwargs),
        )


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
        setattr(  # noqa: B010
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
            setattr(
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

    setattr(  # noqa: B010 - mimic functools.wraps behavior for correct signature checks
        within,
        "__wrapped__",
        function,
    )

    return cast(
        Callable[Args, Coroutine[Any, Any, Result]],
        within,
    )
