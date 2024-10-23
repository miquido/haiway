from asyncio import Future, gather, get_event_loop
from collections.abc import Callable
from contextvars import ContextVar, Token
from copy import copy
from itertools import chain
from logging import DEBUG, ERROR, INFO, WARNING, Logger, getLogger
from time import monotonic
from types import TracebackType
from typing import Any, Self, cast, final, overload
from uuid import uuid4

from haiway.state import State
from haiway.utils import freeze

__all__ = [
    "ScopeMetrics",
    "MetricsContext",
]


@final
class ScopeMetrics:
    def __init__(
        self,
        *,
        trace_id: str | None,
        scope: str,
        logger: Logger | None,
    ) -> None:
        self.trace_id: str = trace_id or uuid4().hex
        self._label: str = f"{self.trace_id}|{scope}" if scope else self.trace_id
        self._logger: Logger = logger or getLogger(name=scope)
        self._metrics: dict[type[State], State] = {}
        self._nested: list[ScopeMetrics] = []
        self._timestamp: float = monotonic()
        self._completed: Future[float] = get_event_loop().create_future()

        freeze(self)

    def __del__(self) -> None:
        self._complete()  # ensure completion on deinit

    def __str__(self) -> str:
        return self._label

    def metrics(
        self,
        *,
        merge: Callable[[State, State], State] = lambda lhs, rhs: lhs,
    ) -> list[State]:
        metrics: dict[type[State], State] = copy(self._metrics)
        for metric in chain.from_iterable(nested.metrics(merge=merge) for nested in self._nested):
            metric_type: type[State] = type(metric)
            if current := metrics.get(metric_type):
                metrics[metric_type] = merge(current, metric)

            else:
                metrics[metric_type] = metric

        return list(metrics.values())

    @overload
    def read[Metric: State](
        self,
        metric: type[Metric],
        /,
    ) -> Metric | None: ...

    @overload
    def read[Metric: State](
        self,
        metric: type[Metric],
        /,
        default: Metric,
    ) -> Metric: ...

    def read[Metric: State](
        self,
        metric: type[Metric],
        /,
        default: Metric | None = None,
    ) -> Metric | None:
        return cast(Metric | None, self._metrics.get(metric, default))

    def record[Metric: State](
        self,
        metric: Metric,
        /,
        *,
        merge: Callable[[Metric, Metric], Metric] = lambda lhs, rhs: rhs,
    ) -> None:
        assert not self._completed.done(), "Can't record using completed metrics scope"  # nosec: B101
        metric_type: type[Metric] = type(metric)
        if current := self._metrics.get(metric_type):
            self._metrics[metric_type] = merge(cast(Metric, current), metric)

        else:
            self._metrics[metric_type] = metric

    @property
    def completed(self) -> bool:
        return self._completed.done() and all(nested.completed for nested in self._nested)

    @property
    def time(self) -> float:
        if self._completed.done():
            return self._completed.result()

        else:
            return monotonic() - self._timestamp

    async def wait(self) -> None:
        await gather(
            self._completed,
            *[nested.wait() for nested in self._nested],
            return_exceptions=False,
        )

    def _complete(self) -> None:
        if self._completed.done():
            return  # already completed

        self._completed.set_result(monotonic() - self._timestamp)

    def scope(
        self,
        name: str,
        /,
    ) -> Self:
        nested: Self = self.__class__(
            scope=name,
            logger=self._logger,
            trace_id=self.trace_id,
        )
        self._nested.append(nested)
        return nested

    def log(
        self,
        level: int,
        message: str,
        /,
        *args: Any,
        exception: BaseException | None = None,
    ) -> None:
        self._logger.log(
            level,
            f"[{self}] {message}",
            *args,
            exc_info=exception,
        )


@final
class MetricsContext:
    _context = ContextVar[ScopeMetrics]("MetricsContext")

    @classmethod
    def scope(
        cls,
        name: str,
        /,
        *,
        trace_id: str | None = None,
        logger: Logger | None = None,
    ) -> Self:
        try:
            context: ScopeMetrics = cls._context.get()
            if trace_id is None or context.trace_id == trace_id:
                return cls(context.scope(name))

            else:
                return cls(
                    ScopeMetrics(
                        trace_id=trace_id,
                        scope=name,
                        logger=logger or context._logger,  # pyright: ignore[reportPrivateUsage]
                    )
                )
        except LookupError:  # create metrics scope when missing yet
            return cls(
                ScopeMetrics(
                    trace_id=trace_id,
                    scope=name,
                    logger=logger,
                )
            )

    @classmethod
    def record[Metric: State](
        cls,
        metric: Metric,
        /,
        *,
        merge: Callable[[Metric, Metric], Metric] = lambda lhs, rhs: rhs,
    ) -> None:
        try:  # catch exceptions - we don't wan't to blow up on metrics
            cls._context.get().record(metric, merge=merge)

        except Exception as exc:
            cls.log_error(
                "Failed to record metric: %s",
                type(metric).__qualname__,
                exception=exc,
            )

    # - LOGS -

    @classmethod
    def log_error(
        cls,
        message: str,
        /,
        *args: Any,
        exception: BaseException | None = None,
    ) -> None:
        try:
            cls._context.get().log(
                ERROR,
                message,
                *args,
                exception=exception,
            )

        except LookupError:
            getLogger().log(
                ERROR,
                message,
                *args,
                exc_info=exception,
            )

    @classmethod
    def log_warning(
        cls,
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
    ) -> None:
        try:
            cls._context.get().log(
                WARNING,
                message,
                *args,
                exception=exception,
            )

        except LookupError:
            getLogger().log(
                WARNING,
                message,
                *args,
                exc_info=exception,
            )

    @classmethod
    def log_info(
        cls,
        message: str,
        /,
        *args: Any,
    ) -> None:
        try:
            cls._context.get().log(
                INFO,
                message,
                *args,
            )

        except LookupError:
            getLogger().log(
                INFO,
                message,
                *args,
            )

    @classmethod
    def log_debug(
        cls,
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
    ) -> None:
        try:
            cls._context.get().log(
                DEBUG,
                message,
                *args,
                exception=exception,
            )

        except LookupError:
            getLogger().log(
                DEBUG,
                message,
                *args,
                exc_info=exception,
            )

    def __init__(
        self,
        metrics: ScopeMetrics,
    ) -> None:
        self._metrics: ScopeMetrics = metrics
        self._token: Token[ScopeMetrics] | None = None
        self._started: float | None = None
        self._finished: float | None = None

    def __enter__(self) -> None:
        assert (  # nosec: B101
            self._token is None and self._started is None
        ), "MetricsContext reentrance is not allowed"
        self._token = MetricsContext._context.set(self._metrics)
        self._started = monotonic()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert (  # nosec: B101
            self._token is not None and self._started is not None and self._finished is None
        ), "Unbalanced MetricsContext context enter/exit"
        self._finished = monotonic()
        MetricsContext._context.reset(self._token)
        self._token = None
