from asyncio import Task, ensure_future, get_running_loop, sleep, timeout
from collections.abc import Iterator, Mapping, MutableSequence
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pika import BasicProperties, DeliveryMode
from pika.exceptions import (
    AMQPConnectionError,
    ChannelClosedByClient,
    ChannelWrongStateError,
    ConnectionClosedByClient,
    ConnectionWrongStateError,
    MethodNotImplemented,
)
from pika.spec import Basic, Connection
from pytest import MonkeyPatch, fixture, mark, raises

from haiway import ctx
from haiway.rabbitmq import RabbitMQ, RabbitMQClient, RabbitMQException
from haiway.rabbitmq import connection as connection_module
from haiway.rabbitmq.connection import RABBITMQ_PREFETCH_DEFAULT


class _Frame:
    """Stand-in for ``pika.frame.Method``."""

    def __init__(
        self,
        method: Any,
    ) -> None:
        self.method = method


class _Deliver:
    def __init__(
        self,
        delivery_tag: int,
        redelivered: bool = False,
    ) -> None:
        self.delivery_tag = delivery_tag
        self.redelivered = redelivered


class FakeChannel:
    """Callback-driven stand-in for ``pika.channel.Channel``."""

    def __init__(self) -> None:
        self.is_open = True
        self.is_closing = False
        self.is_closed = False
        self.published: MutableSequence[dict[str, Any]] = []
        self.acked: MutableSequence[int] = []
        self.rejected: MutableSequence[tuple[int, bool]] = []
        self.cancelled: MutableSequence[str] = []
        self.deleted: MutableSequence[tuple[str, bool, bool]] = []
        self.declared: MutableSequence[dict[str, Any]] = []
        self.qos: int | None = None
        self.closed = False
        self.confirms_enabled = False
        self.consumer_callbacks: dict[str, Any] = {}
        self._ack_nack_callback: Any = None
        self._return_callback: Any = None
        self._cancel_callback: Any = None
        self._close_callbacks: MutableSequence[Any] = []
        self._consumer_counter = 0

    # --- pika surface ---

    def add_on_close_callback(self, callback: Any) -> None:
        self._close_callbacks.append(callback)

    def add_on_return_callback(self, callback: Any) -> None:
        self._return_callback = callback

    def add_on_cancel_callback(self, callback: Any) -> None:
        self._cancel_callback = callback

    def basic_qos(
        self,
        prefetch_size: int = 0,
        prefetch_count: int = 0,
        global_qos: bool = False,
        callback: Any = None,
    ) -> None:
        self.qos = prefetch_count
        if callback is not None:
            get_running_loop().call_soon(callback, _Frame(object()))

    def confirm_delivery(
        self,
        ack_nack_callback: Any,
        callback: Any = None,
    ) -> None:
        self.confirms_enabled = True
        self._ack_nack_callback = ack_nack_callback
        if callback is not None:
            get_running_loop().call_soon(callback, _Frame(object()))

    def basic_publish(
        self,
        exchange: str,
        routing_key: str,
        body: bytes,
        properties: BasicProperties | None = None,
        mandatory: bool = False,
    ) -> None:
        self.published.append(
            {
                "exchange": exchange,
                "routing_key": routing_key,
                "body": body,
                "properties": properties,
                "mandatory": mandatory,
            }
        )

    def basic_consume(
        self,
        queue: str,
        on_message_callback: Any,
        auto_ack: bool = False,
        exclusive: bool = False,
        consumer_tag: str | None = None,
        arguments: dict[str, Any] | None = None,
        callback: Any = None,
    ) -> str:
        self._consumer_counter += 1
        tag = consumer_tag or f"ctag-{self._consumer_counter}"
        self.consumer_callbacks[tag] = on_message_callback
        if callback is not None:
            get_running_loop().call_soon(callback, _Frame(Basic.ConsumeOk(consumer_tag=tag)))

        return tag

    def basic_cancel(self, consumer_tag: str = "", callback: Any = None) -> None:
        self.cancelled.append(consumer_tag)
        # no delivery reaches a cancelled consumer once the broker answered
        self.consumer_callbacks.pop(consumer_tag, None)
        if callback is not None:
            get_running_loop().call_soon(
                callback,
                _Frame(Basic.CancelOk(consumer_tag=consumer_tag)),
            )

    def basic_ack(self, delivery_tag: int = 0, multiple: bool = False) -> None:
        self._raise_if_not_open()
        self.acked.append(delivery_tag)

    def basic_reject(self, delivery_tag: int = 0, requeue: bool = True) -> None:
        self._raise_if_not_open()
        self.rejected.append((delivery_tag, requeue))

    def _raise_if_not_open(self) -> None:
        # pika refuses every operation on a channel that is not open
        if not self.is_open:
            raise ChannelWrongStateError("Channel is closed.")

    def queue_declare(self, queue: str, callback: Any = None, **kwargs: Any) -> None:
        self.declared.append({"queue": queue, **kwargs})
        if callback is not None:
            get_running_loop().call_soon(callback, _Frame(object()))

    def queue_purge(self, queue: str, callback: Any = None) -> None:
        if callback is not None:
            get_running_loop().call_soon(callback, _Frame(object()))

    def queue_delete(
        self,
        queue: str,
        if_unused: bool = False,
        if_empty: bool = False,
        callback: Any = None,
    ) -> None:
        self.deleted.append((queue, if_unused, if_empty))
        if callback is not None:
            get_running_loop().call_soon(callback, _Frame(object()))

    def close(self, reply_code: int = 0, reply_text: str = "Normal shutdown") -> None:
        # pika refuses to close a channel that is already closed or closing,
        # while one that is still opening transitions straight to closing
        if self.is_closed or self.is_closing:
            raise ChannelWrongStateError("Channel is closed.")

        self.closed = True
        self.is_open = False
        self.is_closing = True
        # pika cancels every consumer registered on the channel as part of the
        # close, then completes it only once the broker answers
        for consumer_tag in tuple(self.consumer_callbacks):
            self.cancelled.append(consumer_tag)

        get_running_loop().call_soon(
            self.force_close,
            ChannelClosedByClient(reply_code, reply_text),
        )

    # --- test drivers ---

    def deliver(
        self,
        body: bytes,
        *,
        delivery_tag: int = 1,
        headers: Mapping[str, Any] | None = None,
        redelivered: bool = False,
        tag: str | None = None,
    ) -> None:
        consumer_tag = tag or next(iter(self.consumer_callbacks))
        self.consumer_callbacks[consumer_tag](
            self,
            _Deliver(delivery_tag, redelivered),
            BasicProperties(headers=dict(headers) if headers else None),
            body,
        )

    def confirm(self, delivery_tag: int, *, multiple: bool = False) -> None:
        self._ack_nack_callback(_Frame(Basic.Ack(delivery_tag=delivery_tag, multiple=multiple)))

    def reject_publish(self, delivery_tag: int) -> None:
        self._ack_nack_callback(_Frame(Basic.Nack(delivery_tag=delivery_tag)))

    def return_publish(self, properties: BasicProperties) -> None:
        self._return_callback(self, Basic.Return(), properties, b"")

    def force_close(self, reason: BaseException) -> None:
        self.is_open = False
        self.is_closing = False
        self.is_closed = True
        for callback in tuple(self._close_callbacks):
            callback(self, reason)

    def cancel_consumer(self, tag: str | None = None) -> None:
        """Deliver a broker initiated Basic.Cancel, as pika would."""
        consumer_tag = tag or next(iter(self.consumer_callbacks))
        self.consumer_callbacks.pop(consumer_tag, None)
        self._cancel_callback(_Frame(Basic.Cancel(consumer_tag=consumer_tag)))


