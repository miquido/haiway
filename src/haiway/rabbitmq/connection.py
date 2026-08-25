from asyncio import AbstractEventLoop, Future, Lock, get_running_loop, sleep, timeout
from collections.abc import AsyncIterable, Callable, Coroutine, Mapping, MutableMapping
from contextlib import AbstractAsyncContextManager
from functools import partial
from types import TracebackType
from typing import Any, Final, final

from pika import BaseConnection, BasicProperties, DeliveryMode, URLParameters
from pika.adapters.asyncio_connection import AsyncioConnection
from pika.channel import Channel
from pika.connection import Parameters
from pika.exceptions import AMQPError
from pika.frame import Method
from pika.spec import Basic, Connection

from haiway.context import ctx
from haiway.helpers import MQMessage, MQQueue
from haiway.helpers.message_queue import MQMessageAcknowledging, MQMessageRejecting
from haiway.rabbitmq.types import RabbitMQException
from haiway.types import FlatObject, Meta
from haiway.utils import AsyncQueue, AsyncQueueEmpty, getenv_int, getenv_str

__all__ = ("RabbitMQConnection",)

# bounded by default - unlimited prefetch lets the broker push the whole
# backlog into the in-memory consumer queue
RABBITMQ_PREFETCH_DEFAULT: Final[int] = 8


