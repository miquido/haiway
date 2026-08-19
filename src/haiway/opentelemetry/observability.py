import os
from collections.abc import Mapping, Sequence
from contextvars import Token
from logging import Logger as StdLogger
from logging import getLogger
from sys import float_info
from time import time_ns
from typing import Any, ClassVar, Final, Protocol, Self, cast, final
from uuid import UUID

from grpc import ChannelCredentials
from opentelemetry import metrics, trace
from opentelemetry._logs import (
    Logger,
    SeverityNumber,
    get_logger_provider,
    set_logger_provider,
)
from opentelemetry.context import Context, attach, detach, get_current
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import (  # no public module exists
    OTLPLogExporter,
)
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.metrics import Meter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import (
    BatchLogRecordProcessor,
    ConsoleLogRecordExporter,
    LogRecordExporter,
)
from opentelemetry.sdk.environment_variables import (
    OTEL_EXPORTER_OTLP_ENDPOINT,
    OTEL_EXPORTER_OTLP_LOGS_ENDPOINT,
    OTEL_EXPORTER_OTLP_METRICS_ENDPOINT,
    OTEL_EXPORTER_OTLP_TRACES_ENDPOINT,
)
from opentelemetry.sdk.metrics import MeterProvider as SdkMeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter, SpanExporter
from opentelemetry.trace import Span, StatusCode, Tracer
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
from opentelemetry.util.types import AttributeValue

from haiway.context import (
    ContextIdentifier,
    Observability,
    ObservabilityAttribute,
    ObservabilityLevel,
    ObservabilityMetricKind,
)
from haiway.types import MISSING
from haiway.utils.formatting import escape_controls

__all__ = (
    "OpenTelemetry",
    "OpenTelemetryException",
)


class OpenTelemetryException(Exception):
    """Raised when the OpenTelemetry bridge is misconfigured or misused.

    Signals problems owned by this integration - such as using observability
    before providers were configured - rather than failures inside the
    OpenTelemetry SDK itself.
    """


class ScopeStore:
    # the meter and the metric instruments belong to the adapter, not here - a
    # scope costs its span, its context and the bookkeeping of its nested scopes
    __slots__ = (
        "_completed",
        "_end_time",
        "_log_attributes",
        "_token",
        "context",
        "identifier",
        "logger",
        "pending",
        "span",
    )

    def __init__(
        self,
        identifier: ContextIdentifier,
        /,
        context: Context,
        span: Span,
        logger: Logger,
    ) -> None:
        self.identifier: ContextIdentifier = identifier
        self.span: Span = span
        self.logger: Logger = logger
        self.context: Context = trace.set_span_in_context(
            span,
            context,
        )
        # every log emitted within the scope carries its name, prepare it once
        self._log_attributes: Mapping[str, AttributeValue] = {"scope.name": identifier.name}
        # nested scopes entered but not completed yet - the span may only end
        # once all of them did, which can happen much later when a nested scope
        # outlives this one. completed scopes are never revisited, so counting
        # them is enough and keeps completion checks constant time
        self.pending: int = 0
        # exit timestamp, also marking the scope as exited - the span reports
        # the duration of the scope instead of the one of its longest descendant
        self._end_time: int | None = None
        self._completed: bool = False
        self._token: Token[Context] | None = None

    def attach_context(self) -> None:
        # attaching is deliberately kept apart from completion: completion is
        # deferred until nested scopes finish, which can happen in a different
        # task, while contextvar tokens must be reset in the task and the order
        # they were created in
        assert self._token is None  # nosec: B101
        self._token = attach(self.context)

    def detach_context(self) -> None:
        if self._token is None:
            return  # nothing attached or already detached

        token: Token[Context] = self._token
        self._token = None
        detach(token)

    def exit(self) -> None:
        assert self._end_time is None  # nosec: B101
        self._end_time = time_ns()

    def try_complete(self) -> bool:
        if self._end_time is None:
            return False  # not exited, not elegible for completion yet

        if self._completed:
            return False  # already completed

        if self.pending:
            return False  # nested not completed

        self._completed = True
        self.span.end(end_time=self._end_time)

        return True  # successfully completed

    def record_log(
        self,
        message: str,
        /,
        level: ObservabilityLevel,
        exception: BaseException | None,
    ) -> None:
        severity_number, severity_text = _SEVERITY_MAPPING[level]
        self.logger.emit(
            timestamp=time_ns(),
            context=self.context,
            body=message,
            severity_text=severity_text,
            severity_number=severity_number,
            attributes=self._log_attributes,
            # the log record carries the exception for the logs signal
            exception=exception,
        )
        if exception is not None:
            # while the span event makes the same failure visible on the trace
            self.span.record_exception(exception)

    def record_event(
        self,
        event: str,
        /,
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        self.span.add_event(
            event,
            attributes=_sanitized_attributes(attributes),
        )

    def record_attributes(
        self,
        attributes: Mapping[str, ObservabilityAttribute],
        /,
    ) -> None:
        self.span.set_attributes(_sanitized_attributes(attributes))


def _sanitized_value(
    value: Any,
    /,
) -> str | bool | int | float:
    # bool is a subclass of int, both are accepted as is. the type tuple is spelled
    # out instead of a union - the union would be rebuilt on each of these calls,
    # which dominates the cost of sanitizing
    if isinstance(value, bool | str | int | float):
        return value

    # anything else would be rejected or coerced by the SDK without notice,
    # so make the conversion explicit and keep the value visible
    return str(value)


def _sanitized_sequence(
    value: Sequence[Any],
    /,
) -> Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float] | None:
    elements: list[str | bool | int | float] = [
        _sanitized_value(item) for item in value if item is not None and item is not MISSING
    ]
    if not elements:
        return None  # skip missing/empty

    # OpenTelemetry rejects sequences mixing types, dropping the whole
    # attribute, so a heterogeneous sequence is unified into strings instead
    first_type: type = type(elements[0])
    if any(type(element) is not first_type for element in elements):
        return tuple(str(element) for element in elements)

    return cast(
        Sequence[str] | Sequence[bool] | Sequence[int] | Sequence[float],
        tuple(elements),
    )