class FakeConnection:
    def __init__(
        self,
        *,
        channels: MutableSequence[FakeChannel],
        fail: BaseException | None = None,
        **kwargs: Any,
    ) -> None:
        self.is_open = fail is None
        self.is_closing = False
        self.is_closed = fail is not None
        self.close_calls = 0
        self._channels = channels
        on_open = kwargs.get("on_open_callback")
        on_error = kwargs.get("on_open_error_callback")
        self._on_close = kwargs.get("on_close_callback")
        self._on_blocked: Any = None
        self._on_unblocked: Any = None
        loop = get_running_loop()
        if fail is not None:
            if on_error is not None:
                loop.call_soon(on_error, self, fail)

        elif on_open is not None:
            loop.call_soon(on_open, self)

    def add_on_connection_blocked_callback(self, callback: Any) -> None:
        self._on_blocked = callback

    def add_on_connection_unblocked_callback(self, callback: Any) -> None:
        self._on_unblocked = callback

    def block(self, reason: str = "low on disk space") -> None:
        """Deliver a Connection.Blocked, as the broker does under an alarm."""
        self._on_blocked(self, _Frame(Connection.Blocked(reason=reason)))

    def unblock(self) -> None:
        self._on_unblocked(self, _Frame(Connection.Unblocked()))

    def channel(self, on_open_callback: Any = None) -> FakeChannel:
        created = FakeChannel()
        self._channels.append(created)
        if on_open_callback is not None:
            get_running_loop().call_soon(on_open_callback, created)

        return created

    def close(self) -> None:
        self.close_calls += 1
        self.is_open = False
        self.is_closing = True
        # pika only initiates the close here, completing it when the broker
        # answers - which is what the close callback reports
        get_running_loop().call_soon(self._complete_close)

    def _complete_close(self) -> None:
        self.is_closing = False
        self.is_closed = True
        if self._on_close is not None:
            self._on_close(self, ConnectionClosedByClient(200, "Normal shutdown"))


class FakeBroker:
    def __init__(self) -> None:
        self.channels: MutableSequence[FakeChannel] = []
        self.connections: MutableSequence[FakeConnection] = []


@fixture(autouse=True)
def clear_rabbitmq_environment(monkeypatch: MonkeyPatch) -> None:
    # both variables resolve when a connection or a queue access is created, so
    # the ambient environment would otherwise decide what the tests observe
    for name in ("RABBITMQ_PREFETCH", "RABBITMQ_URL"):
        monkeypatch.delenv(name, raising=False)


@fixture
def broker(monkeypatch: MonkeyPatch) -> Iterator[FakeBroker]:
    created: FakeBroker = FakeBroker()

    def build(**kwargs: Any) -> FakeConnection:
        connection = FakeConnection(channels=created.channels, **kwargs)
        created.connections.append(connection)
        return connection

    monkeypatch.setattr(connection_module, "AsyncioConnection", build)
    yield created


@fixture
def channels(broker: FakeBroker) -> MutableSequence[FakeChannel]:
    return broker.channels


async def _recovered(
    channels: MutableSequence[FakeChannel],
    previous: FakeChannel,
) -> FakeChannel:
    """Wait until a replacement channel carries a re-established consumer."""
    async with timeout(1):
        while channels[-1] is previous or not channels[-1].consumer_callbacks:
            await sleep(0)

    return channels[-1]


def _client(**kwargs: Any) -> RabbitMQClient:
    return RabbitMQClient(url="amqp://guest:guest@localhost:5672/%2F", **kwargs)


# --------------------------------------------------------------------------
# publishing
# --------------------------------------------------------------------------


@mark.asyncio
async def test_publish_carries_attributes_as_headers(
    channels: MutableSequence[FakeChannel],
) -> None:
    # attributes used to be accepted and silently discarded
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]

            task: Task[None] = ensure_future(
                queue.publish(b"payload", attributes={"trace": "abc", "retry": 2})
            )
            await sleep(0)
            channel.confirm(1)
            await task

    properties = channel.published[0]["properties"]
    assert properties is not None
    assert properties.headers is not None
    assert properties.headers["trace"] == "abc"
    assert properties.headers["retry"] == 2


@mark.asyncio
async def test_publish_uses_persistent_delivery_and_mandatory(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)
            channel.confirm(1)
            await task

    published = channel.published[0]
    assert published["mandatory"] is True
    # pika unwraps DeliveryMode to its raw int when building properties
    assert published["properties"].delivery_mode == DeliveryMode.Persistent.value


@mark.asyncio
async def test_publish_waits_for_broker_confirmation(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            assert channel.confirms_enabled is True

            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)
            # not yet acknowledged, so the publish must still be pending
            assert not task.done()

            channel.confirm(1)
            await task


@mark.asyncio
async def test_publish_raises_when_broker_rejects(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)
            channel.reject_publish(1)

            with raises(RabbitMQException, match="rejected"):
                await task


@mark.asyncio
async def test_publish_raises_when_message_is_unroutable(
    channels: MutableSequence[FakeChannel],
) -> None:
    # mandatory publishes are still acked by the broker, so the return has to
    # be correlated back to the publish for it to fail
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)

            channel.return_publish(channel.published[0]["properties"])
            channel.confirm(1)

            with raises(RabbitMQException, match="not routed"):
                await task