class RabbitMQConnection:
    __slots__ = (
        "_blocked",
        "_connection",
        "_connection_timeout",
        "_disconnected",
        "_lock",
        "_mandatory",
        "_operation_timeout",
        "_parameters",
        "_persistent",
        "_publish_timeout",
        "_publisher_confirms",
        "_recovery_attempts",
        "_recovery_delay",
    )

    def __init__(
        self,
        url: str | None = None,
        connection_timeout: float = 5.0,
        operation_timeout: float = 5.0,
        publish_timeout: float = 30.0,
        publisher_confirms: bool = True,
        mandatory: bool = True,
        persistent: bool = True,
        recovery_attempts: int = 3,
        recovery_delay: float = 1.0,
    ) -> None:
        if mandatory and not publisher_confirms:
            # returns are only tracked on a confirming channel, so without
            # confirms an unroutable message would be dropped silently
            raise RabbitMQException(
                "RabbitMQ mandatory publishing requires publisher confirms",
                operation="connect",
            )

        if recovery_attempts < 0:
            raise RabbitMQException(
                "RabbitMQ recovery attempts cannot be negative",
                operation="connect",
            )

        if recovery_delay <= 0:
            raise RabbitMQException(
                "RabbitMQ recovery delay has to be positive",
                operation="connect",
            )

        self._lock: Lock = Lock()
        try:
            self._parameters: Parameters = URLParameters(
                url
                if url is not None
                else getenv_str(
                    "RABBITMQ_URL",
                    default="amqp://localhost:5672",
                )
            )

        except Exception as exc:
            raise RabbitMQException(
                "Invalid RabbitMQ connection URL",
                operation="connect",
            ) from exc

        self._connection: BaseConnection | None = None
        # resolved when the broker acknowledges the close of `_connection`
        self._disconnected: Future[None] | None = None
        # reason reported by the broker while it refuses to read from us, kept
        # so a publish stalling on flow control is not reported as a dead broker
        self._blocked: str | None = None
        self._connection_timeout: float = connection_timeout
        self._operation_timeout: float = operation_timeout
        self._publish_timeout: float = publish_timeout
        self._publisher_confirms: bool = publisher_confirms
        self._mandatory: bool = mandatory
        self._persistent: bool = persistent
        self._recovery_attempts: int = recovery_attempts
        self._recovery_delay: float = recovery_delay

    def _blocked_reason(self) -> str | None:
        return self._blocked

    def _connection_blocked(
        self,
        _connection: BaseConnection,
        frame: Method[Connection.Blocked],
        /,
    ) -> None:
        # the broker stops reading from the socket under a resource alarm, so
        # everything published from here on waits for the alarm to clear
        reason: str = frame.method.reason or "unspecified"
        self._blocked = reason
        ctx.log_warning(f"RabbitMQ broker applied flow control: {reason}")

    def _connection_unblocked(
        self,
        _connection: BaseConnection,
        _frame: Method[Connection.Unblocked],
        /,
    ) -> None:
        self._blocked = None
        ctx.log_info("RabbitMQ broker lifted flow control")

    async def _queue_access[Content](
        self,
        queue: str,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
        prefetch: int | None = None,
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]:
        if extra:
            # silently dropping options is what this parameter used to do
            raise RabbitMQException(
                f"Unsupported queue access options: {', '.join(sorted(extra))}",
                operation="queue",
                queue=queue,
            )

        return _QueueAccess[Content](
            queue=queue,
            content_encoder=content_encoder,
            content_decoder=content_decoder,
            opening=partial(
                self._open_channel,
                prefetch=prefetch
                if prefetch is not None
                else getenv_int(
                    "RABBITMQ_PREFETCH",
                    default=RABBITMQ_PREFETCH_DEFAULT,
                ),
                queue=queue,
            ),
            blocked=self._blocked_reason,
            operation_timeout=self._operation_timeout,
            publish_timeout=self._publish_timeout,
            publisher_confirms=self._publisher_confirms,
            mandatory=self._mandatory,
            persistent=self._persistent,
            recovery_attempts=self._recovery_attempts,
            recovery_delay=self._recovery_delay,
        )

    async def _queue_declare(
        self,
        queue: str,
        passive: bool = False,
        durable: bool = False,
        exclusive: bool = False,
        auto_delete: bool = False,
        arguments: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        if extra:
            # AMQP arguments belong in `arguments` - accepting them here would
            # turn a misspelled declare option into a queue argument
            raise RabbitMQException(
                f"Unsupported queue declare options: {', '.join(sorted(extra))}",
                operation="declare_queue",
                queue=queue,
            )

        def declare(
            channel: Channel,
            callback: Callable[[Any], None],
        ) -> None:
            channel.queue_declare(
                queue=queue,
                passive=passive,
                durable=durable,
                exclusive=exclusive,
                arguments=dict(arguments) if arguments else None,
                auto_delete=auto_delete,
                callback=callback,
            )

        await self._channel_operation(
            declare,
            operation="declare_queue",
            queue=queue,
        )

    async def _queue_purge(
        self,
        queue: str,
    ) -> None:
        def purge(
            channel: Channel,
            callback: Callable[[Any], None],
        ) -> None:
            channel.queue_purge(
                queue=queue,
                callback=callback,
            )

        await self._channel_operation(
            purge,
            operation="purge_queue",
            queue=queue,
        )

    async def _queue_delete(
        self,
        queue: str,
        if_unused: bool = False,
        if_empty: bool = False,
    ) -> None:
        def delete(
            channel: Channel,
            callback: Callable[[Any], None],
        ) -> None:
            channel.queue_delete(
                queue=queue,
                if_unused=if_unused,
                if_empty=if_empty,
                callback=callback,
            )

        await self._channel_operation(
            delete,
            operation="delete_queue",
            queue=queue,
        )

    async def _channel_operation(
        self,
        invoke: Callable[[Channel, Callable[[Any], None]], None],
        /,
        *,
        operation: str,
        queue: str,
    ) -> None:
        # queue management publishes nothing, so it skips the confirms round-trip
        state: _ChannelState = await self._open_channel(
            confirms=False,
            queue=queue,
        )

        try:
            result: Future[Any] = get_running_loop().create_future()
            invoke(state.channel, _resolver_for(result))
            await state.reply(
                result,
                limit=self._operation_timeout,
                message=f"Timed out during {operation}",
                operation=operation,
            )

        except RabbitMQException:
            raise

        except Exception as exc:
            raise RabbitMQException(
                f"Failed to {operation}",
                operation=operation,
                queue=queue,
                retryable=True,
            ) from exc

        finally:
            _close_channel(state.channel)

    async def _open_channel(
        self,
        *,
        prefetch: int = 0,
        confirms: bool = True,
        queue: str | None = None,
    ) -> _ChannelState:
        connection: BaseConnection = await self._ensure_connection()
        opened: Future[Channel] = get_running_loop().create_future()
        channel: Channel
        try:
            channel = connection.channel(on_open_callback=_resolver_for(opened))

        except Exception as exc:
            # allocating a channel is synchronous, so its failures never reach
            # the future the rest of the open awaits
            raise RabbitMQException(
                "Failed to allocate RabbitMQ channel",
                operation="channel",
                queue=queue,
                retryable=True,
            ) from exc

        state: _ChannelState = _ChannelState(channel, queue=queue)
        # a single close hook spans the whole channel lifetime, the open
        # included, so nothing awaited on it can outlive the channel
        channel.add_on_close_callback(  # pyright: ignore[reportUnknownMemberType]
            partial(_channel_aborted, state),
        )
        # the channel number is allocated from here on, so every failure has to
        # release it instead of leaving the channel behind on the connection -
        # including one that never finished opening, which pika lets us close
        try:
            await state.reply(
                opened,
                # the connection is already established, so opening a channel on
                # it is an ordinary channel operation
                limit=self._operation_timeout,
                message="Timed out opening RabbitMQ channel",
                operation="channel",
            )
            await self._prepare_channel(
                state,
                prefetch=prefetch,
                confirms=confirms,
            )

        except BaseException:
            _close_channel(channel)
            raise

        return state

    async def _prepare_channel(
        self,
        state: _ChannelState,
        /,
        *,
        prefetch: int,
        confirms: bool,
    ) -> None:
        loop: AbstractEventLoop = get_running_loop()
        channel: Channel = state.channel

        # basic_qos and confirm_delivery validate and dispatch synchronously, so
        # their failures - a broker without Confirm.Select in particular - would
        # escape as driver errors rather than through the adapter's own type
        try:
            if prefetch:
                ready: Future[Any] = loop.create_future()
                channel.basic_qos(
                    prefetch_count=prefetch,
                    callback=_resolver_for(ready),
                )
                await state.reply(
                    ready,
                    limit=self._operation_timeout,
                    message="Timed out applying RabbitMQ prefetch",
                    operation="channel",
                )

            if not (confirms and self._publisher_confirms):
                return  # nothing more to prepare

            confirmed: Future[Any] = loop.create_future()
            channel.confirm_delivery(
                ack_nack_callback=state.settle,
                callback=_resolver_for(confirmed),
            )
            if self._mandatory:
                channel.add_on_return_callback(  # pyright: ignore[reportUnknownMemberType]
                    partial(_publish_returned, state),
                )

            await state.reply(
                confirmed,
                limit=self._operation_timeout,
                message="Timed out enabling RabbitMQ publisher confirms",
                operation="channel",
            )

        except RabbitMQException:
            raise

        except Exception as exc:
            raise RabbitMQException(
                "Failed to prepare RabbitMQ channel",
                operation="channel",
                queue=state.queue,
                retryable=True,
            ) from exc

    async def _ensure_connection(self) -> BaseConnection:
        loop: AbstractEventLoop = get_running_loop()
        async with self._lock:
            if self._connection is not None and self._connection.is_open:
                return self._connection  # connection already available

            if self._connection is not None:
                # replace a connection which is closed or closing - it is
                # already unusable, so its teardown is not worth waiting for
                _close_connection(self._connection)
                self._connection = None
                self._disconnected = None
                self._blocked = None

            ctx.log_info("Opening rabbitmq connection...")
            connected: Future[BaseConnection] = loop.create_future()
            # pika reports the completed close through a callback, which is the
            # only way to tell an initiated close from a finished one
            disconnected: Future[None] = loop.create_future()
            connection: AsyncioConnection
            try:
                connection = AsyncioConnection(
                    parameters=self._parameters,
                    on_open_callback=_resolver_for(connected),
                    # without this pika raises inside a loop callback, where the
                    # awaiting coroutine would never see the cause
                    on_open_error_callback=lambda _connection, exc: _reject(
                        connected,
                        exception=RabbitMQException(
                            "Failed to open RabbitMQ connection",
                            operation="connect",
                            cause=exc if isinstance(exc, Exception) else None,
                            retryable=True,
                        ),
                    ),
                    on_close_callback=lambda _connection, _reason: _resolve(
                        disconnected,
                        value=None,
                    ),
                    custom_ioloop=loop,
                )

            except Exception as exc:
                raise RabbitMQException(
                    "Failed to open RabbitMQ connection",
                    operation="connect",
                    retryable=True,
                ) from exc

            # a resource alarm on the broker is not an error and never completes
            # a pending publish, so it is tracked separately from the failures
            connection.add_on_connection_blocked_callback(  # pyright: ignore[reportUnknownMemberType]
                self._connection_blocked,
            )
            connection.add_on_connection_unblocked_callback(  # pyright: ignore[reportUnknownMemberType]
                self._connection_unblocked,
            )

            try:
                await _completion(
                    connected,
                    limit=self._connection_timeout,
                    message="Timed out opening RabbitMQ connection",
                    operation="connect",
                    retryable=True,
                )

            except BaseException:
                _close_connection(connection)
                raise

            self._connection = connection
            self._disconnected = disconnected
            self._blocked = None
            ctx.log_info("...rabbitmq connection open!")

            return connection

    async def _disconnect(self) -> None:
        async with self._lock:
            connection: BaseConnection | None = self._connection
            disconnected: Future[None] | None = self._disconnected
            self._connection = None
            self._disconnected = None
            self._blocked = None
            if connection is None:
                return  # no connection available

            if not _close_connection(connection) or disconnected is None:
                return  # closed elsewhere, nothing to wait for

            # pika only initiates the close, so returning here without waiting
            # would leave the socket open and the broker seeing an abrupt
            # disconnect instead of the Connection.Close handshake
            await _closure(
                disconnected,
                limit=self._connection_timeout,
                message="Timed out closing RabbitMQ connection",
            )


# RabbitMQ reports redelivery attempts through the AMQP 1.0 aligned name
_DELIVERY_COUNT_HEADER: Final[str] = "x-delivery-count"


def _meta_value(
    value: Any,
    /,
) -> Any:
    # pika decodes AMQP field-table types beyond what Meta accepts - timestamps
    # become datetime, decimals become Decimal, byte arrays become bytes - and
    # a header carrying one of those would otherwise fail the whole delivery
    match value:
        case None | str() | bool() | int() | float():
            return value

        case bytes() | bytearray():
            return bytes(value).decode("utf-8", errors="replace")

        case [*values]:
            return [_meta_value(element) for element in values]

        case {**values}:
            return {str(key): _meta_value(element) for key, element in values.items()}

        case other:
            return str(other)


def _resolve[Value](
    future: Future[Value],
    *,
    value: Value,
) -> None:
    if future.done():
        return  # already completed

    future.set_result(value)


def _resolver_for[Value](
    future: Future[Value],
) -> Callable[[Value], None]:
    def resolve(value: Value) -> None:
        _resolve(future, value=value)

    return resolve


def _reject[Value](
    future: Future[Value],
    *,
    exception: BaseException,
) -> None:
    if future.done():
        return  # already completed

    future.set_exception(exception)


async def _completion[Value](
    future: Future[Value],
    /,
    *,
    limit: float,
    message: str,
    operation: str,
    queue: str | None = None,
    retryable: bool = True,
) -> Value:
    # every pika RPC is answered through a callback resolving a future, which
    # never completes when the broker goes silent
    try:
        async with timeout(limit):
            return await future

    except TimeoutError as exc:
        raise RabbitMQException(
            message,
            operation=operation,
            queue=queue,
            # a silent broker is the definition of a transient failure - the
            # request may well have reached it, so the caller retries knowing
            # the operation is not guaranteed to have been skipped
            retryable=retryable,
        ) from exc


async def _closure(
    completion: Future[None],
    /,
    *,
    limit: float,
    message: str,
) -> None:
    # a close is only complete once the broker answers it, but teardown must not
    # fail over a slow one - it would mask whatever the caller was already
    # handling - so a stalled close is reported and left behind
    try:
        async with timeout(limit):
            await completion

    except TimeoutError:
        ctx.log_error(message)


def _channel_aborted(
    state: _ChannelState,
    _channel: Channel,
    reason: BaseException,
    /,
) -> None:
    # pika discards every pending callback when a channel closes, which would
    # otherwise leave the awaiting coroutines hanging until they time out
    state.abort(
        "RabbitMQ channel closed",
        cause=reason if isinstance(reason, Exception) else None,
    )
    _resolve(state.closed, value=None)


def _publish_returned(
    state: _ChannelState,
    _channel: Channel,
    _method: Basic.Return,
    properties: BasicProperties,
    _body: bytes,
    /,
) -> None:
    # Basic.Return carries no delivery tag, but it does echo the properties of
    # the returned message, so the publish tag travels back in message_id -
    # application attributes are published as headers, leaving that field free
    message_id: str | None = properties.message_id
    if not message_id:
        return  # published by something else

    publish_id: int
    try:
        publish_id = int(message_id)

    except ValueError:
        return  # published by something else

    state.returned(publish_id)


def _reject_delivery(
    channel: Channel,
    /,
    *,
    delivery_tag: int,
    requeue: bool,
) -> None:
    # a closing channel requeues its unacknowledged deliveries by itself, and
    # rejecting on it would only raise inside the pika callback
    if not channel.is_open:
        return

    try:
        channel.basic_reject(
            delivery_tag=delivery_tag,
            requeue=requeue,
        )

    except Exception as exc:
        ctx.log_error(
            "Failed to reject RabbitMQ delivery",
            exception=exc,
        )


def _close_channel(
    channel: Channel,
    /,
) -> bool:
    """Initiate the channel close, reporting whether the broker will answer it."""
    # closing an already closing/closed channel raises, and there is nothing
    # useful to do about it during teardown - one that is still opening can be
    # closed though, and has to be, or its number stays allocated
    if channel.is_closed or channel.is_closing:
        return False

    try:
        channel.close()

    except Exception as exc:
        ctx.log_error(
            "Failed to close RabbitMQ channel",
            exception=exc,
        )
        return False

    return True


def _close_connection(
    connection: BaseConnection,
    /,
) -> bool:
    """Initiate the connection close, reporting whether the broker will answer it."""
    # closing an already closing/closed connection raises, and there is
    # nothing useful to do about it during teardown
    if connection.is_closed or connection.is_closing:
        return False

    try:
        connection.close()

    except AMQPError as exc:
        ctx.log_error(
            "Failed to close RabbitMQ connection",
            exception=exc,
        )
        return False

    return True


def _delivery_settling(
    channel: Channel,
    /,
    *,
    delivery_tag: int,
    queue: str,
    requeue_rejected: bool,
) -> tuple[MQMessageAcknowledging, MQMessageRejecting]:
    async def acknowledge(
        **extra: Any,
    ) -> None:
        if extra:
            raise RabbitMQException(
                f"Unsupported acknowledge options: {', '.join(sorted(extra))}",
                operation="acknowledge",
                queue=queue,
            )

        try:
            channel.basic_ack(delivery_tag=delivery_tag)

        except Exception as exc:
            raise RabbitMQException(
                "Failed to acknowledge message",
                operation="acknowledge",
                queue=queue,
            ) from exc

    async def reject(
        requeue: bool | None = None,
        **extra: Any,
    ) -> None:
        if extra:
            raise RabbitMQException(
                f"Unsupported reject options: {', '.join(sorted(extra))}",
                operation="reject",
                queue=queue,
            )

        try:
            channel.basic_reject(
                delivery_tag=delivery_tag,
                requeue=requeue if requeue is not None else requeue_rejected,
            )

        except Exception as exc:
            raise RabbitMQException(
                "Failed to reject message",
                operation="reject",
                queue=queue,
            ) from exc

    return (acknowledge, reject)


@final
class _ChannelState:
    """Everything awaiting an answer on a single channel.

    A channel fails as one unit, so the publisher confirms and the RPC replies
    outstanding on it are tracked together and settled together by `abort`.
    """

    __slots__ = ("channel", "closed", "confirms", "queue", "replies", "sequence")

    def __init__(
        self,
        channel: Channel,
        /,
        *,
        queue: str | None = None,
    ) -> None:
        self.channel: Channel = channel
        self.queue: str | None = queue
        self.confirms: MutableMapping[int, Future[None]] = {}
        # awaited RPC replies, mapped to the operation awaiting them
        self.replies: MutableMapping[Future[Any], str] = {}
        # in confirm mode the broker tags the n-th published message as n
        self.sequence: int = 0
        # resolved when the broker acknowledges the channel close
        self.closed: Future[None] = get_running_loop().create_future()

    async def reply[Value](
        self,
        future: Future[Value],
        /,
        *,
        limit: float,
        message: str,
        operation: str,
    ) -> Value:
        """Await a channel reply, failing on channel close instead of stalling."""
        self.replies[future] = operation
        try:
            return await _completion(
                future,
                limit=limit,
                message=message,
                operation=operation,
                queue=self.queue,
            )

        finally:
            self.replies.pop(future, None)

    def next_tag(self) -> int:
        self.sequence += 1
        return self.sequence

    def release_tag(
        self,
        tag: int,
        /,
    ) -> None:
        # the broker only numbers the messages it actually received, so a publish
        # failing before it left the client has to give its tag back - keeping it
        # would offset every following confirmation on this channel. nothing
        # awaits between claiming a tag and publishing it, so the released one is
        # always the last claimed
        assert self.sequence == tag  # nosec: B101
        self.confirms.pop(tag, None)
        self.sequence -= 1

    def settle(
        self,
        frame: Method[Basic.Ack | Basic.Nack],
        /,
    ) -> None:
        method: Any = frame.method
        delivery_tag: int = method.delivery_tag
        acknowledged: bool = isinstance(method, Basic.Ack)
        tags: list[int]
        if method.multiple:
            tags = [tag for tag in self.confirms if tag <= delivery_tag]

        elif delivery_tag in self.confirms:
            tags = [delivery_tag]

        else:
            return  # already settled or not tracked

        for tag in tags:
            confirmation: Future[None] = self.confirms.pop(tag)
            if acknowledged:
                _resolve(confirmation, value=None)

            else:
                _reject(
                    confirmation,
                    exception=RabbitMQException(
                        "Broker rejected published message",
                        operation="publish",
                        queue=self.queue,
                        # a nack means the broker could not take responsibility
                        # for this message, not that the message is unacceptable
                        retryable=True,
                    ),
                )

    def returned(
        self,
        publish_id: int,
        /,
    ) -> None:
        confirmation: Future[None] | None = self.confirms.get(publish_id)
        if confirmation is None:
            return  # already settled or not tracked

        # the broker acknowledges a returned message as well, so failing here
        # first ensures the caller learns it was never routed
        _reject(
            confirmation,
            exception=RabbitMQException(
                "Published message was not routed to any queue",
                operation="publish",
                queue=self.queue,
            ),
        )

    def abort(
        self,
        message: str,
        /,
        *,
        cause: Exception | None = None,
    ) -> None:
        """Fail everything still awaiting an answer on this channel."""
        awaiting: tuple[tuple[Future[Any], str], ...] = (
            *((confirmation, "publish") for confirmation in self.confirms.values()),
            *self.replies.items(),
        )
        self.confirms.clear()
        self.replies.clear()
        for future, operation in awaiting:
            _reject(
                future,
                exception=RabbitMQException(
                    message,
                    operation=operation,
                    queue=self.queue,
                    cause=cause,
                    # the channel carrying the operation is gone, so a retry
                    # runs on a fresh one rather than repeating the failure
                    retryable=True,
                ),
            )


@final
class _Consumer[Content]:
    """One active consumer, retained so it can be cancelled or re-established.

    The `messages` queue outlives the channel the consumer runs on, so an
    iteration in progress survives a recovery onto a replacement channel.
    """

    __slots__ = (
        "arguments",
        "channel",
        "exclusive",
        "messages",
        "requeue_rejected",
        "stopped",
        "tag",
    )

    def __init__(
        self,
        messages: AsyncQueue[MQMessage[Content]],
        *,
        requeue_rejected: bool,
        exclusive: bool,
        arguments: Mapping[str, Any] | None,
    ) -> None:
        self.messages: AsyncQueue[MQMessage[Content]] = messages
        self.requeue_rejected: bool = requeue_rejected
        # retained so the consumer can be started again on a replacement channel
        self.exclusive: bool = exclusive
        self.arguments: Mapping[str, Any] | None = arguments
        # the channel the consumer tag belongs to - a tag is meaningless on any
        # other channel, including the one replacing it after a reopen
        self.channel: Channel | None = None
        self.tag: str | None = None
        # set when the subscription is left, which a recovery already in flight
        # has to see - the queue is only finished once the cancel completes
        self.stopped: bool = False

    def reset(self) -> None:
        """Detach from a channel that is gone, leaving the iteration open."""
        # buffered deliveries return to the queue as soon as the consumer or its
        # channel goes away, so handing them to the application would duplicate
        # the work with no delivery tag left to settle it with
        self.messages.clear()
        self.channel = None
        self.tag = None

    def discard(
        self,
        reason: BaseException | None = None,
        /,
    ) -> None:
        """End the iteration, dropping whatever the broker is about to reclaim."""
        self.reset()
        self.messages.finish(reason)


@final
class _Consumption[Content]:
    """Scoped consumer subscription, cancelled when its context exits."""

    __slots__ = ("_messages", "_starting", "_stopping")

    def __init__(
        self,
        *,
        messages: AsyncQueue[MQMessage[Content]],
        starting: Callable[[], Coroutine[Any, Any, None]],
        stopping: Callable[[], Coroutine[Any, Any, None]],
    ) -> None:
        self._messages: AsyncQueue[MQMessage[Content]] = messages
        self._starting: Callable[[], Coroutine[Any, Any, None]] = starting
        self._stopping: Callable[[], Coroutine[Any, Any, None]] = stopping

    async def __aenter__(self) -> AsyncIterable[MQMessage[Content]]:
        await self._starting()

        return self._messages

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._stopping()


@final
class _QueueAccess[Content]:
    """Channel scoped access to a single queue, shared by publishes and consumers.

    Entering opens a dedicated channel and yields the `MQQueue` bound to it,
    exiting cancels the consumers started through it and closes the channel.
    """

    __slots__ = (
        "_blocked",
        "_channel",
        "_closed",
        "_consumers",
        "_content_decoder",
        "_content_encoder",
        "_lock",
        "_mandatory",
        "_opening",
        "_operation_timeout",
        "_persistent",
        "_publish_timeout",
        "_publisher_confirms",
        "_queue",
        "_recovery_attempts",
        "_recovery_delay",
    )

    def __init__(
        self,
        *,
        queue: str,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
        opening: Callable[[], Coroutine[Any, Any, _ChannelState]],
        blocked: Callable[[], str | None],
        operation_timeout: float,
        publish_timeout: float,
        publisher_confirms: bool,
        mandatory: bool,
        persistent: bool,
        recovery_attempts: int,
        recovery_delay: float,
    ) -> None:
        self._queue: str = queue
        self._content_encoder: Callable[[Content], bytes] = content_encoder
        self._content_decoder: Callable[[bytes], Content] = content_decoder
        self._opening: Callable[[], Coroutine[Any, Any, _ChannelState]] = opening
        self._blocked: Callable[[], str | None] = blocked
        self._operation_timeout: float = operation_timeout
        self._publish_timeout: float = publish_timeout
        self._publisher_confirms: bool = publisher_confirms
        self._mandatory: bool = mandatory
        self._persistent: bool = persistent
        self._recovery_attempts: int = recovery_attempts
        self._recovery_delay: float = recovery_delay
        self._lock: Lock = Lock()
        self._channel: _ChannelState | None = None
        self._consumers: list[_Consumer[Content]] = []
        self._closed: bool = False

    async def __aenter__(self) -> MQQueue[Content]:
        await self._ensure_channel()

        return MQQueue[Content](
            publishing=self._publish,
            consuming=self._consume,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        consumers: tuple[_Consumer[Content], ...]
        channel: _ChannelState | None
        # the same lock every channel acquisition holds, so a reopen still in
        # flight cannot leave a live channel or a running consumer behind
        async with self._lock:
            self._closed = True
            channel = self._channel
            self._channel = None
            consumers = tuple(self._consumers)
            self._consumers.clear()

        if channel is not None:
            # close the channel before finishing the queues, so a delivery still
            # in flight is requeued rather than raising inside a pika callback -
            # pika cancels the consumers registered on it as part of the close
            closing: bool = _close_channel(channel.channel)
            channel.abort("RabbitMQ queue access closed")
            if closing:
                # the consumers are only really gone once the broker answers the
                # close, and until then they still hold the queue - waiting keeps
                # a delete or a redeclare right after this block from racing them
                await _closure(
                    channel.closed,
                    limit=self._operation_timeout,
                    message="Timed out closing RabbitMQ channel",
                )

        for consumer in consumers:
            consumer.discard()

    async def _ensure_channel(self) -> _ChannelState:
        channel: _ChannelState | None = self._channel
        if channel is not None and channel.channel.is_open:
            return channel  # already open, no need to serialize

        async with self._lock:
            return await self._locked_channel()

    async def _locked_channel(self) -> _ChannelState:
        """Resolve the current channel, opening one when needed.

        The caller must hold `_lock`, which is what keeps a teardown from
        interleaving with an open still in flight.
        """
        if self._closed:
            raise RabbitMQException(
                "RabbitMQ queue access is already closed",
                operation="queue",
                queue=self._queue,
            )

        channel: _ChannelState | None = self._channel
        if channel is not None and channel.channel.is_open:
            return channel  # opened while waiting for the lock

        channel = await self._opening()
        channel.channel.add_on_close_callback(  # pyright: ignore[reportUnknownMemberType]
            partial(self._channel_closed, channel),
        )
        channel.channel.add_on_cancel_callback(  # pyright: ignore[reportUnknownMemberType]
            partial(self._consumer_cancelled, channel),
        )
        self._channel = channel

        return channel

    def _channel_closed(
        self,
        closing: _ChannelState,
        _channel: Channel,
        reason: BaseException,
        /,
    ) -> None:
        # the notification concerns the channel it was registered for, not
        # whichever channel is current by the time it fires - those differ
        # once the channel has been reopened
        self._interrupted(
            [consumer for consumer in self._consumers if consumer.channel is closing.channel],
            message="RabbitMQ channel closed",
            cause=reason if isinstance(reason, Exception) else None,
        )

    def _consumer_cancelled(
        self,
        cancelled: _ChannelState,
        frame: Method[Basic.Cancel],
        /,
    ) -> None:
        # the broker cancels a consumer when the queue it reads is deleted or
        # moves to another node; pika answers that by dropping its own
        # bookkeeping only, which would leave the iteration waiting forever
        consumer_tag: str = frame.method.consumer_tag
        self._interrupted(
            [
                consumer
                for consumer in self._consumers
                if consumer.tag == consumer_tag and consumer.channel is cancelled.channel
            ],
            message="RabbitMQ consumer cancelled by the broker",
            cause=None,
        )

    def _interrupted(
        self,
        consumers: list[_Consumer[Content]],
        /,
        *,
        message: str,
        cause: Exception | None,
    ) -> None:
        """Handle consumers the broker took away, recovering them when allowed."""
        if not consumers:
            return  # nothing of ours was affected

        # dropped from tracking either way - a recovery re-registers them on the
        # replacement channel, so leaving them here would double the bookkeeping
        for consumer in consumers:
            if consumer in self._consumers:
                self._consumers.remove(consumer)

            # the deliveries it holds are already on their way back to the queue
            consumer.reset()

        if not (self._closed or self._recovery_attempts < 1):
            try:
                ctx.spawn(
                    self._recover,
                    consumers,
                    message=message,
                    cause=cause,
                )
                return  # the recovery owns them now

            except Exception as exc:
                # nothing is left to run the recovery on - the scope owning the
                # queue access is gone, which ends the iteration either way
                ctx.log_error(
                    "Failed to start RabbitMQ consumer recovery",
                    exception=exc,
                )

        for consumer in consumers:
            consumer.discard(
                RabbitMQException(
                    message,
                    operation="consume",
                    queue=self._queue,
                    cause=cause,
                    retryable=True,
                )
            )

    async def _recover(
        self,
        consumers: list[_Consumer[Content]],
        /,
        *,
        message: str,
        cause: Exception | None,
    ) -> None:
        ctx.log_warning(f"{message}, recovering rabbitmq consumers...")
        delay: float = self._recovery_delay
        failure: Exception | None = cause
        for attempt in range(self._recovery_attempts):
            if attempt:
                await sleep(delay)
                delay *= 2

            recovered: bool = False
            pending: list[_Consumer[Content]]
            async with self._lock:
                if self._closed:
                    break  # teardown won the race

                # an iteration abandoned while the recovery was waiting has
                # nothing left to recover onto. resolved under the lock, so a
                # subscription being left cannot slip between the check and the
                # restart and leave a consumer nobody cancels
                pending = [
                    consumer
                    for consumer in consumers
                    if not (consumer.stopped or consumer.messages.is_finished)
                ]
                if not pending:
                    return  # nobody is listening anymore

                try:
                    for consumer in pending:
                        await self._locked_start(consumer)

                    recovered = True

                except Exception as exc:
                    failure = exc
                    # a partial recovery is not one - the consumers that did
                    # start are cancelled so the next attempt starts them all
                    for consumer in pending:
                        self._detach_consumer(consumer)

            if recovered:
                ctx.log_info("...rabbitmq consumers recovered!")
                return

            ctx.log_warning(
                f"...rabbitmq consumer recovery attempt {attempt + 1}"
                f" of {self._recovery_attempts} failed",
                exception=failure,
            )

        for consumer in consumers:
            consumer.discard(
                RabbitMQException(
                    f"{message} and could not be recovered",
                    operation="consume",
                    queue=self._queue,
                    cause=failure,
                    retryable=True,
                )
            )

    def _untrack_consumer(
        self,
        consumer: _Consumer[Content],
        /,
    ) -> tuple[Channel, str] | None:
        """Drop the consumer from tracking, reporting what is left to cancel."""
        # a consumer that failed to start must not linger on the channel nor in
        # the tracking list - a close notification may have dropped it already
        if consumer in self._consumers:
            self._consumers.remove(consumer)

        channel: Channel | None = consumer.channel
        tag: str | None = consumer.tag
        consumer.channel = None
        consumer.tag = None
        # a consumer of an already replaced or closed channel has nothing left
        # to cancel - its tag does not exist there anymore
        if channel is None or tag is None or not channel.is_open:
            return None

        return (channel, tag)

    def _detach_consumer(
        self,
        consumer: _Consumer[Content],
        /,
    ) -> None:
        """Stop the consumer at the broker without waiting for the answer."""
        registration: tuple[Channel, str] | None = self._untrack_consumer(consumer)
        if registration is None:
            return  # nothing registered anymore

        channel, tag = registration
        try:
            channel.basic_cancel(consumer_tag=tag)

        except Exception as exc:
            ctx.log_error(
                "Failed to cancel RabbitMQ consumer",
                exception=exc,
            )

    async def _publish(
        self,
        message: Content,
        attributes: FlatObject | None,
        exchange: str | None = None,
        routing_key: str | None = None,
        **extra: Any,
    ) -> None:
        if extra:
            raise RabbitMQException(
                f"Unsupported publish options: {', '.join(sorted(extra))}",
                operation="publish",
                queue=self._queue,
            )

        # encode before claiming a delivery tag - an encoding failure must not
        # consume a tag the broker will never see
        body: bytes
        try:
            body = self._content_encoder(message)

        except Exception as exc:
            raise RabbitMQException(
                "Failed to encode published message",
                operation="publish",
                queue=self._queue,
            ) from exc

        # capture the channel so allocation, publish, and cleanup all refer to
        # the same one even if it is reopened meanwhile
        channel: _ChannelState = await self._ensure_channel()

        headers: MutableMapping[str, Any] = (
            {key: value for key, value in attributes.items() if value is not None}
            if attributes
            else {}
        )
        publish_id: int | None = None
        confirmation: Future[None] | None = None
        if self._publisher_confirms:
            publish_id = channel.next_tag()
            confirmation = get_running_loop().create_future()
            channel.confirms[publish_id] = confirmation

        try:
            channel.channel.basic_publish(
                exchange=exchange if exchange is not None else "",
                routing_key=routing_key if routing_key is not None else self._queue,
                body=body,
                mandatory=self._mandatory,
                properties=BasicProperties(
                    headers=headers or None,
                    # an unroutable message comes back with its properties
                    # intact, which is the only way to tell which publish it was
                    message_id=str(publish_id)
                    if publish_id is not None and self._mandatory
                    else None,
                    delivery_mode=DeliveryMode.Persistent
                    if self._persistent
                    else DeliveryMode.Transient,
                ),
            )

        except Exception as exc:
            if publish_id is not None:
                # nothing reached the broker, keep the sequence aligned
                channel.release_tag(publish_id)

            raise RabbitMQException(
                "Failed to publish message",
                operation="publish",
                queue=self._queue,
                retryable=True,
            ) from exc

        if confirmation is None:
            return  # fire and forget

        try:
            async with timeout(self._publish_timeout):
                await confirmation

        except TimeoutError as exc:
            # under a resource alarm the broker stops reading from the socket
            # entirely, so the confirmation is not late - it cannot arrive until
            # the alarm clears, and reporting a timeout would hide why
            blocked: str | None = self._blocked()
            raise RabbitMQException(
                f"RabbitMQ broker applied flow control: {blocked}"
                if blocked is not None
                else "Timed out waiting for publish confirmation",
                operation="publish",
                queue=self._queue,
                retryable=True,
            ) from exc

        finally:
            if publish_id is not None:
                channel.confirms.pop(publish_id, None)

    async def _consume(
        self,
        requeue_rejected: bool = True,
        exclusive: bool = False,
        arguments: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> AbstractAsyncContextManager[AsyncIterable[MQMessage[Content]]]:
        if extra:
            # auto_ack in particular would invalidate every ack/reject
            raise RabbitMQException(
                f"Unsupported consume options: {', '.join(sorted(extra))}",
                operation="consume",
                queue=self._queue,
            )

        # a dedicated queue per consumer, since AsyncQueue supports a
        # single active iterator
        consumer: _Consumer[Content] = _Consumer(
            AsyncQueue[MQMessage[Content]](),
            requeue_rejected=requeue_rejected,
            exclusive=exclusive,
            arguments=arguments,
        )

        return _Consumption[Content](
            messages=consumer.messages,
            starting=partial(self._start_consumer, consumer),
            stopping=partial(self._stop_consumer, consumer),
        )

    async def _start_consumer(
        self,
        consumer: _Consumer[Content],
        /,
    ) -> None:
        # bound to its channel under the lock that guards teardown, so the
        # consumer cannot end up running past the queue access that started it
        async with self._lock:
            try:
                await self._locked_start(consumer)

            except BaseException:
                # a consumer that never started must not be left half registered
                self._detach_consumer(consumer)
                consumer.discard()
                raise

    async def _locked_start(
        self,
        consumer: _Consumer[Content],
        /,
    ) -> None:
        """Register the consumer on the current channel.

        The caller must hold `_lock` and is responsible for detaching the
        consumer when this raises.
        """
        channel: _ChannelState = await self._locked_channel()
        started: Future[Any] = get_running_loop().create_future()
        try:
            consumer.channel = channel.channel
            consumer.tag = channel.channel.basic_consume(
                queue=self._queue,
                on_message_callback=partial(self._deliver, consumer),
                exclusive=consumer.exclusive,
                arguments=dict(consumer.arguments) if consumer.arguments else None,
                callback=_resolver_for(started),
            )
            # tracked before awaiting, so a broker refusal closing the
            # channel surfaces through the iteration instead of stalling
            self._consumers.append(consumer)
            await channel.reply(
                started,
                limit=self._operation_timeout,
                message="Timed out starting consumer",
                operation="consume",
            )

        except RabbitMQException:
            raise

        except Exception as exc:
            raise RabbitMQException(
                "Failed to start consuming",
                operation="consume",
                queue=self._queue,
                retryable=True,
            ) from exc

    async def _stop_consumer(
        self,
        consumer: _Consumer[Content],
        /,
    ) -> None:
        registration: tuple[Channel, str] | None
        async with self._lock:
            consumer.stopped = True
            registration = self._untrack_consumer(consumer)

        if registration is not None:
            # the cancellation has to be answered before the buffer is released,
            # otherwise a delivery still in flight lands in a queue nobody reads
            await self._cancelled(*registration)
            await self._release_buffered(consumer)

        # whatever is left was never handed out and cannot be settled anymore
        consumer.discard()

    async def _cancelled(
        self,
        channel: Channel,
        tag: str,
        /,
    ) -> None:
        cancelled: Future[Any] = get_running_loop().create_future()
        try:
            channel.basic_cancel(
                consumer_tag=tag,
                callback=_resolver_for(cancelled),
            )

        except Exception as exc:
            # the channel went away on its own, which cancels the consumer too
            ctx.log_error(
                "Failed to cancel RabbitMQ consumer",
                exception=exc,
            )
            return

        # leaving a consumer behind is worth reporting, but never worth failing
        # the block that was already on its way out
        await _closure(
            cancelled,
            limit=self._operation_timeout,
            message="Timed out cancelling RabbitMQ consumer",
        )

    async def _release_buffered(
        self,
        consumer: _Consumer[Content],
        /,
    ) -> None:
        """Requeue the deliveries the iteration never asked for."""
        # the broker would only reclaim these once the channel closes, which can
        # be much later - the channel outlives any single consumer on it
        while True:
            message: MQMessage[Content]
            try:
                message = consumer.messages.pending_next()

            except AsyncQueueEmpty:
                return  # nothing left to release

            except BaseException:
                return  # already finished, nothing was buffered

            try:
                await message.reject(requeue=True)

            except Exception as exc:
                ctx.log_error(
                    "Failed to release buffered RabbitMQ delivery",
                    exception=exc,
                )
                return  # the channel is gone, the broker reclaims the rest

    def _deliver(
        self,
        consumer: _Consumer[Content],
        channel: Channel,
        method: Basic.Deliver,
        properties: BasicProperties,
        body: bytes,
        /,
    ) -> None:
        delivery_tag: Any = method.delivery_tag
        if not delivery_tag:
            ctx.log_error(
                "Received message without a delivery tag, discarding it",
                exception=RabbitMQException(
                    "Message delivery_tag is missing",
                    operation="consume",
                    queue=self._queue,
                ),
            )
            return  # can't settle it, so it must not be held

        if consumer.messages.is_finished:
            # the context exited while this delivery was in flight
            _reject_delivery(
                channel,
                delivery_tag=delivery_tag,
                requeue=True,
            )
            return

        content: Content
        try:
            content = self._content_decoder(body)

        except Exception as exc:
            # requeueing an undecodable message redelivers it forever,
            # so it is dead-lettered regardless of the reject policy
            _reject_delivery(
                channel,
                delivery_tag=delivery_tag,
                requeue=False,
            )
            ctx.log_error(
                "Failed to decode message content, dead-lettering it",
                exception=exc,
            )
            return  # can't process

        # built as one mapping so a producer-supplied "attempt" header cannot
        # collide with the derived value
        meta_values: MutableMapping[str, Any] = {
            str(key): _meta_value(value) for key, value in (properties.headers or {}).items()
        }
        # both x-delivery-count and the redelivered flag count the deliveries
        # that came before, so the current attempt is one past them; the header
        # is absent on classic queues, where the flag is the only signal left
        delivered: Any = meta_values.get(_DELIVERY_COUNT_HEADER)
        if not isinstance(delivered, int) or isinstance(delivered, bool):
            delivered = 1 if method.redelivered else 0

        meta_values["attempt"] = delivered + 1

        acknowledge: MQMessageAcknowledging
        reject: MQMessageRejecting
        acknowledge, reject = _delivery_settling(
            channel,
            delivery_tag=delivery_tag,
            queue=self._queue,
            requeue_rejected=consumer.requeue_rejected,
        )
        consumer.messages.enqueue(
            MQMessage(
                content=content,
                acknowledge=acknowledge,
                reject=reject,
                meta=Meta.from_mapping(meta_values),
            ),
        )
