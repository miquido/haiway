from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, Protocol, Self, final, runtime_checkable

from haiway.context.identifier import ScopeIdentifier
from haiway.context.logging import LoggerContext
from haiway.state import State

__all__ = [
    "MetricsContext",
    "MetricsHandler",
    "MetricsReading",
    "MetricsRecording",
    "MetricsScopeEntering",
    "MetricsScopeExiting",
]


@runtime_checkable
class MetricsRecording(Protocol):
    def __call__(
        self,
        scope: ScopeIdentifier,
        /,
        metric: State,
    ) -> None: ...


@runtime_checkable
class MetricsReading(Protocol):
    async def __call__[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
        *,
        metric: type[Metric],
        merged: bool,
    ) -> Metric | None: ...


@runtime_checkable
class MetricsScopeEntering(Protocol):
    def __call__[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None: ...


@runtime_checkable
class MetricsScopeExiting(Protocol):
    def __call__[Metric: State](
        self,
        scope: ScopeIdentifier,
        /,
    ) -> None: ...


class MetricsHandler(State):
    record: MetricsRecording
    read: MetricsReading
    enter_scope: MetricsScopeEntering
    exit_scope: MetricsScopeExiting


@final
class MetricsContext:
    _context = ContextVar[Self]("MetricsContext")

    @classmethod
    def scope(
        cls,
        scope: ScopeIdentifier,
        /,
        *,
        metrics: MetricsHandler | None,
    ) -> Self:
        current: Self
        try:  # check for current scope
            current = cls._context.get()

        except LookupError:
            # create root scope when missing
            return cls(
                scope=scope,
                metrics=metrics,
            )

        # create nested scope otherwise
        return cls(
            scope=scope,
            metrics=metrics or current._metrics,
        )

    @classmethod
    def record(
        cls,
        metric: State,
        /,
    ) -> None:
        try:  # catch exceptions - we don't wan't to blow up on metrics
            metrics: Self = cls._context.get()

            if metrics._metrics is not None:
                metrics._metrics.record(
                    metrics._scope,
                    metric,
                )

        except Exception as exc:
            LoggerContext.log_error(
                "Failed to record metric: %s",
                type(metric).__qualname__,
                exception=exc,
            )

    @classmethod
    async def read[Metric: State](
        cls,
        metric: type[Metric],
        /,
        merged: bool,
    ) -> Metric | None:
        try:  # catch exceptions - we don't wan't to blow up on metrics
            metrics: Self = cls._context.get()

            if metrics._metrics is not None:
                return await metrics._metrics.read(
                    metrics._scope,
                    metric=metric,
                    merged=merged,
                )

        except Exception as exc:
            LoggerContext.log_error(
                "Failed to read metric: %s",
                metric.__qualname__,
                exception=exc,
            )

    __slots__ = (
        "_metrics",
        "_scope",
        "_token",
    )

    def __init__(
        self,
        scope: ScopeIdentifier,
        metrics: MetricsHandler | None,
    ) -> None:
        self._scope: ScopeIdentifier
        object.__setattr__(
            self,
            "_scope",
            scope,
        )
        self._metrics: MetricsHandler | None
        object.__setattr__(
            self,
            "_metrics",
            metrics,
        )
        self._token: Token[MetricsContext] | None
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
            MetricsContext._context.set(self),
        )
        if self._metrics is not None:
            self._metrics.enter_scope(self._scope)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        MetricsContext._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )
        if self._metrics is not None:
            self._metrics.exit_scope(self._scope)
