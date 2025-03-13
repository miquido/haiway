from asyncio import AbstractEventLoop, CancelledError, Future, get_running_loop
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

__all__ = [
    "AsyncQueue",
]


class AsyncQueue[Element](AsyncIterator[Element]):
    """
    Asynchronous queue supporting iteration and finishing.
    Cannot be concurrently consumed by multiple readers.
    """

    __slots__ = (
        "_finish_reason",
        "_loop",
        "_queue",
        "_waiting",
    )

    def __init__(
        self,
        *elements: Element,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        self._loop: AbstractEventLoop
        object.__setattr__(
            self,
            "_loop",
            loop or get_running_loop(),
        )
        self._queue: deque[Element]
        object.__setattr__(
            self,
            "_queue",
            deque(elements),
        )
        self._waiting: Future[Element] | None
        object.__setattr__(
            self,
            "_waiting",
            None,
        )
        self._finish_reason: BaseException | None
        object.__setattr__(
            self,
            "_finish_reason",
            None,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    @property
    def is_finished(self) -> bool:
        return self._finish_reason is not None

    def enqueue(
        self,
        element: Element,
        /,
    ) -> None:
        if self.is_finished:
            raise RuntimeError("AsyncQueue is already finished")

        if self._waiting is not None and not self._waiting.done():
            self._waiting.set_result(element)

        else:
            self._queue.append(element)

    def finish(
        self,
        exception: BaseException | None = None,
    ) -> None:
        if self.is_finished:
            return  # already finished, ignore

        object.__setattr__(
            self,
            "_finish_reason",
            exception or StopAsyncIteration(),
        )

        if self._waiting is not None and not self._waiting.done():
            # checking loop only on finish as the rest of operations
            # should always have a valid loop in a typical environment
            # and we are not supporting multithreading yet
            if get_running_loop() is not self._loop:
                self._loop.call_soon_threadsafe(
                    self._waiting.set_exception,
                    self._finish_reason,  # pyright: ignore[reportArgumentType]
                )

            else:
                self._waiting.set_exception(self._finish_reason)  # pyright: ignore[reportArgumentType]

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
            object.__setattr__(
                self,
                "_waiting",
                self._loop.create_future(),
            )
            # wait for the result
            return await self._waiting  # pyright: ignore[reportGeneralTypeIssues]

        finally:
            # cleanup
            object.__setattr__(
                self,
                "_waiting",
                None,
            )
