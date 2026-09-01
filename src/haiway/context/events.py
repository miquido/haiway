from asyncio import (
    FIRST_COMPLETED,
    AbstractEventLoop,
    Future,
    InvalidStateError,
    get_running_loop,
    wait,
)
from collections.abc import AsyncGenerator, MutableMapping, Sequence
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, ClassVar, NoReturn, Self, final
from uuid import UUID

from haiway.attributes import State
from haiway.context.closing import ContextClosing
from haiway.context.identifier import ContextIdentifier
from haiway.context.types import ContextMissing
from haiway.utils.exceptions import thrown_exception

__all__ = (
    "ContextEvents",
    "EventsSubscription",
)


@final  # consider immutable
class Event[Payload: State]:
    __slots__ = (
        "next",
        "path",
        "payload",
    )

    def __init__(
        self,
        payload: Payload,
        next: Future[Self],  # noqa: A002
        path: Sequence[UUID],
    ) -> None:
        self.payload: Payload = payload
        self.next: Future[Self] = next
        # scopes allowed to receive the event - the sending scope and its
        # ancestors, or empty when it was sent to every subscriber
        self.path: Sequence[UUID] = path


@final  # consider immutable
class EventsSubscription[Payload: State](AsyncGenerator[Payload]):
    __slots__ = (
        "_finished",
        "_future_event",
        "_running",
        "_scope_closing",
        "_scope_id",
    )

    def __init__(
        self,
        scope_id: UUID,
        scope_closing: Future[None],
        future_event: Future[Event[Payload]],
    ) -> None:
        # cleared when the subscription finishes - it releases the events chain
        # and makes all subsequent iterations end immediately, the same way an
        # exhausted generator frame would
        self._future_event: Future[Event[Payload]] | None = future_event
        # scope this subscription belongs to - events sent below or beside it
        # are skipped unless they were sent to every subscriber. required, so
        # filtering can never be disabled by omitting it
        self._scope_id: UUID = scope_id
        # completed when that same scope begins closing - captured here instead of
        # on each iteration, so the subscription ends with the scope which owns it
        # and not with whichever scope happens to be current at a given step. also
        # cleared when the subscription finishes - it can hold the exception which
        # ended the scope, and there is nothing left to deliver it to
        self._scope_closing: Future[None] | None = scope_closing
        # completed when the subscription itself finishes - a suspended `__anext__`
        # keeps the futures it races as locals, so clearing them can't release it
        self._finished: Future[None] = scope_closing.get_loop().create_future()
        # set for as long as an iteration is in progress - guards the position
        # within the events chain the same way a generator frame guards itself
        self._running: bool = False

    def _finish(self) -> None:
        self._future_event = None
        self._scope_closing = None
        if not self._finished.done():
            self._finished.set_result(None)

    async def __anext__(self) -> Payload:
        future_event: Future[Event[Payload]] | None = self._future_event
        scope_closing: Future[None] | None = self._scope_closing
        if future_event is None or scope_closing is None:
            raise StopAsyncIteration  # already finished

        # an iteration advances the position within the events chain, so concurrent
        # iterations would each deliver the same event - refused exactly the way an
        # async generator frame refuses to be resumed while it is already running
        if self._running:
            raise RuntimeError("EventsSubscription is already running")

        self._running = True
        try:
            finished: Future[None] = self._finished
            while True:
                try:
                    await wait(  # race the event against the subscription or its scope ending
                        (future_event, scope_closing, finished),
                        return_when=FIRST_COMPLETED,
                    )

                    if finished.done():
                        # closing ends the iteration where it stands, exactly as a
                        # `GeneratorExit` would within a generator frame - a pending
                        # event is not delivered to a subscription which was closed
                        raise StopAsyncIteration

                    if future_event.done():
                        # raises StopAsyncIteration when the events context was closed
                        event: Event[Payload] = future_event.result()
                        future_event = event.next
                        self._future_event = future_event
                        if not event.path:
                            return event.payload  # sent to every subscriber

                        # delivery goes upwards - the sending scope and all of its ancestors
                        if self._scope_id in event.path:
                            return event.payload

                        # not addressed to this subscription - wait for the next or terminate

                except BaseException:
                    # ending or cancelling finishes the subscription, the same way an
                    # exception raised within a generator frame would finish it
                    self._finish()
                    raise

                # a closing scope ends the subscription only after everything which
                # already arrived was delivered - checked on the updated chain position,
                # so an event skipped by the path filtering can't discard the next one
                if scope_closing.done() and not future_event.done():
                    self._finish()
                    raise StopAsyncIteration

        finally:
            # released on every exit - a subscription which ended its iteration
            # can be iterated again, it just finishes immediately
            self._running = False

    async def asend(
        self,
        value: None = None,
        /,
    ) -> Payload:
        # there is nothing to receive the value - only resuming is supported
        if value is not None:
            raise TypeError("EventsSubscription can't receive values")

        return await self.__anext__()

    async def athrow(
        self,
        typ: type[BaseException] | BaseException,
        val: object = None,
        tb: TracebackType | None = None,
        /,
    ) -> NoReturn:
        # resolved before finishing - a malformed call is rejected without
        # ending a subscription which is still perfectly usable
        exception: BaseException = thrown_exception(typ, val, tb)

        # nothing within can handle it - throwing always finishes the
        # subscription and propagates the exception to the caller
        self._finish()

        raise exception

    async def aclose(self) -> None:
        # there is no cleanup to run within - closing only finishes the iteration,
        # releasing an `__anext__` which is suspended waiting for the next event
        self._finish()


