from asyncio import AbstractEventLoop, Future, InvalidStateError, get_running_loop
from collections.abc import AsyncIterator, MutableMapping
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, ClassVar, Self, final

from haiway.attributes import State
from haiway.context.types import ContextMissing

__all__ = (
    "ContextEvents",
    "EventsSubscription",
)


@final  # consider immutable
class Event[Payload: State]:
    __slots__ = (
        "next",
        "payload",
    )

    def __init__(
        self,
        payload: Payload,
        next: Future[Self],  # noqa: A002
    ) -> None:
        self.payload: Payload = payload
        self.next: Future[Self] = next


@final  # consider immutable
class EventsSubscription[Payload: State](AsyncIterator[Payload]):
    __slots__ = ("_future_event",)

    def __init__(
        self,
        future_event: Future[Event[Payload]],
    ) -> None:
        self._future_event: Future[Event[Payload]] = future_event

    async def __anext__(self) -> Payload:
        event: Event[Payload] = await self._future_event
        self._future_event = event.next
        return event.payload


@final  # consider immutable
class ContextEvents:
    @classmethod
    def send(
        cls,
        event: State,
    ) -> None:
        try:
            return cls._context.get()._send(event)

        except LookupError:
            raise ContextMissing("ContextEvents requested but not defined!") from None

    @classmethod
    def subscribe[Event: State](
        cls,
        event: type[Event],
        /,
    ) -> EventsSubscription[Event]:
        try:
            return cls._context.get()._subscribe(event)

        except LookupError:
            raise ContextMissing("ContextEvents requested but not defined!") from None

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
        )
        self._threads[payload_type] = event.next
        current.set_result(event)

    def _subscribe[Payload: State](
        self,
        payload: type[Payload],
    ) -> EventsSubscription[Payload]:
        assert self._loop == get_running_loop()  # nosec: B101

        current: Future[Event[Payload]] | None = self._threads.get(payload)
        if current is None:  # prepare for upcoming events
            current = self._loop.create_future()
            self._threads[payload] = current

        return EventsSubscription(future_event=current)

    def _close(self) -> None:
        for future in tuple(self._threads.values()):
            if future.done():
                continue

            # end all incomplete futures
            try:
                future.set_exception(StopAsyncIteration())

            except InvalidStateError:
                pass  # Already done by concurrent send

        # Clear all references to allow garbage collection
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
            self._close()

        finally:
            self._token = None
