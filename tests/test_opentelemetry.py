import asyncio
from collections.abc import Iterator, Mapping, Sequence
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry._logs import NoOpLoggerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    InMemoryLogRecordExporter,
    SimpleLogRecordProcessor,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import ProxyTracerProvider, SpanContext, StatusCode, Tracer
from pytest import LogCaptureFixture, MonkeyPatch, fixture, mark, raises

from haiway import ctx
from haiway.opentelemetry import OpenTelemetry, OpenTelemetryException, observability

_CONFIGURATION_ATTRIBUTES: Sequence[str] = (
    "service",
    "version",
    "environment",
    "_logger",
    "_logger_provider",
    "_meter_provider",
    "_tracer_provider",
)


@fixture(autouse=True)
def isolated_configuration() -> Iterator[None]:
    """Keep `OpenTelemetry` process wide configuration from leaking between tests."""
    snapshot = {name: getattr(OpenTelemetry, name) for name in _CONFIGURATION_ATTRIBUTES}
    yield
    for name, value in snapshot.items():
        setattr(OpenTelemetry, name, value)


@fixture
def spans(monkeypatch: MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """Install an in-memory tracer provider for the duration of one test."""

    # OTEL_* in the ambient environment must not decide whether spans are
    # recorded - an always-on sampler keeps the test independent of the shell
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)

    exporter = InMemorySpanExporter()
    # shutdown_on_exit=False - the provider is per test, an atexit hook per test
    # would pile up for the whole session
    provider = TracerProvider(sampler=ALWAYS_ON, shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def get_tracer_provider() -> TracerProvider:
        return provider

    def get_tracer(*args: object, **kwargs: object) -> Tracer:
        return provider.get_tracer("test")

    # the global tracer provider is set-once, so patch the lookup instead
    monkeypatch.setattr(trace, "get_tracer_provider", get_tracer_provider)
    monkeypatch.setattr(trace, "get_tracer", get_tracer)

    OpenTelemetry.autoconfigure(
        service="test-service",
        version="1.2.3",
        environment="test",
    )

    yield exporter

    exporter.clear()


def _named(
    spans: InMemorySpanExporter,
    name: str,
    /,
) -> ReadableSpan:
    matching: Sequence[ReadableSpan] = [
        span for span in spans.get_finished_spans() if span.name == name
    ]
    assert len(matching) == 1, f"expected exactly one {name!r} span, got {len(matching)}"
    return matching[0]


def _context(
    span: ReadableSpan,
    /,
) -> SpanContext:
    context: SpanContext | None = span.get_span_context()
    assert context is not None
    return context


def _parent(
    span: ReadableSpan,
    /,
) -> SpanContext:
    assert span.parent is not None
    return span.parent


@mark.asyncio
async def test_autoconfigure_allows_using_global_providers(spans: InMemorySpanExporter) -> None:
    async with ctx.scope(
        "root",
        observability=OpenTelemetry.observability(),
    ):
        ctx.log_info("configured through global providers")
        ctx.record_info(
            metric="requests.total",
            value=1,
            kind="counter",
        )

    assert OpenTelemetry.service == "test-service"
    assert OpenTelemetry.version == "1.2.3"
    assert OpenTelemetry.environment == "test"


@mark.asyncio
async def test_nested_scope_restores_parent_context(spans: InMemorySpanExporter) -> None:
    # a nested scope completing in another task must not leave the ambient
    # OpenTelemetry context pointing away from the still-active parent
    async def leaf() -> None:
        async with ctx.scope("leaf"):
            await asyncio.sleep(0.02)

    async with ctx.scope("root", observability=OpenTelemetry.observability()):
        root_context = trace.get_current_span().get_span_context()

        async with ctx.scope("mid"):
            ctx.spawn(leaf)
            await asyncio.sleep(0)

        await asyncio.sleep(0.05)

        assert trace.get_current_span().get_span_context() == root_context


@mark.asyncio
async def test_nested_scopes_form_a_span_hierarchy(spans: InMemorySpanExporter) -> None:
    async with ctx.scope("root", observability=OpenTelemetry.observability()):
        async with ctx.scope("child"):
            pass

    root = _named(spans, "root")
    child = _named(spans, "child")

    assert _parent(child).span_id == _context(root).span_id
    assert _context(child).trace_id == _context(root).trace_id


@mark.asyncio
async def test_concurrent_root_scopes_share_one_adapter(spans: InMemorySpanExporter) -> None:
    # one adapter hoisted out of the scopes it backs must not crash
    observability = OpenTelemetry.observability()

    async def worker(index: int) -> None:
        async with ctx.scope(f"worker-{index}", observability=observability):
            await asyncio.sleep(0.01)

    await asyncio.gather(worker(1), worker(2))

    first = _named(spans, "worker-1")
    second = _named(spans, "worker-2")
    # independent roots, so distinct traces
    assert _context(first).trace_id != _context(second).trace_id


@mark.asyncio
async def test_traceparent_continues_the_remote_trace(spans: InMemorySpanExporter) -> None:
    observability = OpenTelemetry.observability(
        traceparent="00-1234567890abcdef1234567890abcdef-abcdef1234567890-01",
    )

    async with ctx.scope("downstream", observability=observability):
        pass

    span = _named(spans, "downstream")
    assert format(_context(span).trace_id, "032x") == "1234567890abcdef1234567890abcdef"
    assert format(_parent(span).span_id, "016x") == "abcdef1234567890"
    assert _parent(span).is_remote is True


@mark.asyncio
async def test_traceparent_roundtrips_through_encoder(spans: InMemorySpanExporter) -> None:
    async with ctx.scope("upstream", observability=OpenTelemetry.observability()):
        encoded = OpenTelemetry.traceparent()

    assert encoded is not None

    async with ctx.scope(
        "downstream",
        observability=OpenTelemetry.observability(traceparent=encoded),
    ):
        pass

    upstream = _named(spans, "upstream")
    downstream = _named(spans, "downstream")
    assert _context(downstream).trace_id == _context(upstream).trace_id


@mark.asyncio
@mark.parametrize(
    "traceparent",
    [
        "garbage",
        "00-not-hex-values-01",
        "00-00000000000000000000000000000000-abcdef1234567890-01",
        "00-1234567890abcdef1234567890abcdef-0000000000000000-01",
    ],
)
async def test_invalid_traceparent_is_ignored(
    spans: InMemorySpanExporter,
    traceparent: str,
) -> None:
    async with ctx.scope(
        "resilient",
        observability=OpenTelemetry.observability(traceparent=traceparent),
    ):
        pass

    # the scope still produces a usable root span of its own
    span = _named(spans, "resilient")
    assert span.parent is None
    assert _context(span).trace_id != 0


@mark.asyncio
async def test_scope_exiting_with_exception_marks_span_error(
    spans: InMemorySpanExporter,
) -> None:
    with raises(ValueError):
        async with ctx.scope("failing", observability=OpenTelemetry.observability()):
            raise ValueError("boom")

    span = _named(spans, "failing")
    assert span.status.status_code is StatusCode.ERROR


@mark.asyncio
async def test_successful_scope_leaves_status_unset(spans: InMemorySpanExporter) -> None:
    # OK is reserved for explicitly asserted success and is terminal in the SDK
    async with ctx.scope("succeeding", observability=OpenTelemetry.observability()):
        pass

    span = _named(spans, "succeeding")
    assert span.status.status_code is StatusCode.UNSET


@mark.asyncio
async def test_heterogeneous_sequence_attribute_is_preserved(
    spans: InMemorySpanExporter,
) -> None:
    # OpenTelemetry drops sequences that mix types, so they are unified instead.
    # ObservabilityAttribute does not permit a mixed sequence, so this covers
    # the defensive path for values arriving from untyped call sites.
    mixed: Mapping[str, Any] = {"mixed": ["a", 1, True]}
    async with ctx.scope("attributed", observability=OpenTelemetry.observability()):
        ctx.record_info(attributes=mixed)

    span = _named(spans, "attributed")
    assert span.attributes is not None
    assert span.attributes["mixed"] == ("a", "1", "True")


@mark.asyncio
async def test_homogeneous_sequence_attribute_keeps_types(
    spans: InMemorySpanExporter,
) -> None:
    async with ctx.scope("attributed", observability=OpenTelemetry.observability()):
        ctx.record_info(attributes={"counts": [1, 2, 3]})

    span = _named(spans, "attributed")
    assert span.attributes is not None
    assert span.attributes["counts"] == (1, 2, 3)


@mark.asyncio
async def test_non_finite_metric_is_skipped(spans: InMemorySpanExporter) -> None:
    # the SDK would drop these silently, so recording must not raise either
    async with ctx.scope("metered", observability=OpenTelemetry.observability()):
        ctx.record_info(metric="broken", value=float("nan"), kind="counter")
        ctx.record_info(metric="broken", value=float("inf"), kind="gauge")
        ctx.record_info(metric="fine", value=1, kind="counter")


@fixture
def metrics_reader(monkeypatch: MonkeyPatch) -> Iterator[InMemoryMetricReader]:
    """Install an in-memory meter provider for the duration of one test."""
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])

    # the global meter provider is set-once, so patch the lookup instead
    monkeypatch.setattr(metrics, "get_meter", lambda *args, **kwargs: provider.get_meter("test"))

    yield reader

    provider.shutdown()