def _sanitized_attributes(
    attributes: Mapping[str, Any],
    /,
) -> Mapping[str, AttributeValue]:
    sanitized: dict[str, AttributeValue] = {}
    for key, value in attributes.items():
        # scalars are by far the most common case, check them without abc lookups
        if isinstance(value, bool | str | int | float):
            sanitized[key] = value

        elif value is None or value is MISSING:
            continue  # skip missing/empty

        elif isinstance(value, Mapping):
            # mappings are flattened into dotted keys - not part of
            # ObservabilityAttribute, yet a documented convenience of this bridge
            for name, item in cast(Mapping[str, Any], value).items():
                if item is None or item is MISSING:
                    continue  # skip missing/empty

                sanitized[f"{key}.{name}"] = _sanitized_value(item)

        # binary values are sequences too, but exporting them element wise would
        # be nonsense - render them like any other unsupported value instead
        elif isinstance(value, (bytes, bytearray, memoryview)):
            sanitized[key] = str(cast(Any, value))

        elif isinstance(value, Sequence):
            elements: Sequence[Any] | None = _sanitized_sequence(cast(Sequence[Any], value))
            if elements is None:
                continue  # skip missing/empty

            sanitized[key] = elements

        else:
            sanitized[key] = str(value)

    return sanitized


_NO_TRACE_ID: Final[UUID] = UUID(int=0)

_logger: Final[StdLogger] = getLogger("OpenTelemetry")

# canonical short names from the OpenTelemetry logs data model
_SEVERITY_MAPPING: Final[Mapping[ObservabilityLevel, tuple[SeverityNumber, str]]] = {
    ObservabilityLevel.DEBUG: (SeverityNumber.DEBUG, "DEBUG"),
    ObservabilityLevel.INFO: (SeverityNumber.INFO, "INFO"),
    ObservabilityLevel.WARNING: (SeverityNumber.WARN, "WARN"),
    ObservabilityLevel.ERROR: (SeverityNumber.ERROR, "ERROR"),
}


class _TelemetryProvider(Protocol):
    def force_flush(
        self,
        timeout_millis: int = 30000,
    ) -> bool: ...

    def shutdown(self) -> None: ...


