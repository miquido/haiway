from asyncio import (
    FIRST_COMPLETED,
    AbstractEventLoop,
    Future,
    InvalidStateError,
    get_running_loop,
    wait,
)
from collections.abc import AsyncGenerator, Collection, MutableMapping, Sequence
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
        path: Collection[UUID],
    ) -> None:
        self.payload: Payload = payload
        self.next: Future[Self] = next
        # scopes allowed to receive the event - the sending scope and its
        # ancestors, or empty when it was sent to every subscriber
        self.path: Collection[UUID] = path


@final  # consider immutable
class EventsSubscription[Payload: State](AsyncGenerator[Payload]):
    __slots__ = (
        "_finished",
        "_future_event",
        "_scope_closing",
        "_scope_id",
    )

    def __init__(
        self,
        scope_id: UUID,
        scope_closing: Future[UUID],
        future_event: Future[Event[Payload]],
    ) -> None:
        self._scope_id: UUID = scope_id
        self._scope_closing: Future[UUID] = scope_closing
        # cleared when the subscription ends - it releases the events chain and
        # makes all subsequent iterations end immediately
        self._future_event: Future[Event[Payload]] | None = future_event
        # completed when the subscription ends - a suspended `__anext__` keeps the
        # futures it races as locals, so clearing them alone can't release it
        self._finished: Future[None] = scope_closing.get_loop().create_future()

    def _finish(self) -> None:
        self._future_event = None
        if self._finished.done():
            return  # already finished

        self._finished.set_result(None)

    async def __anext__(self) -> Payload:
        while future_event := self._future_event:  # cleared when the subscription ended
            # exactly one of three things releases the wait - the event arriving, the
            # subscription ending, or the scope which owns it beginning to close
            await wait(
                (future_event, self._scope_closing, self._finished),
                return_when=FIRST_COMPLETED,
            )

            if self._finished.done():
                break  # ended while waiting - a pending event is not delivered to it

            if not future_event.done() and self._scope_closing.done():
                break  # scope closing and no event to deliver

            # only the iteration which delivers advances the position within the chain -
            # finding it already moved means another one is running concurrently, and
            # both of them would deliver the very same event
            assert self._future_event is future_event  # nosec: B101

            try:
                result: Event[Payload] = future_event.result()

            except StopAsyncIteration:
                break  # the events context was closed

            self._future_event = result.next
            if not result.path or self._scope_id in result.path:
                return result.payload

            # not addressed to this subscription - wait for the next or terminate

        self._finish()
        raise StopAsyncIteration

    async def asend(
        self,
        value: None = None,
        /,
    ) -> Payload:
        assert value is None  # nosec: B101
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
        self._finish()
        raise exception

    async def aclose(self) -> None:
        # there is no cleanup to run within - closing only ends the iteration,
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
        scope_closing: Future[UUID],
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
