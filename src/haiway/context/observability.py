from collections.abc import Sequence
from contextvars import ContextVar, Token
from enum import IntEnum
from logging import DEBUG as DEBUG_LOGGING
from logging import ERROR as ERROR_LOGGING
from logging import INFO as INFO_LOGGING
from logging import WARNING as WARNING_LOGGING
from logging import Logger, getLogger
from types import TracebackType
from typing import Any, Final, Protocol, Self, final, runtime_checkable

from haiway.context.identifier import ScopeIdentifier

# from haiway.context.logging import LoggerContext
from haiway.state import State

__all__ = (
    "DEBUG",
    "ERROR",
    "INFO",
    "WARNING",
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
)


class ObservabilityLevel(IntEnum):
    # values from logging package
    ERROR = ERROR_LOGGING
    WARNING = WARNING_LOGGING
    INFO = INFO_LOGGING
    DEBUG = DEBUG_LOGGING


ERROR: Final[int] = ObservabilityLevel.ERROR
WARNING: Final[int] = ObservabilityLevel.WARNING
INFO: Final[int] = ObservabilityLevel.INFO
DEBUG: Final[int] = ObservabilityLevel.DEBUG

type ObservabilityAttribute = (
    Sequence[str] | Sequence[float] | Sequence[int] | Sequence[bool] | str | float | int | bool
)


@runtime_checkable
class ObservabilityLogRecording(Protocol):
    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class ObservabilityEventRecording(Protocol):
    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        *,
        level: ObservabilityLevel,
        event: State,
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class ObservabilityMetricRecording(Protocol):
    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        *,
        metric: str,
        value: float | int,
        unit: str | None,
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class ObservabilityAttributesRecording(Protocol):
    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        **attributes: ObservabilityAttribute,
    ) -> None: ...