def _resolved_endpoint(
    otlp_endpoint: str | None,
    /,
    signal_variable: str,
) -> str | None:
    # the exporters resolve these variables themselves, but the console/OTLP
    # choice is made here, so an environment configured process would silently
    # fall back to console exporters unless they are honored at this level too
    return (
        otlp_endpoint
        or os.environ.get(signal_variable)
        or os.environ.get(OTEL_EXPORTER_OTLP_ENDPOINT)
        or None
    )


def _installed[Provider: _TelemetryProvider](
    provider: Provider,
    /,
    current: object,
) -> Provider | None:
    if current is provider:
        return provider

    # the global slot was already claimed by a provider which is neither an SDK
    # instance to adopt nor replaceable - release the one just built instead of
    # leaking its exporter threads and channels for the process lifetime
    provider.shutdown()

    return None  # nothing is retained for a slot this integration does not own


def _resolved_logger_provider(
    *,
    resource: Resource,
    otlp_endpoint: str | None,
    insecure: bool | None,
    credentials: ChannelCredentials | None,
) -> LoggerProvider | None:
    current: Any = get_logger_provider()
    if isinstance(current, LoggerProvider):
        return current

    endpoint: str | None = _resolved_endpoint(otlp_endpoint, OTEL_EXPORTER_OTLP_LOGS_ENDPOINT)
    exporter: LogRecordExporter = (
        OTLPLogExporter(
            endpoint=endpoint,
            insecure=insecure,
            credentials=credentials,
        )
        if endpoint
        else ConsoleLogRecordExporter()
    )
    provider: LoggerProvider = LoggerProvider(
        resource=resource,
        shutdown_on_exit=True,
    )
    provider.add_log_record_processor(BatchLogRecordProcessor(exporter))
    set_logger_provider(provider)

    return _installed(
        provider,
        get_logger_provider(),
    )


def _resolved_meter_provider(
    *,
    resource: Resource,
    otlp_endpoint: str | None,
    insecure: bool | None,
    credentials: ChannelCredentials | None,
    export_interval_millis: int,
) -> SdkMeterProvider | None:
    current: Any = metrics.get_meter_provider()
    if isinstance(current, SdkMeterProvider):
        return current

    endpoint: str | None = _resolved_endpoint(otlp_endpoint, OTEL_EXPORTER_OTLP_METRICS_ENDPOINT)
    exporter: MetricExporter = (
        OTLPMetricExporter(
            endpoint=endpoint,
            insecure=insecure,
            credentials=credentials,
        )
        if endpoint
        else ConsoleMetricExporter()
    )
    provider: SdkMeterProvider = SdkMeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                exporter,
                export_interval_millis=export_interval_millis,
            )
        ],
        shutdown_on_exit=True,
    )
    metrics.set_meter_provider(provider)

    return _installed(
        provider,
        metrics.get_meter_provider(),
    )


def _resolved_tracer_provider(
    *,
    resource: Resource,
    otlp_endpoint: str | None,
    insecure: bool | None,
    credentials: ChannelCredentials | None,
) -> TracerProvider | None:
    current: Any = trace.get_tracer_provider()
    if isinstance(current, TracerProvider):
        return current

    endpoint: str | None = _resolved_endpoint(otlp_endpoint, OTEL_EXPORTER_OTLP_TRACES_ENDPOINT)
    exporter: SpanExporter = (
        OTLPSpanExporter(
            endpoint=endpoint,
            insecure=insecure,
            credentials=credentials,
        )
        if endpoint
        else ConsoleSpanExporter()
    )
    provider: TracerProvider = TracerProvider(
        resource=resource,
        shutdown_on_exit=True,
    )
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    return _installed(
        provider,
        trace.get_tracer_provider(),
    )


class _MetricRecording(Protocol):
    def __call__(
        self,
        amount: int | float,
        attributes: Mapping[str, AttributeValue] | None = None,
        context: Context | None = None,
    ) -> None: ...


