from asyncio import (
    AbstractEventLoop,
    CancelledError,
    Future,
    get_running_loop,
)
from collections.abc import AsyncIterator

__all__ = ("AsyncStream",)


class AsyncStream[Element](AsyncIterator[Element]):
    """
    An asynchronous stream implementation supporting push-based async iteration.

    AsyncStream provides a way to create a stream of elements where producers can
    asynchronously send elements and consumers can iterate over them using async
    iteration. Only one consumer can iterate through the stream at a time.

    This class implements a flow-controlled stream where the producer waits until
    the consumer is ready to receive the next element, ensuring back-pressure.

    Unlike AsyncQueue, AsyncStream cannot be reused for multiple iterations and
    requires coordination between producer and consumer.
    """

    def __init__(
        self,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        """
        Initialize a new asynchronous stream.

        Parameters
        ----------
        loop : AbstractEventLoop | None, default=None
            Event loop to use for async operations. If None, the running loop is used.
        """
        self._loop: AbstractEventLoop = loop or get_running_loop()
        self._ready: Future[None] = self._loop.create_future()
        self._waiting: Future[Element] | None = None
        self._finish_reason: BaseException | None = None

    @property
    def finished(self) -> bool:
        """
        Check if the stream has been marked as finished.

        Returns
        -------
        bool
            True if the stream has been finished, False otherwise
        """
        return self._finish_reason is not None

    async def send(
        self,
        element: Element,
        /,
    ) -> None:
        """
        Send an element to the stream.

        This method waits until the consumer is ready to receive the element,
        implementing back-pressure. If the stream is finished, the element will
        be silently discarded.

        Parameters
        ----------
        element : Element
            The element to send to the stream
        """
        if self._finish_reason is not None:
            return  # already finished

        # wait for readiness
        await self._ready
        # we could finish while waiting
        if self._finish_reason is not None:
            return  # already finished

        assert self._waiting is not None and not self._waiting.done()  # nosec: B101
        # send the element
        self._waiting.set_result(element)
        # and create new readiness future afterwards
        self._ready = self._loop.create_future()

    def finish(
        self,
        exception: BaseException | None = None,
    ) -> None:
        """
        Mark the stream as finished, optionally with an exception.

        After finishing, sent elements will be silently discarded. The consumer
        will receive the provided exception or StopAsyncIteration when attempting
        to get the next element.

        If the stream is already finished, this method does nothing.

        Parameters
        ----------
        exception : BaseException | None, default=None
            Optional exception to raise in the consumer. If None, StopAsyncIteration
            is used to signal normal completion.
        """
        if self.finished:
            return  # already finished, ignore

        self._finish_reason = exception or StopAsyncIteration()

        if not self._ready.done():
            if get_running_loop() is not self._loop:
                self._loop.call_soon_threadsafe(
                    self._ready.set_result,
                    None,
                )

            else:
                self._ready.set_result(None)

        if self._waiting is not None and not self._waiting.done():
            if get_running_loop() is not self._loop:
                self._loop.call_soon_threadsafe(
                    self._waiting.set_exception,
                    self._finish_reason,
                )

            else:
                self._waiting.set_exception(self._finish_reason)

    def cancel(self) -> None:
        """
        Cancel the stream with a CancelledError exception.

        This is a convenience method that calls finish() with a CancelledError.
        The consumer will receive this exception when attempting to get the next element.
        """
        self.finish(exception=CancelledError())

    async def __anext__(self) -> Element:
        """
        Get the next element from the stream.

        This method is called automatically when the stream is used in an
        async for loop. It waits for the next element to be sent or for
        the stream to be finished.

        Returns
        -------
        Element
            The next element from the stream

        Raises
        ------
        BaseException
            The exception provided to finish(), or StopAsyncIteration if
            finish() was called without an exception
        AssertionError
            If the stream is being consumed by multiple consumers
        """
        assert self._waiting is None, "AsyncStream can't be reused"  # nosec: B101

        if self._finish_reason:
            raise self._finish_reason

        try:
            assert not self._ready.done()  # nosec: B101
            # create new waiting future
            self._waiting = self._loop.create_future()
            # and notify readiness
            self._ready.set_result(None)
            # and wait for the result
            return await self._waiting

        finally:
            # cleanup waiting future
            self._waiting = None
