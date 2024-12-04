from asyncio import (
    AbstractEventLoop,
    Future,
    gather,
    get_event_loop,
    iscoroutinefunction,
    run_coroutine_threadsafe,
)
from collections.abc import Callable, Coroutine
from contextvars import ContextVar, Token
from copy import copy
from itertools import chain
from logging import DEBUG, ERROR, INFO, WARNING, Logger, getLogger
from time import monotonic
from types import TracebackType
from typing import Any, Self, cast, final, overload
from uuid import uuid4

from haiway.state import State
from haiway.types import MISSING, Missing, not_missing
from haiway.utils import freeze

__all__ = [
    "MetricsContext",
    "ScopeMetrics",
]


@final
class ScopeMetrics:
    def __init__(
        self,
        *,
        trace_id: str | None,
        scope: str,
        logger: Logger | None,
        parent: Self | None,
        completion: Callable[[Self], Coroutine[None, None, None]] | Callable[[Self], None] | None,
    ) -> None:
        self.trace_id: str = trace_id or uuid4().hex
        self.identifier: str = uuid4().hex
        self.label: str = scope
        self._logger_prefix: str = (
            f"[{self.trace_id}] [{scope}] [{self.identifier}]"
            if scope
            else f"[{self.trace_id}] [{self.identifier}]"
        )
        self._logger: Logger = logger or getLogger(name=scope)
        self._parent: Self | None = parent if parent else None
        self._metrics: dict[type[State], State] = {}
        self._nested: list[ScopeMetrics] = []
        self._timestamp: float = monotonic()
        self._finished: bool = False
        self._loop: AbstractEventLoop = get_event_loop()
        self._completed: Future[float] = self._loop.create_future()

        if parent := parent:
            parent._nested.append(self)

        freeze(self)

        if completion := completion:
            metrics: Self = self
            if iscoroutinefunction(completion):

                def callback(_: Future[float]) -> None:
                    run_coroutine_threadsafe(
                        completion(metrics),
                        metrics._loop,
                    )

            else:

                def callback(_: Future[float]) -> None:
                    completion(metrics)

            self._completed.add_done_callback(callback)

    def __del__(self) -> None:
        assert self.is_completed, "Deinitializing not completed scope metrics"  # nosec: B101

    def __str__(self) -> str:
        return f"{self.label}[{self.identifier}]@[{self.trace_id}]"

    def metrics(
        self,
        *,
        merge: Callable[[State | Missing, State], State | Missing] | None = None,
    ) -> list[State]:
        if not merge:
            return list(self._metrics.values())

        metrics: dict[type[State], State] = copy(self._metrics)
        for metric in chain.from_iterable(nested.metrics(merge=merge) for nested in self._nested):
            metric_type: type[State] = type(metric)
            merged: State | Missing = merge(
                metrics.get(  # current
                    metric_type,
                    MISSING,
                ),
                metric,  # received
            )

            if not_missing(merged):
                metrics[metric_type] = merged

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
    def is_completed(self) -> bool:
        return self._completed.done() and all(nested.is_completed for nested in self._nested)

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

    def _finish(self) -> None:
        assert (  # nosec: B101
            not self._completed.done()
        ), "Invalid state - called finish on already completed scope"

        assert (  # nosec: B101
            not self._finished
        ), "Invalid state - called completion on already finished scope"

        self._finished = True  # self is now finished

        self._complete_if_able()

    def _complete_if_able(self) -> None:
        assert (  # nosec: B101
            not self._completed.done()
        ), "Invalid state - called complete on already completed scope"

        if not self._finished:
            return  # wait for finishing self

        if any(not nested.is_completed for nested in self._nested):
            return  # wait for completing all nested scopes

        # set completion time
        self._completed.set_result(monotonic() - self._timestamp)

        # notify parent about completion
        if parent := self._parent:
            parent._complete_if_able()

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
            f"{self._logger_prefix} {message}",
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
        completion: Callable[[ScopeMetrics], Coroutine[None, None, None]]
        | Callable[[ScopeMetrics], None]
        | None,
    ) -> Self:
        current: ScopeMetrics
        try:  # check for current scope context
            current = cls._context.get()

        except LookupError:
            # create metrics scope when missing yet
            return cls(
                ScopeMetrics(
                    trace_id=trace_id,
                    scope=name,
                    logger=logger,
                    parent=None,
                    completion=completion,
                )
            )

        # or create nested metrics otherwise
        return cls(
            ScopeMetrics(
                trace_id=trace_id,
                scope=name,
                logger=logger or current._logger,  # pyright: ignore[reportPrivateUsage]
                parent=current,
                completion=completion,
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

    def __enter__(self) -> None:
        assert (  # nosec: B101
            self._token is None and not self._metrics._finished  # pyright: ignore[reportPrivateUsage]
        ), "MetricsContext reentrance is not allowed"
        self._token = MetricsContext._context.set(self._metrics)
        self._metrics.log(INFO, "Started...")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert (  # nosec: B101
            self._token is not None
        ), "Unbalanced MetricsContext context enter/exit"
        MetricsContext._context.reset(self._token)
        self._metrics._finish()  # pyright: ignore[reportPrivateUsage]
        self._token = None
        self._metrics.log(INFO, f"...finished after {self._metrics.time:.2f}s")
