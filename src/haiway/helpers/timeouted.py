from asyncio import AbstractEventLoop, Future, Task, TimerHandle, get_running_loop
from collections.abc import Callable, Coroutine
from typing import Any

from haiway.utils.mimic import mimic_function

__all__ = [
    "timeout",
]


def timeout[**Args, Result](
    timeout: float,
    /,
) -> Callable[
    [Callable[Args, Coroutine[Any, Any, Result]]],
    Callable[Args, Coroutine[Any, Any, Result]],
]:
    """\
    Timeout wrapper for a function call. \
    When the timeout time will pass before function returns function execution will be \
    cancelled and TimeoutError exception will raise. Make sure that wrapped \
    function handles cancellation properly.
    This wrapper is not thread safe.

    Parameters
    ----------
    timeout: float
        timeout time in seconds

    Returns
    -------
    Callable[[Callable[_Args, _Result]], Callable[_Args, _Result]] | Callable[_Args, _Result]
        function wrapper adding timeout
    """

    def _wrap(
        function: Callable[Args, Coroutine[Any, Any, Result]],
    ) -> Callable[Args, Coroutine[Any, Any, Result]]:
        return _AsyncTimeout(
            function,
            timeout=timeout,
        )

    return _wrap


class _AsyncTimeout[**Args, Result]:
    __slots__ = (
        "__annotations__",
        "__defaults__",
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

        # mimic function attributes if able
        mimic_function(function, within=self)

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        loop: AbstractEventLoop = get_running_loop()
        future: Future[Result] = loop.create_future()
        task: Task[Result] = loop.create_task(
            self._function(
                *args,
                **kwargs,
            ),
        )

        def on_timeout(
            future: Future[Result],
        ) -> None:
            if future.done():
                return  # ignore if already finished

            # result future on its completion will ensure that task will complete
            future.set_exception(TimeoutError())

        timeout_handle: TimerHandle = loop.call_later(
            self._timeout,
            on_timeout,
            future,
        )

        def on_completion(
            task: Task[Result],
        ) -> None:
            timeout_handle.cancel()  # at this stage we no longer need timeout to trigger

            if future.done():
                return  # ignore if already finished

            try:
                future.set_result(task.result())

            except Exception as exc:
                future.set_exception(exc)

        task.add_done_callback(on_completion)

        def on_result(
            future: Future[Result],
        ) -> None:
            task.cancel()  # when result future completes make sure that task also completes

        future.add_done_callback(on_result)

        return await future