@final  # consider immutable
class ContextEvents:
    @classmethod
    def send(
        cls,
        event: State,
        *,
        broadcast: bool = False,
    ) -> None:
        events: Self
        try:
            events = cls._context.get()

        except LookupError:
            raise ContextMissing("ContextEvents requested but not defined!") from None

        return events._send(
            event,
            # without a path the event reaches every subscriber of its type
            path=() if broadcast else ContextIdentifier.current().path,
        )

    @classmethod
    def subscribe[Event: State](
        cls,
        event: type[Event],
        /,
    ) -> EventsSubscription[Event]:
        events: Self
        try:
            events = cls._context.get()

        except LookupError:
            raise ContextMissing("ContextEvents requested but not defined!") from None

        return events._subscribe(
            event,
            scope_id=ContextIdentifier.current().scope_id,
            # bound to the scope subscribing, not to the one iterating - the two
            # can differ and only the subscribing scope owns this subscription
            scope_closing=ContextClosing.current(),
        )

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextEvents")

    __slots__ = (
        "_loop",
        "_threads",
        "_token",
    )

    def __init__(
        self,
        loop: AbstractEventLoop,
    ) -> None:
        self._loop: AbstractEventLoop = loop
        self._threads: MutableMapping[type[State], Future[Event[Any]]] = {}
        self._token: Token[ContextEvents] | None = None

    def _send(
        self,
        payload: State,
        *,
        path: Sequence[UUID],
    ) -> None:
        assert self._loop == get_running_loop()  # nosec: B101

        payload_type: type[State] = type(payload)
        current: Future[Event[State]] | None = self._threads.get(payload_type)
        if current is None:
            return  # if no one watches, no need to send anywhere

        assert not current.done()  # nosec: B101

        event: Event[State] = Event(
            payload=payload,
            next=self._loop.create_future(),
            path=path,
        )
        self._threads[payload_type] = event.next
        current.set_result(event)

    def _subscribe[Payload: State](
        self,
        payload: type[Payload],
        *,
        scope_id: UUID,
        scope_closing: Future[None],
    ) -> EventsSubscription[Payload]:
        assert self._loop == get_running_loop()  # nosec: B101

        if self._token is None:  # no more events can arrive after closing
            closed_future: Future[Event[Payload]] = self._loop.create_future()
            closed_future.set_exception(StopAsyncIteration)
            closed_future.exception()  # silence runtime warning
            return EventsSubscription(
                scope_id=scope_id,
                scope_closing=scope_closing,
                future_event=closed_future,
            )

        current: Future[Event[Payload]] | None = self._threads.get(payload)
        if current is None:  # prepare for upcoming events
            current = self._loop.create_future()
            self._threads[payload] = current

        return EventsSubscription(
            scope_id=scope_id,
            scope_closing=scope_closing,
            future_event=current,
        )

    def _close(self) -> None:
        # clearing the token marks closed - subscribers which kept this
        # instance within their context can't wait for events anymore
        self._token = None
        for future in self._threads.values():
            if future.done():
                continue

            # end all incomplete futures
            try:
                future.set_exception(StopAsyncIteration())
                # retrieve the exception to prevent warnings when never awaited
                future.exception()

            except InvalidStateError:
                pass  # already done by concurrent send

        # clear all references to allow garbage collection
        self._threads.clear()

    async def __aenter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        self._token = ContextEvents._context.set(self)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced ContextEvents enter/exit"  # nosec: B101

        try:
            ContextEvents._context.reset(self._token)

        finally:
            self._close()
