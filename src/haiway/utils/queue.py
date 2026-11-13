from asyncio import AbstractEventLoop, CancelledError, Future, get_running_loop
from collections import deque
from collections.abc import AsyncGenerator, Awaitable
from types import TracebackType
from typing import Any, NoReturn, cast, final

from haiway.utils.exceptions import thrown_exception
from haiway.utils.loops import loop_safe

__all__ = (
    "AsyncQueue",
    "AsyncQueueEmpty",
)


@final
class AsyncQueueEmpty(Exception):
    """
    Signal that a non-blocking queue read found no available element.

    This exception is raised by :meth:`AsyncQueue.pending_next` when the queue
    has not been finished but currently has no buffered elements to return.
    """


@final
class AsyncQueue[Element](AsyncGenerator[Element]):
    """
    Asynchronous queue supporting iteration and finishing.

    A queue implementation optimized for asynchronous workflows. Producers can
    enqueue items ahead of the consumer, and buffered items are preserved until
    they are consumed or explicitly cleared.

    Only one consumer may actively iterate through the queue at a time.

    Parameters
    ----------
    *elements : Element
        Initial elements to populate the queue with
    limit : int | None, default=None
        Maximum number of buffered elements, the oldest dropped to make room for a new
        one - initial elements beyond it included. None for an unbounded queue.
    loop : AbstractEventLoop | None, default=None
        Event loop to use for async operations. If None, the running loop is used.

    Raises
    ------
    ValueError
        If ``limit`` is not positive.
    RuntimeError
        From iteration, when a second consumer waits while another one already does.

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
        limit: int | None = None,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        assert limit is None or limit > 0  # nosec: B101

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
            deque(elements, maxlen=limit),
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
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
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
        assert get_running_loop() is self._loop  # nosec: B101
        return self._finish_reason is not None

    @property
    def limit(self) -> int | None:
        """
        The maximum number of elements the buffer may hold, or None if unbounded.

        When the buffer is full and a new element is enqueued without a waiting
        consumer, the oldest buffered element is silently dropped to make room.
        Elements delivered directly to a waiting consumer never count against
        this limit.
        """
        return self._queue.maxlen

    @loop_safe
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
            If the queue has already been finished. Called from another thread, the
            element is handed over to the queue's loop and this is raised there.
        """
        if self.is_finished:
            raise RuntimeError("AsyncQueue is already finished")

        if self._waiting is not None and not self._waiting.done():
            self._waiting.set_result(element)

        else:
            self._queue.append(element)

    @loop_safe
    def finish(
        self,
        exception: BaseException | None = None,
        clear: bool = False,
    ) -> None:
        """
        Mark the queue as finished, optionally with an exception.

        After finishing, no more elements can be enqueued. Already buffered
        elements remain available to the consumer. Once the buffer is drained,
        the consumer receives the provided exception or ``StopAsyncIteration``.
        If the queue is already finished, this method does nothing. It may be
        called from another thread (so may ``enqueue()``, ``cancel()`` and ``clear()``), it
        takes effect on the queue's loop then - the queue is not finished yet when it
        returns. Every other operation, including ``is_finished``, has to run within the
        loop of the queue.

        This is the producer side of ending a queue and it keeps the buffer by default -
        use ``aclose()`` to end the iteration itself, which drops it.

        Parameters
        ----------
        exception : BaseException | None, default=None
            Optional exception to raise in consumers. If None, StopAsyncIteration
            is used to signal normal completion.
        clear : bool, default=False
            Drop the buffered elements as well, applied even when the queue is already
            finished (its reason is kept) and releasing a consumer which was handed
            an element but did not resume yet, so that element is not put back.
        """
        if clear:  # ensure cleanup even if finished
            self._queue.clear()

        if self.is_finished:
            return  # already finished keeps its reason

        reason: BaseException = StopAsyncIteration() if exception is None else exception
        object.__setattr__(
            self,
            "_finish_reason",
            reason,
        )

        if self._waiting is None:
            return

        if not self._waiting.done():
            self._waiting.set_exception(reason.with_traceback(reason.__traceback__))

        if clear:
            # the consumer is released - an element already handed to it
            # must not be put back after the buffer has been dropped
            object.__setattr__(
                self,
                "_waiting",
                None,
            )

    def cancel(self) -> None:
        """
        Cancel the queue with a CancelledError exception.

        This is a convenience method that calls finish() with a CancelledError.
        Any waiting consumers will receive this exception.
        """
        self.finish(exception=CancelledError())

    def pending_next(self) -> Element:
        """
        Return the next buffered element without waiting.

        This method is useful when callers need an immediate answer and want to
        distinguish between an empty queue and a finished queue.

        Returns
        -------
        Element
            The next buffered element.

        Raises
        ------
        AsyncQueueEmpty
            If the queue is still open but has no buffered elements.
        BaseException
            Re-raises the finish reason when the queue has already been finished.
        """
        assert get_running_loop() is self._loop  # nosec: B101

        if self._queue:  # check the queue, let it finish
            return self._queue.popleft()

        if self._finish_reason is not None:  # check if is finished
            raise self._finish_reason.with_traceback(self._finish_reason.__traceback__)

        raise AsyncQueueEmpty()

    async def next(self) -> Element:
        """
        Await the next available element from the queue.

        This is a named alternative to awaiting :meth:`__anext__`, which can be
        preferable at call sites that are not using ``async for`` iteration.

        Returns
        -------
        Element
            The next available element.

        Raises
        ------
        BaseException
            Re-raises the finish reason when the queue has been finished.
        """
        return await self.__anext__()

    async def __anext__(self) -> Element:
        assert get_running_loop() is self._loop  # nosec: B101
        if self._waiting is not None:
            raise RuntimeError("Only a single queue consumer is supported!")

        if self._queue:  # check the queue, let it finish
            return self._queue.popleft()

        if self._finish_reason is not None:  # check if is finished
            raise self._finish_reason.with_traceback(self._finish_reason.__traceback__)

        # create a new future to wait for next
        waiting: Future[Element] = self._loop.create_future()
        try:
            object.__setattr__(
                self,
                "_waiting",
                waiting,
            )
            # wait for the result
            return await cast(Awaitable[Element], waiting)

        except CancelledError:
            # an element delivered to the waiting consumer cancelled before it resumed
            # would be lost - it goes back to the front of the buffer instead, unless
            # the buffer has been refilled up to its limit, in which case the oldest
            # element is the one a bounded buffer drops anyway
            if (
                self._waiting is waiting  # pyright: ignore[reportUnnecessaryComparison]
                and waiting.done()
                and not waiting.cancelled()
                and waiting.exception() is None
                and (self._queue.maxlen is None or len(self._queue) < self._queue.maxlen)
            ):
                self._queue.appendleft(waiting.result())

            raise

        finally:
            # cleanup
            object.__setattr__(
                self,
                "_waiting",
                None,
            )

    async def asend(
        self,
        value: None = None,
        /,
    ) -> Element:
        """
        Resume the iteration, conforming to the async generator protocol.

        This is not the producer side of the queue - use :meth:`enqueue` to add
        elements. Only None can be sent, exactly like resuming a generator which
        does not use the value of its yield expression.

        Parameters
        ----------
        value : None, default=None
            Must be None - there is nothing within the queue to receive it.

        Returns
        -------
        Element
            The next available element

        Raises
        ------
        BaseException
            Re-raises the finish reason when the queue has been finished
        """
        assert value is None  # nosec: B101
        assert get_running_loop() is self._loop  # nosec: B101

        return await self.__anext__()

    async def athrow(
        self,
        typ: type[BaseException] | BaseException,
        val: object = None,
        tb: TracebackType | None = None,
        /,
    ) -> NoReturn:
        """
        Throw an exception into the queue, finishing it.

        Nothing within the queue can handle the exception - it finishes the queue
        with it, the same way an exception raised within a generator frame would
        end it, and propagates to the caller. Buffered elements are dropped, as
        they are by aclose(): ending the iteration where it stands leaves nothing
        to deliver them to.

        Parameters
        ----------
        typ : type[BaseException] | BaseException
            Exception type or instance to throw into the queue
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
        self.finish(
            exception=exception,
            clear=True,
        )
        raise exception

    async def aclose(self) -> None:
        """
        Close the queue, ending the iteration immediately.

        Finishes the queue and drops whatever it still holds, so every subsequent
        iteration raises the finish reason - closing a generator ends it, and a
        closed one cannot hand out elements. That reason is ``StopAsyncIteration``
        unless the queue was already finished, which keeps whichever reason ended
        it: a queue closed after ``cancel()`` keeps raising ``CancelledError``
        rather than reporting a clean end.

        Use ``finish()`` instead to stop accepting elements while leaving the
        buffered ones available: a producer which has nothing more to send is not
        asking the consumer to stop reading, and finishing that way costs no
        element which was already accepted.
        """
        self.finish(clear=True)

    @loop_safe
    def clear(self) -> None:
        """
        Clear all pending elements from the queue.

        This method removes currently buffered elements only. It does nothing
        while a consumer is actively waiting for the next element, so that a
        queued wake-up is not disrupted. Like ``finish()``, it may be called from
        another thread and takes effect on the queue's loop then.
        """
        if self._waiting is None or self._waiting.done():
            self._queue.clear()
