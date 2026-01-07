import os
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Self, cast, final
from uuid import UUID

from grpc import ChannelCredentials
from opentelemetry import metrics, trace
from opentelemetry._logs import set_logger_provider
from opentelemetry._logs._internal import Logger
from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.context import Context, attach, detach, get_current
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics._internal import Meter
from opentelemetry.metrics._internal.instrument import Counter, Gauge, Histogram
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs._internal.export import (
    BatchLogRecordProcessor,
    ConsoleLogRecordExporter,
    LogRecordExporter,
)
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics._internal.export import MetricExporter
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.trace import Link, Span, SpanContext, StatusCode, TraceFlags, Tracer

from haiway.context import (
    ContextIdentifier,
    Observability,
    ObservabilityAttribute,
    ObservabilityLevel,
    ObservabilityMetricKind,
    ctx,
)
from haiway.types import MISSING

__all__ = ("OpenTelemetry",)


class ScopeStore:
    """
    Internal class for storing and managing OpenTelemetry scope data.

    This class tracks scope state including its span, meter, logger, and context.
    It manages the lifecycle of OpenTelemetry resources for a specific scope,
    including recording logs, metrics, events, and maintaining the context hierarchy.
    """

    __slots__ = (
        "_completed",
        "_counters",
        "_exited",
        "_gauges",
        "_histograms",
        "_token",
        "context",
        "identifier",
        "logger",
        "meter",
        "nested",
        "span",
    )

    def __init__(
        self,
        identifier: ContextIdentifier,
        /,
        context: Context,
        span: Span,
        meter: Meter,
        logger: Logger,
    ) -> None:
        """
        Initialize a new scope store with OpenTelemetry resources.

        Parameters
        ----------
        identifier : ContextIdentifier
            The identifier for this scope
        context : Context
            The OpenTelemetry context for this scope
        span : Span
            The OpenTelemetry span for this scope
        meter : Meter
            The OpenTelemetry meter for recording metrics
        logger : Logger
            The OpenTelemetry logger for recording logs
        """
        self.identifier: ContextIdentifier = identifier
        self.nested: list[ScopeStore] = []
        self._counters: dict[str, Counter] = {}
        self._histograms: dict[str, Histogram] = {}
        self._gauges: dict[str, Gauge] = {}
        self._exited: bool = False
        self._completed: bool = False
        self.span: Span = span
        self.meter: Meter = meter
        self.logger: Logger = logger
        self.context: Context = trace.set_span_in_context(
            span,
            context,
        )
        self._token: Any = attach(self.context)

    @property
    def exited(self) -> bool:
        """
        Check if this scope has been marked as exited.

        Returns
        -------
        bool
            True if the scope has been exited, False otherwise
        """
        return self._exited

    def exit(self) -> None:
        """
        Mark this scope as exited.
        """
        assert not self._exited  # nosec: B101
        self._exited = True

    @property
    def completed(self) -> bool:
        """
        Check if this scope and all its nested scopes are completed.

        A scope is considered completed when it has been marked as completed
        and all of its nested scopes are also completed.

        Returns
        -------
        bool
            True if the scope and all nested scopes are completed
        """
        return self._completed and all(nested.completed for nested in self.nested)

    def try_complete(self) -> bool:
        """
        Try to complete this scope if all conditions are met.

        A scope can be completed if:
        - It has been exited
        - It has not already been completed
        - All nested scopes are completed

        When completed, the span is ended and the context token is detached.

        Returns
        -------
        bool
            True if the scope was successfully completed, False otherwise
        """
        if not self._exited:
            return False  # not elegible for completion yet

        if self._completed:
            return False  # already completed

        if not all(nested.completed for nested in self.nested):
            return False  # nested not completed

        self._completed = True
        try:
            self.span.end()

        finally:
            detach(self._token)

        return True  # successfully completed

    def record_log(
        self,
        message: str,
        /,
        level: ObservabilityLevel,
    ) -> None:
        """
        Record a log message with the specified level.

        Emits a log record with the current span context and scope identifiers
        using the OpenTelemetry logger.

        Parameters
        ----------
        message : str
            The log message to record
        level : ObservabilityLevel
            The severity level of the log
        """

        self.logger.emit(
            context=self.context,
            body=message,
            severity_text=level.name,
            severity_number=SEVERITY_MAPPING[level],
        )

    def record_exception(
        self,
        exception: BaseException,
        /,
    ) -> None:
        """
        Record an exception in the current span.

        Parameters
        ----------
        exception : BaseException
            The exception to record
        """
        self.span.record_exception(exception)

    def record_event(
        self,
        event: str,
        /,
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        """
        Record an event in the current span.

        Parameters
        ----------
        event : str
            The name of the event to record
        attributes : Mapping[str, ObservabilityAttribute]
            Attributes to attach to the event
        """

        self.span.add_event(
            event,
            attributes=_sanitized_attributes(attributes),
        )

    def record_metric(
        self,
        name: str,
        /,
        *,
        value: float | int,
        unit: str | None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        """
        Record a metric with the given name, value, and attributes.

        Creates a counter if one does not already exist for the metric name,
        and adds the value to it with the provided attributes.

        Parameters
        ----------
        name : str
            The name of the metric to record
        value : float | int
            The value to add to the metric
        unit : str | None
            The unit of the metric (if any)
        kind: ObservabilityMetricKind
            The metric kind defining its value handling.
        attributes : Mapping[str, ObservabilityAttribute]
            Attributes to attach to the metric
        """
        match kind:
            case "counter":
                if name not in self._counters:
                    self._counters[name] = self.meter.create_counter(
                        name=name,
                        unit=unit or "",
                    )

                self._counters[name].add(
                    value,
                    attributes=_sanitized_attributes(attributes),
                )

            case "histogram":
                if name not in self._histograms:
                    self._histograms[name] = self.meter.create_histogram(
                        name=name,
                        unit=unit or "",
                    )

                self._histograms[name].record(
                    value,
                    attributes=_sanitized_attributes(attributes),
                )

            case "gauge":
                if name not in self._gauges:
                    self._gauges[name] = self.meter.create_gauge(
                        name=name,
                        unit=unit or "",
                    )

                self._gauges[name].set(
                    value,
                    attributes=_sanitized_attributes(attributes),
                )

    def record_attributes(
        self,
        attributes: Mapping[str, ObservabilityAttribute],
        /,
    ) -> None:
        """
        Record attributes in the current span.

        Sets each attribute on the span, skipping None and MISSING values.

        Parameters
        ----------
        attributes : Mapping[str, ObservabilityAttribute]
            Attributes to set on the span
        """

        for name, value in _sanitized_attributes(attributes).items():
            self.span.set_attribute(
                name,
                value=value,
            )


def _sanitized_attributes(
    attributes: Mapping[str, Any],
    /,
) -> Mapping[str, Any]:
    sanitized: dict[str, Any] = {}
    for key, value in attributes.items():
        if value is None or value is MISSING:
            continue  # skip missing/empty

        if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
            elements: list[Any] = []
            for item in cast(Sequence[Any], value):
                if item is None or item is MISSING:
                    continue  # skip missing/empty

                assert isinstance(item, str | float | int | bool)  # nosec: B101
                elements.append(item)

            if not elements:
                continue  # skip missing/empty

            sanitized[key] = tuple(elements)

        elif isinstance(value, Mapping):
            for name, item in cast(Mapping[str, Any], value).items():
                if item is None or item is MISSING:
                    continue  # skip missing/empty

                assert isinstance(item, str | float | int | bool)  # nosec: B101
                sanitized[f"{key}.{name}"] = item

        else:
            assert isinstance(value, str | float | int | bool)  # nosec: B101
            sanitized[key] = value

    return sanitized


@final
class OpenTelemetry:
    """
    Integration with OpenTelemetry for distributed tracing, metrics, and logging.

    This class provides a bridge between Haiway's observability abstractions and
    the OpenTelemetry SDK, enabling distributed tracing, metrics collection, and
    structured logging with minimal configuration.

    The class must be configured once at application startup using the configure()
    class method before it can be used.
    """

    service: ClassVar[str] = ""
    version: ClassVar[str] = ""
    environment: ClassVar[str] = ""
    _logger: ClassVar[Logger | None] = None

    @classmethod
    def configure(
        cls,
        *,
        service: str,
        version: str,
        instance: str = str(os.getpid()),
        environment: str,
        otlp_endpoint: str | None = None,
        insecure: bool = True,
        credentials: ChannelCredentials | None = None,
        export_interval_millis: int = 5000,
        attributes: Mapping[str, Any] | None = None,
    ) -> type[Self]:
        """
        Configure the OpenTelemetry integration.

        This method must be called once at application startup to configure the
        OpenTelemetry SDK with the appropriate service information, exporters,
        and resource attributes.

        Parameters
        ----------
        service : str
            The name of the service
        version : str
            The version of the service
        instance : str
            The deployment instance name or identifier (default is PID).
        environment : str
            The deployment environment (e.g., "production", "staging")
        otlp_endpoint : str | None, optional
            The OTLP endpoint URL to export telemetry data to. If None, console
            exporters will be used instead.
        insecure : bool, default=True
            Whether to use insecure connections to the OTLP endpoint
        credentials : ChannelCredentials | None, optional
            Shared gRPC channel credentials used by all OTLP exporters. When
            provided, secure channel configuration will be enforced.
        export_interval_millis : int, default=5000
            How often to export metrics, in milliseconds
        attributes : Mapping[str, Any] | None, optional
            Additional resource attributes to include with all telemetry

        Returns
        -------
        type[Self]
            The OpenTelemetry class, for method chaining
        """
        # Create shared resource for both metrics and traces
        resource: Resource = Resource.create(
            {
                "service.name": service,
                "service.version": version,
                "deployment.instance": instance,
                "deployment.environment": environment,
                **(attributes if attributes is not None else {}),
            },
        )
        cls.service = service
        cls.version = version
        cls.environment = environment

        logs_exporter: LogRecordExporter
        span_exporter: SpanExporter
        metric_exporter: MetricExporter

        if otlp_endpoint:
            exporter_insecure: bool = insecure if credentials is None else False
            logs_exporter = OTLPLogExporter(
                endpoint=otlp_endpoint,
                insecure=exporter_insecure,
                credentials=credentials,
            )
            span_exporter = OTLPSpanExporter(
                endpoint=otlp_endpoint,
                insecure=exporter_insecure,
                credentials=credentials,
            )
            metric_exporter = OTLPMetricExporter(
                endpoint=otlp_endpoint,
                insecure=exporter_insecure,
                credentials=credentials,
            )

        else:
            logs_exporter = ConsoleLogRecordExporter()
            span_exporter = ConsoleSpanExporter()
            metric_exporter = ConsoleMetricExporter()

        # Set up logger provider
        logger_provider: LoggerProvider = LoggerProvider(
            resource=resource,
            shutdown_on_exit=True,
        )
        log_processor: BatchLogRecordProcessor = BatchLogRecordProcessor(logs_exporter)
        logger_provider.add_log_record_processor(log_processor)
        cls._logger = logger_provider.get_logger(service, version=version)
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
    def traceparent(
        cls,
        version: int = 0,
    ) -> str | None:
        """
        Get the active span encoded as a W3C traceparent string.

        Parameters
        ----------
        version: int, default=0
            Traceparent version to encode in the header.

        Returns
        -------
        str | None
            Encoded traceparent value when a valid span context exists, otherwise None.
        """
        try:
            span_context: trace.SpanContext = trace.get_current_span().get_span_context()
            if not span_context.is_valid:
                return None

            return (
                f"{version:02x}-"
                f"{span_context.trace_id:032x}-"
                f"{span_context.span_id:016x}-"
                f"{int(span_context.trace_flags):02x}"
            )

        except Exception:
            return None

    @classmethod
    def observability(  # noqa: C901, PLR0915
        cls,
        level: ObservabilityLevel = ObservabilityLevel.INFO,
        *,
        traceparent: str | None = None,
    ) -> Observability:
        """
        Create an Observability implementation using OpenTelemetry.

        This method creates an Observability implementation that bridges Haiway's
        observability abstractions to OpenTelemetry, allowing transparent usage
        of OpenTelemetry for distributed tracing, metrics, and logging.

        Parameters
        ----------
        level : ObservabilityLevel, default=ObservabilityLevel.INFO
            The minimum observability level to record
        traceparent : str | None, optional
            Standardized traceparent allowing to link the otel scope with external systems.
            If provided, the root span will be linked to this external trace.

        Returns
        -------
        Observability
            An Observability implementation that uses OpenTelemetry

        Notes
        -----
        The OpenTelemetry class must be configured using configure() before
        calling this method.
        """
        tracer: Tracer = trace.get_tracer(cls.service)
        meter: Meter | None = None
        root_scope: ContextIdentifier | None = None
        scopes: dict[UUID, ScopeStore] = {}
        observed_level: ObservabilityLevel = level

        def trace_identifying(
            scope: ContextIdentifier,
            /,
        ) -> UUID:
            """
            Get the unique trace identifier for a scope.

            This function retrieves the OpenTelemetry trace ID for the specified scope
            and converts it to a UUID for compatibility with Haiway's observability system.

            Parameters
            ----------
            scope: ContextIdentifier
                The scope identifier to get the trace ID for

            Returns
            -------
            UUID
                A UUID representation of the OpenTelemetry trace ID
            """
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            return UUID(int=scopes[scope.scope_id].span.get_span_context().trace_id)

        def log_recording(
            scope: ContextIdentifier,
            /,
            level: ObservabilityLevel,
            message: str,
            *args: Any,
            exception: BaseException | None,
        ) -> None:
            """
            Record a log message using OpenTelemetry logging.

            Creates a log record with the appropriate severity level and attributes
            based on the current scope context.

            Parameters
            ----------
            scope: ContextIdentifier
                The scope identifier the log is associated with
            level: ObservabilityLevel
                The severity level for this log message
            message: str
                The log message text, may contain format placeholders
            *args: Any
                Format arguments for the message
            exception: BaseException | None
                Optional exception to associate with the log
            """
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            if level < observed_level:
                return

            formatted_message: str = message
            if args:
                try:
                    formatted_message = message % args
                except Exception:
                    formatted_message = message

            scopes[scope.scope_id].record_log(
                formatted_message,
                level=level,
            )
            if exception is not None:
                scopes[scope.scope_id].record_exception(exception)

        def event_recording(
            scope: ContextIdentifier,
            /,
            level: ObservabilityLevel,
            *,
            event: str,
            attributes: Mapping[str, ObservabilityAttribute],
        ) -> None:
            """
            Record an event using OpenTelemetry spans.

            Creates a span event with the specified name and attributes in the
            current active span for the scope.

            Parameters
            ----------
            scope: ContextIdentifier
                The scope identifier the event is associated with
            level: ObservabilityLevel
                The severity level for this event
            event: str
                The name of the event
            attributes: Mapping[str, ObservabilityAttribute]
                Key-value attributes associated with the event
            """
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            if level < observed_level:
                return

            scopes[scope.scope_id].record_event(
                event,
                attributes=attributes,
            )

        def metric_recording(
            scope: ContextIdentifier,
            /,
            level: ObservabilityLevel,
            *,
            metric: str,
            value: float | int,
            unit: str | None,
            kind: ObservabilityMetricKind,
            attributes: Mapping[str, ObservabilityAttribute],
        ) -> None:
            """
            Record a metric using OpenTelemetry metrics.

            Records a numeric measurement using the appropriate OpenTelemetry
            instrument type based on the metric name and value type.

            Parameters
            ----------
            scope: ContextIdentifier
                The scope identifier the metric is associated with
            level: ObservabilityLevel
                The severity level for this metric
            metric: str
                The name of the metric
            value: float | int
                The numeric value of the metric
            unit: str | None
                Optional unit for the metric (e.g., "ms", "bytes")
            kind: ObservabilityMetricKind
                The metric kind defining its value handling.
            attributes: Mapping[str, ObservabilityAttribute]
                Key-value attributes associated with the metric
            """
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            if level < observed_level:
                return

            scopes[scope.scope_id].record_metric(
                metric,
                value=value,
                unit=unit,
                kind=kind,
                attributes=attributes,
            )

        def attributes_recording(
            scope: ContextIdentifier,
            /,
            level: ObservabilityLevel,
            attributes: Mapping[str, ObservabilityAttribute],
        ) -> None:
            """
            Record standalone attributes using OpenTelemetry span attributes.

            Records key-value attributes by adding them to the current active span
            for the scope.

            Parameters
            ----------
            scope: ContextIdentifier
                The scope identifier the attributes are associated with
            level: ObservabilityLevel
                The severity level for these attributes
            attributes: Mapping[str, ObservabilityAttribute]
                Key-value attributes to record
            """
            if level < observed_level:
                return

            if not attributes:
                return

            scopes[scope.scope_id].record_attributes(attributes)

        def scope_entering(
            scope: ContextIdentifier,
            /,
        ) -> str:
            """
            Handle scope entry by creating a new OpenTelemetry span.

            This method is called when a new scope is entered. It creates a new
            OpenTelemetry span for the scope and sets up the appropriate parent-child
            relationships with existing spans.

            Parameters
            ----------
            scope: ContextIdentifier
                The identifier for the scope being entered

            Returns
            -------
            str
                Trace identifier for the entered scope

            Notes
            -----
            This method initializes the scopes dictionary entry for the new scope
            and creates meter instruments if this is the first scope entry.
            """
            assert scope.scope_id not in scopes  # nosec: B101
            assert cls._logger is not None, (  # nosec: B101
                "OpenTelemetry.configure must be called before using observability."
            )

            nonlocal root_scope
            nonlocal meter

            scope_store: ScopeStore
            if root_scope is None:
                meter = metrics.get_meter(scope.name)
                context: Context = get_current()

                # Handle distributed tracing with external trace ID
                links: Sequence[Link] | None = None
                if traceparent is not None:
                    try:
                        assert len(traceparent.split("-")) == 4  # nosec: B101 # noqa: PLR2004
                        # Assume correct traceparent format
                        _, trace_id, span_id, trace_flags = traceparent.split("-")

                        # Create a link to the external trace
                        links = (
                            Link(
                                SpanContext(
                                    int(trace_id, 16),
                                    int(span_id, 16),
                                    True,  # is_remote
                                    TraceFlags(int(trace_flags, 16)),
                                )
                            ),
                        )

                    except Exception as exc:
                        ctx.log_error(
                            f"Failed to link using provided traceparent: {traceparent}",
                            exception=exc,
                        )

                scope_store = ScopeStore(
                    scope,
                    context=context,
                    span=tracer.start_span(
                        name=scope.name,
                        context=context,
                        links=links,
                    ),
                    meter=meter,
                    logger=cls._logger,
                )
                root_scope = scope

            else:
                assert meter is not None  # nosec: B101
                scope_store = ScopeStore(
                    scope,
                    context=scopes[scope.parent_id].context,
                    span=tracer.start_span(
                        name=scope.name,
                        context=scopes[scope.parent_id].context,
                    ),
                    meter=meter,
                    logger=cls._logger,
                )
                scopes[scope.parent_id].nested.append(scope_store)

            scopes[scope.scope_id] = scope_store

            return str(UUID(int=scope_store.span.get_span_context().trace_id))

        def scope_exiting(
            scope: ContextIdentifier,
            /,
            *,
            exception: BaseException | None,
        ) -> None:
            """
            Handle scope exit by completing the OpenTelemetry span.

            This method is called when a scope is exited. It marks the scope as exited,
            attempts to complete it, and ends the associated OpenTelemetry span.

            Parameters
            ----------
            scope: ContextIdentifier
                The identifier for the scope being exited
            exception: BaseException | None
                Optional exception that caused the scope to exit

            Returns
            -------
            None

            Notes
            -----
            This method ensures proper cleanup of spans, including recording any
            exception that occurred during the scope's execution.
            """
            nonlocal root_scope
            nonlocal scopes
            nonlocal meter
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
            if scope != root_scope:
                parent_id: UUID = scope.parent_id
                while scopes[parent_id].try_complete():
                    if scopes[parent_id].identifier == root_scope:
                        break

                    parent_id = scopes[parent_id].identifier.parent_id

            # check for root completion
            if scopes[root_scope.scope_id].completed:
                # finished root - cleanup state
                root_scope = None
                meter = None
                scopes = {}

        return Observability(
            trace_identifying=trace_identifying,
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
