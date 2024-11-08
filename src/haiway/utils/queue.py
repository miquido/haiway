from asyncio import AbstractEventLoop, CancelledError, Future, get_running_loop
from collections import deque
from collections.abc import AsyncIterator

__all__ = [
    "AsyncQueue",
]


class AsyncQueue[Element](AsyncIterator[Element]):
    """
    Asynchronous queue supporting iteration and finishing.
    Cannot be concurrently consumed by multiple readers.
    """

    def __init__(
        self,
        *elements: Element,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        self._loop: AbstractEventLoop = loop or get_running_loop()
        self._queue: deque[Element] = deque(elements)
        self._waiting: Future[Element] | None = None
        self._finish_reason: BaseException | None = None

    def __del__(self) -> None:
        self.finish()

    @property
    def is_finished(self) -> bool:
        return self._finish_reason is not None

    def enqueue(
        self,
        element: Element,
        /,
        *elements: Element,
    ) -> None:
        if self.is_finished:
            raise RuntimeError("AsyncQueue is already finished")

        if self._waiting is not None and not self._waiting.done():
            self._waiting.set_result(element)

        else:
            self._queue.append(element)

        self._queue.extend(elements)

    def finish(
        self,
        exception: BaseException | None = None,
    ) -> None:
        if self.is_finished:
            return  # already finished, ignore

        self._finish_reason = exception or StopAsyncIteration()

        if self._waiting is not None and not self._waiting.done():
            self._waiting.set_exception(self._finish_reason)

    def cancel(self) -> None:
        self.finish(exception=CancelledError())

    async def __anext__(self) -> Element:
        assert self._waiting is None, "Only a single queue consumer is supported!"  # nosec: B101

        if self._queue:  # check the queue, let it finish
            return self._queue.popleft()

        if self._finish_reason is not None:  # check if is finished
            raise self._finish_reason

        try:
            # create a new future to wait for next
            self._waiting = self._loop.create_future()
            # wait for the result
            return await self._waiting

        finally:
            # cleanup
            self._waiting = None
