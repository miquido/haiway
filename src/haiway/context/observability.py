from collections.abc import Mapping, Sequence
from contextvars import ContextVar, Token
from enum import IntEnum
from logging import DEBUG as DEBUG_LOGGING
from logging import ERROR as ERROR_LOGGING
from logging import INFO as INFO_LOGGING
from logging import WARNING as WARNING_LOGGING
from logging import Logger, getLogger
from types import TracebackType
from typing import Any, ClassVar, Literal, NoReturn, Protocol, Self, final, runtime_checkable
from uuid import UUID, uuid4

from haiway.context.identifier import ContextIdentifier
from haiway.context.types import ContextMissing
from haiway.types import Missing
from haiway.utils.formatting import format_str

__all__ = (
    "ContextObservability",
    "Observability",
    "ObservabilityAttribute",
    "ObservabilityAttributesRecording",
    "ObservabilityEventRecording",
    "ObservabilityLevel",
    "ObservabilityLogRecording",
    "ObservabilityMetricKind",
    "ObservabilityMetricRecording",
    "ObservabilityScopeEntering",
    "ObservabilityScopeExiting",
    "ObservabilityTraceIdentifying",
)


class ObservabilityLevel(IntEnum):
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


@runtime_checkable
class ObservabilityTraceIdentifying(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
    ) -> UUID: ...


