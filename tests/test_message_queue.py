from collections.abc import AsyncGenerator
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from logging import DEBUG, Handler, LogRecord, getLogger
from typing import Any

import pytest

from haiway import ctx
from haiway.helpers.message_queue import MQMessage, MQMessageSource, MQQueue
from haiway.types import FlatObject, Meta


class _Collecting(Handler):
    def __init__(self) -> None:
        super().__init__()
        self.messages: list[str] = []

    def emit(self, record: LogRecord) -> None:
        self.messages.append(record.getMessage())


class Recorder:
    def __init__(self) -> None:
        self.acknowledged: list[dict[str, Any]] = []
        self.rejected: list[dict[str, Any]] = []

    async def acknowledge(self, **extra: Any) -> None:
        self.acknowledged.append(extra)

    async def reject(self, **extra: Any) -> None:
        self.rejected.append(extra)

    def message[Content](self, content: Content) -> MQMessage[Content]:
        return MQMessage(
            content=content,
            acknowledge=self.acknowledge,
            reject=self.reject,
            meta=Meta.of({"source": "test"}),
        )


@pytest.mark.asyncio
async def test_message_exposes_content_and_meta() -> None:
    recorder = Recorder()
    message = recorder.message("payload")

    assert message.content == "payload"
    assert message.meta == Meta.of({"source": "test"})
    assert recorder.acknowledged == []
    assert recorder.rejected == []


@pytest.mark.asyncio
async def test_acknowledge_and_reject_forward_extra_options() -> None:
    recorder = Recorder()
    message = recorder.message("payload")

    await message.acknowledge()
    await message.acknowledge(multiple=True)
    await message.reject(requeue=False)

    assert recorder.acknowledged == [{}, {"multiple": True}]
    assert recorder.rejected == [{"requeue": False}]


@pytest.mark.asyncio
async def test_context_manager_acknowledges_on_success() -> None:
    recorder = Recorder()

    async with recorder.message({"id": 1}) as content:
        assert content == {"id": 1}

    assert recorder.acknowledged == [{}]
    assert recorder.rejected == []


@pytest.mark.asyncio
async def test_context_manager_rejects_on_exception_and_propagates() -> None:
    recorder = Recorder()

    with pytest.raises(RuntimeError, match="boom"):
        async with recorder.message("payload"):
            raise RuntimeError("boom")

    assert recorder.acknowledged == []
    assert recorder.rejected == [{}]


@pytest.mark.asyncio
async def test_map_transforms_content_and_keeps_settlement() -> None:
    recorder = Recorder()
    message = recorder.message("42")

    mapped = message.map(int)

    assert mapped.content == 42
    assert mapped.meta == message.meta
    assert message.content == "42"  # original untouched

    await mapped.acknowledge(flag=1)
    await mapped.reject()
    assert recorder.acknowledged == [{"flag": 1}]
    assert recorder.rejected == [{}]


@pytest.mark.asyncio
async def test_map_propagates_mapping_errors() -> None:
    recorder = Recorder()

    with pytest.raises(ValueError):
        recorder.message("not a number").map(int)


class FakeBroker:
    def __init__(self) -> None:
        self.published: list[tuple[str, FlatObject | None, dict[str, Any]]] = []
        self.recorder = Recorder()
        self.consumer_options: list[dict[str, Any]] = []
        self.cancelled: int = 0
        self.pending: list[str] = []

    async def publishing(
        self,
        message: str,
        attributes: FlatObject | None,
        **extra: Any,
    ) -> None:
        self.published.append((message, attributes, extra))

    async def consuming(
        self,
        **extra: Any,
    ) -> AbstractAsyncContextManager[AsyncGenerator[MQMessage[str]]]:
        self.consumer_options.append(extra)
        return self._consumer()

    @asynccontextmanager
    async def _consumer(self) -> AsyncGenerator[AsyncGenerator[MQMessage[str]]]:

        async def messages() -> AsyncGenerator[MQMessage[str]]:
            for content in self.pending:
                yield self.recorder.message(content)

        try:
            yield messages()

        finally:
            self.cancelled += 1

    def queue(self) -> MQQueue[str]:
        return MQQueue[str](
            publishing=self.publishing,
            consuming=self.consuming,
        )


