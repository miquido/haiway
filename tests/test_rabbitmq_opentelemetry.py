from asyncio import CancelledError, Task, ensure_future, sleep
from collections.abc import Iterator, MutableSequence
from typing import Any

import pytest

pytest.importorskip("pika")
pytest.importorskip("opentelemetry")

from opentelemetry import metrics, trace
from opentelemetry._logs import SeverityNumber
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind, Tracer
from pytest import MonkeyPatch, fixture, mark, raises

from haiway import ctx
from haiway.context import ObservabilityLevel
from haiway.opentelemetry import OpenTelemetry, observability
from haiway.rabbitmq import RabbitMQException
from haiway.rabbitmq import connection as connection_module
from tests.test_rabbitmq import FakeBroker, FakeChannel, FakeConnection, _client


@fixture
def channels(monkeypatch: MonkeyPatch) -> Iterator[MutableSequence[FakeChannel]]:
    # both variables resolve when a connection is created, the ambient environment
    # would otherwise decide what the tests observe
    for name in ("RABBITMQ_PREFETCH", "RABBITMQ_URL"):
        monkeypatch.delenv(name, raising=False)

    created: FakeBroker = FakeBroker()

    def build(**kwargs: Any) -> FakeConnection:
        connection = FakeConnection(channels=created.channels, **kwargs)
        created.connections.append(connection)
        return connection

    monkeypatch.setattr(connection_module, "AsyncioConnection", build)
    yield created.channels


@fixture
def logs(
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
    monkeypatch: MonkeyPatch,
) -> Iterator[InMemoryLogRecordExporter]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider(shutdown_on_exit=False)
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    monkeypatch.setattr(observability, "get_logger_provider", lambda: provider)
    OpenTelemetry.autoconfigure(service="test-service")

    yield exporter

    provider.shutdown()


@fixture
def telemetry(
    monkeypatch: MonkeyPatch,
) -> Iterator[tuple[InMemorySpanExporter, InMemoryMetricReader]]:
    spans = InMemorySpanExporter()
    tracer_provider = TracerProvider(sampler=ALWAYS_ON, shutdown_on_exit=False)
    tracer_provider.add_span_processor(SimpleSpanProcessor(spans))
    reader = InMemoryMetricReader()
    meter_provider = MeterProvider(metric_readers=[reader], shutdown_on_exit=False)

    def get_tracer(*args: object, **kwargs: object) -> Tracer:
        return tracer_provider.get_tracer("test")

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: tracer_provider)
    monkeypatch.setattr(trace, "get_tracer", get_tracer)
    monkeypatch.setattr(metrics, "get_meter_provider", lambda: meter_provider)
    monkeypatch.setattr(metrics, "get_meter", lambda *a, **k: meter_provider.get_meter("test"))

    snapshot = (
        OpenTelemetry._tracer_provider,
        OpenTelemetry._meter_provider,
        OpenTelemetry._logger_provider,
        OpenTelemetry._logger,
    )
    OpenTelemetry.autoconfigure(service="test-service")

    yield spans, reader

    (
        OpenTelemetry._tracer_provider,
        OpenTelemetry._meter_provider,
        OpenTelemetry._logger_provider,
        OpenTelemetry._logger,
    ) = snapshot


def _metric_points(reader: InMemoryMetricReader, name: str) -> list[dict[str, object]]:
    data = reader.get_metrics_data()
    assert data is not None
    return [
        dict(point.attributes)
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points
    ]