@runtime_checkable
class ObservabilityLogRecording(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None: ...


@runtime_checkable
class ObservabilityEventRecording(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...


type ObservabilityMetricKind = Literal["counter", "histogram", "gauge"]


@runtime_checkable
class ObservabilityMetricRecording(Protocol):
    def __call__(
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
    ) -> None: ...


@runtime_checkable
class ObservabilityAttributesRecording(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...


@runtime_checkable
class ObservabilityScopeEntering(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
    ) -> str: ...


@runtime_checkable
class ObservabilityScopeExiting(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None: ...


@final  # immutable
class Observability:  # avoiding State inheritance to prevent propagation as scope state
    __slots__ = (
        "attributes_recording",
        "event_recording",
        "log_recording",
        "metric_recording",
        "scope_entering",
        "scope_exiting",
        "trace_identifying",
    )

    def __init__(
        self,
        trace_identifying: ObservabilityTraceIdentifying,
        log_recording: ObservabilityLogRecording,
        metric_recording: ObservabilityMetricRecording,
        event_recording: ObservabilityEventRecording,
        attributes_recording: ObservabilityAttributesRecording,
        scope_entering: ObservabilityScopeEntering,
        scope_exiting: ObservabilityScopeExiting,
    ) -> None:
        self.trace_identifying: ObservabilityTraceIdentifying
        assert isinstance(trace_identifying, ObservabilityTraceIdentifying)  # nosec: B101
        object.__setattr__(
            self,
            "trace_identifying",
            trace_identifying,
        )
        self.log_recording: ObservabilityLogRecording
        assert isinstance(log_recording, ObservabilityLogRecording)  # nosec: B101
        object.__setattr__(
            self,
            "log_recording",
            log_recording,
        )
        self.metric_recording: ObservabilityMetricRecording
        assert isinstance(metric_recording, ObservabilityMetricRecording)  # nosec: B101
        object.__setattr__(
            self,
            "metric_recording",
            metric_recording,
        )
        self.event_recording: ObservabilityEventRecording
        assert isinstance(event_recording, ObservabilityEventRecording)  # nosec: B101
        object.__setattr__(
            self,
            "event_recording",
            event_recording,
        )
        self.attributes_recording: ObservabilityAttributesRecording
        assert isinstance(attributes_recording, ObservabilityAttributesRecording)  # nosec: B101
        object.__setattr__(
            self,
            "attributes_recording",
            attributes_recording,
        )
        self.scope_entering: ObservabilityScopeEntering
        assert isinstance(scope_entering, ObservabilityScopeEntering)  # nosec: B101
        object.__setattr__(
            self,
            "scope_entering",
            scope_entering,
        )
        self.scope_exiting: ObservabilityScopeExiting
        assert isinstance(scope_exiting, ObservabilityScopeExiting)  # nosec: B101
        object.__setattr__(
            self,
            "scope_exiting",
            scope_exiting,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be deleted"
        )


def _logger_observability(  # noqa: C901
    logger: Logger,
    /,
) -> Observability:
    trace_id: UUID = uuid4()
    trace_id_hex: str = str(trace_id)

    def trace_identifying(
        scope: ContextIdentifier,
        /,
    ) -> UUID:
        return trace_id

    def log_recording(
        scope: ContextIdentifier,
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
        scope: ContextIdentifier,
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
        scope: ContextIdentifier,
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
        scope: ContextIdentifier,
        /,
    ) -> str:
        logger.log(
            ObservabilityLevel.DEBUG,
            f"[{trace_id_hex}] {scope.unique_name} Entering scope: {scope.name}",
        )
        return trace_id_hex

    def scope_exiting(
        scope: ContextIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None:
        logger.log(
            ObservabilityLevel.DEBUG,
            f"[{trace_id_hex}] {scope.unique_name} Exiting scope: {scope.name}",
            exc_info=exception,
        )

        if isinstance(exception, Exception):
            logger.log(
                ObservabilityLevel.ERROR,
                f"[{trace_id_hex}] {scope.unique_name} Scope error: {exception}",
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


@final  # consider immutable
class ContextObservability:
    @classmethod
    def scope(
        cls,
        scope: ContextIdentifier,
        /,
        *,
        observability: Observability | Logger | None,
    ) -> Self:
        current: Self
        try:  # check for current scope
            current = cls._context.get()

        except LookupError:  # create root scope when missing
            if observability is None:
                return cls(
                    scope=scope,
                    observability=_logger_observability(getLogger(scope.name)),
                )

            elif isinstance(observability, Logger):
                return cls(
                    scope=scope,
                    observability=_logger_observability(observability),
                )

            else:
                return cls(
                    scope=scope,
                    observability=observability,
                )

        assert observability is None  # nosec: B101
        # create nested scope
        return cls(
            scope=scope,
            observability=current.observability,
        )

    @classmethod
    def trace_id(cls) -> str:
        try:
            return str(
                cls._context.get().observability.trace_identifying(ContextIdentifier.current())
            )

        except LookupError:
            raise ContextMissing("Context observability requested but not defined!") from None

    @classmethod
    def record_log(
        cls,
        level: ObservabilityLevel,
        message: str,
        /,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        try:
            context: Self = cls._context.get()
            context.observability.log_recording(
                context._scope,
                level,
                message,
                *args,
                exception=exception,
            )

        except LookupError:  # fallback for access out of context
            return getLogger().log(
                level,
                message,
                *args,
                exc_info=exception,
            )

        # catch exceptions - we don't want to blow up on observability
        except Exception as exc:
            logger: Logger = getLogger()
            logger.log(
                ObservabilityLevel.ERROR,
                "Failed to log a message within observability system",
                exc_info=exc,
            )
            logger.log(
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
        try:  # catch exceptions - we don't want to blow up on observability
            context: Self = cls._context.get()

            context.observability.event_recording(
                context._scope,
                level=level,
                event=event,
                attributes=attributes,
            )

        except Exception as exc:
            cls.record_log(
                ObservabilityLevel.ERROR,
                f"Failed to record event: {event}",
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
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        try:  # catch exceptions - we don't want to blow up on observability
            context: Self = cls._context.get()

            context.observability.metric_recording(
                context._scope,
                level=level,
                metric=metric,
                value=value,
                unit=unit,
                kind=kind,
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
        try:  # catch exceptions - we don't want to blow up on observability
            context: Self = cls._context.get()

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

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextObservability")
    __slots__ = (
        "_scope",
        "_token",
        "observability",
    )

    def __init__(
        self,
        scope: ContextIdentifier,
        observability: Observability,
    ) -> None:
        self.observability: Observability = observability
        self._scope: ContextIdentifier = scope
        self._token: Token[ContextObservability] | None = None

    def __enter__(self) -> str:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        self._token = ContextObservability._context.set(self)
        return self.observability.scope_entering(self._scope)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ContextObservability._context.reset(self._token)
        self._token = None

        try:
            self.observability.scope_exiting(
                self._scope,
                exception=exc_val,
            )

        except Exception as exc:
            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                "Failed to properly exit observability scope",
                exception=exc,
            )
