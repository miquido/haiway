import os
from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, Self, cast, final
from uuid import UUID

from opentelemetry import metrics, trace
from opentelemetry._logs import get_logger, set_logger_provider
from opentelemetry._logs._internal import Logger
from opentelemetry._logs.severity import SeverityNumber
from opentelemetry.context import Context, attach, detach, get_current
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics._internal import Meter
from opentelemetry.metrics._internal.instrument import Counter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs._internal import LogRecord
from opentelemetry.sdk._logs._internal.export import (
    BatchLogRecordProcessor,
    ConsoleLogExporter,
    LogExporter,
)
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics._internal.export import MetricExporter
from opentelemetry.sdk.metrics.export import ConsoleMetricExporter, PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.sdk.trace.id_generator import RandomIdGenerator
from opentelemetry.trace import Link, Span, SpanContext, StatusCode, TraceFlags, Tracer

from haiway.context import (
    Observability,
    ObservabilityAttribute,
    ObservabilityLevel,
    ScopeIdentifier,
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
        identifier: ScopeIdentifier,
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
        identifier : ScopeIdentifier
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
        self.identifier: ScopeIdentifier = identifier
        self.nested: list[ScopeStore] = []
        self._counters: dict[str, Counter] = {}
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

        Raises
        ------
        AssertionError
            If the scope has already been exited
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
        self.span.end()

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

        Creates a LogRecord with the current span context and scope identifiers,
        and emits it through the OpenTelemetry logger.

        Parameters
        ----------
        message : str
            The log message to record
        level : ObservabilityLevel
            The severity level of the log
        """
        span_context: SpanContext = self.span.get_span_context()
        self.logger.emit(
            LogRecord(
                span_id=span_context.span_id,
                trace_id=span_context.trace_id,
                trace_flags=span_context.trace_flags,
                body=message,
                severity_text=level.name,
                severity_number=SEVERITY_MAPPING[level],
            )
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
            attributes={
                key: cast(Any, value)
                for key, value in attributes.items()
                if value is not None and value is not MISSING
            },
        )

    def record_metric(
        self,
        name: str,
        /,
        *,
        value: float | int,
        unit: str | None,
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
        attributes : Mapping[str, ObservabilityAttribute]
            Attributes to attach to the metric
        """
        if name not in self._counters:
            self._counters[name] = self.meter.create_counter(
                name=name,
                unit=unit or "",
            )

        self._counters[name].add(
            value,
            attributes={
                key: cast(Any, value)
                for key, value in attributes.items()
                if value is not None and value is not MISSING
            },
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
        for name, value in attributes.items():
            if value is None or value is MISSING:
                continue

            self.span.set_attribute(
                name,
                value=cast(Any, value),
            )


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

    service: ClassVar[str]
    environment: ClassVar[str]

    @classmethod
    def configure(
        cls,
        *,
        service: str,
        version: str,
        environment: str,
        otlp_endpoint: str | None = None,
        insecure: bool = True,
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
        environment : str
            The deployment environment (e.g., "production", "staging")
        otlp_endpoint : str | None, optional
            The OTLP endpoint URL to export telemetry data to. If None, console
            exporters will be used instead.
        insecure : bool, default=True
            Whether to use insecure connections to the OTLP endpoint
        export_interval_millis : int, default=5000
            How often to export metrics, in milliseconds
        attributes : Mapping[str, Any] | None, optional
            Additional resource attributes to include with all telemetry

        Returns
        -------
        type[Self]
            The OpenTelemetry class, for method chaining
        """
        cls.service = service
        cls.environment = environment
        # Create shared resource for both metrics and traces
        resource: Resource = Resource.create(
            {
                "service.name": service,
                "service.version": version,
                "service.pid": os.getpid(),
                "deployment.environment": environment,
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
        *,
        external_trace_id: str | None = None,
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
        external_trace_id : str | None, optional
            External trace ID for distributed tracing context propagation.
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
        root_scope: ScopeIdentifier | None = None
        scopes: dict[UUID, ScopeStore] = {}
        observed_level: ObservabilityLevel = level

        def trace_identifying(
            scope: ScopeIdentifier,
            /,
        ) -> UUID:
            """
            Get the unique trace identifier for a scope.

            This function retrieves the OpenTelemetry trace ID for the specified scope
            and converts it to a UUID for compatibility with Haiway's observability system.

            Parameters
            ----------
            scope: ScopeIdentifier
                The scope identifier to get the trace ID for

            Returns
            -------
            UUID
                A UUID representation of the OpenTelemetry trace ID

            Raises
            ------
            AssertionError
                If called outside an initialized scope context
            """
            assert root_scope is not None  # nosec: B101
            assert scope.scope_id in scopes  # nosec: B101

            return UUID(int=scopes[scope.scope_id].span.get_span_context().trace_id)

        def log_recording(
            scope: ScopeIdentifier,
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
            scope: ScopeIdentifier
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

            scopes[scope.scope_id].record_log(
                message % args,
                level=level,
            )
            if exception is not None:
                scopes[scope.scope_id].record_exception(exception)

        def event_recording(
            scope: ScopeIdentifier,
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
            scope: ScopeIdentifier
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
            scope: ScopeIdentifier,
            /,
            level: ObservabilityLevel,
            *,
            metric: str,
            value: float | int,
            unit: str | None,
            attributes: Mapping[str, ObservabilityAttribute],
        ) -> None:
            """
            Record a metric using OpenTelemetry metrics.

            Records a numeric measurement using the appropriate OpenTelemetry
            instrument type based on the metric name and value type.

            Parameters
            ----------
            scope: ScopeIdentifier
                The scope identifier the metric is associated with
            level: ObservabilityLevel
                The severity level for this metric
            metric: str
                The name of the metric
            value: float | int
                The numeric value of the metric
            unit: str | None
                Optional unit for the metric (e.g., "ms", "bytes")
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
                attributes=attributes,
            )

        def attributes_recording(
            scope: ScopeIdentifier,
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
            scope: ScopeIdentifier
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
            scope: ScopeIdentifier,
            /,
        ) -> None:
            """
            Handle scope entry by creating a new OpenTelemetry span.

            This method is called when a new scope is entered. It creates a new
            OpenTelemetry span for the scope and sets up the appropriate parent-child
            relationships with existing spans.

            Parameters
            ----------
            scope: ScopeIdentifier
                The identifier for the scope being entered

            Returns
            -------
            None

            Notes
            -----
            This method initializes the scopes dictionary entry for the new scope
            and creates meter instruments if this is the first scope entry.
            """
            assert scope.scope_id not in scopes  # nosec: B101

            nonlocal root_scope
            nonlocal meter

            scope_store: ScopeStore
            if root_scope is None:
                meter = metrics.get_meter(scope.name)
                context: Context = get_current()

                # Handle distributed tracing with external trace ID
                links: Sequence[Link] | None = None
                if external_trace_id is not None:
                    # Convert external trace ID to OpenTelemetry trace ID format
                    try:
                        # Generate a proper span ID for the external trace link
                        id_generator = RandomIdGenerator()

                        # Create a link to the external trace
                        links = (
                            Link(
                                SpanContext(
                                    (  # Assume external_trace_id is a hex string, convert to int
                                        int(external_trace_id, 16)
                                        if isinstance(external_trace_id, str)
                                        else int(external_trace_id)
                                    ),
                                    id_generator.generate_span_id(),  # Generate proper span ID
                                    True,  # is_remote=True
                                    TraceFlags.SAMPLED,  # pyright: ignore[reportArgumentType]
                                )
                            ),
                        )

                    except (ValueError, TypeError):
                        ctx.log_warning(
                            "Failed to convert external trace ID to OpenTelemetry format"
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
                    logger=get_logger(scope.name),
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
                    logger=get_logger(scope.name),
                )
                scopes[scope.parent_id].nested.append(scope_store)

            scopes[scope.scope_id] = scope_store

        def scope_exiting(
            scope: ScopeIdentifier,
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
            scope: ScopeIdentifier
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
"""Mapping from Haiway ObservabilityLevel to OpenTelemetry SeverityNumber."""
