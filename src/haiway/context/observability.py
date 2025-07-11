from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from enum import IntEnum
from logging import DEBUG as DEBUG_LOGGING
from logging import ERROR as ERROR_LOGGING
from logging import INFO as INFO_LOGGING
from logging import WARNING as WARNING_LOGGING
from logging import Logger, getLogger
from types import TracebackType
from typing import Any, ClassVar, Protocol, Self, runtime_checkable
from uuid import UUID, uuid4

from haiway.context.identifier import ScopeIdentifier
from haiway.state import Immutable
from haiway.types import Missing
from haiway.utils.formatting import format_str

__all__ = (
    "Observability",
    "ObservabilityAttribute",
    "ObservabilityAttributesRecording",
    "ObservabilityContext",
    "ObservabilityEventRecording",
    "ObservabilityLevel",
    "ObservabilityLogRecording",
    "ObservabilityMetricRecording",
    "ObservabilityScopeEntering",
    "ObservabilityScopeExiting",
    "ObservabilityTraceIdentifying",
)


class ObservabilityLevel(IntEnum):
    """
    Defines the severity levels for observability recordings.

    These levels correspond to standard logging levels, allowing consistent
    severity indication across different types of observability records.
    """

    # values from logging package
    ERROR = ERROR_LOGGING
    WARNING = WARNING_LOGGING
    INFO = INFO_LOGGING
    DEBUG = DEBUG_LOGGING


type ObservabilityAttribute = (
    Sequence[str]
    | Sequence[float]
    | Sequence[int]
    | Sequence[bool]
    | str
    | float
    | int
    | bool
    | None
    | Missing
)
"""
A type representing values that can be recorded as observability attributes.

Includes scalar types (strings, numbers, booleans), sequences of these types,
None, or Missing marker.
"""


@runtime_checkable
class ObservabilityTraceIdentifying(Protocol):
    """
    Protocol for accessing trace identifier in an observability system.
    """

    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
    ) -> UUID: ...


@runtime_checkable
class ObservabilityLogRecording(Protocol):
    """
    Protocol for recording log messages in an observability system.

    Implementations should handle formatting and storing log messages
    with appropriate contextual information from the scope.
    """

    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None: ...


@runtime_checkable
class ObservabilityEventRecording(Protocol):
    """
    Protocol for recording events in an observability system.

    Implementations should handle recording named events with
    associated attributes and appropriate contextual information.
    """

    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...


@runtime_checkable
class ObservabilityMetricRecording(Protocol):
    """
    Protocol for recording metrics in an observability system.

    Implementations should handle recording numeric measurements with
    optional units and associated attributes.
    """

    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        metric: str,
        value: float | int,
        unit: str | None,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...


@runtime_checkable
class ObservabilityAttributesRecording(Protocol):
    """
    Protocol for recording standalone attributes in an observability system.

    Implementations should handle recording contextual attributes
    that are not directly associated with logs, events, or metrics.
    """

    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...


@runtime_checkable
class ObservabilityScopeEntering(Protocol):
    """
    Protocol for handling scope entry in an observability system.

    Implementations should record when execution enters a new scope.
    """

    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None: ...


@runtime_checkable
class ObservabilityScopeExiting(Protocol):
    """
    Protocol for handling scope exit in an observability system.

    Implementations should record when execution exits a scope,
    including any exceptions that caused the exit.
    """

    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None: ...


class Observability(Immutable):  # avoiding State inheritance to prevent propagation as scope state
    """
    Container for observability recording functions.

    Provides a unified interface for recording various types of observability
    data including logs, events, metrics, and attributes. Also handles recording
    when scopes are entered and exited.

    This class is immutable after initialization.
    """

    trace_identifying: ObservabilityTraceIdentifying
    log_recording: ObservabilityLogRecording
    metric_recording: ObservabilityMetricRecording
    event_recording: ObservabilityEventRecording
    attributes_recording: ObservabilityAttributesRecording
    scope_entering: ObservabilityScopeEntering
    scope_exiting: ObservabilityScopeExiting