def _remote_parent(
    traceparent: str | None,
    tracestate: str | None,
    /,
    propagator: TraceContextTextMapPropagator,
) -> Span | None:
    """
    Decode a W3C trace context into the remote span to be continued.

    Delegates to the OpenTelemetry trace context propagator, so field lengths,
    reserved versions, zeroed identifiers, and vendor trace state are handled
    exactly as the specification requires.
    """
    if traceparent is None:
        return None

    carrier: dict[str, str] = {"traceparent": traceparent}
    if tracestate is not None:
        carrier["tracestate"] = tracestate

    # extraction defaults to an empty context, so a rejected value leaves no
    # span behind and can not be mistaken for the ambient one
    span: Span = trace.get_current_span(propagator.extract(carrier))
    if not span.get_span_context().is_valid:
        # the value comes straight from an untrusted carrier - escaping keeps it
        # from forging additional log records
        _logger.warning(
            "Ignoring malformed traceparent: %s",
            escape_controls(traceparent),
        )
        return None

    return span


def _injected_trace_context(
    propagator: TraceContextTextMapPropagator,
    /,
    context: Context | None = None,
) -> Mapping[str, str]:
    """Encode a trace position into a fresh W3C trace context carrier.

    Injects from the given context, or from the ambient one when none is given.
    """
    carrier: dict[str, str] = {}
    propagator.inject(carrier, context=context)
    return carrier


