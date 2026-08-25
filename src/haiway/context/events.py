from asyncio import (
    FIRST_COMPLETED,
    AbstractEventLoop,
    CancelledError,
    Future,
    InvalidStateError,
    get_running_loop,
    wait,
)
from collections.abc import AsyncIterator, MutableMapping, Sequence
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, ClassVar, Self, final
from uuid import UUID

from haiway.attributes import State
from haiway.context.identifier import ContextIdentifier
from haiway.context.types import ContextMissing

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
class EventsSubscription[Payload: State](AsyncIterator[Payload]):
    __slots__ = (
        "_future_event",
        "_scope_id",
        "_termination",
    )

    def __init__(
        self,
        scope_id: UUID,
        future_event: Future[Event[Payload]],
        termination: Future[None],
    ) -> None:
        self._future_event: Future[Event[Payload]] = future_event
        # scope this subscription belongs to - events sent below or beside it
        # are skipped unless they were sent to every subscriber. required, so
        # filtering can never be disabled by omitting it
        self._scope_id: UUID = scope_id
        # completed when the subscribing scope exits - the bus can live in an
        # ancestor scope and outlive it, so waiting only for the bus to close
        # would keep this subscriber running past the scope owning its task
        self._termination: Future[None] = termination

    async def __anext__(self) -> Payload:
        while True:
            await wait(  # race the event against the subscribing scope exiting
                (self._future_event, self._termination),
                return_when=FIRST_COMPLETED,
            )
            if self._future_event.done():
                # raises StopAsyncIteration when the events context was closed
                event: Event[Payload] = self._future_event.result()
                self._future_event = event.next
                if not event.path:
                    return event.payload  # sent to every subscriber

                # delivery goes upwards - the sending scope and all of its ancestors
                if self._scope_id in event.path:
                    return event.payload

                # not addressed to this subscription - wait for the next one or terminate

            if self._termination.done():
                raise StopAsyncIteration


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
        )

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextEvents")

    __slots__ = (
        "_loop",
        "_terminated",
        "_threads",
        "_token",
    )

    def __init__(
        self,
        loop: AbstractEventLoop,
    ) -> None:
        self._loop: AbstractEventLoop = loop
        self._threads: MutableMapping[type[State], Future[Event[Any]]] = {}
        self._terminated: Future[None] = loop.create_future()
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
    ) -> EventsSubscription[Payload]:
        assert self._loop == get_running_loop()  # nosec: B101

        if self._token is None:  # no more events can arrive after closing
            closed_future: Future[Event[Payload]] = self._loop.create_future()
            closed_future.set_exception(StopAsyncIteration)
            closed_future.exception()  # silence runtime warning
            return EventsSubscription(
                scope_id=scope_id,
                future_event=closed_future,
                termination=self._terminated,
            )

        current: Future[Event[Payload]] | None = self._threads.get(payload)
        if current is None:  # prepare for upcoming events
            current = self._loop.create_future()
            self._threads[payload] = current

        return EventsSubscription(
            scope_id=scope_id,
            future_event=current,
            termination=self._terminated,
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

        # set termination future to cancelled
        self._terminated.set_exception(CancelledError())
        self._terminated.exception()  # silence runtime warning
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
