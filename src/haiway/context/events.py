from asyncio import AbstractEventLoop, Future, get_running_loop
from collections.abc import AsyncIterator, MutableMapping
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, ClassVar, Self, final

from haiway.context.types import MissingContext
from haiway.state import Immutable, State
from haiway.types import Default

__all__ = (
    "EventSubscription",
    "EventsContext",
)


class Event[Payload: State](Immutable):
    payload: Payload
    next: Future[Self]


@final  # it can't be Immutable due to MetaClass conflicts
class EventSubscription[Payload: State](AsyncIterator[Payload]):
    """
    Async iterator for consuming events of a specific type.

    EventSubscription provides a way to asynchronously iterate over events
    as they are sent through the event bus. It automatically advances through
    the linked list of events, yielding payloads to the consumer.

    The subscription maintains its position in the event stream using an internal
    future reference. As events are consumed, the subscription advances to the
    next event in the chain, releasing memory for consumed events.

    Type Parameters
    ---------------
    Payload : State
        The type of state objects this subscription will yield

    Notes
    -----
    - Subscriptions are automatically cleaned up when no longer referenced
    - Multiple subscriptions to the same event type are supported
    - Each subscription maintains its own position in the event stream
    - Events are kept in memory only while there are active subscriptions
    """

    __slots__ = ("_future_event",)

    def __init__(
        self,
        future_event: Future[Event[Payload]],
    ) -> None:
        self._future_event: Future[Event[Payload]]
        object.__setattr__(
            self,
            "_future_event",
            future_event,
        )

    async def __anext__(self) -> Payload:
        event: Event[Payload] = await self._future_event
        object.__setattr__(
            self,
            "_future_event",
            event.next,
        )
        return event.payload

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


class Events(Immutable):
    _loop: AbstractEventLoop
    _heads: MutableMapping[type[State], Future[Event[Any]]] = Default(factory=dict)

    def send(
        self,
        payload: State,
    ) -> None:
        assert self._loop == get_running_loop()  # nosec: B101
        payload_type: type[State] = type(payload)
        current: Future[Event[State]] | None = self._heads.get(payload_type)
        if current is None:
            return  # if no one watches, no need to send anywhere

        assert not current.done()  # nosec: B101
        event: Event[State] = Event(
            payload=payload,
            next=self._loop.create_future(),
        )
        self._heads[payload_type] = event.next
        current.set_result(event)

    def subscribe[Payload: State](
        self,
        payload_type: type[Payload],
    ) -> EventSubscription[Payload]:
        assert self._loop == get_running_loop()  # nosec: B101
        current: Future[Event[Payload]] | None = self._heads.get(payload_type)
        if current is None:  # prepare for upcoming events
            current = self._loop.create_future()
            self._heads[payload_type] = current

        return EventSubscription(future_event=current)

    def __del__(self) -> None:
        for future in self._heads.values():
            if future.done():
                continue

            # cancel all incomplete futures
            future.cancel()


class EventsContext(Immutable):
    """
    Context manager for scoped event bus functionality.

    EventsContext provides a scoped event bus that allows type-safe publishing
    and subscribing to events within an async context. It ensures that events
    are isolated to their context scope and automatically cleaned up when the
    context exits.

    The event bus uses a publish-subscribe pattern where:
    - Events are published by type using `send()`
    - Subscribers receive events of a specific type via async iteration
    - Multiple subscribers can listen to the same event type
    - Events are only stored while there are active subscribers

    Examples
    --------
    Basic event bus usage with ctx.scope:

    >>> from haiway import ctx
    >>>
    >>> async def process_events():
    ...     async for event in ctx.subscribe(OrderCreated):
    ...         await handle_order(event)
    >>>
    >>> async with ctx.scope("orders"):
    ...     task = ctx.spawn(process_events)
    ...     ctx.send(OrderCreated(order_id="12345", amount=99.99))

    Notes
    -----
    - Events are scoped to the context - they don't leak between contexts
    - Memory efficient: events without subscribers are immediately discarded
    - Thread-safe within the same event loop
    - Automatic cleanup of pending futures on context exit
    """

    _context: ClassVar[ContextVar[Events]] = ContextVar[Events]("EventsContext")

    @classmethod
    def send(
        cls,
        payload: State,
    ) -> None:
        """
        Send an event to all active subscribers of its type.

        Events are dispatched based on their exact type - subscribers must
        subscribe to the specific State type to receive events. If there are
        no active subscribers for the event type, the event is discarded
        immediately to conserve memory.

        Parameters
        ----------
        payload : State
            The event payload to send. Must be a State instance.

        Raises
        ------
        MissingContext
            If called outside of an EventsContext
        """
        try:
            return cls._context.get().send(payload)

        except LookupError as exc:
            raise MissingContext("EventsContext requested but not defined!") from exc

    @classmethod
    def subscribe[Payload: State](
        cls,
        payload_type: type[Payload],
    ) -> EventSubscription[Payload]:
        """
        Subscribe to events of a specific type.

        Creates a subscription that will receive all events of the specified
        type sent after the subscription is created. The subscription is an
        async iterator that yields event payloads as they are sent.

        Multiple subscriptions to the same event type are supported - each
        subscriber maintains its own position in the event stream and receives
        all events independently.

        Parameters
        ----------
        payload_type : type[Payload]
            The State type to subscribe to. Must be a State class.

        Returns
        -------
        EventSubscription[Payload]
            An async iterator that yields events of the specified type

        Raises
        ------
        MissingContext
            If called outside of an EventsContext
        """
        try:
            return cls._context.get().subscribe(payload_type)

        except LookupError as exc:
            raise MissingContext("EventsContext requested but not defined!") from exc

    _events: Events | None
    _token: Token[Events] | None = None

    def __init__(self) -> None:
        object.__setattr__(
            self,
            "_events",
            None,
        )
        object.__setattr__(
            self,
            "_token",
            None,
        )

    async def __aenter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        assert self._events is None  # nosec: B101
        object.__setattr__(
            self,
            "_events",
            Events(_loop=get_running_loop()),
        )

        assert self._events is not None  # nosec: B101
        object.__setattr__(
            self,
            "_token",
            EventsContext._context.set(self._events),
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        assert self._events is not None  # nosec: B101

        EventsContext._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )
        object.__setattr__(
            self,
            "_events",
            None,
        )
