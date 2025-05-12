import os
from collections.abc import Mapping
from typing import Any, Self, final

from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger, set_logger_provider
from opentelemetry._logs._internal import Logger
from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.context import Context
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics._internal import Meter
from opentelemetry.metrics._internal.instrument import Counter, Histogram
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs._internal import LogRecord
from opentelemetry.sdk._logs._internal.export import (
    BatchLogRecordProcessor,
    ConsoleLogExporter,
    LogExporter,
)
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics._internal.export import MetricExporter
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.trace import Span, StatusCode, Tracer

from haiway.context import Observability, ObservabilityLevel, ScopeIdentifier

###
from haiway.context.observability import ObservabilityAttribute
from haiway.state import State

__all__ = ("OpenTelemetry",)


class ScopeStore:
    __slots__ = (
        "_completed",
        "_counters",
        "_exited",
        "_histograms",
        "identifier",
        "logger",
        "logger_2",
        "meter",
        "nested",
        "span",
    )

    def __init__(
        self,
        identifier: ScopeIdentifier,
        /,
        span: Span,
        meter: Meter,
        logger: Logger,
    ) -> None:
        self.identifier: ScopeIdentifier = identifier
        self.nested: list[ScopeStore] = []
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._exited: bool = False
        self._completed: bool = False
        self.span: Span = span
        self.meter: Meter = meter
        self.logger: Logger = logger

    @property
    def exited(self) -> bool:
        return self._exited

    def exit(self) -> None:
        assert not self._exited  # nosec: B101
        self._exited = True

    @property
    def completed(self) -> bool:
        return self._completed and all(nested.completed for nested in self.nested)

    def try_complete(self) -> bool:
        if not self._exited:
            return False  # not elegible for completion yet

        if self._completed:
            return False  # already completed

        if not all(nested.completed for nested in self.nested):
            return False  # nested not completed

        self._completed = True
        self.span.end()
        return True  # successfully completed

    def record_log(
        self,
        message: str,
        /,
        level: ObservabilityLevel,
    ) -> None:
        self.logger.emit(
            LogRecord(
                span_id=self.span.get_span_context().span_id,
                trace_id=self.span.get_span_context().trace_id,
                trace_flags=self.span.get_span_context().trace_flags,
                body=message,
                severity_text=level.name,
                severity_number=SEVERITY_MAPPING[level],
                attributes={
                    "trace_id": self.identifier.trace_id,
                    "scope_id": self.identifier.scope_id,
                    "parent_id": self.identifier.parent_id,
                },
            )
        )

    def record_exception(
        self,
        exception: BaseException,
        /,
    ) -> None:
        self.span.record_exception(exception)

    def record_event(
        self,
        event: State,
        /,
    ) -> None:
        self.span.add_event(
            str(type(event).__name__),
            attributes=event.to_mapping(recursive=True),
        )

    def record_metric(
        self,
        name: str,
        /,
        *,
        value: float | int,
        unit: str | None,
    ) -> None:
        attributes: Mapping[str, Any] = {
            "trace_id": self.identifier.trace_id,
            "scope_id": self.identifier.scope_id,
            "parent_id": self.identifier.parent_id,
        }

        if name not in self._counters:
            self._counters[name] = self.meter.create_counter(
                name=name,
                unit=unit or "",
            )

        self._counters[name].add(
            value,
            attributes=attributes,
        )

    def record_attribute(
        self,
        name: str,
        /,
        *,
        value: ObservabilityAttribute,
    ) -> None:
        self.span.set_attribute(
            name,
            value=value,
        )