def _instruments(
    reader: InMemoryMetricReader,
    /,
    name: str,
) -> Sequence[tuple[str, tuple[int, ...]]]:
    data = reader.get_metrics_data()
    assert data is not None
    return [
        (metric.unit, tuple(point.count for point in metric.data.data_points))  # pyright: ignore
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
    ]


def _points(
    reader: InMemoryMetricReader,
    /,
    name: str,
) -> Sequence[int | float]:
    data = reader.get_metrics_data()
    assert data is not None
    return [
        point.value  # pyright: ignore
        for resource in data.resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == name
        for point in metric.data.data_points  # pyright: ignore
    ]


@mark.asyncio
async def test_span_reports_its_own_duration(spans: InMemorySpanExporter) -> None:
    # the span reports when the scope itself left - after joining the tasks
    # spawned within it, but without waiting for the scopes enclosing it
    async def leaf() -> None:
        async with ctx.scope("leaf"):
            await asyncio.sleep(0.05)

    async with ctx.scope("root", observability=OpenTelemetry.observability()):
        async with ctx.scope("mid"):
            ctx.spawn(leaf)
            await asyncio.sleep(0)

        await asyncio.sleep(0.1)

    mid = _named(spans, "mid")
    leaf_span = _named(spans, "leaf")
    root = _named(spans, "root")

    assert mid.end_time is not None
    assert leaf_span.end_time is not None
    assert root.end_time is not None
    # the scope owns the tasks spawned within it, so it joins them before ending
    assert leaf_span.end_time <= mid.end_time
    # and it still ends long before the scope enclosing it
    assert mid.end_time < root.end_time
    # while the scope containing both still ends last
    assert leaf_span.end_time <= root.end_time


