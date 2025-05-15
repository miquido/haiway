from asyncio import AbstractEventLoop, Future, Task, TimerHandle, get_running_loop
from collections.abc import Callable, Coroutine
from typing import Any

from haiway.utils.mimic import mimic_function

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