@final
class OpenTelemetry:
    @classmethod
    def configure(
        cls,
        *,
        service: str,
        version: str,
        environment: str,
        otlp_endpoint: str | None = None,
        insecure: bool = True,
        export_interval_millis: int = 10000,
        attributes: Mapping[str, Any] | None = None,
    ) -> type[Self]:
        # Create shared resource for both metrics and traces
        resource: Resource = Resource.create(
            {
                "service.name": service,
                "service.version": version,
                "deployment.environment": environment,
                "service.pid": os.getpid(),
                **(attributes if attributes is not None else {}),
            },
        )

        logs_exporter: LogExporter
        span_exporter: SpanExporter
        metric_exporter: MetricExporter

        if otlp_endpoint:
            logs_exporter = OTLPLogExporter(
                endpoint=otlp_endpoint,
                insecure=insecure,
            )
            span_exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint,
                insecure=insecure,
            )
            metric_exporter = OTLPMetricExporter(
                endpoint=otlp_endpoint,
                insecure=insecure,
            )

        else:
            logs_exporter = ConsoleLogExporter()
            span_exporter = ConsoleSpanExporter()
            metric_exporter = ConsoleMetricExporter()

        # Set up logger provider
        logger_provider: LoggerProvider = LoggerProvider(
            resource=resource,
            shutdown_on_exit=True,
        )
        log_processor: BatchLogRecordProcessor = BatchLogRecordProcessor(logs_exporter)
        logger_provider.add_log_record_processor(log_processor)
        set_logger_provider(logger_provider)

        # Set up metrics provider
        meter_provider: SdkMeterProvider = SdkMeterProvider(
            resource=resource,
            metric_readers=[
                PeriodicExportingMetricReader(
                    metric_exporter,
                    export_interval_millis=export_interval_millis,
                )
            ],
            shutdown_on_exit=True,
        )
        metrics.set_meter_provider(meter_provider)

        # Set up trace provider
        tracer_provider: TracerProvider = TracerProvider(
            resource=resource,
            shutdown_on_exit=True,
        )
        span_processor: BatchSpanProcessor = BatchSpanProcessor(span_exporter)
        tracer_provider.add_span_processor(span_processor)
        trace.set_tracer_provider(tracer_provider)

        return cls

    @classmethod
    def observability(  # noqa: C901, PLR0915
        cls,
        level: ObservabilityLevel = ObservabilityLevel.INFO,
    ) -> Observability:
        tracer: Tracer | None = None
        meter: Meter | None = None
        root_scope: ScopeIdentifier | None = None
        scopes: dict[str, ScopeStore] = {}
        observed_level: ObservabilityLevel = level

        def log_recording(
            scope: ScopeIdentifier,
            /,
            level: ObservabilityLevel,
            message: str,
            *args: Any,
            exception: BaseException | None,
            **extra: Any,
        ) -> None:
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            if level < observed_level:
                return

            scopes[scope.scope_id].record_log(
                message % args,
                level=level,
            )
            if exception is not None:
                scopes[scope.scope_id].record_exception(exception)

        def event_recording(
            scope: ScopeIdentifier,
            /,
            *,
            level: ObservabilityLevel,
            event: State,
            **extra: Any,
        ) -> None:
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            if level < observed_level:
                return

            scopes[scope.scope_id].record_event(event)

        def metric_recording(
            scope: ScopeIdentifier,
            /,
            *,
            metric: str,
            value: float | int,
            unit: str | None,
            **extra: Any,
        ) -> None:
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            if level < observed_level:
                return

            scopes[scope.scope_id].record_metric(
                metric,
                value=value,
                unit=unit,
            )

        def attributes_recording(
            scope: ScopeIdentifier,
            /,
            **attributes: ObservabilityAttribute,
        ) -> None:
            if not attributes:
                return

            for attribute, value in attributes.items():
                scopes[scope.scope_id].record_attribute(
                    attribute,
                    value=value,
                )

        def scope_entering[Metric: State](
            scope: ScopeIdentifier,
            /,
        ) -> None:
            nonlocal tracer
            assert scope.scope_id not in scopes  # nosec: B101

            nonlocal root_scope
            nonlocal meter

            scope_store: ScopeStore
            if root_scope is None:
                tracer = trace.get_tracer(scope.trace_id)
                meter = metrics.get_meter(scope.trace_id)
                scope_store = ScopeStore(
                    scope,
                    span=tracer.start_span(
                        name=scope.label,
                        context=Context(
                            trace_id=scope.trace_id,
                            scope_id=scope.scope_id,
                            parent_id=scope.parent_id,
                        ),
                        attributes={
                            "trace_id": scope.trace_id,
                            "scope_id": scope.scope_id,
                            "parent_id": scope.parent_id,
                        },
                    ),
                    meter=meter,
                    logger=get_logger(scope.label),
                )
                root_scope = scope

            else:
                assert tracer is not None  # nosec: B101
                assert meter is not None  # nosec: B101

                scope_store = ScopeStore(
                    scope,
                    span=tracer.start_span(
                        name=scope.label,
                        context=trace.set_span_in_context(
                            scopes[scope.parent_id].span,
                            Context(
                                trace_id=scope.trace_id,
                                scope_id=scope.scope_id,
                                parent_id=scope.parent_id,
                            ),
                        ),
                        attributes={
                            "trace_id": scope.trace_id,
                            "scope_id": scope.scope_id,
                            "parent_id": scope.parent_id,
                        },
                    ),
                    meter=meter,
                    logger=get_logger(scope.label),
                )
                scopes[scope.parent_id].nested.append(scope_store)

            scopes[scope.scope_id] = scope_store

        def scope_exiting[Metric: State](
            scope: ScopeIdentifier,
            /,
            *,
            exception: BaseException | None,
        ) -> None:
            nonlocal root_scope
            nonlocal scopes
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            scopes[scope.scope_id].exit()
            if exception is not None:
                scopes[scope.scope_id].span.set_status(status=StatusCode.ERROR)

            else:
                scopes[scope.scope_id].span.set_status(status=StatusCode.OK)

            if not scopes[scope.scope_id].try_complete():
                return  # not completed yet or already completed

            # try complete parent scopes
            parent_id: str = scope.parent_id
            while scopes[parent_id].try_complete():
                parent_id = scopes[parent_id].identifier.parent_id

            # check for root completion
            if scopes[root_scope.scope_id].completed:
                # finished root - cleanup state
                root_scope = None
                scopes = {}

        return Observability(
            log_recording=log_recording,
            event_recording=event_recording,
            metric_recording=metric_recording,
            attributes_recording=attributes_recording,
            scope_entering=scope_entering,
            scope_exiting=scope_exiting,
        )


SEVERITY_MAPPING = {
    ObservabilityLevel.DEBUG: SeverityNumber.DEBUG,
    ObservabilityLevel.INFO: SeverityNumber.INFO,
    ObservabilityLevel.WARNING: SeverityNumber.WARN,
    ObservabilityLevel.ERROR: SeverityNumber.ERROR,
}
