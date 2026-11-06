"""Micro benchmarks of the OpenTelemetry bridge.

Run with ``make benchmark-otel`` or ``uv run python -O benchmarks/opentelemetry_benchmark.py``.

    --json PATH      write the results as json
    --compare PATH   print the ratio against results written earlier
    --filter TEXT    run the cases whose name contains TEXT only
    --budget SECONDS time spent measuring each case (default 0.5)

Every operation is measured under three setups: ``bare`` - a scope without observability
backend of its own (plain logging, nothing enabled), ``logger`` - the standard library
logger backend, and ``otel`` - the OpenTelemetry bridge on real SDK providers with batch
processors writing into exporters which drop everything. Provider slots are claimed once
per process, so the benchmark has to own its process. Results are nanoseconds per call.
"""

import argparse
import asyncio
import gc
import json
import logging
import sys
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry.sdk._logs import LoggerProvider, ReadableLogRecord
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    LogRecordExporter,
    LogRecordExportResult,
)
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    MetricExporter,
    MetricExportResult,
    MetricsData,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, SpanExporter, SpanExportResult

from haiway import ctx
from haiway.context import Observability, ObservabilityLevel
from haiway.opentelemetry import OpenTelemetry


class DropSpans(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        return SpanExportResult.SUCCESS


class DropLogs(LogRecordExporter):
    def export(self, batch: Sequence[ReadableLogRecord]) -> LogRecordExportResult:
        return LogRecordExportResult.SUCCESS

    def force_flush(self, timeout_millis: int = 10_000) -> bool:
        return True

    def shutdown(self) -> None:
        pass


class DropMetrics(MetricExporter):
    def export(
        self,
        metrics_data: MetricsData,
        timeout_millis: float = 10_000,
        **kwargs: Any,
    ) -> MetricExportResult:
        return MetricExportResult.SUCCESS

    def force_flush(self, timeout_millis: float = 10_000) -> bool:
        return True

    def shutdown(self, timeout_millis: float = 30_000, **kwargs: Any) -> None:
        pass


def configure_providers() -> None:
    tracer_provider = TracerProvider()
    tracer_provider.add_span_processor(
        BatchSpanProcessor(DropSpans(), max_queue_size=1 << 20, schedule_delay_millis=200)
    )
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(
        MeterProvider(
            metric_readers=[
                PeriodicExportingMetricReader(DropMetrics(), export_interval_millis=1000)
            ]
        )
    )
    logger_provider = LoggerProvider()
    logger_provider.add_log_record_processor(
        BatchLogRecordProcessor(DropLogs(), max_queue_size=1 << 20, schedule_delay_millis=200)
    )
    set_logger_provider(logger_provider)
    OpenTelemetry.autoconfigure(service="benchmark", version="0")


def backend(setup: str) -> Observability | logging.Logger:
    if setup == "otel":
        return OpenTelemetry.observability(ObservabilityLevel.INFO)

    logger = logging.getLogger("benchmark")
    logger.setLevel(logging.CRITICAL)
    logger.propagate = False
    return logger


async def scope_flat() -> None:
    async with ctx.scope("flat"):
        pass


async def scope_nested() -> None:
    async with ctx.scope("a"):
        async with ctx.scope("b"):
            async with ctx.scope("c"):
                pass


async def log_below() -> None:
    ctx.log_debug("below the threshold %s", 1)


async def log_above() -> None:
    ctx.log_info("above the threshold %s", 1)


async def metric() -> None:
    ctx.record_info(metric="bench.count", value=1, unit="1", kind="counter", attributes={"a": "b"})


async def attributes() -> None:
    ctx.record_info(attributes={"a": "b", "c": 1})


async def event() -> None:
    ctx.record_info(event="bench.event", attributes={"a": "b", "c": 1})


# (name, operation)
CASES: Sequence[tuple[str, Callable[[], Awaitable[None]]]] = (
    ("scope/flat", scope_flat),
    ("scope/nested3", scope_nested),
    ("log/below_threshold", log_below),
    ("log/above_threshold", log_above),
    ("metric/counter", metric),
    ("attributes/two", attributes),
    ("event/two_attributes", event),
)


async def measure(
    setup: str,
    operation: Callable[[], Awaitable[None]],
    budget: float,
) -> float:
    best: float = float("inf")
    async with (
        ctx.scope("benchmark", observability=backend(setup))
        if setup != "bare"
        else ctx.scope("benchmark")
    ):
        for _ in range(5):
            gc.collect()
            calls: int = 0
            started: int = perf_counter_ns()
            deadline: int = started + int(budget / 5 * 1e9)
            while perf_counter_ns() < deadline:
                for _ in range(100):
                    await operation()
                calls += 100
            best = min(best, (perf_counter_ns() - started) / calls)
    return best


async def run(arguments: argparse.Namespace) -> dict[str, float]:
    results: dict[str, float] = {}
    selected = [
        (name, operation)
        for name, operation in CASES
        if not arguments.filter or arguments.filter in name
    ]
    for index, (name, operation) in enumerate(selected):
        # the order alternates between cases, so that what the machine does while
        # warming up or cooling down is not charged to the same setup every time
        setups: tuple[str, ...] = ("bare", "logger", "otel")
        for setup in setups if index % 2 == 0 else tuple(reversed(setups)):
            results[f"{name}/{setup}"] = await measure(setup, operation, arguments.budget)
            print(f"{name + '/' + setup:<36}{results[f'{name}/{setup}']:>12.0f} ns")

    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json")
    parser.add_argument("--compare")
    parser.add_argument("--filter")
    parser.add_argument("--budget", type=float, default=0.5)
    arguments = parser.parse_args()
    configure_providers()
    results = asyncio.run(run(arguments))
    if arguments.json:
        Path(arguments.json).write_text(json.dumps(results, indent=2))

    if arguments.compare:
        previous: dict[str, float] = json.loads(Path(arguments.compare).read_text())
        for key, value in results.items():
            if key in previous:
                print(f"{key:<36}{value / previous[key]:>8.2f}x")

    OpenTelemetry.force_flush(2000)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
