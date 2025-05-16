from asyncio import AbstractEventLoop, CancelledError, Future, get_running_loop
from collections import deque
from collections.abc import AsyncIterator
from typing import Any

__all__ = ("AsyncQueue",)


class AsyncQueue[Element](AsyncIterator[Element]):
    """
    Asynchronous queue supporting iteration and finishing.

    A queue implementation optimized for asynchronous workflows, providing async
    iteration over elements and supporting operations like enqueuing elements,
    finishing the queue, and cancellation.

    Cannot be concurrently consumed by multiple readers - only one consumer
    can iterate through the queue at a time.

    Parameters
    ----------
    *elements : Element
        Initial elements to populate the queue with
    loop : AbstractEventLoop | None, default=None
        Event loop to use for async operations. If None, the running loop is used.

    Notes
    -----
    This class is immutable with respect to its attributes after initialization.
    Any attempt to modify its attributes directly will raise an AttributeError.
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
        """
        Check if the queue has been marked as finished.

        Returns
        -------
        bool
            True if the queue has been finished, False otherwise
        """
        return self._finish_reason is not None

    def enqueue(
        self,
        element: Element,
        /,
    ) -> None:
        """
        Add an element to the queue.

        If a consumer is waiting for an element, it will be immediately notified.
        Otherwise, the element is appended to the queue.

        Parameters
        ----------
        element : Element
            The element to add to the queue

        Raises
        ------
        RuntimeError
            If the queue has already been finished
        """
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
        """
        Mark the queue as finished, optionally with an exception.

        After finishing, no more elements can be enqueued. Any waiting consumers
        will be notified with the provided exception or StopAsyncIteration.
        If the queue is already finished, this method does nothing.

        Parameters
        ----------
        exception : BaseException | None, default=None
            Optional exception to raise in consumers. If None, StopAsyncIteration
            is used to signal normal completion.
        """
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
        """
        Cancel the queue with a CancelledError exception.

        This is a convenience method that calls finish() with a CancelledError.
        Any waiting consumers will receive this exception.
        """
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

    def clear(self) -> None:
        """
        Clear all pending elements from the queue.

        This method removes all elements currently in the queue. It will only
        clear the queue if no consumer is currently waiting for an element.
        """
        if self._waiting is None or self._waiting.done():
            self._queue.clear()