@final
class _ObservabilityAdapter:
    # one adapter may back several independent scope trees, including concurrent
    # ones, as long as they run on a single event loop. the tracer, meter, logger
    # and metric instruments are shared by every scope it tracks, so entering a
    # scope allocates only its span and context
    __slots__ = (
        "_instruments",
        "_level",
        "_logger",
        "_meter",
        "_propagator",
        "_remote_parent",
        "_scopes",
        "_tracer",
    )

    def __init__(
        self,
        *,
        level: ObservabilityLevel,
        logger: Logger,
        tracer: Tracer,
        meter: Meter,
        propagator: TraceContextTextMapPropagator,
        remote_parent: Span | None,
    ) -> None:
        self._level: ObservabilityLevel = level
        self._logger: Logger = logger
        self._tracer: Tracer = tracer
        self._meter: Meter = meter
        self._propagator: TraceContextTextMapPropagator = propagator
        # decoded once instead of on each root scope entry
        self._remote_parent: Span | None = remote_parent
        # live scopes of every subtree entered through this adapter
        self._scopes: dict[UUID, ScopeStore] = {}
        # instruments are identified by kind, name and unit - keyed on all three
        # and shared by every scope, so nested scopes contribute to one stream
        self._instruments: dict[tuple[ObservabilityMetricKind, str, str], _MetricRecording] = {}

    def trace_identifying(
        self,
        scope: ContextIdentifier,
        /,
    ) -> UUID:
        store: ScopeStore | None = self._scopes.get(scope.scope_id)
        if store is None:
            # an untracked scope belongs to no trace known here - reporting the
            # zero identifier keeps resolving one from failing, the same way
            # encoding a trace context for such a scope yields nothing
            return _NO_TRACE_ID

        return UUID(int=store.span.get_span_context().trace_id)

    def trace_context_encoding(
        self,
        scope: ContextIdentifier,
        /,
    ) -> Mapping[str, str]:
        store: ScopeStore | None = self._scopes.get(scope.scope_id)
        if store is None:
            return {}  # nothing to propagate for an untracked scope

        # injected from the scope's own context rather than the ambient one, so
        # the propagated position is the scope asking for it in all cases
        return _injected_trace_context(self._propagator, context=store.context)

    def log_recording(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        if level < self._level:
            return  # skip low level

        store: ScopeStore | None = self._scopes.get(scope.scope_id)
        if store is None:
            return  # skip without store

        formatted_message: str = message
        if args:
            try:  # interpolate here, the structured backend encodes the result
                formatted_message = message % args

            except Exception:  # a mismatched format must not lose the arguments
                formatted_message = f"{message} {args!r}"

        store.record_log(
            formatted_message,
            level=level,
            exception=exception,
        )

    def event_recording(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        if level < self._level:
            return  # skip low level

        store: ScopeStore | None = self._scopes.get(scope.scope_id)
        if store is None:
            return  # skip without store

        store.record_event(
            event,
            attributes=attributes,
        )

    def metric_recording(
        self,
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
        if level < self._level:
            return  # skip low level

        store: ScopeStore | None = self._scopes.get(scope.scope_id)
        if store is None:
            return  # skip without store

        # NaN and both infinities fail every comparison, as do integers too wide
        # to be converted to a float
        if not -float_info.max <= value <= float_info.max:
            _logger.warning(
                "Skipping metric %s with an unrepresentable value %r",
                escape_controls(metric),
                value,
            )
            return  # recording it would raise inside the SDK instead

        resolved_unit: str = unit or ""
        identity: tuple[ObservabilityMetricKind, str, str] = (kind, metric, resolved_unit)
        recording: _MetricRecording | None = self._instruments.get(identity)
        if recording is None:
            # `kind` is typed as a literal, yet it arrives at runtime through
            # the untyped `Observability` protocol - match it as a plain string
            match cast(str, kind):
                case "counter":
                    recording = self._meter.create_counter(name=metric, unit=resolved_unit).add

                case "histogram":
                    recording = self._meter.create_histogram(name=metric, unit=resolved_unit).record

                case "gauge":
                    recording = self._meter.create_gauge(name=metric, unit=resolved_unit).set

                case unsupported:
                    _logger.warning(
                        "Skipping metric %s with an unsupported kind %r",
                        escape_controls(metric),
                        unsupported,
                    )
                    # nothing is cached - caching a missing instrument would make
                    # every later recording of this metric fail the same way
                    return

            self._instruments[identity] = recording

        # the scope context makes exemplars point at the recording span even
        # when the measurement is taken where that context is not attached
        recording(value, _sanitized_attributes(attributes), store.context)

    def attributes_recording(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        if level < self._level:
            return  # skip low level

        if not attributes:
            return  # skip empty

        store: ScopeStore | None = self._scopes.get(scope.scope_id)
        if store is None:
            return  # skip without store

        store.record_attributes(attributes)

    def scope_entering(
        self,
        scope: ContextIdentifier,
        /,
    ) -> str:
        assert scope.scope_id not in self._scopes  # nosec: B101

        parent: ScopeStore | None = self._scopes.get(scope.parent_id)
        context: Context
        if parent is not None:
            context = parent.context

        elif self._remote_parent is not None and scope.is_root:
            # only an actual root continues the remote trace - a nested scope
            # whose parent already completed is no longer tracked here, yet its
            # ambient context still carries the span it belongs under
            context = trace.set_span_in_context(self._remote_parent)

        else:
            context = get_current()

        store: ScopeStore = ScopeStore(
            scope,
            context=context,
            span=self._tracer.start_span(
                name=scope.name,
                context=context,
            ),
            logger=self._logger,
        )
        if parent is not None:
            parent.pending += 1

        self._scopes[scope.scope_id] = store
        store.attach_context()

        return UUID(int=store.span.get_span_context().trace_id).hex

    def scope_exiting(
        self,
        scope: ContextIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None:
        store: ScopeStore | None = self._scopes.get(scope.scope_id)
        if store is None:
            return  # skip without store

        # only regular exceptions fail the span - cancellation is routine control
        # flow under structured concurrency and marking it would paint whole
        # subtrees red. `StatusCode.OK` is never set either: the specification
        # reserves it for explicitly asserted success and the SDK treats it as
        # terminal, preventing a later error on the same span
        if isinstance(exception, Exception):
            store.span.set_status(
                StatusCode.ERROR,
                f"{type(exception).__name__}: {exception}",
            )

        store.exit()
        # detach within the exiting task, before any deferred completion
        store.detach_context()

        # complete the scope and every ancestor which was waiting for it
        while store.try_complete():
            # a completed scope is never referenced again, unlink it immediately
            # instead of retaining the whole tree until its root completes
            del self._scopes[store.identifier.scope_id]
            # a root scope is its own parent, so unlinking it ends the walk
            parent: ScopeStore | None = self._scopes.get(store.identifier.parent_id)
            if parent is None:
                break

            parent.pending -= 1
            store = parent


@final
class OpenTelemetry:
    """
    Bridge Haiway observability callbacks to the OpenTelemetry SDK.

    Configure providers once at application startup, then pass
    ``OpenTelemetry.observability()`` into a root ``ctx.scope(...)`` to have
    nested Haiway scopes emit spans, logs, metrics, and span attributes through
    OpenTelemetry.
    """

    service: ClassVar[str] = ""
    version: ClassVar[str] = ""
    environment: ClassVar[str] = ""
    _logger: ClassVar[Logger | None] = None
    _logger_provider: ClassVar[LoggerProvider | None] = None
    _meter_provider: ClassVar[SdkMeterProvider | None] = None
    _tracer_provider: ClassVar[TracerProvider | None] = None
    # spec compliant W3C trace context codec, shared by extraction and injection
    _propagator: ClassVar[TraceContextTextMapPropagator] = TraceContextTextMapPropagator()

    @classmethod
    def autoconfigure(
        cls,
        *,
        service: str,
        version: str = "",
        environment: str = "",
    ) -> type[Self]:
        """
        Bind Haiway observability to already configured global OpenTelemetry providers.

        This method does not create exporters or install SDK providers. It only
        stores service metadata used by the Haiway bridge, retains the process
        global providers so they can be flushed, and enables
        ``OpenTelemetry.observability()`` to consume providers configured
        externally through the OpenTelemetry SDK, environment variables, or
        auto-instrumentation.

        Parameters
        ----------
        service : str
            The name of the service
        version : str, default=""
            The version of the service
        environment : str, default=""
            The deployment environment (e.g., "production", "staging")

        Returns
        -------
        type[Self]
            The OpenTelemetry class, for method chaining

        Raises
        ------
        OpenTelemetryException
            If ``service`` is empty.

        Notes
        -----
        When no SDK provider is installed for any signal, every global provider
        is still a non-exporting proxy. That is reported as a warning rather than
        raised, since providers may be installed later, but until then telemetry
        is discarded and scopes report a zero trace identifier.

        Binding again is allowed, and is how a process picks up providers which
        were installed after the first call. It can not tell a provider which was
        shut down from a live one, so avoid it after ``shutdown()``.
        """
        if not service:
            raise OpenTelemetryException("OpenTelemetry autoconfigure requires a non-empty service")

        cls.service = service
        cls.version = version
        cls.environment = environment

        # adopted providers have to be retained as well, otherwise force_flush
        # and shutdown would silently skip buffered telemetry
        tracer_provider: Any = trace.get_tracer_provider()
        cls._tracer_provider = (
            tracer_provider if isinstance(tracer_provider, TracerProvider) else None
        )
        meter_provider: Any = metrics.get_meter_provider()
        cls._meter_provider = (
            meter_provider if isinstance(meter_provider, SdkMeterProvider) else None
        )
        logger_provider: Any = get_logger_provider()
        cls._logger_provider = (
            logger_provider if isinstance(logger_provider, LoggerProvider) else None
        )
        cls._logger = logger_provider.get_logger(
            service,
            version=version,
        )

        if (
            cls._tracer_provider is None
            and cls._meter_provider is None
            and cls._logger_provider is None
        ):
            _logger.warning(
                "OpenTelemetry autoconfigure found no SDK providers installed"
                " - telemetry will be discarded and scopes will report a zero trace"
                " identifier. Install providers through the OpenTelemetry SDK before"
                " autoconfigure, or use configure() instead."
            )

        return cls

    @classmethod
    def configure(
        cls,
        *,
        service: str,
        version: str,
        instance: str | None = None,
        environment: str,
        otlp_endpoint: str | None = None,
        insecure: bool | None = None,
        credentials: ChannelCredentials | None = None,
        export_interval_millis: int = 5000,
        attributes: Mapping[str, Any] | None = None,
    ) -> type[Self]:
        """
        Configure the OpenTelemetry integration.

        This installs global OpenTelemetry logger, meter, and tracer providers
        for the current process. Call it during application startup before
        creating OpenTelemetry-backed observability scopes.

        Parameters
        ----------
        service : str
            The name of the service
        version : str
            The version of the service
        instance : str | None, optional
            The deployment instance identifier. Resolved to the current process
            id when not provided.
        environment : str
            The deployment environment (e.g., "production", "staging")
        otlp_endpoint : str | None, optional
            The OTLP endpoint URL to export telemetry data to. When not
            provided, it is resolved from the standard
            ``OTEL_EXPORTER_OTLP_ENDPOINT`` environment variables, and console
            exporters are used only when those are absent as well.
        insecure : bool | None, optional
            Whether to use insecure connections to the OTLP endpoint. When not
            provided, the exporters resolve it from the OpenTelemetry
            environment configuration.
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

        Raises
        ------
        OpenTelemetryException
            If ``service`` is empty, or if provider slots were already claimed.

        Notes
        -----
        This method installs the process-level providers used by this
        integration. It should be treated as startup configuration rather than a
        per-request or dynamic reconfiguration API.

        OpenTelemetry providers can only be installed once per process, and each
        signal has its own global slot. Providers already present - through
        auto-instrumentation, a previous call, or another library - are adopted
        per signal, while the remaining slots are filled with providers built
        here. That way a partially configured process still ends up with active
        tracer, meter, and logger providers instead of silently keeping proxy
        implementations for the signals this call could not install.

        A slot may also be held by a provider which is neither an SDK instance to
        adopt nor replaceable, such as an explicitly installed no-op. Such a
        signal is reported as an error and the provider built for it is released
        immediately, so its exporters do not linger unused.
        """
        if (
            cls._tracer_provider is not None
            or cls._meter_provider is not None
            or cls._logger_provider is not None
        ):
            raise OpenTelemetryException(
                "OpenTelemetry already claimed its provider slots - they are claimed once"
                " per process and can neither be replaced nor restored after a shutdown"
            )

        if not service:
            raise OpenTelemetryException("OpenTelemetry configure requires a non-empty service")

        # credentials imply a secure channel, so insecure has to be turned off
        exporter_insecure: bool | None = insecure if credentials is None else False

        # Create shared resource for all signals
        resource: Resource = Resource.create(
            {
                "service.name": service,
                "service.version": version,
                "service.instance.id": instance if instance is not None else str(os.getpid()),
                "deployment.environment.name": environment,
                **(attributes if attributes is not None else {}),
            },
        )
        cls.service = service
        cls.version = version
        cls.environment = environment

        cls._tracer_provider = _resolved_tracer_provider(
            resource=resource,
            otlp_endpoint=otlp_endpoint,
            insecure=exporter_insecure,
            credentials=credentials,
        )
        cls._meter_provider = _resolved_meter_provider(
            resource=resource,
            otlp_endpoint=otlp_endpoint,
            insecure=exporter_insecure,
            credentials=credentials,
            export_interval_millis=export_interval_millis,
        )
        cls._logger_provider = _resolved_logger_provider(
            resource=resource,
            otlp_endpoint=otlp_endpoint,
            insecure=exporter_insecure,
            credentials=credentials,
        )
        for signal, provider in (
            ("traces", cls._tracer_provider),
            ("metrics", cls._meter_provider),
            ("logs", cls._logger_provider),
        ):
            if provider is None:
                _logger.error(
                    f"OpenTelemetry could not claim the provider slot for {signal}"
                    " - it is held outside of the SDK and this signal will be discarded.",
                )
        # always take the logger from the globally installed provider, the same
        # place spans and metrics come from, even when it is not the one built here
        cls._logger = get_logger_provider().get_logger(
            service,
            version=version,
        )

        return cls

    @classmethod
    def force_flush(
        cls,
        timeout_millis: int = 30000,
    ) -> None:
        """
        Flush pending telemetry to the configured exporters.

        Useful before a process exits through a path that skips interpreter
        shutdown hooks, and in tests that assert on exported telemetry.

        Parameters
        ----------
        timeout_millis : int, default=30000
            Budget for flushing each provider, in milliseconds.

        Notes
        -----
        Only providers resolved by ``configure()`` or ``autoconfigure()`` are
        flushed. Failures are logged rather than raised, so flushing never breaks
        a shutdown path.
        """
        for provider in (cls._tracer_provider, cls._meter_provider, cls._logger_provider):
            if provider is None:
                continue

            try:
                provider.force_flush(timeout_millis)

            except Exception as exc:
                _logger.warning(
                    "Failed to flush %s",
                    type(provider).__name__,
                    exc_info=exc,
                )

    @classmethod
    def shutdown(cls) -> None:
        """
        Flush and shut down the resolved providers.

        Notes
        -----
        Providers installed by ``configure()`` also register interpreter exit
        hooks, so calling this is only required when shutting down explicitly.
        Failures are logged rather than raised.

        The integration is left unconfigured afterwards, so ``observability()``
        raises again instead of handing out adapters writing into providers which
        no longer export anything.

        Shutting down is terminal. OpenTelemetry claims its process global
        provider slots once and never releases them, so ``configure()`` and
        ``autoconfigure()`` raise afterwards rather than adopting the providers
        which were just shut down and discarding every signal in silence.
        """

        for provider in (cls._tracer_provider, cls._meter_provider, cls._logger_provider):
            if provider is None:
                continue

            try:
                provider.shutdown()

            except Exception as exc:
                _logger.warning(
                    "Failed to shut down %s",
                    type(provider).__name__,
                    exc_info=exc,
                )

        # the providers stay retained - they are the record of the slots claimed by
        # this integration, which can not be claimed again. only the logger handed
        # out to adapters is released, so `observability()` refuses from now on
        cls._logger = None

    @classmethod
    def traceparent(cls) -> str | None:
        """
        Encode the current active span context as a W3C ``traceparent`` value.

        Returns
        -------
        str | None
            Encoded ``traceparent`` value when a valid span context is active,
            otherwise ``None``.
        """
        return _injected_trace_context(cls._propagator).get("traceparent")

    @classmethod
    def tracestate(cls) -> str | None:
        """
        Encode the current vendor trace state as a W3C ``tracestate`` value.

        Returns
        -------
        str | None
            Encoded ``tracestate`` value when a valid span context carrying
            vendor trace state is active, otherwise ``None``.
        """
        return _injected_trace_context(cls._propagator).get("tracestate")

    @classmethod
    def observability(
        cls,
        level: ObservabilityLevel = ObservabilityLevel.INFO,
        *,
        traceparent: str | None = None,
        tracestate: str | None = None,
    ) -> Observability:
        """
        Create a Haiway ``Observability`` adapter backed by OpenTelemetry.

        The returned object is intended to be installed on a root Haiway scope.
        Nested scopes then reuse the same adapter, producing child spans under
        that root and routing logs, metrics, events, and attributes through the
        configured OpenTelemetry providers.

        Parameters
        ----------
        level : ObservabilityLevel, default=ObservabilityLevel.INFO
            Minimum level recorded by this adapter. The threshold applies to
            logs, events, metrics, and attributes.
        traceparent : str | None, optional
            W3C ``traceparent`` value continued by the root span. The remote span
            context becomes the parent of that span, so the Haiway trace joins
            the incoming trace instead of starting a new one. A value which is
            not a valid W3C ``traceparent`` is ignored with a warning.
        tracestate : str | None, optional
            W3C ``tracestate`` value accompanying ``traceparent``, propagating
            vendor specific trace state. Ignored without a valid ``traceparent``.

        Returns
        -------
        Observability
            An Observability implementation that uses OpenTelemetry

        Raises
        ------
        OpenTelemetryException
            If neither ``configure()`` nor ``autoconfigure()`` was called, or if
            the integration was shut down since.

        Notes
        -----
        A single adapter may back several independent scope trees, including
        concurrent ones, as long as they run on one event loop. Sharing one
        adapter across threads is not supported - create one per loop instead.
        """
        if cls._logger is None:
            raise OpenTelemetryException(
                "OpenTelemetry.configure or OpenTelemetry.autoconfigure must be"
                " called before creating observability"
            )

        adapter: _ObservabilityAdapter = _ObservabilityAdapter(
            level=level,
            logger=cls._logger,
            tracer=trace.get_tracer(cls.service, cls.version),
            meter=metrics.get_meter(cls.service, cls.version),
            propagator=cls._propagator,
            remote_parent=_remote_parent(traceparent, tracestate, cls._propagator),
        )

        return Observability(
            trace_identifying=adapter.trace_identifying,
            log_recording=adapter.log_recording,
            event_recording=adapter.event_recording,
            metric_recording=adapter.metric_recording,
            attributes_recording=adapter.attributes_recording,
            scope_entering=adapter.scope_entering,
            scope_exiting=adapter.scope_exiting,
            trace_context_encoding=adapter.trace_context_encoding,
        )