@mark.asyncio
async def test_metric_unit_change_keeps_its_own_instrument(
    spans: InMemorySpanExporter,
    metrics_reader: InMemoryMetricReader,
) -> None:
    async with ctx.scope("metered", observability=OpenTelemetry.observability()):
        ctx.record_info(metric="latency", value=1, unit="ms", kind="histogram")
        ctx.record_info(metric="latency", value=2, unit="ms", kind="histogram")

        async with ctx.scope("nested"):
            # a nested scope contributes to the very same stream...
            ctx.record_info(metric="latency", value=3, unit="ms", kind="histogram")
            # ...while a different unit is a different instrument, not a silently
            # discarded one
            ctx.record_info(metric="latency", value=4, unit="s", kind="histogram")

    assert sorted(_instruments(metrics_reader, "latency")) == [("ms", (3,)), ("s", (1,))]


@mark.asyncio
async def test_unsupported_metric_kind_is_skipped(
    spans: InMemorySpanExporter,
    metrics_reader: InMemoryMetricReader,
    caplog: LogCaptureFixture,
) -> None:
    # the kind is typed as a literal, yet it arrives through the untyped
    # `Observability` protocol - an unrecognized one must not be cached as a
    # missing instrument, which would break every later recording as well
    async with ctx.scope("metered", observability=OpenTelemetry.observability()):
        ctx.record_info(metric="latency", value=1, unit="ms", kind="summary")  # pyright: ignore[reportArgumentType]
        ctx.record_info(metric="latency", value=2, unit="ms", kind="summary")  # pyright: ignore[reportArgumentType]
        ctx.record_info(metric="latency", value=3, unit="ms", kind="histogram")

    assert _instruments(metrics_reader, "latency") == [("ms", (1,))]
    assert "unsupported kind" in caplog.text