@mark.asyncio
async def test_publish_without_confirms_returns_immediately(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with (
        ctx.scope("test"),
        # returns cannot be observed without confirms, so mandatory has to go too
        _client(publisher_confirms=False, mandatory=False) as mq,
    ):
        async with await mq.queue("jobs", bytes, bytes) as queue:
            await queue.publish(b"payload")
            assert channels[-1].confirms_enabled is False
            assert len(channels[-1].published) == 1
            # without returns to correlate, message_id stays the application's
            assert channels[-1].published[0]["properties"].message_id is None


@mark.asyncio
async def test_publish_rejects_unsupported_options(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            with raises(RabbitMQException, match="Unsupported publish options"):
                await queue.publish(b"payload", priority="high")


@mark.asyncio
async def test_publish_keeps_confirm_sequence_aligned_after_failure(
    channels: MutableSequence[FakeChannel],
) -> None:
    # the broker numbers only the messages it received, so a publish failing
    # before it left the client must not consume a delivery tag
    def encode(content: bytes) -> bytes:
        if content == b"bad":
            raise ValueError("cannot encode")

        return content

    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", encode, bytes) as queue:
            channel = channels[-1]

            with raises(RabbitMQException, match="encode"):
                await queue.publish(b"bad")

            assert channel.published == []

            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)
            assert len(channel.published) == 1

            channel.confirm(1)  # the broker's first received message
            await task


@mark.asyncio
async def test_publish_keeps_confirm_sequence_aligned_after_channel_error(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]

            def failing_publish(**kwargs: Any) -> None:
                raise RuntimeError("channel write failed")

            original = channel.basic_publish
            channel.basic_publish = failing_publish  # pyright: ignore[reportAttributeAccessIssue]
            with raises(RabbitMQException, match="Failed to publish"):
                await queue.publish(b"payload")

            channel.basic_publish = original  # pyright: ignore[reportAttributeAccessIssue]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)
            channel.confirm(1)
            await task


# --------------------------------------------------------------------------
# consuming
# --------------------------------------------------------------------------


@mark.asyncio
async def test_consume_reads_delivery_count_for_attempt(
    channels: MutableSequence[FakeChannel],
) -> None:
    # the adapter previously read the non-existent x-redelivery-count header
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"payload", headers={"x-delivery-count": 3})

                async for message in messages:
                    # x-delivery-count counts the deliveries that came before
                    assert message.meta.get("attempt") == 4
                    break


@mark.asyncio
async def test_consume_falls_back_to_redelivered_flag(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"payload", redelivered=True)

                async for message in messages:
                    assert message.meta.get("attempt") == 2
                    break


@mark.asyncio
async def test_producer_headers_cannot_shadow_attempt(
    channels: MutableSequence[FakeChannel],
) -> None:
    # headers are producer controlled, so a header named "attempt" must not
    # collide with the derived redelivery count
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(
                    b"payload",
                    headers={"attempt": "spoofed", "x-delivery-count": 2},
                )

                async for message in messages:
                    assert message.meta.get("attempt") == 3
                    break


@mark.asyncio
async def test_publish_correlates_returns_through_the_message_id(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)

            # the correlation rides in the AMQP message_id, leaving the header
            # table to the application
            properties = channel.published[0]["properties"]
            assert properties.message_id == "1"
            assert properties.headers is None

            channel.confirm(1)
            await task


@mark.asyncio
async def test_unroutable_publish_is_matched_by_its_message_id(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)

            channel.return_publish(BasicProperties(message_id="1"))
            channel.confirm(1)

            with raises(RabbitMQException):
                await task


@mark.asyncio
async def test_return_without_a_matching_message_id_is_ignored(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)

            # a return produced by any other publisher on the broker must not
            # fail a publish of ours, whatever it uses its message_id for
            channel.return_publish(BasicProperties(message_id="other"))
            channel.return_publish(BasicProperties(message_id=""))
            channel.return_publish(BasicProperties(headers={"message_id": "1"}))
            channel.return_publish(BasicProperties())
            channel.confirm(1)

            await task