@runtime_checkable
class ObservabilityScopeEntering(Protocol):
    def __call__[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None: ...


@runtime_checkable
class ObservabilityScopeExiting(Protocol):
    def __call__[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None: ...


class Observability:  # avoiding State inheritance to prevent propagation as scope state
    __slots__ = (
        "attributes_recording",
        "event_recording",
        "log_recording",
        "metric_recording",
        "scope_entering",
        "scope_exiting",
    )

    def __init__(
        self,
        log_recording: ObservabilityLogRecording,
        metric_recording: ObservabilityMetricRecording,
        event_recording: ObservabilityEventRecording,
        attributes_recording: ObservabilityAttributesRecording,
        scope_entering: ObservabilityScopeEntering,
        scope_exiting: ObservabilityScopeExiting,
    ) -> None:
        self.log_recording: ObservabilityLogRecording
        object.__setattr__(
            self,
            "log_recording",
            log_recording,
        )
        self.metric_recording: ObservabilityMetricRecording
        object.__setattr__(
            self,
            "metric_recording",
            metric_recording,
        )
        self.event_recording: ObservabilityEventRecording
        object.__setattr__(
            self,
            "event_recording",
            event_recording,
        )
        self.attributes_recording: ObservabilityAttributesRecording
        object.__setattr__(
            self,
            "attributes_recording",
            attributes_recording,
        )

        self.scope_entering: ObservabilityScopeEntering
        object.__setattr__(
            self,
            "scope_entering",
            scope_entering,
        )
        self.scope_exiting: ObservabilityScopeExiting
        object.__setattr__(
            self,
            "scope_exiting",
            scope_exiting,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )


def _logger_observability(
    logger: Logger,
    /,
) -> Observability:
    def log_recording(
        scope: ScopeIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
        **extra: Any,
    ) -> None:
        logger.log(
            level,
            f"{scope.unique_name} {message}",
            *args,
            exc_info=exception,
        )

    def event_recording(
        scope: ScopeIdentifier,
        /,
        *,
        level: ObservabilityLevel,
        event: State,
        **extra: Any,
    ) -> None:
        logger.log(
            level,
            f"{scope.unique_name} Recorded event:\n{event.to_str(pretty=True)}",
        )

    def metric_recording(
        scope: ScopeIdentifier,
        /,
        *,
        metric: str,
        value: float | int,
        unit: str | None,
        **extra: Any,
    ) -> None:
        logger.log(
            INFO,
            f"{scope.unique_name} Recorded metric: {metric}={value}{unit or ''}",
        )

    def attributes_recording(
        scope: ScopeIdentifier,
        /,
        **attributes: ObservabilityAttribute,
    ) -> None:
        if not attributes:
            return

        logger.log(
            INFO,
            f"{scope.unique_name} Recorded attributes:"
            f"\n{'\n'.join([f'{k}: {v}' for k, v in attributes.items()])}",
        )

    def scope_entering[Metric: State](
        scope: ScopeIdentifier,
        /,
    ) -> None:
        logger.log(
            DEBUG,
            f"{scope.unique_name} Entering scope: {scope.label}",
        )

    def scope_exiting[Metric: State](
        scope: ScopeIdentifier,
        /,
        *,
        exception: BaseException | None,
    ) -> None:
        logger.log(
            DEBUG,
            f"{scope.unique_name} Exiting scope: {scope.label}",
            exc_info=exception,
        )

    return Observability(
        log_recording=log_recording,
        event_recording=event_recording,
        metric_recording=metric_recording,
        attributes_recording=attributes_recording,
        scope_entering=scope_entering,
        scope_exiting=scope_exiting,
    )


@final
class ObservabilityContext:
    _context = ContextVar[Self]("ObservabilityContext")

    @classmethod
    def scope(
        cls,
        scope: ScopeIdentifier,
        /,
        *,
        observability: Observability | Logger | None,
    ) -> Self:
        current: Self
        try:  # check for current scope
            current = cls._context.get()

        except LookupError:
            resolved_observability: Observability
            match observability:
                case Observability() as observability:
                    resolved_observability = observability

                case None:
                    resolved_observability = _logger_observability(getLogger(scope.label))

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
    def record_log(
        cls,
        level: ObservabilityLevel,
        message: str,
        /,
        *args: Any,
        exception: BaseException | None,
        **extra: Any,
    ) -> None:
        try:  # catch exceptions - we don't wan't to blow up on observability
            context: Self = cls._context.get()

            if context.observability is not None:
                context.observability.log_recording(
                    context._scope,
                    level,
                    message,
                    *args,
                    exception=exception,
                    **extra,
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
        event: State,
        /,
        *,
        level: ObservabilityLevel,
        **extra: Any,
    ) -> None:
        try:  # catch exceptions - we don't wan't to blow up on observability
            context: Self = cls._context.get()

            if context.observability is not None:
                context.observability.event_recording(
                    context._scope,
                    level=level,
                    event=event,
                    **extra,
                )

        except Exception as exc:
            cls.record_log(
                ERROR,
                f"Failed to record event: {type(event).__qualname__}",
                exception=exc,
            )

    @classmethod
    def record_metric(
        cls,
        metric: str,
        /,
        *,
        value: float | int,
        unit: str | None,
        **extra: Any,
    ) -> None:
        try:  # catch exceptions - we don't wan't to blow up on observability
            context: Self = cls._context.get()

            if context.observability is not None:
                context.observability.metric_recording(
                    context._scope,
                    metric=metric,
                    value=value,
                    unit=unit,
                    **extra,
                )

        except Exception as exc:
            cls.record_log(
                ERROR,
                f"Failed to record metric: {metric}",
                exception=exc,
            )

    @classmethod
    def record_attributes(
        cls,
        **attributes: ObservabilityAttribute,
    ) -> None:
        try:  # catch exceptions - we don't wan't to blow up on observability
            context: Self = cls._context.get()

            if context.observability is not None:
                context.observability.attributes_recording(
                    context._scope,
                    **attributes,
                )

        except Exception as exc:
            cls.record_log(
                ERROR,
                f"Failed to record attributes: {attributes}",
                exception=exc,
            )

    __slots__ = (
        "_scope",
        "_token",
        "observability",
    )

    def __init__(
        self,
        scope: ScopeIdentifier,
        observability: Observability | None,
    ) -> None:
        self._scope: ScopeIdentifier
        object.__setattr__(
            self,
            "_scope",
            scope,
        )
        self.observability: Observability
        object.__setattr__(
            self,
            "observability",
            observability,
        )
        self._token: Token[ObservabilityContext] | None
        object.__setattr__(
            self,
            "_token",
            None,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    def __enter__(self) -> None:
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
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ObservabilityContext._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )
        self.observability.scope_exiting(
            self._scope,
            exception=exc_val,
        )