@mark.asyncio
async def test_tracestate_accompanies_the_remote_parent(spans: InMemorySpanExporter) -> None:
    observability = OpenTelemetry.observability(
        traceparent="00-1234567890abcdef1234567890abcdef-abcdef1234567890-01",
        tracestate="vendor=value",
    )

    async with ctx.scope("downstream", observability=observability):
        pass

    span = _named(spans, "downstream")
    # vendor trace state has to survive the hop, it carries sampling decisions
    assert _parent(span).trace_state.get("vendor") == "value"
    assert _context(span).trace_state.get("vendor") == "value"


@mark.asyncio
async def test_trace_context_roundtrips_with_tracestate(spans: InMemorySpanExporter) -> None:
    async with ctx.scope(
        "upstream",
        observability=OpenTelemetry.observability(
            traceparent="00-1234567890abcdef1234567890abcdef-abcdef1234567890-01",
            tracestate="vendor=value",
        ),
    ):
        traceparent = OpenTelemetry.traceparent()
        tracestate = OpenTelemetry.tracestate()

    assert traceparent is not None
    assert tracestate == "vendor=value"

    async with ctx.scope(
        "downstream",
        observability=OpenTelemetry.observability(
            traceparent=traceparent,
            tracestate=tracestate,
        ),
    ):
        pass

    downstream = _named(spans, "downstream")
    upstream = _named(spans, "upstream")
    assert _context(downstream).trace_id == _context(upstream).trace_id


@mark.asyncio
async def test_observability_requires_configuration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(OpenTelemetry, "_logger", None)

    with raises(OpenTelemetryException):
        OpenTelemetry.observability()


def test_autoconfigure_rejects_empty_service(monkeypatch: MonkeyPatch) -> None:

    with raises(OpenTelemetryException):
        OpenTelemetry.autoconfigure(service="")


def test_configure_adopts_already_installed_providers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(OpenTelemetry, "_tracer_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_meter_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_logger_provider", None)

    installed_tracing = TracerProvider()
    installed_metering = MeterProvider()

    def get_tracer_provider() -> TracerProvider:
        return installed_tracing

    def get_meter_provider() -> MeterProvider:
        return installed_metering

    installed_logging = LoggerProvider(shutdown_on_exit=False)

    def get_logger_provider() -> LoggerProvider:
        return installed_logging

    # an SDK provider present means something already configured that signal
    monkeypatch.setattr(trace, "get_tracer_provider", get_tracer_provider)
    monkeypatch.setattr(metrics, "get_meter_provider", get_meter_provider)
    monkeypatch.setattr(observability, "get_logger_provider", get_logger_provider)

    OpenTelemetry.configure(
        service="adopting",
        version="2.0.0",
        environment="staging",
    )

    assert OpenTelemetry.service == "adopting"
    # adoption installs no providers of its own, but has to retain the existing
    # ones so force_flush and shutdown still reach spans and metrics
    assert OpenTelemetry._tracer_provider is installed_tracing
    assert OpenTelemetry._meter_provider is installed_metering
    assert OpenTelemetry._logger_provider is installed_logging


