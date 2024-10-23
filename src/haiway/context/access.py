from asyncio import (
    Task,
    current_task,
)
from collections.abc import (
    Callable,
    Coroutine,
)
from logging import Logger
from types import TracebackType
from typing import Any, final

from haiway.context.metrics import MetricsContext, ScopeMetrics
from haiway.context.state import StateContext
from haiway.context.tasks import TaskGroupContext
from haiway.state import State
from haiway.utils import freeze

__all__ = [
    "ctx",
]


@final
class ScopeContext:
    def __init__(
        self,
        task_group: TaskGroupContext,
        state: StateContext,
        metrics: MetricsContext,
        completion: Callable[[ScopeMetrics], Coroutine[None, None, None]] | None,
    ) -> None:
        self._task_group: TaskGroupContext = task_group
        self._state: StateContext = state
        self._metrics: MetricsContext = metrics
        self._completion: Callable[[ScopeMetrics], Coroutine[None, None, None]] | None = completion

        freeze(self)

    def __enter__(self) -> None:
        assert self._completion is None, "Can't enter synchronous context with completion"  # nosec: B101

        self._state.__enter__()
        self._metrics.__enter__()

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._metrics.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._state.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

    async def __aenter__(self) -> None:
        self._state.__enter__()
        self._metrics.__enter__()
        await self._task_group.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._task_group.__aexit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._metrics.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._state.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        if completion := self._completion:
            await completion(self._metrics._metrics)  # pyright: ignore[reportPrivateUsage]


@final
class ctx:
    @staticmethod
    def scope(
        name: str,
        /,
        *state: State,
        logger: Logger | None = None,
        trace_id: str | None = None,
        completion: Callable[[ScopeMetrics], Coroutine[None, None, None]] | None = None,
    ) -> ScopeContext:
        """
        Access scope context with given parameters. When called within an existing context\
         it becomes nested with current context as its predecessor.

        Parameters
        ----------
        name: Value
            name of the scope context

        *state: State
            state propagated within the scope context, will be merged with current if any\
             by replacing current with provided on conflict

        logger: Logger | None
            logger used within the scope context, when not provided current logger will be used\
             if any, otherwise the logger with the scope name will be requested.

        trace_id: str | None = None
            tracing identifier included in logs produced within the scope context, when not\
             provided current identifier will be used if any, otherwise it random id will\
             be generated

        completion: Callable[[ScopeMetrics], Coroutine[None, None, None]] | None = None
            completion callback called on exit from the scope granting access to finished\
             scope metrics. Completion is called outside of the context when its metrics is\
             already finished. Make sure to avoid any long operations within the completion.

        Returns
        -------
        ScopeContext
            context object intended to enter context manager with it
        """

        return ScopeContext(
            task_group=TaskGroupContext(),
            metrics=MetricsContext.scope(
                name,
                logger=logger,
                trace_id=trace_id,
            ),
            state=StateContext.updated(state),
            completion=completion,
        )

    @staticmethod
    def updated(
        *state: State,
    ) -> StateContext:
        """
        Update scope context with given state. When called within an existing context\
         it becomes nested with current context as its predecessor.

        Parameters
        ----------
        *state: State
            state propagated within the updated scope context, will be merged with current if any\
             by replacing current with provided on conflict

        Returns
        -------
        StateContext
            state part of context object intended to enter context manager with it
        """

        return StateContext.updated(state)

    @staticmethod
    def spawn[Result, **Arguments](
        function: Callable[Arguments, Coroutine[None, None, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Spawn an async task within current scope context task group. When called outside of context\
         it will spawn detached task instead.

        Parameters
        ----------
        function: Callable[Arguments, Coroutine[None, None, Result]]
            function to be called within the task group

        *args: Arguments.args
            positional arguments passed to function call

        **kwargs: Arguments.kwargs
            keyword arguments passed to function call

        Returns
        -------
        Task[Result]
            task for tracking function execution and result
        """

        return TaskGroupContext.run(function, *args, **kwargs)

    @staticmethod
    def cancel() -> None:
        """
        Cancel current asyncio task
        """

        if task := current_task():
            task.cancel()

        else:
            raise RuntimeError("Attempting to cancel context out of asyncio task")

    @staticmethod
    def state[StateType: State](
        state: type[StateType],
        /,
        default: StateType | None = None,
    ) -> StateType:
        """
        Access current scope context state by its type. If there is no matching state defined\
         default value will be created if able, an exception will raise otherwise.

        Parameters
        ----------
        state: type[StateType]
            type of requested state

        Returns
        -------
        StateType
            resolved state instance
        """
        return StateContext.current(
            state,
            default=default,
        )

    @staticmethod
    def record[Metric: State](
        metric: Metric,
        /,
        merge: Callable[[Metric, Metric], Metric] = lambda lhs, rhs: rhs,
    ) -> None:
        """
        Record metric within current scope context.

        Parameters
        ----------
        metric: MetricType
            value of metric to be recorded

        merge: Callable[[MetricType, MetricType], MetricType] = lambda lhs, rhs: rhs
            merge method used on to resolve conflicts when a metric of the same type\
             was already recorded. When not provided value will be override current if any.

        Returns
        -------
        None
        """

        MetricsContext.record(
            metric,
            merge=merge,
        )

    @staticmethod
    def log_error(
        message: str,
        /,
        *args: Any,
        exception: BaseException | None = None,
    ) -> None:
        """
        Log using ERROR level within current scope context. When there is no current scope\
         root logger will be used without additional details.

        Parameters
        ----------
        message: str
            message to be written to log

        *args: Any
            message format arguments

        exception: BaseException | None = None
            exception associated with log, when provided full stack trace will be recorded

        Returns
        -------
        None
        """

        MetricsContext.log_error(
            message,
            *args,
            exception=exception,
        )

    @staticmethod
    def log_warning(
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
    ) -> None:
        """
        Log using WARNING level within current scope context. When there is no current scope\
         root logger will be used without additional details.

        Parameters
        ----------
        message: str
            message to be written to log

        *args: Any
            message format arguments

        exception: BaseException | None = None
            exception associated with log, when provided full stack trace will be recorded

        Returns
        -------
        None
        """

        MetricsContext.log_warning(
            message,
            *args,
            exception=exception,
        )

    @staticmethod
    def log_info(
        message: str,
        /,
        *args: Any,
    ) -> None:
        """
        Log using INFO level within current scope context. When there is no current scope\
         root logger will be used without additional details.

        Parameters
        ----------
        message: str
            message to be written to log

        *args: Any
            message format arguments

        Returns
        -------
        None
        """

        MetricsContext.log_info(
            message,
            *args,
        )

    @staticmethod
    def log_debug(
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
    ) -> None:
        """
        Log using DEBUG level within current scope context. When there is no current scope\
         root logger will be used without additional details.

        Parameters
        ----------
        message: str
            message to be written to log

        *args: Any
            message format arguments

        exception: BaseException | None = None
            exception associated with log, when provided full stack trace will be recorded

        Returns
        -------
        None
        """

        MetricsContext.log_debug(
            message,
            *args,
            exception=exception,
        )
