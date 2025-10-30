from __future__ import annotations

from typing import Any

import pytest
from opentelemetry.sdk._logs._internal.export import LogExporter, LogExportResult
from opentelemetry.sdk.metrics._internal.export import MetricExporter, MetricExportResult
from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

from haiway.context.identifier import ScopeIdentifier
from haiway.context.observability import ObservabilityLevel
from haiway.opentelemetry.observability import OpenTelemetry, ScopeStore, _sanitized_attributes
from haiway.types import MISSING


@pytest.fixture(scope="module", autouse=True)
def configure_opentelemetry() -> None:
    monkeypatch = pytest.MonkeyPatch()

    class _NoOpLogExporter(LogExporter):
        def export(self, batch: Any) -> LogExportResult:
            return LogExportResult.SUCCESS

        def shutdown(self) -> None:
            return None

    class _NoOpMetricExporter(MetricExporter):
        def export(self, metrics_data: Any) -> MetricExportResult:
            return MetricExportResult.SUCCESS

        def force_flush(self, timeout_millis: float = 10_000) -> bool:
            return True

        def shutdown(self, timeout_millis: float = 30_000, **_: Any) -> None:
            return None

    class _NoOpSpanExporter(SpanExporter):
        def export(self, spans: Any) -> SpanExportResult:
            return SpanExportResult.SUCCESS

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return True

        def shutdown(self, timeout_millis: int = 30_000) -> None:
            return None

    monkeypatch.setattr(
        "haiway.opentelemetry.observability.ConsoleLogExporter",
        _NoOpLogExporter,
    )
    monkeypatch.setattr(
        "haiway.opentelemetry.observability.ConsoleMetricExporter",
        _NoOpMetricExporter,
    )
    monkeypatch.setattr(
        "haiway.opentelemetry.observability.ConsoleSpanExporter",
        _NoOpSpanExporter,
    )

    OpenTelemetry.configure(
        service="haiway-tests",
        version="0.0.0",
        environment="test",
    )
    yield
    monkeypatch.undo()


def test_log_recording_preserves_percent_literals(monkeypatch: pytest.MonkeyPatch) -> None:
    records: list[str] = []

    def capture_log(self: ScopeStore, message: str, /, *, level: ObservabilityLevel) -> None:
        records.append(message)

    monkeypatch.setattr(
        ScopeStore,
        "record_log",
        capture_log,
        raising=False,
    )

    observability = OpenTelemetry.observability()
    scope = ScopeIdentifier.scope("log-test")
    observability.scope_entering(scope)
    try:
        observability.log_recording(
            scope,
            ObservabilityLevel.INFO,
            "ready 100%",
            exception=None,
        )
    finally:
        observability.scope_exiting(scope, exception=None)

    assert records == ["ready 100%"]


def test_metric_recording_gauge_reuses_instrument(monkeypatch: pytest.MonkeyPatch) -> None:
    gauge_calls: list[tuple[float | int, dict[str, Any] | None]] = []

    class FakeGauge:
        def set(
            self,
            amount: float | int,
            attributes: dict[str, Any] | None = None,
            context: Any | None = None,
        ) -> None:
            gauge_calls.append((amount, attributes))

    class FakeMeter:
        def __init__(self) -> None:
            self._gauge = FakeGauge()
            self.created = 0

        def create_counter(self, *_: Any, **__: Any) -> Any:  # pragma: no cover - unused in test
            raise AssertionError("counter creation is unexpected in this test")

        def create_histogram(self, *_: Any, **__: Any) -> Any:  # pragma: no cover - unused in test
            raise AssertionError("histogram creation is unexpected in this test")

        def create_gauge(self, *_: Any, **__: Any) -> FakeGauge:
            self.created += 1
            return self._gauge

    fake_meter = FakeMeter()
    monkeypatch.setattr(
        "haiway.opentelemetry.observability.metrics.get_meter",
        lambda _: fake_meter,
    )

    observability = OpenTelemetry.observability()
    scope = ScopeIdentifier.scope("gauge-test")
    observability.scope_entering(scope)
    try:
        observability.metric_recording(
            scope,
            ObservabilityLevel.INFO,
            metric="gauge_metric",
            value=5,
            unit="ms",
            kind="gauge",
            attributes={
                "kept": "value",
                "ignored_none": None,
            },
        )
        observability.metric_recording(
            scope,
            ObservabilityLevel.INFO,
            metric="gauge_metric",
            value=7,
            unit="ms",
            kind="gauge",
            attributes={
                "kept": "next",
                "ignored_missing": MISSING,
            },
        )
    finally:
        observability.scope_exiting(scope, exception=None)

    assert fake_meter.created == 1
    assert gauge_calls == [
        (5, {"kept": "value"}),
        (7, {"kept": "next"}),
    ]


def test_scope_store_sanitizes_sequence_attributes() -> None:
    sanitized = _sanitized_attributes(
        {
            "tags": ["a", "b"],
            "ints": (1, 2, 3),
            "booleans": [True, True, False],
            "skip_none": [None, "ok"],
            "skip_missing": [MISSING, "kept"],
            "skip_all": [None, MISSING],
            "scalar": "value",
        }
    )

    assert sanitized is not None
    assert sanitized == {
        "tags": ("a", "b"),
        "ints": (1, 2, 3),
        "booleans": (True, True, False),
        "skip_none": ("ok",),
        "skip_missing": ("kept",),
        "scalar": "value",
    }
    assert frozenset(sanitized.items())


def test_scope_store_sanitization_returns_empty_for_empty_mapping() -> None:
    sanitized = _sanitized_attributes(
        {
            "none": None,
            "missing": MISSING,
            "empty_sequence": [None, MISSING],
        }
    )

    assert sanitized == {}
