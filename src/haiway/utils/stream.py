from asyncio import (
    AbstractEventLoop,
    CancelledError,
    Future,
    get_running_loop,
)
from collections import deque
from collections.abc import AsyncGenerator
from types import TracebackType
from typing import NoReturn, final

from haiway.utils.exceptions import thrown_exception

__all__ = ("AsyncStream",)


@final
class AsyncStream[Element](AsyncGenerator[Element]):
    """
    An asynchronous stream implementation supporting push-based async iteration.

    AsyncStream provides a way to create a stream of elements where producers
    asynchronously send elements and a single consumer iterates over them using
    async iteration.

    This class implements a flow-controlled stream. When no consumer is waiting,
    a producer calling :meth:`send` waits until that element is consumed,
    providing back-pressure.

    Unlike :class:`AsyncQueue`, this primitive is designed for paced handoff
    rather than unconstrained buffering.
    """

    __slots__ = (
        "_finish_reason",
        "_loop",
        "_pending",
        "_waiting",
    )

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
        self._pending: deque[tuple[Element, Future[None]]] = deque()
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

        If a consumer is already waiting, the element is delivered immediately.
        Otherwise the element is queued and this call waits until the consumer
        takes it, implementing back-pressure. If the stream is finished, the
        element is silently discarded.

        Parameters
        ----------
        element : Element
            The element to send to the stream
        """
        assert get_running_loop() is self._loop  # nosec: B101

        if self._finish_reason is not None:
            return  # already finished

        # fulfill waiting first
        if self._waiting is not None and not self._waiting.done():
            self._waiting.set_result(element)

        else:  # otherwise wait pending
            consumed: Future[None] = self._loop.create_future()
            self._pending.append((element, consumed))
            await consumed

    def finish(
        self,
        exception: BaseException | None = None,
    ) -> None:
        """
        Mark the stream as finished, optionally with an exception.

        After finishing, future sends are silently discarded. Pending producers
        are released, and the consumer receives the provided exception or
        ``StopAsyncIteration`` when attempting to get the next element.

        If the stream is already finished, this method does nothing.

        Parameters
        ----------
        exception : BaseException | None, default=None
            Optional exception to raise in the consumer. If None, StopAsyncIteration
            is used to signal normal completion.
        """
        if self.finished:
            return  # already finished, ignore

        self._finish_reason = exception if exception is not None else StopAsyncIteration()

        while self._pending:
            _, pending = self._pending.popleft()
            if pending.done():
                continue

            if get_running_loop() is not self._loop:
                self._loop.call_soon_threadsafe(
                    pending.set_result,
                    None,
                )

            else:
                pending.set_result(None)

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
        """
        assert self._waiting is None, "Only a single stream consumer is supported!"  # nosec: B101

        if self._finish_reason:
            raise self._finish_reason

        try:
            while self._pending:  # consume pending values
                element, consumed = self._pending.popleft()
                if consumed.done():
                    # the producer was cancelled while waiting for this element
                    # to be taken - delivering it would leave the consumer with
                    # a value nobody is waiting to hear was consumed, so the
                    # abandoned element is dropped in favor of the next one
                    continue

                consumed.set_result(None)  # notify consumed
                return element

            # nothing pending - create new waiting future
            self._waiting = self._loop.create_future()
            # and wait for the result
            return await self._waiting

        except CancelledError:
            self.cancel()  # when consumer is cancelled, signal producers to stop waiting
            raise

        finally:
            # cleanup waiting future
            self._waiting = None

    async def asend(
        self,
        value: None = None,
        /,
    ) -> Element:
        """
        Resume the iteration, conforming to the async generator protocol.

        This is not the producer side of the stream - use :meth:`send` to deliver
        elements. Only None can be sent, exactly like resuming a generator which
        does not use the value of its yield expression.

        Parameters
        ----------
        value : None, default=None
            Must be None - there is nothing within the stream to receive it.

        Returns
        -------
        Element
            The next element from the stream

        Raises
        ------
        TypeError
            If a value other than None was sent
        BaseException
            The exception provided to finish(), or StopAsyncIteration if
            finish() was called without an exception
        """
        if value is not None:
            raise TypeError("AsyncStream can't receive values, use `send` to deliver elements")

        return await self.__anext__()

    async def athrow(
        self,
        typ: type[BaseException] | BaseException,
        val: object = None,
        tb: TracebackType | None = None,
        /,
    ) -> NoReturn:
        """
        Throw an exception into the stream, finishing it.

        Nothing within the stream can handle the exception - it finishes the
        stream with it, the same way an exception raised within a generator frame
        would end it, and propagates to the caller. Waiting producers are released.

        Parameters
        ----------
        typ : type[BaseException] | BaseException
            Exception type or instance to throw into the stream
        val : object, default=None
            Exception instance or its argument when a type was provided
        tb : TracebackType | None, default=None
            Optional traceback to attach to the exception

        Raises
        ------
        BaseException
            Always - the exception which was thrown in
        """
        exception: BaseException = thrown_exception(typ, val, tb)

        self.finish(exception=exception)
        raise exception

    async def aclose(self) -> None:
        """
        Finish the stream, ending the iteration.

        Equivalent to calling finish() without an exception - waiting producers
        are released, not yet consumed elements are dropped and all subsequent
        iterations raise StopAsyncIteration. Doing nothing when already finished,
        which preserves the existing finish reason - when cancel(), athrow() or
        finish() finished the stream first, subsequent iterations keep raising
        that original exception instead of StopAsyncIteration.
        """
        self.finish()
