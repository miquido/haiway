from asyncio import (
    CancelledError,
    Task,
    current_task,
    iscoroutinefunction,
)
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Coroutine,
    Iterable,
)
from contextvars import Context, copy_context
from logging import Logger
from types import TracebackType
from typing import Any, final, overload

from haiway.context.disposables import Disposable, Disposables
from haiway.context.identifier import ScopeIdentifier
from haiway.context.logging import LoggerContext
from haiway.context.metrics import MetricsContext, MetricsHandler
from haiway.context.state import StateContext
from haiway.context.tasks import TaskGroupContext
from haiway.state import State
from haiway.utils import mimic_function

__all__ = [
    "ctx",
]


@final
class ScopeContext:
    __slots__ = (
        "_disposables",
        "_identifier",
        "_logger_context",
        "_metrics_context",
        "_state",
        "_state_context",
        "_task_group_context",
    )

    def __init__(
        self,
        label: str,
        logger: Logger | None,
        state: tuple[State, ...],
        disposables: Disposables | None,
        metrics: MetricsHandler | None,
    ) -> None:
        self._identifier: ScopeIdentifier
        object.__setattr__(
            self,
            "_identifier",
            ScopeIdentifier.scope(label),
        )
        self._logger_context: LoggerContext
        object.__setattr__(
            self,
            "_logger_context",
            LoggerContext(
                self._identifier,
                logger=logger,
            ),
        )
        # postponing task group creation to include only when needed
        self._task_group_context: TaskGroupContext
        # postponing state creation to include disposables state when prepared
        self._state_context: StateContext
        self._state: tuple[State, ...]
        object.__setattr__(
            self,
            "_state",
            state,
        )
        self._disposables: Disposables | None
        object.__setattr__(
            self,
            "_disposables",
            disposables,
        )
        self._metrics_context: MetricsContext
        object.__setattr__(
            self,
            "_metrics_context",
            # pre-building metrics context to ensure nested context registering
            MetricsContext.scope(
                self._identifier,
                metrics=metrics,
            ),
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

    def __enter__(self) -> str:
        assert self._disposables is None, "Can't enter synchronous context with disposables"  # nosec: B101
        self._identifier.__enter__()
        self._logger_context.__enter__()
        # lazily initialize state
        object.__setattr__(
            self,
            "_state_context",
            StateContext.updated(self._state),
        )
        self._state_context.__enter__()
        self._metrics_context.__enter__()

        return self._identifier.trace_id

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._metrics_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._state_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._logger_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._identifier.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

    async def __aenter__(self) -> str:
        self._identifier.__enter__()
        self._logger_context.__enter__()
        # lazily initialize group when needed
        object.__setattr__(
            self,
            "_task_group_context",
            TaskGroupContext(),
        )
        await self._task_group_context.__aenter__()

        # lazily initialize state to include disposables results
        if self._disposables is not None:
            object.__setattr__(
                self,
                "_state_context",
                StateContext.updated(
                    (
                        *self._state,
                        *await self._disposables.__aenter__(),
                    )
                ),
            )

        else:
            object.__setattr__(
                self,
                "_state_context",
                StateContext.updated(self._state),
            )

        self._state_context.__enter__()
        self._metrics_context.__enter__()

        return self._identifier.trace_id

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._disposables is not None:
            await self._disposables.__aexit__(
                exc_type=exc_type,
                exc_val=exc_val,
                exc_tb=exc_tb,
            )

        await self._task_group_context.__aexit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._metrics_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._state_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._logger_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._identifier.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

    @overload
    def __call__[Result, **Arguments](
        self,
        function: Callable[Arguments, Coroutine[None, None, Result]],
    ) -> Callable[Arguments, Coroutine[None, None, Result]]: ...

    @overload
    def __call__[Result, **Arguments](
        self,
        function: Callable[Arguments, Result],
    ) -> Callable[Arguments, Result]: ...

    def __call__[Result, **Arguments](
        self,
        function: Callable[Arguments, Coroutine[None, None, Result]] | Callable[Arguments, Result],
    ) -> Callable[Arguments, Coroutine[None, None, Result]] | Callable[Arguments, Result]:
        if iscoroutinefunction(function):

            async def async_context(
                *args: Arguments.args,
                **kwargs: Arguments.kwargs,
            ) -> Result:
                async with self:
                    return await function(*args, **kwargs)

            return mimic_function(function, within=async_context)

        else:

            def sync_context(
                *args: Arguments.args,
                **kwargs: Arguments.kwargs,
            ) -> Result:
                with self:
                    return function(*args, **kwargs)  # pyright: ignore[reportReturnType]

            return mimic_function(function, within=sync_context)  # pyright: ignore[reportReturnType]


@final
class ctx:
    __slots__ = ()

    @staticmethod
    def trace_id() -> str:
        """
        Get the current context trace identifier.
        """

        return ScopeIdentifier.current_trace_id()

    @staticmethod
    def scope(
        label: str,
        /,
        *state: State,
        disposables: Disposables | Iterable[Disposable] | None = None,
        logger: Logger | None = None,
        metrics: MetricsHandler | None = None,
    ) -> ScopeContext:
        """
        Prepare scope context with given parameters. When called within an existing context\
         it becomes nested with current context as its parent.

        Parameters
        ----------
        label: str
            name of the scope context

        *state: State | Disposable
            state propagated within the scope context, will be merged with current state by\
             replacing current with provided on conflict.

        disposables: Disposables | Iterable[Disposable] | None
            disposables consumed within the context when entered. Produced state will automatically\
             be added to the scope state. Using asynchronous context is required if any disposables\
             were provided.

        logger: Logger | None
            logger used within the scope context, when not provided current logger will be used\
             if any, otherwise the logger with the scope name will be requested.

        metrics_store: MetricsStore | None = None
            metrics storage solution responsible for recording and storing metrics.\
             Metrics recroding will be ignored if storage is not provided.
            Assigning metrics_store within existing context will result in an error.

        Returns
        -------
        ScopeContext
            context object intended to enter context manager with.\
             context manager will provide trace_id of current context.
        """

        resolved_disposables: Disposables | None
        match disposables:
            case None:
                resolved_disposables = None

            case Disposables() as disposables:
                resolved_disposables = disposables

            case iterable:
                resolved_disposables = Disposables(*iterable)

        return ScopeContext(
            label=label,
            logger=logger,
            state=state,
            disposables=resolved_disposables,
            metrics=metrics,
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
    def stream[Result, **Arguments](
        source: Callable[Arguments, AsyncGenerator[Result, None]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> AsyncIterator[Result]:
        """
        Stream results produced by a generator within the proper context state.

        Parameters
        ----------
        source: Callable[Arguments, AsyncGenerator[Result, None]]
            generator streamed as the result

        *args: Arguments.args
            positional arguments passed to generator call

        **kwargs: Arguments.kwargs
            keyword arguments passed to generator call

        Returns
        -------
        AsyncIterator[Result]
            iterator for accessing generated results
        """

        # prepare context snapshot
        context_snapshot: Context = copy_context()

        # prepare nested context
        streaming_context: ScopeContext = ctx.scope(
            getattr(
                source,
                "__name__",
                "streaming",
            )
        )

        async def generator() -> AsyncGenerator[Result, None]:
            async with streaming_context:
                async for result in source(*args, **kwargs):
                    yield result

        # finally return it as an iterator
        return context_snapshot.run(generator)

    @staticmethod
    def check_cancellation() -> None:
        """
        Check if current asyncio task is cancelled, raises CancelledError if so.
        """

        if (task := current_task()) and task.cancelled():
            raise CancelledError()

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
    def record(
        metric: State,
        /,
    ) -> None:
        """
        Record metric within current scope context.

        Parameters
        ----------
        metric: State
            value of metric to be recorded. When a metric implements __add__ it will be added to\
             current value if any, otherwise subsequent calls may replace existing value.

        Returns
        -------
        None
        """

        MetricsContext.record(metric)

    @overload
    @staticmethod
    async def read[Metric: State](
        metric: type[Metric],
        /,
        *,
        merged: bool = False,
    ) -> Metric | None: ...

    @overload
    @staticmethod
    async def read[Metric: State](
        metric: type[Metric],
        /,
        *,
        merged: bool = False,
        default: Metric,
    ) -> Metric: ...

    @staticmethod
    async def read[Metric: State](
        metric: type[Metric],
        /,
        *,
        merged: bool = False,
        default: Metric | None = None,
    ) -> Metric | None:
        """
        Read metric within current scope context.

        Parameters
        ----------
        metric: type[Metric]
            type of metric to be read from current context.

        merged: bool
            control wheather to merge metrics from nested scopes (True)\
             or access only the current scope value (False) without combining them

        default: Metric | None
            default value to return when metric was not recorded yet.

        Returns
        -------
        Metric | None
        """

        value: Metric | None = await MetricsContext.read(
            metric,
            merged=merged,
        )
        if value is None:
            return default

        return value

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

        LoggerContext.log_error(
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

        LoggerContext.log_warning(
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

        LoggerContext.log_info(
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

        LoggerContext.log_debug(
            message,
            *args,
            exception=exception,
        )