@mark.asyncio
async def test_publish_is_a_producer_span_and_carries_its_context(
    channels: MutableSequence[FakeChannel],
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    spans, _ = telemetry
    async with (
        ctx.scope("test", observability=OpenTelemetry.observability()),
        _client() as mq,
        await mq.queue("jobs", bytes, bytes) as queue,
    ):
        channel = channels[-1]
        task: Task[None] = ensure_future(queue.publish(b"payload", attributes={"retry": 2}))
        await sleep(0)
        channel.confirm(1)
        await task

    (producer,) = [span for span in spans.get_finished_spans() if span.name == "publish jobs"]
    assert producer.kind is SpanKind.PRODUCER
    assert producer.attributes is not None
    assert producer.attributes["messaging.system"] == "rabbitmq"
    assert producer.attributes["messaging.destination.name"] == "jobs"
    assert producer.attributes["messaging.rabbitmq.destination.routing_key"] == "jobs"
    assert producer.attributes["messaging.message.body.size"] == len(b"payload")

    headers = channel.published[0]["properties"].headers
    assert producer.context is not None
    # the consumer continues from the producer span, never from its caller
    assert headers["traceparent"].startswith(
        f"00-{producer.context.trace_id:032x}-{producer.context.span_id:016x}-"
    )
    assert headers["retry"] == 2


@mark.asyncio
async def test_publish_attributes_win_over_the_propagated_context(
    channels: MutableSequence[FakeChannel],
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    async with (
        ctx.scope("test", observability=OpenTelemetry.observability()),
        _client() as mq,
        await mq.queue("jobs", bytes, bytes) as queue,
    ):
        channel = channels[-1]
        task: Task[None] = ensure_future(
            queue.publish(b"payload", attributes={"traceparent": "explicit"})
        )
        await sleep(0)
        channel.confirm(1)
        await task

    assert channel.published[0]["properties"].headers["traceparent"] == "explicit"


@mark.asyncio
async def test_publish_is_measured_with_bounded_attributes(
    channels: MutableSequence[FakeChannel],
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    _, reader = telemetry
    async with (
        ctx.scope("test", observability=OpenTelemetry.observability()),
        _client() as mq,
        await mq.queue("jobs", bytes, bytes) as queue,
    ):
        channel = channels[-1]
        task: Task[None] = ensure_future(queue.publish(b"payload"))
        await sleep(0)
        channel.confirm(1)
        await task

    expected = {
        "messaging.system": "rabbitmq",
        "messaging.operation.name": "publish",
        "messaging.operation.type": "send",
        "messaging.destination.name": "jobs",
    }
    assert _metric_points(reader, "messaging.client.operation.duration") == [expected]
    assert _metric_points(reader, "messaging.client.sent.messages") == [expected]


@mark.asyncio
async def test_failed_publish_fails_its_span_and_reports_the_error_type(
    channels: MutableSequence[FakeChannel],
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    spans, reader = telemetry

    def failing(**kwargs: object) -> None:
        raise RuntimeError("boom")

    async with (
        ctx.scope("test", observability=OpenTelemetry.observability()),
        _client() as mq,
        await mq.queue("jobs", bytes, bytes) as queue,
    ):
        channels[-1].basic_publish = failing  # pyright: ignore[reportAttributeAccessIssue]
        with raises(RabbitMQException):
            await queue.publish(b"payload")

    (producer,) = [span for span in spans.get_finished_spans() if span.name == "publish jobs"]
    assert producer.attributes is not None
    assert producer.attributes["error.type"] == "RabbitMQException"
    (point,) = _metric_points(reader, "messaging.client.operation.duration")
    assert point["error.type"] == "RabbitMQException"


@mark.asyncio
async def test_cancelled_publish_is_measured_as_a_failure(
    channels: MutableSequence[FakeChannel],
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
) -> None:
    _, reader = telemetry
    async with (
        ctx.scope("test", observability=OpenTelemetry.observability()),
        _client() as mq,
        await mq.queue("jobs", bytes, bytes) as queue,
    ):
        task: Task[None] = ensure_future(queue.publish(b"payload"))
        await sleep(0)  # waiting for the confirmation, which never comes
        task.cancel()
        with raises(CancelledError):
            await task

    # cancelled is not completed - it must not read as a successful publish
    for name in ("messaging.client.operation.duration", "messaging.client.sent.messages"):
        (point,) = _metric_points(reader, name)
        assert point["error.type"] == "CancelledError"


@mark.asyncio
async def test_settling_records_an_event_within_the_current_scope(
    channels: MutableSequence[FakeChannel],
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
    logs: InMemoryLogRecordExporter,
) -> None:
    spans, _ = telemetry
    async with (
        ctx.scope("loop", observability=OpenTelemetry.observability(ObservabilityLevel.DEBUG)),
        _client() as mq,
        await mq.queue("jobs", bytes, bytes) as queue,
    ):
        channel = channels[-1]
        async with await queue.consume() as messages:
            channel.deliver(b"payload", headers={"authorization": "Bearer SECRET"})
            async for message in messages:
                async with message as payload:
                    assert payload == b"payload"

                break

    # no scope of its own - the processing belongs to the scope that opened the message
    assert [span.name for span in spans.get_finished_spans()] == ["loop"]
    (record,) = [
        entry.log_record
        for entry in logs.get_finished_logs()
        if entry.log_record.event_name == "messaging.settle"
    ]
    assert record.severity_number is SeverityNumber.DEBUG
    assert record.attributes is not None
    assert record.attributes["messaging.system"] == "rabbitmq"
    assert record.attributes["messaging.destination.name"] == "jobs"
    assert record.attributes["messaging.operation.name"] == "ack"
    assert record.attributes["messaging.operation.type"] == "settle"
    assert record.attributes["messaging.rabbitmq.destination.routing_key"] == "jobs"
    assert "SECRET" not in repr(dict(record.attributes))
    (loop_span,) = spans.get_finished_spans()
    assert loop_span.context is not None
    assert record.span_id == loop_span.context.span_id
    assert channel.acked == [1]


@mark.asyncio
async def test_rejecting_is_recorded_at_info_level_with_its_options(
    channels: MutableSequence[FakeChannel],
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
    logs: InMemoryLogRecordExporter,
) -> None:
    async with (
        ctx.scope("loop", observability=OpenTelemetry.observability()),
        _client() as mq,
        await mq.queue("jobs", bytes, bytes) as queue,
    ):
        channel = channels[-1]
        async with await queue.consume() as messages:
            channel.deliver(b"payload")
            async for message in messages:
                await message.reject(requeue=False)
                break

    (record,) = [
        entry.log_record
        for entry in logs.get_finished_logs()
        if entry.log_record.event_name == "messaging.settle"
    ]
    assert record.severity_number is SeverityNumber.INFO
    assert record.attributes is not None
    assert record.attributes["messaging.operation.name"] == "reject"
    assert record.attributes["messaging.settle.requeue"] is False
    assert channel.rejected == [(1, False)]


@mark.asyncio
async def test_acknowledged_message_stays_quiet_at_the_default_level(
    channels: MutableSequence[FakeChannel],
    telemetry: tuple[InMemorySpanExporter, InMemoryMetricReader],
    logs: InMemoryLogRecordExporter,
) -> None:
    async with (
        ctx.scope("loop", observability=OpenTelemetry.observability()),
        _client() as mq,
        await mq.queue("jobs", bytes, bytes) as queue,
    ):
        channel = channels[-1]
        async with await queue.consume() as messages:
            channel.deliver(b"payload")
            async for message in messages:
                await message.acknowledge()
                break

    # connection logs of the adapter aside, nothing is recorded for the routine outcome
    assert not [
        entry
        for entry in logs.get_finished_logs()
        if entry.log_record.event_name == "messaging.settle"
    ]


@mark.asyncio
async def test_settling_works_without_a_tracing_backend(
    channels: MutableSequence[FakeChannel],
) -> None:
    async with ctx.scope("loop"), _client() as mq, await mq.queue("jobs", bytes, bytes) as queue:
        channel = channels[-1]
        async with await queue.consume() as messages:
            channel.deliver(b"payload", headers={"traceparent": "ignored"})
            async for message in messages:
                async with message as payload:
                    assert payload == b"payload"

                break

    assert channel.acked == [1]