def _logger_observability(
    logger: Logger,
    /,
) -> Observability:
    """
    Create an Observability instance that uses a Logger for recording.

    Adapts a standard Python logger to the Observability interface,
    mapping observability concepts to log messages.

    Parameters
    ----------
    logger: Logger
        The logger to use for recording observability data

    Returns
    -------
    Observability
        An Observability instance that uses the logger
    """

    trace_id: UUID = uuid4()
    trace_id_hex: str = trace_id.hex

    def trace_identifying(
        scope: ScopeIdentifier,
        /,
    ) -> UUID:
        return trace_id

    def log_recording(
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        logger.log(
            level,
            f"[{trace_id_hex}] {scope.unique_name} {message}",
            *args,
            exc_info=exception,
        )

    def event_recording(
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        logger.log(
            level,
            f"[{trace_id_hex}] {scope.unique_name} Recorded event:"
            f" {event} {format_str(attributes)}",
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
        if attributes:
            logger.log(
                level,
                f"[{trace_id_hex}] {scope.unique_name} Recorded metric:"
                f" {metric} = {value} {unit or ''}"
                f"\n{format_str(attributes)}",
            )

        else:
            logger.log(
                level,
                f"[{trace_id_hex}] {scope.unique_name} Recorded metric:"
                f" {metric} = {value} {unit or ''}",
            )

    def attributes_recording(
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        if not attributes:
            return

        logger.log(
            level,
            f"[{trace_id_hex}] {scope.unique_name} Recorded attributes: {format_str(attributes)}",
        )

    def scope_entering(
        scope: ScopeIdentifier,
        /,
    ) -> None:
        logger.log(
            ObservabilityLevel.DEBUG,
            f"[{trace_id_hex}] {scope.unique_name} Entering scope: {scope.name}",
        )

    def scope_exiting(
        scope: ScopeIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None:
        logger.log(
            ObservabilityLevel.DEBUG,
            f"{scope.unique_name} Exiting scope: {scope.name}",
            exc_info=exception,
        )

    return Observability(
        trace_identifying=trace_identifying,
        log_recording=log_recording,
        event_recording=event_recording,
        metric_recording=metric_recording,
        attributes_recording=attributes_recording,
        scope_entering=scope_entering,
        scope_exiting=scope_exiting,
    )


class ObservabilityContext(Immutable):
    """
    Context manager for observability within a scope.

    Manages observability recording within a context, propagating the
    appropriate observability handler and scope information. Records
    scope entry and exit events automatically.

    This class is immutable after initialization.
    """

    _context: ClassVar[ContextVar[Self]] = ContextVar[Self]("ObservabilityContext")

    @classmethod
    def scope(
        cls,
        scope: ScopeIdentifier,
        /,
        *,
        observability: Observability | Logger | None,
    ) -> Self:
        """
        Create an observability context for a scope.

        If called within an existing context, inherits the observability
        handler unless a new one is specified. If called outside any context,
        creates a new root context with the specified or default observability.

        Parameters
        ----------
        scope: ScopeIdentifier
            The scope identifier this context is associated with
        observability: Observability | Logger | None
            The observability handler to use, or None to inherit or create default

        Returns
        -------
        Self
            A new observability context
        """
        current: Self
        try:  # check for current scope
            current = cls._context.get()

        except LookupError:
            resolved_observability: Observability
            match observability:
                case Observability() as observability:
                    resolved_observability = observability

                case None:
                    resolved_observability = _logger_observability(getLogger(scope.name))

                case Logger() as logger:
                    resolved_observability = _logger_observability(logger)

            # create root scope when missing
            return cls(
                scope=scope,
                observability=resolved_observability,
            )

        # create nested scope otherwise
        resolved_observability: Observability
        match observability:
            case None:
                resolved_observability = current.observability

            case Logger() as logger:
                resolved_observability = _logger_observability(logger)

            case observability:
                resolved_observability = observability

        return cls(
            scope=scope,
            observability=resolved_observability,
        )

    @classmethod
    def trace_id(
        cls,
        scope_identifier: ScopeIdentifier | None = None,
    ) -> str:
        """
        Get the hexadecimal trace identifier for the specified scope or current scope.

        This class method retrieves the trace identifier from the current observability context,
        which can be used to correlate logs, events, and metrics across different components.

        Parameters
        ----------
        scope_identifier: ScopeIdentifier | None, default=None
            The scope identifier to get the trace ID for. If None, the current scope's
            trace ID is returned.

        Returns
        -------
        str
            The string representation of the trace ID

        Raises
        ------
        RuntimeError
            If called outside of any scope context
        """
        try:
            return str(
                cls._context.get().observability.trace_identifying(
                    scope_identifier if scope_identifier is not None else ScopeIdentifier.current()
                )
            )

        except LookupError as exc:
            raise RuntimeError("Attempting to access scope identifier outside of scope") from exc

    @classmethod
    def record_log(
        cls,
        level: ObservabilityLevel,
        message: str,
        /,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        """
        Record a log message in the current observability context.

        If no context is active, falls back to the root logger.

        Parameters
        ----------
        level: ObservabilityLevel
            The severity level for this log message
        message: str
            The log message text, may contain format placeholders
        *args: Any
            Format arguments for the message
        exception: BaseException | None
            Optional exception to associate with the log
        """
        try:  # catch exceptions - we don't wan't to blow up on observability
            context: Self = cls._context.get()

            if context.observability is not None:
                context.observability.log_recording(
                    context._scope,
                    level,
                    message,
                    *args,
                    exception=exception,
                )

        except LookupError:
            getLogger().log(
                level,
                message,
                *args,
                exc_info=exception,
            )

    @classmethod
    def record_event(
        cls,
        level: ObservabilityLevel,
        event: str,
        /,
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        """
        Record an event in the current observability context.

        Records a named event with associated attributes. Falls back to logging
        an error if recording fails.

        Parameters
        ----------
        level: ObservabilityLevel
            The severity level for this event
        event: str
            The name of the event
        attributes: Mapping[str, ObservabilityAttribute]
            Key-value attributes associated with the event
        """
        try:  # catch exceptions - we don't wan't to blow up on observability
            context: Self = cls._context.get()

            if context.observability is not None:
                context.observability.event_recording(
                    context._scope,
                    level=level,
                    event=event,
                    attributes=attributes,
                )

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                f"Failed to record event: {type(event).__qualname__}",
                exception=exc,
            )

    @classmethod
    def record_metric(
        cls,
        level: ObservabilityLevel,
        metric: str,
        /,
        *,
        value: float | int,
        unit: str | None,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        """
        Record a metric in the current observability context.

        Records a numeric measurement with an optional unit and associated attributes.
        Falls back to logging an error if recording fails.

        Parameters
        ----------
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
        try:  # catch exceptions - we don't wan't to blow up on observability
            context: Self = cls._context.get()

            if context.observability is not None:
                context.observability.metric_recording(
                    context._scope,
                    level=level,
                    metric=metric,
                    value=value,
                    unit=unit,
                    attributes=attributes,
                )

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                f"Failed to record metric: {metric}",
                exception=exc,
            )

    @classmethod
    def record_attributes(
        cls,
        level: ObservabilityLevel,
        /,
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        """
        Record standalone attributes in the current observability context.

        Records key-value attributes not directly associated with a specific log,
        event, or metric. Falls back to logging an error if recording fails.

        Parameters
        ----------
        level: ObservabilityLevel
            The severity level for these attributes
        attributes: Mapping[str, ObservabilityAttribute]
            Key-value attributes to record
        """
        try:  # catch exceptions - we don't wan't to blow up on observability
            context: Self = cls._context.get()

            if context.observability is not None:
                context.observability.attributes_recording(
                    context._scope,
                    level=level,
                    attributes=attributes,
                )

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                "Failed to record attributes",
                exception=exc,
            )

    _scope: ScopeIdentifier
    observability: Observability
    _token: Token[Self] | None = None

    def __init__(
        self,
        scope: ScopeIdentifier,
        observability: Observability | None,
    ) -> None:
        object.__setattr__(
            self,
            "_scope",
            scope,
        )
        object.__setattr__(
            self,
            "observability",
            observability,
        )
        object.__setattr__(
            self,
            "_token",
            None,
        )

    def __enter__(self) -> None:
        """
        Enter this observability context.

        Sets this context as the current one and records scope entry.

        Raises
        ------
        AssertionError
            If attempting to re-enter an already active context
        """
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        object.__setattr__(
            self,
            "_token",
            ObservabilityContext._context.set(self),
        )
        self.observability.scope_entering(self._scope)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Exit this observability context.

        Restores the previous context and records scope exit.

        Parameters
        ----------
        exc_type: type[BaseException] | None
            Type of exception that caused the exit
        exc_val: BaseException | None
            Exception instance that caused the exit
        exc_tb: TracebackType | None
            Traceback for the exception

        Raises
        ------
        AssertionError
            If the context is not active
        """
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ObservabilityContext._context.reset(self._token)  # pyright: ignore[reportArgumentType]
        object.__setattr__(
            self,
            "_token",
            None,
        )
        self.observability.scope_exiting(
            self._scope,
            exception=exc_val,
        )