@mark.asyncio
async def test_unsupported_header_types_are_delivered_as_meta(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                # pika decodes AMQP timestamps, decimals and byte arrays into types
                # Meta does not accept - a dead lettered message carries all of them
                channel.deliver(
                    b"payload",
                    headers={
                        "x-death": [{"count": 1, "time": datetime(2026, 1, 1, tzinfo=UTC)}],
                        "amount": Decimal("1.5"),
                        "raw": b"blob",
                    },
                )

                async for message in messages:
                    assert message.meta.get("amount") == "1.5"
                    assert message.meta.get("raw") == "blob"
                    assert "2026-01-01" in str(message.meta.get("x-death"))
                    break


def test_mandatory_without_confirms_is_rejected() -> None:
    with raises(RabbitMQException, match="publisher confirms"):
        _client(publisher_confirms=False)


@mark.asyncio
async def test_undecodable_message_is_dead_lettered(
    channels: MutableSequence[FakeChannel],
) -> None:
    # requeueing an undecodable message redelivers it forever
    def failing_decoder(payload: bytes) -> str:
        raise ValueError("cannot decode")

    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", str.encode, failing_decoder) as queue:
            channel = channels[-1]
            async with await queue.consume():
                channel.deliver(b"garbage", delivery_tag=7)

    assert channel.rejected == [(7, False)]


@mark.asyncio
async def test_message_without_delivery_tag_is_discarded(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume():
                channel.deliver(b"payload", delivery_tag=0)

    # nothing to settle, and nothing enqueued
    assert channel.acked == []


@mark.asyncio
async def test_acknowledge_and_reject_settle_the_delivery(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"ok", delivery_tag=1)
                channel.deliver(b"bad", delivery_tag=2)

                received: list[Any] = []
                async for message in messages:
                    received.append(message)
                    if len(received) == 2:
                        break

                await received[0].acknowledge()
                await received[1].reject(requeue=False)

    assert channel.acked == [1]
    assert channel.rejected == [(2, False)]


@mark.asyncio
async def test_settling_translates_driver_errors(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"ok", delivery_tag=1)

                received: list[Any] = []
                async for message in messages:
                    received.append(message)
                    break

                def failing(**kwargs: Any) -> None:
                    raise RuntimeError("channel is closed")

                channel.basic_ack = failing  # pyright: ignore[reportAttributeAccessIssue]
                channel.basic_reject = failing  # pyright: ignore[reportAttributeAccessIssue]

                with raises(RabbitMQException, match="Failed to acknowledge"):
                    await received[0].acknowledge()

                with raises(RabbitMQException, match="Failed to reject"):
                    await received[0].reject()


@mark.asyncio
async def test_each_consume_gets_its_own_queue(
    channels: MutableSequence[FakeChannel],
) -> None:
    # one shared AsyncQueue would strand one of two iterators
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            async with await queue.consume() as first, await queue.consume() as second:
                assert first is not second
                assert len(channels[-1].consumer_callbacks) == 2


@mark.asyncio
async def test_consume_rejects_auto_ack(
    channels: MutableSequence[FakeChannel],
) -> None:
    # auto_ack would invalidate every acknowledge/reject closure
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            with raises(RabbitMQException, match="Unsupported consume options"):
                await queue.consume(auto_ack=True)


# --------------------------------------------------------------------------
# lifecycle
# --------------------------------------------------------------------------


@mark.asyncio
async def test_queue_access_cancels_consumers_and_closes_channel(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            # entered and never left, so the access teardown is what ends it
            await (await queue.consume()).__aenter__()
            channel = channels[-1]

        assert channel.cancelled == ["ctag-1"]
        assert channel.closed is True


@mark.asyncio
async def test_queue_access_cleans_up_on_exception(
    channels: MutableSequence[FakeChannel],
) -> None:
    # cleanup used to run only on the happy path
    async with ctx.scope("test"), _client() as mq:
        with raises(RuntimeError, match="boom"):
            async with await mq.queue("jobs", bytes, bytes) as queue:
                await (await queue.consume()).__aenter__()
                raise RuntimeError("boom")

        assert channels[-1].closed is True
        assert channels[-1].cancelled == ["ctag-1"]


@mark.asyncio
async def test_late_delivery_after_close_is_left_to_the_broker(
    channels: MutableSequence[FakeChannel],
) -> None:
    # closing is asynchronous, so a delivery can still arrive afterwards. it used
    # to raise "AsyncQueue is already finished", and then to reject on a channel
    # pika had already closed - the close itself requeues the delivery
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            await (await queue.consume()).__aenter__()
            channel = channels[-1]

        channel.deliver(b"late", delivery_tag=9)

    assert channel.rejected == []
    assert channel.acked == []


@mark.asyncio
async def test_channel_close_drops_the_consumers_it_ended(
    channels: MutableSequence[FakeChannel],
) -> None:
    # with recovery off, consumers of a replaced channel are not re-established,
    # so keeping them around only piles up dead AsyncQueues across reopens
    async with ctx.scope("test"), _client(recovery_attempts=0) as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        access: Any = queue_access
        async with queue_access as queue:
            async with await queue.consume(), await queue.consume():
                assert len(access._consumers) == 2

                channels[-1].force_close(AMQPConnectionError("channel gone"))
                assert access._consumers == []


@mark.asyncio
async def test_exit_during_a_reopen_leaves_no_channel_behind(
    channels: MutableSequence[FakeChannel],
) -> None:
    # the exit used to skip the lock guarding reopens, so a channel opened for a
    # concurrent consume outlived the queue access and its consumer never ended
    async with ctx.scope("test"), _client() as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        queue = await queue_access.__aenter__()
        first = channels[-1]
        first.force_close(AMQPConnectionError("channel gone"))

        # consuming has to reopen the channel, which the exit must wait for
        consuming: Task[Any] = ensure_future((await queue.consume()).__aenter__())
        await sleep(0)
        await queue_access.__aexit__(None, None, None)

        messages = await consuming
        reopened = channels[-1]
        assert reopened is not first
        assert reopened.closed is True
        assert reopened.cancelled == ["ctag-1"]
        assert messages.is_finished is True


@mark.asyncio
async def test_exit_during_a_reopen_closes_the_channel_it_opened(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        queue = await queue_access.__aenter__()
        first = channels[-1]
        first.force_close(AMQPConnectionError("channel gone"))

        publishing: Task[None] = ensure_future(queue.publish(b"payload"))
        for _ in range(4):
            await sleep(0)

        await queue_access.__aexit__(None, None, None)
        assert channels[-1] is not first
        assert channels[-1].closed is True

        with raises(RabbitMQException):
            await publishing


@mark.asyncio
async def test_consume_reports_a_broker_refusal(
    channels: MutableSequence[FakeChannel],
) -> None:
    # consuming a missing queue closes the channel; it used to be requested
    # without waiting for Basic.ConsumeOk, so the caller saw a live iterator
    def refusing(
        self: FakeChannel,
        queue: str,
        on_message_callback: Any,
        auto_ack: bool = False,
        exclusive: bool = False,
        consumer_tag: str | None = None,
        arguments: dict[str, Any] | None = None,
        callback: Any = None,
    ) -> str:
        get_running_loop().call_soon(self.force_close, AMQPConnectionError("NOT_FOUND"))
        return "ctag-refused"

    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            with MonkeyPatch.context() as patch:
                patch.setattr(FakeChannel, "basic_consume", refusing)
                with raises(RabbitMQException, match="channel closed"):
                    async with await queue.consume():
                        pass


@mark.asyncio
async def test_publish_honours_an_explicit_routing_key(
    channels: MutableSequence[FakeChannel],
) -> None:
    # publishing through an exchange used to force the queue name as routing key
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(
                queue.publish(b"payload", exchange="events", routing_key="jobs.created")
            )
            await sleep(0)
            channel.confirm(1)
            await task

    assert channel.published[0]["exchange"] == "events"
    assert channel.published[0]["routing_key"] == "jobs.created"


@mark.asyncio
async def test_queue_management_skips_publisher_confirms(
    channels: MutableSequence[FakeChannel],
) -> None:
    # declaring a queue publishes nothing, so the confirms round-trip is waste
    async with ctx.scope("test"), _client() as mq, ctx.scope("mq", mq):
        await RabbitMQ.declare_queue("jobs", durable=True)

    assert channels[-1].confirms_enabled is False


@mark.asyncio
async def test_channel_close_fails_active_consumers(
    channels: MutableSequence[FakeChannel],
) -> None:
    # a closed channel silently stops delivering, so consumers must be told
    async with ctx.scope("test"), _client(recovery_attempts=0) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            async with await queue.consume() as messages:
                channels[-1].force_close(AMQPConnectionError("channel gone"))

                with raises(RabbitMQException, match="channel closed"):
                    async for _ in messages:
                        pass


@mark.asyncio
async def test_stale_channel_close_does_not_abort_current_publishes(
    channels: MutableSequence[FakeChannel],
) -> None:
    # a close notification arriving after the channel was replaced must not
    # fail publishes outstanding on the healthy replacement
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            first = channels[-1]
            # closed, but pika has not delivered the notification yet
            first.is_open = False

            task: Task[None] = ensure_future(queue.publish(b"payload"))
            # yield until ensure_open has replaced the channel and published on
            # it; task.done() only happens on failure here, since the
            # confirmation is still outstanding
            # bounded - without the timeout a regression that never replaces the
            # channel would spin here forever and hang the run instead of failing
            async with timeout(5):
                while not task.done() and (channels[-1] is first or not channels[-1].published):
                    await sleep(0)

            second = channels[-1]
            assert second is not first
            assert len(second.published) == 1

            # the stale notification arrives only now
            first.force_close(AMQPConnectionError("stale channel"))
            second.confirm(1)

            await task  # must still succeed


@mark.asyncio
async def test_channel_close_fails_pending_publishes(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)
            channel.force_close(AMQPConnectionError("channel gone"))

            with raises(RabbitMQException):
                await task


# --------------------------------------------------------------------------
# queue management
# --------------------------------------------------------------------------


@mark.asyncio
async def test_purge_and_delete_match_pika_signatures(
    channels: MutableSequence[FakeChannel],
) -> None:
    # forwarding **extra into these used to raise TypeError
    async with ctx.scope("test"), _client() as mq, ctx.scope("mq", mq):
        await RabbitMQ.purge_queue("jobs")
        await RabbitMQ.delete_queue("jobs", if_unused=True, if_empty=True)
        await RabbitMQ.declare_queue("jobs", durable=True)

    # the delete flags have to reach pika instead of being dropped
    assert [record for channel in channels for record in channel.deleted] == [("jobs", True, True)]
    # a channel is opened and closed for each operation
    assert all(channel.closed for channel in channels)


@mark.asyncio
async def test_queue_access_rejects_unsupported_options(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        with raises(RabbitMQException, match="Unsupported queue access options"):
            await mq.queue("jobs", bytes, bytes, unknown_option=1)


@mark.asyncio
async def test_channel_setup_failure_closes_the_channel(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    # a channel left open on a setup failure would linger on the connection
    def never_confirming(
        self: FakeChannel,
        prefetch_size: int = 0,
        prefetch_count: int = 0,
        global_qos: bool = False,
        callback: Any = None,
    ) -> None:
        self.qos = prefetch_count  # no callback, so the setup times out

    monkeypatch.setattr(FakeChannel, "basic_qos", never_confirming)

    async with ctx.scope("test"), _client(operation_timeout=0.05) as mq:
        queue_access = await mq.queue("jobs", bytes, bytes, prefetch=10)
        with raises(RabbitMQException, match="Timed out applying RabbitMQ prefetch"):
            async with queue_access:
                pass  # pragma: no cover

        assert channels[-1].closed is True


@mark.asyncio
async def test_prefetch_is_applied(
    channels: MutableSequence[FakeChannel],
) -> None:
    # prefetch was previously unreachable, leaving unlimited unacked delivery
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes, prefetch=25):
            assert channels[-1].qos == 25


@mark.asyncio
async def test_prefetch_defaults_to_bounded(
    channels: MutableSequence[FakeChannel],
) -> None:
    # an unlimited default would let the broker fill the consumer queue - prefetch
    # 0 means unlimited, and then no qos would be requested at all
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes):
            assert channels[-1].qos == RABBITMQ_PREFETCH_DEFAULT
            assert RABBITMQ_PREFETCH_DEFAULT > 0


@mark.asyncio
async def test_prefetch_reads_the_environment_when_accessed(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    # exported after this module was imported, which is what an ordinary
    # `load_env()` in `main()` does
    monkeypatch.setenv("RABBITMQ_PREFETCH", "17")

    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes):
            assert channels[-1].qos == 17


def test_url_reads_the_environment_when_connecting(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("RABBITMQ_URL", "amqp://user:pass@broker.internal:5673/vhost")

    parameters = RabbitMQClient()._parameters  # pyright: ignore[reportPrivateUsage]

    assert parameters.host == "broker.internal"
    assert parameters.port == 5673
    assert parameters.virtual_host == "vhost"


def test_url_defaults_to_a_local_broker() -> None:
    parameters = RabbitMQClient()._parameters  # pyright: ignore[reportPrivateUsage]

    assert parameters.host == "localhost"
    assert parameters.port == 5672


@mark.asyncio
async def test_prefetch_zero_requests_unlimited(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes, prefetch=0):
            assert channels[-1].qos is None


# --------------------------------------------------------------------------
# connection
# --------------------------------------------------------------------------


@mark.asyncio
async def test_connection_failure_reports_the_cause(monkeypatch: MonkeyPatch) -> None:
    # failures used to surface only as an opaque TimeoutError
    created: MutableSequence[FakeChannel] = []
    failure = AMQPConnectionError("broker unreachable")

    def build(**kwargs: Any) -> FakeConnection:
        return FakeConnection(channels=created, fail=failure, **kwargs)

    monkeypatch.setattr(connection_module, "AsyncioConnection", build)

    async with ctx.scope("test"):
        with raises(RabbitMQException, match="Failed to open RabbitMQ connection") as exc_info:
            async with _client():
                pass

    assert exc_info.value.operation == "connect"
    assert exc_info.value.__cause__ is failure


@mark.asyncio
async def test_disconnect_tolerates_a_closed_connection(
    channels: MutableSequence[FakeChannel],
) -> None:
    # closing an already closed connection raises in pika
    async with ctx.scope("test"):
        client = _client()
        async with client:
            connection = client._connection
            assert connection is not None
            connection.is_open = False
            connection.is_closed = True

        assert connection.close_calls == 0


@mark.asyncio
async def test_exception_without_cause_keeps_context() -> None:
    try:
        try:
            raise ValueError("inner")

        except ValueError:
            raise RabbitMQException("outer", operation="publish")  # noqa: B904

    except RabbitMQException as exc:
        assert exc.__suppress_context__ is False
        assert isinstance(exc.__context__, ValueError)
        assert exc.operation == "publish"


@mark.asyncio
async def test_consumer_that_never_starts_is_discarded(
    channels: MutableSequence[FakeChannel],
) -> None:
    # a consumer left registered after a failed start would keep its broker side
    # subscription and its buffer alive until the queue context exits
    def silent(
        self: FakeChannel,
        queue: str,
        on_message_callback: Any,
        auto_ack: bool = False,
        exclusive: bool = False,
        consumer_tag: str | None = None,
        arguments: dict[str, Any] | None = None,
        callback: Any = None,
    ) -> str:
        self.consumer_callbacks["ctag-silent"] = on_message_callback
        return "ctag-silent"  # no Basic.ConsumeOk, so the start times out

    async with ctx.scope("test"), _client(operation_timeout=0.05) as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        access: Any = queue_access
        async with queue_access as queue:
            with MonkeyPatch.context() as patch:
                patch.setattr(FakeChannel, "basic_consume", silent)
                with raises(RabbitMQException, match="Timed out starting consumer"):
                    async with await queue.consume():
                        pass

            assert access._consumers == []
            assert channels[-1].cancelled == ["ctag-silent"]


@mark.asyncio
async def test_broker_cancellation_ends_the_iteration(
    channels: MutableSequence[FakeChannel],
) -> None:
    # the broker cancels a consumer when its queue is deleted; pika only drops
    # its own bookkeeping, which used to leave the iteration waiting forever
    async with ctx.scope("test"), _client(recovery_attempts=0) as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        access: Any = queue_access
        async with queue_access as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"payload", delivery_tag=1)
                channel.cancel_consumer()

                with raises(RabbitMQException, match="cancelled by the broker"):
                    async for _ in messages:
                        raise AssertionError("the delivery is already back on the queue")

                assert access._consumers == []


@mark.asyncio
async def test_buffered_deliveries_are_dropped_on_exit(
    channels: MutableSequence[FakeChannel],
) -> None:
    # closing the channel requeues everything unacknowledged, so handing the
    # buffer to the application would duplicate the work unacknowledgeably
    async with ctx.scope("test"), _client() as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        async with queue_access as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"first", delivery_tag=1)
                channel.deliver(b"second", delivery_tag=2)

        async for _ in messages:
            raise AssertionError("the deliveries are already back on the queue")

        assert channel.acked == []


@mark.asyncio
async def test_buffered_deliveries_are_dropped_on_channel_close(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client(recovery_attempts=0) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"payload", delivery_tag=1)
                channel.force_close(AMQPConnectionError("channel gone"))

                with raises(RabbitMQException, match="RabbitMQ channel closed"):
                    async for _ in messages:
                        raise AssertionError("the delivery is already back on the queue")


@mark.asyncio
async def test_channel_setup_reports_driver_errors(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    # a broker without Confirm.Select fails synchronously inside pika
    def unsupported(self: FakeChannel, *args: Any, **kwargs: Any) -> None:
        raise MethodNotImplemented("Confirm.Select not Supported by Server")

    monkeypatch.setattr(FakeChannel, "confirm_delivery", unsupported)

    async with ctx.scope("test"), _client() as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        with raises(RabbitMQException, match="Failed to prepare RabbitMQ channel") as error:
            async with queue_access:
                pass  # pragma: no cover

        assert isinstance(error.value.__cause__, MethodNotImplemented)
        assert channels[-1].closed is True


@mark.asyncio
async def test_channel_allocation_reports_driver_errors(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    # allocating a channel is synchronous, so its failures never reach the
    # future the rest of the open awaits
    def exhausted(self: FakeConnection, on_open_callback: Any = None) -> FakeChannel:
        raise ConnectionWrongStateError("No free channels")

    monkeypatch.setattr(FakeConnection, "channel", exhausted)

    async with ctx.scope("test"), _client() as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        with raises(RabbitMQException, match="Failed to allocate RabbitMQ channel"):
            async with queue_access:
                pass  # pragma: no cover


def test_invalid_url_is_reported() -> None:
    with raises(RabbitMQException, match="Invalid RabbitMQ connection URL"):
        RabbitMQClient(url="not a url")


@mark.asyncio
async def test_a_channel_that_never_opens_is_closed(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    # pika transitions an opening channel straight to closing; skipping it
    # would leave its number allocated for the life of the connection
    opening: MutableSequence[FakeChannel] = []

    def never_opening(
        self: FakeConnection,
        on_open_callback: Any = None,
    ) -> FakeChannel:
        channel = FakeChannel()
        channel.is_open = False  # no Channel.OpenOk is coming
        opening.append(channel)
        return channel

    monkeypatch.setattr(FakeConnection, "channel", never_opening)

    async with ctx.scope("test"), _client(operation_timeout=0.05) as mq, ctx.scope("mq", mq):
        with raises(RabbitMQException, match="Timed out opening RabbitMQ channel"):
            await RabbitMQ.declare_queue("jobs")

    assert opening[-1].closed is True


@mark.asyncio
async def test_correlation_header_is_not_delivered_as_meta(
    channels: MutableSequence[FakeChannel],
) -> None:
    # the publish correlation header is an adapter detail - the broker echoes it
    # to consumers, where it used to show up as application metadata
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)
            channel.confirm(1)
            await task

            async with await queue.consume() as messages:
                # exactly the headers the broker would hand back to a consumer
                channel.deliver(b"payload", headers=channel.published[0]["properties"].headers)

                async for message in messages:
                    assert dict(message.meta) == {"attempt": 1}
                    break


@mark.asyncio
async def test_declare_queue_forwards_amqp_arguments(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq, ctx.scope("mq", mq):
        await RabbitMQ.declare_queue(
            "jobs",
            durable=True,
            arguments={"x-message-ttl": 60000},
        )

    declared = [record for channel in channels for record in channel.declared]
    assert declared == [
        {
            "queue": "jobs",
            "passive": False,
            "durable": True,
            "exclusive": False,
            "auto_delete": False,
            "arguments": {"x-message-ttl": 60000},
        }
    ]


@mark.asyncio
async def test_declare_queue_rejects_unsupported_options(
    channels: MutableSequence[FakeChannel],
) -> None:
    # a misspelled declare option used to become an AMQP queue argument, which
    # the broker accepts without complaint
    async with ctx.scope("test"), _client() as mq, ctx.scope("mq", mq):
        with raises(RabbitMQException, match="Unsupported queue declare options: durabel"):
            await RabbitMQ.declare_queue("jobs", durabel=True)  # pyright: ignore[reportCallIssue]

    assert [record for channel in channels for record in channel.declared] == []


@mark.asyncio
async def test_queue_access_waits_for_the_channel_close(
    channels: MutableSequence[FakeChannel],
) -> None:
    # pika only initiates the close, and until the broker answers it the
    # consumers still hold the queue against a delete or a redeclare
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            await (await queue.consume()).__aenter__()
            channel = channels[-1]

        assert channel.is_closing is False
        assert channel.is_closed is True


@mark.asyncio
async def test_a_channel_close_that_stalls_does_not_fail_teardown(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    def stalling_close(
        self: FakeChannel,
        reply_code: int = 0,
        reply_text: str = "Normal shutdown",
    ) -> None:
        self.closed = True
        self.is_open = False
        self.is_closing = True  # no Channel.CloseOk is coming

    monkeypatch.setattr(FakeChannel, "close", stalling_close)

    async with ctx.scope("test"), _client(operation_timeout=0.05) as mq:
        async with await mq.queue("jobs", bytes, bytes):
            pass  # exits without raising over the stalled close

    assert channels[-1].closed is True


@mark.asyncio
async def test_disconnect_waits_for_the_connection_close(monkeypatch: MonkeyPatch) -> None:
    # returning from the client context before the Connection.Close handshake
    # left the socket to event loop teardown and the broker to an abrupt drop
    created: MutableSequence[FakeChannel] = []
    connections: MutableSequence[FakeConnection] = []

    def build(**kwargs: Any) -> FakeConnection:
        connection = FakeConnection(channels=created, **kwargs)
        connections.append(connection)
        return connection

    monkeypatch.setattr(connection_module, "AsyncioConnection", build)

    async with ctx.scope("test"):
        async with _client():
            pass

        # asserted with nothing awaited since, so only the wait inside the exit
        # can have let the close complete
        assert connections[-1].close_calls == 1
        assert connections[-1].is_closing is False
        assert connections[-1].is_closed is True


@mark.asyncio
async def test_a_connection_close_that_stalls_does_not_fail_teardown(
    monkeypatch: MonkeyPatch,
) -> None:
    created: MutableSequence[FakeChannel] = []

    def stalling_close(self: FakeConnection) -> None:
        self.close_calls += 1
        self.is_open = False
        self.is_closing = True  # no Connection.CloseOk is coming

    def build(**kwargs: Any) -> FakeConnection:
        return FakeConnection(channels=created, **kwargs)

    monkeypatch.setattr(FakeConnection, "close", stalling_close)
    monkeypatch.setattr(connection_module, "AsyncioConnection", build)

    async with ctx.scope("test"), _client(connection_timeout=0.05):
        pass  # exits without raising over the stalled close


# --------------------------------------------------------------------------
# scoped consumption
# --------------------------------------------------------------------------


@mark.asyncio
async def test_leaving_the_consumption_cancels_the_consumer(
    channels: MutableSequence[FakeChannel],
) -> None:
    # breaking out of the loop used to leave the consumer registered, holding
    # its prefetched deliveries until the whole queue access was torn down
    async with ctx.scope("test"), _client() as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        access: Any = queue_access
        async with queue_access as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"payload", delivery_tag=1)
                async for message in messages:
                    await message.acknowledge()
                    break

            assert channel.cancelled == ["ctag-1"]
            assert access._consumers == []
            # the channel stays available for publishing and further consumers
            assert channel.is_open is True


@mark.asyncio
async def test_leaving_the_consumption_requeues_what_it_never_handed_out(
    channels: MutableSequence[FakeChannel],
) -> None:
    # the channel outlives the consumer, so nothing else would return these
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"first", delivery_tag=1)
                channel.deliver(b"second", delivery_tag=2)
                channel.deliver(b"third", delivery_tag=3)

                async for message in messages:
                    await message.acknowledge()
                    break

            assert channel.acked == [1]
            assert channel.rejected == [(2, True), (3, True)]


@mark.asyncio
async def test_a_finished_consumption_stops_delivering(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            async with await queue.consume() as messages:
                pass

            assert messages.is_finished is True
            assert channels[-1].cancelled == ["ctag-1"]


@mark.asyncio
async def test_consumptions_are_cancelled_independently(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        access: Any = queue_access
        async with queue_access as queue:
            channel = channels[-1]
            async with await queue.consume() as first:
                async with await queue.consume():
                    assert len(access._consumers) == 2

                # only the inner one is gone, the outer keeps receiving
                assert channel.cancelled == ["ctag-2"]
                assert len(access._consumers) == 1
                channel.deliver(b"payload", delivery_tag=1, tag="ctag-1")
                async for message in first:
                    await message.acknowledge()
                    break

    assert channel.acked == [1]


@mark.asyncio
async def test_a_stalled_cancel_does_not_fail_the_consumption_exit(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    def stalling_cancel(
        self: FakeChannel,
        consumer_tag: str = "",
        callback: Any = None,
    ) -> None:
        self.cancelled.append(consumer_tag)  # no Basic.CancelOk is coming

    monkeypatch.setattr(FakeChannel, "basic_cancel", stalling_cancel)
    async with ctx.scope("test"), _client(operation_timeout=0.05) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            async with await queue.consume():
                pass  # exits without raising over the stalled cancel

            assert channels[-1].cancelled == ["ctag-1"]


# --------------------------------------------------------------------------
# consumer recovery
# --------------------------------------------------------------------------


@mark.asyncio
async def test_consumers_are_re_established_after_a_channel_close(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            first = channels[-1]
            async with await queue.consume() as messages:
                first.force_close(AMQPConnectionError("channel gone"))
                # the recovery runs as a task, so let it reach the broker
                second = await _recovered(channels, first)
                assert second is not first
                assert len(second.consumer_callbacks) == 1

                # the iteration never ended, it just runs on the new channel
                assert messages.is_finished is False
                second.deliver(b"payload", delivery_tag=1)
                async for message in messages:
                    await message.acknowledge()
                    break

            assert second.acked == [1]


@mark.asyncio
async def test_consumers_are_re_established_after_a_broker_cancellation(
    channels: MutableSequence[FakeChannel],
) -> None:
    # a quorum queue moving to another node cancels its consumers
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.cancel_consumer()
                async with timeout(1):
                    while "ctag-2" not in channel.consumer_callbacks:
                        await sleep(0)

                assert messages.is_finished is False
                channel.deliver(b"payload", delivery_tag=1, tag="ctag-2")
                async for message in messages:
                    await message.acknowledge()
                    break

            assert channel.acked == [1]


@mark.asyncio
async def test_recovery_drops_the_deliveries_the_broker_reclaimed(
    channels: MutableSequence[FakeChannel],
) -> None:
    # everything unacknowledged goes back to the queue when the channel dies,
    # so handing the buffer over would duplicate the work unacknowledgeably
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            first = channels[-1]
            async with await queue.consume() as messages:
                first.deliver(b"stale", delivery_tag=1)
                first.force_close(AMQPConnectionError("channel gone"))
                second = await _recovered(channels, first)
                second.deliver(b"fresh", delivery_tag=1)
                async for message in messages:
                    assert message.content == b"fresh"
                    await message.acknowledge()
                    break


@mark.asyncio
async def test_recovery_gives_up_after_the_configured_attempts(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    async with ctx.scope("test"), _client(recovery_attempts=2, recovery_delay=0.01) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:

                def refusing(self: FakeChannel, *args: Any, **kwargs: Any) -> str:
                    raise ChannelWrongStateError("Channel is closed.")

                monkeypatch.setattr(FakeChannel, "basic_consume", refusing)
                channel.force_close(AMQPConnectionError("channel gone"))

                with raises(RabbitMQException, match="could not be recovered") as error:
                    async with timeout(1):
                        async for _ in messages:
                            raise AssertionError("nothing can be delivered")

                assert error.value.retryable is True


@mark.asyncio
async def test_recovery_stops_when_the_consumption_is_abandoned(
    channels: MutableSequence[FakeChannel],
) -> None:
    # nothing is listening anymore, so re-registering would only take deliveries
    # out of the queue and drop them
    async with ctx.scope("test"), _client(recovery_delay=0.01) as mq:
        queue_access = await mq.queue("jobs", bytes, bytes)
        access: Any = queue_access
        async with queue_access as queue:
            consumption = await queue.consume()
            messages = await consumption.__aenter__()
            channels[-1].force_close(AMQPConnectionError("channel gone"))
            await consumption.__aexit__(None, None, None)

            assert messages.is_finished is True
            await sleep(0.05)  # long enough for a recovery attempt to run
            assert access._consumers == []


# --------------------------------------------------------------------------
# flow control and error classification
# --------------------------------------------------------------------------


@mark.asyncio
async def test_a_blocked_broker_is_reported_as_flow_control(
    broker: FakeBroker,
) -> None:
    async with ctx.scope("test"), _client(publish_timeout=0.05) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            broker.connections[-1].block("low on disk space")

            with raises(RabbitMQException, match="flow control: low on disk space") as error:
                await queue.publish(b"payload")

            assert error.value.retryable is True


@mark.asyncio
async def test_a_lifted_alarm_restores_ordinary_timeout_reporting(
    broker: FakeBroker,
) -> None:
    async with ctx.scope("test"), _client(publish_timeout=0.05) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            broker.connections[-1].block()
            broker.connections[-1].unblock()

            with raises(RabbitMQException, match="Timed out waiting for publish confirmation"):
                await queue.publish(b"payload")


@mark.asyncio
async def test_publish_confirmation_uses_its_own_timeout(
    broker: FakeBroker,
) -> None:
    # confirms on a loaded durable queue outlast an ordinary channel operation
    async with ctx.scope("test"), _client(operation_timeout=0.01, publish_timeout=5.0) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = broker.channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0.05)  # well past the channel operation timeout

            assert task.done() is False
            channel.confirm(1)
            await task


@mark.asyncio
@mark.parametrize(
    ("retryable", "failure"),
    [
        (True, "nack"),
        (False, "unroutable"),
        (False, "encode"),
    ],
)
async def test_publish_failures_carry_their_retryability(
    channels: MutableSequence[FakeChannel],
    retryable: bool,
    failure: str,
) -> None:
    def failing_encoder(_: bytes) -> bytes:
        raise ValueError("cannot encode")

    async with ctx.scope("test"), _client() as mq:
        encoder: Any = failing_encoder if failure == "encode" else bytes
        async with await mq.queue("jobs", encoder, bytes) as queue:
            channel = channels[-1]
            task: Task[None] = ensure_future(queue.publish(b"payload"))
            await sleep(0)
            match failure:
                case "nack":
                    channel.reject_publish(1)

                case "unroutable":
                    channel.return_publish(channel.published[-1]["properties"])

                case _:
                    pass  # the encoder already failed

            with raises(RabbitMQException) as error:
                await task

            assert error.value.retryable is retryable


@mark.asyncio
async def test_settling_a_dead_delivery_is_not_retryable(
    channels: MutableSequence[FakeChannel],
) -> None:
    # the broker requeues it anyway, so repeating the acknowledge cannot help
    async with ctx.scope("test"), _client(recovery_attempts=0) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            async with await queue.consume() as messages:
                channel.deliver(b"payload", delivery_tag=1)
                async for message in messages:
                    channel.force_close(AMQPConnectionError("channel gone"))
                    with raises(RabbitMQException, match="Failed to acknowledge") as error:
                        await message.acknowledge()

                    assert error.value.retryable is False
                    break


@mark.asyncio
async def test_adapter_misuse_is_not_retryable(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            with raises(RabbitMQException) as error:
                await queue.consume(auto_ack=True)

            assert error.value.retryable is False


def test_invalid_recovery_settings_are_rejected() -> None:
    with raises(RabbitMQException, match="recovery attempts cannot be negative"):
        _client(recovery_attempts=-1)

    with raises(RabbitMQException, match="recovery delay has to be positive"):
        _client(recovery_delay=0)


@mark.asyncio
async def test_a_failing_cancel_does_not_fail_the_consumption_exit(
    channels: MutableSequence[FakeChannel],
    monkeypatch: MonkeyPatch,
) -> None:
    # the channel can go away between the check and the cancel, which cancels
    # the consumer anyway - there is nothing left to do about it
    def refusing_cancel(
        self: FakeChannel,
        consumer_tag: str = "",
        callback: Any = None,
    ) -> None:
        raise ChannelWrongStateError("Channel is closed.")

    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            async with await queue.consume() as messages:
                monkeypatch.setattr(FakeChannel, "basic_cancel", refusing_cancel)

            assert messages.is_finished is True

        assert channels[-1].closed is True


@mark.asyncio
async def test_a_failing_release_leaves_the_rest_to_the_broker(
    channels: MutableSequence[FakeChannel],
) -> None:
    # rejecting on a channel that just died raises, and the close requeues
    # everything unacknowledged regardless
    async with ctx.scope("test"), _client(recovery_attempts=0) as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            consumption = await queue.consume()
            await consumption.__aenter__()
            channel.deliver(b"first", delivery_tag=1)
            channel.deliver(b"second", delivery_tag=2)

            def dying_cancel(consumer_tag: str = "", callback: Any = None) -> None:
                channel.cancelled.append(consumer_tag)
                channel.is_open = False
                get_running_loop().call_soon(callback, _Frame(object()))

            channel.basic_cancel = dying_cancel  # pyright: ignore[reportAttributeAccessIssue]
            await consumption.__aexit__(None, None, None)

            # neither could be released, both are back on the queue instead
            assert channel.rejected == []


@mark.asyncio
async def test_a_cancelled_iteration_still_has_its_buffer_released(
    channels: MutableSequence[FakeChannel],
) -> None:
    # ending the iteration is not settling what it never read, and the channel
    # outlives the consumer, so those deliveries have to be handed back here
    async with ctx.scope("test"), _client() as mq:
        async with await mq.queue("jobs", bytes, bytes) as queue:
            channel = channels[-1]
            consumption = await queue.consume()
            messages = await consumption.__aenter__()
            channel.deliver(b"payload", delivery_tag=1)
            # the application ended the iteration on its own
            messages.cancel()

            await consumption.__aexit__(None, None, None)
            assert channel.rejected == [(1, True)]