def test_configure_installs_providers_missing_from_a_partial_setup(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setattr(OpenTelemetry, "_tracer_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_meter_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_logger_provider", None)

    # only logs are configured externally, leaving traces and metrics on proxies
    installed_logging = LoggerProvider(shutdown_on_exit=False)
    tracing: Any = ProxyTracerProvider()
    metering: Any = NoOpMeterProvider()

    def get_logger_provider() -> LoggerProvider:
        return installed_logging

    def set_logger_provider(provider: object) -> None:
        raise AssertionError("an already installed logger provider must not be replaced")

    # the fakes have to behave like the real slots, which reflect what was set
    def get_tracer_provider() -> Any:
        return tracing

    def set_tracer_provider(provider: TracerProvider) -> None:
        nonlocal tracing
        tracing = provider

    def get_meter_provider() -> Any:
        return metering

    def set_meter_provider(provider: MeterProvider) -> None:
        nonlocal metering
        metering = provider

    # the global provider slots are set-once, so patch the accessors instead
    monkeypatch.setattr(observability, "get_logger_provider", get_logger_provider)
    monkeypatch.setattr(observability, "set_logger_provider", set_logger_provider)
    monkeypatch.setattr(trace, "get_tracer_provider", get_tracer_provider)
    monkeypatch.setattr(trace, "set_tracer_provider", set_tracer_provider)
    monkeypatch.setattr(metrics, "get_meter_provider", get_meter_provider)
    monkeypatch.setattr(metrics, "set_meter_provider", set_meter_provider)

    try:
        OpenTelemetry.configure(
            service="partial",
            version="3.0.0",
            environment="staging",
        )

        # the externally configured signal is adopted...
        assert OpenTelemetry._logger_provider is installed_logging
        # ...while the missing ones get exporting providers of their own
        assert isinstance(tracing, TracerProvider)
        assert isinstance(metering, MeterProvider)
        assert OpenTelemetry._tracer_provider is tracing
        assert OpenTelemetry._meter_provider is metering

    finally:
        if isinstance(tracing, TracerProvider):
            tracing.shutdown()

        if isinstance(metering, MeterProvider):
            metering.shutdown()


def test_configure_releases_providers_it_could_not_install(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    monkeypatch.setattr(OpenTelemetry, "_tracer_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_meter_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_logger_provider", None)

    # a non SDK provider holds the slot, so it can neither be adopted nor
    # replaced - the real accessors refuse the override and keep the no-op
    installed_metering = NoOpMeterProvider()
    built: list[MeterProvider] = []

    def get_meter_provider() -> Any:
        return installed_metering

    def set_meter_provider(provider: MeterProvider) -> None:
        built.append(provider)  # refused, exactly like the set-once global slot

    monkeypatch.setattr(metrics, "get_meter_provider", get_meter_provider)
    monkeypatch.setattr(metrics, "set_meter_provider", set_meter_provider)
    monkeypatch.setattr(trace, "get_tracer_provider", ProxyTracerProvider)
    monkeypatch.setattr(trace, "set_tracer_provider", lambda provider: None)
    monkeypatch.setattr(observability, "get_logger_provider", LoggerProvider)
    monkeypatch.setattr(observability, "set_logger_provider", lambda provider: None)

    OpenTelemetry.configure(
        service="refused",
        version="4.0.0",
        environment="staging",
    )

    # nothing is retained for a slot this integration does not own, so flushing
    # and shutting down never act on a provider telemetry does not reach
    assert OpenTelemetry._meter_provider is None
    # and the provider built for it is released instead of leaking its exporters
    assert len(built) == 1
    assert built[0]._shutdown is True
    assert any(record.levelname == "ERROR" for record in caplog.records)


def test_environment_endpoint_selects_otlp_exporters(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(OpenTelemetry, "_tracer_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_meter_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_logger_provider", None)
    # the SDK standard variable has to be honored, not only the explicit argument
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://collector.invalid:4317")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_INSECURE", "true")

    tracing: Any = ProxyTracerProvider()

    def set_tracer_provider(provider: TracerProvider) -> None:
        nonlocal tracing
        tracing = provider

    monkeypatch.setattr(trace, "get_tracer_provider", lambda: tracing)
    monkeypatch.setattr(trace, "set_tracer_provider", set_tracer_provider)
    monkeypatch.setattr(metrics, "get_meter_provider", MeterProvider)
    monkeypatch.setattr(observability, "get_logger_provider", LoggerProvider)

    try:
        OpenTelemetry.configure(
            service="exporting",
            version="5.0.0",
            environment="staging",
        )

        assert isinstance(tracing, TracerProvider)
        processors = tracing._active_span_processor._span_processors
        assert isinstance(processors[0].span_exporter, OTLPSpanExporter)

    finally:
        if isinstance(tracing, TracerProvider):
            tracing.shutdown()


def test_shutdown_requires_reconfiguration(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(OpenTelemetry, "_tracer_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_meter_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_logger_provider", None)

    OpenTelemetry.autoconfigure(service="stopping")
    OpenTelemetry.shutdown()

    # providers no longer export anything, so adapters must not be handed out
    with raises(OpenTelemetryException):
        OpenTelemetry.observability()


def test_autoconfigure_warns_without_installed_providers(
    monkeypatch: MonkeyPatch,
    caplog: LogCaptureFixture,
) -> None:
    monkeypatch.setattr(OpenTelemetry, "_tracer_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_meter_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_logger_provider", None)
    monkeypatch.setattr(trace, "get_tracer_provider", ProxyTracerProvider)
    monkeypatch.setattr(metrics, "get_meter_provider", NoOpMeterProvider)
    monkeypatch.setattr(observability, "get_logger_provider", NoOpLoggerProvider)

    OpenTelemetry.autoconfigure(service="unconfigured")

    # binding to proxies exports nothing, which must not pass unnoticed
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_flush_and_shutdown_without_configured_providers(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(OpenTelemetry, "_tracer_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_meter_provider", None)
    monkeypatch.setattr(OpenTelemetry, "_logger_provider", None)

    # both are safe to call regardless of configuration state
    OpenTelemetry.force_flush()
    OpenTelemetry.shutdown()


@fixture
def logs(
    spans: InMemorySpanExporter,
    monkeypatch: MonkeyPatch,
) -> Iterator[InMemoryLogRecordExporter]:
    """Install an in-memory logger provider on top of the `spans` fixture."""
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider(shutdown_on_exit=False)
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))

    # the global logger provider is set-once, so patch the lookup instead
    monkeypatch.setattr(observability, "get_logger_provider", lambda: provider)
    # re-resolve the bridge logger now that the provider lookup is patched
    OpenTelemetry.autoconfigure(
        service="test-service",
        version="1.2.3",
        environment="test",
    )

    yield exporter

    provider.shutdown()


def _bodies(
    logs: InMemoryLogRecordExporter,
    /,
) -> Sequence[Any]:
    return [record.log_record.body for record in logs.get_finished_logs()]


@mark.asyncio
async def test_error_status_describes_the_exception(spans: InMemorySpanExporter) -> None:
    with raises(ValueError):
        async with ctx.scope("failing", observability=OpenTelemetry.observability()):
            raise ValueError("boom")

    span = _named(spans, "failing")
    assert span.status.status_code is StatusCode.ERROR
    assert span.status.description == "ValueError: boom"


@mark.asyncio
async def test_cancellation_leaves_span_status_unset(spans: InMemorySpanExporter) -> None:
    # cancellation is routine control flow under structured concurrency, marking
    # it would paint whole subtrees red - matching the logger backend, which
    # reports an error only for regular exceptions
    async def cancelled() -> None:
        async with ctx.scope("cancelled", observability=OpenTelemetry.observability()):
            await asyncio.sleep(10)

    task = asyncio.create_task(cancelled())
    await asyncio.sleep(0)
    task.cancel()
    with raises(asyncio.CancelledError):
        await task

    span = _named(spans, "cancelled")
    assert span.status.status_code is StatusCode.UNSET


@mark.asyncio
async def test_scope_ends_after_every_scope_spawned_within_it(
    spans: InMemorySpanExporter,
) -> None:
    # the scope owning the spawned ones joins all of them before its span ends -
    # one slower scope must not be left behind
    async def leaf(index: int) -> None:
        async with ctx.scope(f"leaf-{index}"):
            await asyncio.sleep(0.01 * (index + 1))

    async with ctx.scope("root", observability=OpenTelemetry.observability()):
        async with ctx.scope("mid"):
            for index in range(4):
                ctx.spawn(leaf, index)

            await asyncio.sleep(0)

    mid = _named(spans, "mid")
    assert mid.end_time is not None
    for index in range(4):
        leaf_span = _named(spans, f"leaf-{index}")
        assert leaf_span.end_time is not None
        # nested under the scope they were spawned in, which awaited each of them
        assert _parent(leaf_span).span_id == _context(mid).span_id
        assert leaf_span.end_time <= mid.end_time


@mark.asyncio
async def test_oversized_integer_metric_is_skipped(
    spans: InMemorySpanExporter,
    metrics_reader: InMemoryMetricReader,
    caplog: LogCaptureFixture,
) -> None:
    # an integer too wide for a float makes the SDK raise while probing it, so it
    # has to be rejected here - without raising either, and without breaking the
    # instrument for the recordings which follow
    async with ctx.scope("metered", observability=OpenTelemetry.observability()):
        ctx.record_info(metric="huge", value=10**400, unit="ops", kind="histogram")
        ctx.record_info(metric="huge", value=1, unit="ops", kind="histogram")

    assert _instruments(metrics_reader, "huge") == [("ops", (1,))]
    assert "unrepresentable value" in caplog.text


@mark.asyncio
async def test_zero_metric_is_recorded(
    spans: InMemorySpanExporter,
    metrics_reader: InMemoryMetricReader,
    caplog: LogCaptureFixture,
) -> None:
    # only NaN, the infinities and integers too wide for a float are beyond what
    # the SDK accepts - zero is a perfectly representable measurement
    async with ctx.scope("metered", observability=OpenTelemetry.observability()):
        ctx.record_info(metric="delta", value=0, unit="ops", kind="histogram")

    assert _instruments(metrics_reader, "delta") == [("ops", (1,))]
    assert "unrepresentable value" not in caplog.text


@mark.asyncio
async def test_negative_gauge_metric_is_recorded(
    spans: InMemorySpanExporter,
    metrics_reader: InMemoryMetricReader,
    caplog: LogCaptureFixture,
) -> None:
    # a gauge is free to go below zero, unlike counters and histograms which the
    # SDK itself keeps non-negative
    async with ctx.scope("metered", observability=OpenTelemetry.observability()):
        ctx.record_info(metric="offset", value=-42, kind="gauge")

    assert _points(metrics_reader, "offset") == [-42]
    assert "unrepresentable value" not in caplog.text


@mark.asyncio
async def test_mismatched_log_format_keeps_its_arguments(
    logs: InMemoryLogRecordExporter,
) -> None:
    async with ctx.scope("logged", observability=OpenTelemetry.observability()):
        ctx.log_info("only %s placeholder", "one", "extra")

    assert _bodies(logs) == ["only %s placeholder ('one', 'extra')"]


@mark.asyncio
async def test_logged_exception_reaches_logs_and_traces(
    spans: InMemorySpanExporter,
    logs: InMemoryLogRecordExporter,
) -> None:
    async with ctx.scope("logged", observability=OpenTelemetry.observability()):
        ctx.log_error("failed", exception=ValueError("boom"))

    record = logs.get_finished_logs()[0].log_record
    assert record.body == "failed"
    assert record.attributes is not None
    # the scope name stays alongside the exception details
    assert record.attributes["scope.name"] == "logged"
    assert record.attributes["exception.type"] == "ValueError"
    assert record.attributes["exception.message"] == "boom"
    # while the trace carries the very same failure as a span event
    span = _named(spans, "logged")
    assert [event.name for event in span.events] == ["exception"]
    assert span.events[0].attributes is not None
    assert span.events[0].attributes["exception.type"] == "ValueError"


@mark.asyncio
async def test_scope_spawned_within_a_task_keeps_its_parent_under_a_remote_trace(
    spans: InMemorySpanExporter,
) -> None:
    # a scope entered within a spawned task belongs under the span that task
    # inherited - not under the remote parent the trace was continued from
    released = asyncio.Event()

    async def leaf() -> None:
        await released.wait()
        async with ctx.scope("leaf"):
            pass

    observability = OpenTelemetry.observability(
        traceparent="00-1234567890abcdef1234567890abcdef-abcdef1234567890-01",
    )
    async with ctx.scope("root", observability=observability):
        async with ctx.scope("mid"):
            ctx.spawn(leaf)
            released.set()

    mid = _named(spans, "mid")
    leaf_span = _named(spans, "leaf")
    assert _parent(leaf_span).span_id == _context(mid).span_id
    assert _parent(leaf_span).is_remote is False


@mark.asyncio
async def test_malformed_traceparent_can_not_forge_log_records(
    spans: InMemorySpanExporter,
    caplog: LogCaptureFixture,
) -> None:
    # the value arrives from an untrusted carrier, so a line feed within it must
    # not end up written as a separate, seemingly independent record
    async with ctx.scope(
        "downstream",
        observability=OpenTelemetry.observability(traceparent="00-\nERROR forged-\n-01"),
    ):
        pass

    warnings = [record for record in caplog.records if record.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "\n" not in warnings[0].getMessage()
    assert "\\n" in warnings[0].getMessage()


def test_configure_after_shutdown_is_rejected(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(OpenTelemetry, "_tracer_provider", TracerProvider(shutdown_on_exit=False))

    OpenTelemetry.shutdown()

    # the process global provider slots are claimed once and never released, so
    # adopting the providers just shut down would discard every signal in silence
    with raises(OpenTelemetryException):
        OpenTelemetry.configure(service="restarting", version="1", environment="test")


def test_configure_twice_is_rejected(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setattr(OpenTelemetry, "_meter_provider", MeterProvider(shutdown_on_exit=False))

    # the second call would silently keep the first configuration, discarding the
    # resource and the endpoint it was given
    with raises(OpenTelemetryException):
        OpenTelemetry.configure(service="again", version="1", environment="test")


@mark.asyncio
async def test_binary_attribute_is_not_exported_element_wise(
    spans: InMemorySpanExporter,
) -> None:
    # bytes, bytearray and memoryview are all sequences, yet exporting their
    # bytes one by one would say nothing about the value
    async with ctx.scope("root", observability=OpenTelemetry.observability()):
        ctx.record_info(
            attributes={
                "bytes": b"ab",
                "array": bytearray(b"ab"),
                "view": memoryview(b"ab"),
            },
        )

    attributes = _named(spans, "root").attributes or {}
    assert all(isinstance(attributes[key], str) for key in ("bytes", "array", "view"))


@mark.asyncio
async def test_entered_scope_yields_the_span_trace_identifier(
    spans: InMemorySpanExporter,
) -> None:
    root_trace: str
    nested_trace: str
    async with ctx.scope("root", observability=OpenTelemetry.observability()) as root_trace:
        async with ctx.scope("nested") as nested_trace:
            assert ctx.trace_id() == root_trace

    # the yielded identifier is the trace of the span, in the unpadded hex form a
    # trace backend queries by - so it pastes straight into one
    assert root_trace == format(_context(_named(spans, "root")).trace_id, "032x")
    assert nested_trace == root_trace


@mark.asyncio
async def test_trace_context_identifies_the_current_scope(spans: InMemorySpanExporter) -> None:
    outer_context: Mapping[str, str]
    inner_context: Mapping[str, str]
    async with ctx.scope("outer", observability=OpenTelemetry.observability()):
        outer_context = ctx.trace_context()
        async with ctx.scope("inner"):
            inner_context = ctx.trace_context()

    outer = _named(spans, "outer")
    inner = _named(spans, "inner")
    # the encoded position is the scope asking for it, not merely its trace -
    # the trace flags are left to the SDK, which owns the sampling decision
    assert outer_context["traceparent"].split("-")[1:3] == [
        format(_context(outer).trace_id, "032x"),
        format(_context(outer).span_id, "016x"),
    ]
    assert inner_context["traceparent"].split("-")[1:3] == [
        format(_context(inner).trace_id, "032x"),
        format(_context(inner).span_id, "016x"),
    ]


@mark.asyncio
async def test_trace_context_carries_vendor_trace_state(spans: InMemorySpanExporter) -> None:
    async with ctx.scope(
        "root",
        observability=OpenTelemetry.observability(
            traceparent="00-1234567890abcdef1234567890abcdef-abcdef1234567890-01",
            tracestate="vendor=value",
        ),
    ):
        trace_context = ctx.trace_context()

    # vendor trace state has to survive the hop, it carries sampling decisions
    assert trace_context["tracestate"] == "vendor=value"


@mark.asyncio
async def test_trace_context_continues_across_a_hop(spans: InMemorySpanExporter) -> None:
    async with ctx.scope("upstream", observability=OpenTelemetry.observability()):
        carrier = ctx.trace_context()

    async with ctx.scope(
        "downstream",
        observability=OpenTelemetry.observability(
            traceparent=carrier.get("traceparent"),
            tracestate=carrier.get("tracestate"),
        ),
    ):
        pass

    upstream = _named(spans, "upstream")
    downstream = _named(spans, "downstream")
    # what one service propagates is what the next one continues
    assert _context(downstream).trace_id == _context(upstream).trace_id
    assert _parent(downstream).span_id == _context(upstream).span_id