@pytest.mark.asyncio
async def test_publish_on_instance_forwards_arguments() -> None:
    broker = FakeBroker()
    queue = broker.queue()

    await queue.publish("hello")
    await queue.publish("world", attributes={"k": "v"}, exchange="events")

    assert broker.published == [
        ("hello", None, {}),
        ("world", {"k": "v"}, {"exchange": "events"}),
    ]


@pytest.mark.asyncio
async def test_publish_on_class_uses_context_state() -> None:
    broker = FakeBroker()

    async with ctx.scope("mq-publish", broker.queue()):
        await MQQueue[str].publish("via-state", attributes={"a": 1})

    assert broker.published == [("via-state", {"a": 1}, {})]


@pytest.mark.asyncio
async def test_publish_propagates_backend_errors() -> None:
    async def failing(message: str, attributes: FlatObject | None, **extra: Any) -> None:
        raise ConnectionError("broker down")

    async def unused(
        **extra: Any,
    ) -> AbstractAsyncContextManager[AsyncGenerator[MQMessage[str]]]:
        raise AssertionError("not consumed")

    queue = MQQueue[str](publishing=failing, consuming=unused)

    with pytest.raises(ConnectionError, match="broker down"):
        await queue.publish("x")


@pytest.mark.asyncio
async def test_consume_yields_messages_and_cancels_on_exit() -> None:
    broker = FakeBroker()
    broker.pending = ["a", "b"]
    queue = broker.queue()

    received: list[str] = []
    async with await queue.consume(requeue_rejected=True) as messages:
        async for message in messages:
            async with message as content:
                received.append(content)

    assert received == ["a", "b"]
    assert broker.consumer_options == [{"requeue_rejected": True}]
    assert broker.recorder.acknowledged == [{}, {}]
    assert broker.cancelled == 1


@pytest.mark.asyncio
async def test_consume_early_exit_still_releases_consumer() -> None:
    broker = FakeBroker()
    broker.pending = ["a", "b", "c"]

    async with ctx.scope("mq-consume", broker.queue()):
        async with await MQQueue[str].consume() as messages:
            async for message in messages:
                await message.acknowledge()
                break

    assert broker.recorder.acknowledged == [{}]
    assert broker.cancelled == 1


@pytest.mark.asyncio
async def test_consume_releases_consumer_when_loop_raises() -> None:
    broker = FakeBroker()
    broker.pending = ["a"]
    queue = broker.queue()

    with pytest.raises(RuntimeError, match="handler failed"):
        async with await queue.consume() as messages:
            async for message in messages:
                async with message:
                    raise RuntimeError("handler failed")

    assert broker.recorder.rejected == [{}]
    assert broker.cancelled == 1


@pytest.mark.asyncio
async def test_settling_records_the_source_of_the_message() -> None:
    logger = getLogger("test-message-source")
    logger.setLevel(DEBUG)
    handler = _Collecting()
    logger.addHandler(handler)
    recorder = Recorder()
    message = MQMessage(
        content="payload",
        acknowledge=recorder.acknowledge,
        reject=recorder.reject,
        meta=Meta.of({}),
        source=MQMessageSource(system="custom", destination="jobs", attributes={"shard": 3}),
    )
    try:
        async with ctx.scope("test", observability=logger):
            await message.map(len).acknowledge()
            await message.reject(requeue=False)

    finally:
        logger.removeHandler(handler)

    rendered: str = "\n".join(handler.messages)
    # the source survives mapping, and describes both outcomes alike
    assert rendered.count("Event: messaging.settle") == 2
    assert '"messaging.system"]: "custom"' in rendered
    assert '"messaging.destination.name"]: "jobs"' in rendered
    assert '"shard"]: 3' in rendered
    assert '"messaging.settle.requeue"]: False' in rendered


@pytest.mark.asyncio
async def test_settling_a_message_without_a_source_records_the_outcome_only() -> None:
    logger = getLogger("test-message-no-source")
    logger.setLevel(DEBUG)
    handler = _Collecting()
    logger.addHandler(handler)
    recorder = Recorder()
    try:
        async with ctx.scope("test", observability=logger):
            await recorder.message("payload").acknowledge()

    finally:
        logger.removeHandler(handler)

    rendered: str = "\n".join(handler.messages)
    assert '"messaging.operation.name"]: "ack"' in rendered
    assert "messaging.system" not in rendered
