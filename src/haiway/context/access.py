from asyncio import (
    CancelledError,
    Task,
    TaskGroup,
    current_task,
    iscoroutinefunction,
)
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
)
from logging import Logger
from types import TracebackType
from typing import Any, final, overload

from haiway.context.disposables import Disposable, Disposables
from haiway.context.identifier import ScopeIdentifier
from haiway.context.observability import (
    Observability,
    ObservabilityAttribute,
    ObservabilityContext,
    ObservabilityLevel,
)
from haiway.context.state import ScopeState, StateContext
from haiway.context.tasks import TaskGroupContext
from haiway.state import State
from haiway.utils import mimic_function
from haiway.utils.stream import AsyncStream

__all__ = ("ctx",)


@final
class ScopeContext:
    """
    Context manager for executing code within a defined scope.

    ScopeContext manages scope-related data and behavior including identity, state,
    observability, and task coordination. It enforces immutability and provides both
    synchronous and asynchronous context management interfaces.

    This class should not be instantiated directly; use the ctx.scope() factory method
    to create scope contexts.
    """

    __slots__ = (
        "_disposables",
        "_identifier",
        "_observability_context",
        "_state_context",
        "_task_group_context",
    )

    def __init__(
        self,
        label: str,
        task_group: TaskGroup | None,
        state: tuple[State, ...],
        disposables: Disposables | None,
        observability: Observability | Logger | None,
    ) -> None:
        self._identifier: ScopeIdentifier
        object.__setattr__(
            self,
            "_identifier",
            ScopeIdentifier.scope(label),
        )
        # prepare state context to capture current state
        self._state_context: StateContext
        object.__setattr__(
            self,
            "_state_context",
            StateContext.updated(state),
        )
        self._disposables: Disposables | None
        object.__setattr__(
            self,
            "_disposables",
            disposables,
        )
        self._observability_context: ObservabilityContext
        object.__setattr__(
            self,
            "_observability_context",
            # pre-building observability context to ensure nested context registering
            ObservabilityContext.scope(
                self._identifier,
                observability=observability,
            ),
        )
        self._task_group_context: TaskGroupContext | None
        object.__setattr__(
            self,
            "_task_group_context",
            TaskGroupContext(task_group=task_group)
            if task_group is not None or self._identifier.is_root
            else None,
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
        assert (  # nosec: B101
            self._task_group_context is None or self._identifier.is_root
        ), "Can't enter synchronous context with task group"
        assert self._disposables is None, "Can't enter synchronous context with disposables"  # nosec: B101
        self._identifier.__enter__()
        self._observability_context.__enter__()
        self._state_context.__enter__()

        return self._observability_context.observability.trace_identifying(self._identifier).hex

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._state_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )
        self._observability_context.__exit__(
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
        self._observability_context.__enter__()

        if task_group := self._task_group_context:
            await task_group.__aenter__()

        # lazily initialize state to include disposables results
        if disposables := self._disposables:
            assert self._state_context._token is None  # nosec: B101
            object.__setattr__(
                self,
                "_state_context",
                StateContext(
                    state=ScopeState(
                        (
                            *self._state_context._state._state.values(),
                            *await disposables.prepare(),
                        )
                    ),
                ),
            )

        self._state_context.__enter__()

        return self._observability_context.observability.trace_identifying(self._identifier).hex

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if disposables := self._disposables:
            await disposables.dispose(
                exc_type=exc_type,
                exc_val=exc_val,
                exc_tb=exc_tb,
            )

        if task_group := self._task_group_context:
            await task_group.__aexit__(
                exc_type=exc_type,
                exc_val=exc_val,
                exc_tb=exc_tb,
            )

        self._state_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )

        self._observability_context.__exit__(
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
        function: Callable[Arguments, Coroutine[Any, Any, Result]],
    ) -> Callable[Arguments, Coroutine[Any, Any, Result]]: ...

    @overload
    def __call__[Result, **Arguments](
        self,
        function: Callable[Arguments, Result],
    ) -> Callable[Arguments, Result]: ...

    def __call__[Result, **Arguments](
        self,
        function: Callable[Arguments, Coroutine[Any, Any, Result]] | Callable[Arguments, Result],
    ) -> Callable[Arguments, Coroutine[Any, Any, Result]] | Callable[Arguments, Result]:
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
    """
    Static access to the current scope context.

    Provides static methods for accessing and manipulating the current scope context,
    including creating scopes, accessing state, logging, and task management.

    This class is not meant to be instantiated; all methods are static.
    """

    __slots__ = ()

    @staticmethod
    def trace_id(
        scope_identifier: ScopeIdentifier | None = None,
    ) -> str:
        """
        Get the trace identifier for the specified scope or current scope.

        The trace identifier is a unique identifier that can be used to correlate
        logs, events, and metrics across different components and services.

        Parameters
        ----------
        scope_identifier: ScopeIdentifier | None, default=None
            The scope identifier to get the trace ID for. If None, the current scope's
            trace ID is returned.

        Returns
        -------
        str
            The hexadecimal representation of the trace ID

        Raises
        ------
        RuntimeError
            If called outside of any scope context
        """
        return ObservabilityContext.trace_id(scope_identifier)

    @staticmethod
    def scope(
        label: str,
        /,
        *state: State | None,
        disposables: Disposables | Iterable[Disposable] | None = None,
        task_group: TaskGroup | None = None,
        observability: Observability | Logger | None = None,
    ) -> ScopeContext:
        """
        Prepare scope context with given parameters. When called within an existing context\
         it becomes nested with current context as its parent.

        Parameters
        ----------
        label: str
            name of the scope context

        *state: State | None
            state propagated within the scope context, will be merged with current state by\
             replacing current with provided on conflict.

        disposables: Disposables | Iterable[Disposable] | None
            disposables consumed within the context when entered. Produced state will automatically\
             be added to the scope state. Using asynchronous context is required if any disposables\
             were provided.

        task_group: TaskGroup | None
            task group used for spawning and joining tasks within the context. Root scope will
             always have task group created even when not set.

        observability: Observability | Logger | None = None
            observability solution responsible for recording and storing metrics, logs and events.\
             Assigning observability within existing context will result in an error.
            When not provided, logger with the scope name will be requested and used.

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
            task_group=task_group,
            state=tuple(element for element in state if element is not None),
            disposables=resolved_disposables,
            observability=observability,
        )

    @staticmethod
    def updated(
        *state: State | None,
    ) -> StateContext:
        """
        Update scope context with given state. When called within an existing context\
         it becomes nested with current context as its predecessor.

        Parameters
        ----------
        *state: State | None
            state propagated within the updated scope context, will be merged with current if any\
             by replacing current with provided on conflict

        Returns
        -------
        StateContext
            state part of context object intended to enter context manager with it
        """

        return StateContext.updated(element for element in state if element is not None)

    @staticmethod
    def disposables(
        *disposables: Disposable | None,
    ) -> Disposables:
        """
        Create a container for managing multiple disposable resources.

        Disposables are async context managers that can provide state objects and
        require proper cleanup. This method creates a Disposables container that
        manages multiple disposable resources as a single unit, handling their
        lifecycle and state propagation.

        Parameters
        ----------
        *disposables: Disposable | None
            Variable number of disposable resources to be managed together.
            None values are filtered out automatically.

        Returns
        -------
        Disposables
            A container that manages the lifecycle of all provided disposables
            and propagates their state to the context when used with ctx.scope()

        Examples
        --------
        Using disposables with database connections:

        >>> from haiway import ctx
        >>> async def main():
        ...
        ...     async with ctx.scope(
        ...         "database_work",
        ...         disposables=(database_connection(),)
        ...     ):
        ...         # ConnectionState is now available in context
        ...         conn_state = ctx.state(ConnectionState)
        ...         await conn_state.connection.execute("SELECT 1")
        """

        return Disposables(*(disposable for disposable in disposables if disposable is not None))

    @staticmethod
    def spawn[Result, **Arguments](
        function: Callable[Arguments, Coroutine[Any, Any, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Spawn an async task within current scope context task group. When called outside of context\
         it will spawn detached task instead.

        Parameters
        ----------
        function: Callable[Arguments, Coroutine[Any, Any, Result]]
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
    def stream[Element, **Arguments](
        source: Callable[Arguments, AsyncGenerator[Element, None]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> AsyncIterator[Element]:
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

        output_stream = AsyncStream[Element]()

        @ctx.scope("stream")
        async def stream() -> None:
            try:
                async for result in source(*args, **kwargs):
                    await output_stream.send(result)

            except BaseException as exc:
                output_stream.finish(exception=exc)

            else:
                output_stream.finish()

        TaskGroupContext.run(stream)
        return output_stream

    @staticmethod
    def check_cancellation() -> None:
        """
        Check if current asyncio task is cancelled, raises CancelledError if so.

        Allows cooperative cancellation by checking and responding to cancellation
        requests at appropriate points in the code.

        Raises
        ------
        CancelledError
            If the current task has been cancelled
        """

        if (task := current_task()) and task.cancelled():
            raise CancelledError()

    @staticmethod
    def cancel() -> None:
        """
        Cancel current asyncio task.

        Cancels the current running asyncio task. This will result in a CancelledError
        being raised in the task.

        Raises
        ------
        RuntimeError
            If called outside of an asyncio task
        """

        if task := current_task():
            task.cancel()

        else:
            raise RuntimeError("Attempting to cancel context out of asyncio task")

    @staticmethod
    def check_state[StateType: State](
        state: type[StateType],
        /,
        *,
        instantiate_defaults: bool = False,
    ) -> bool:
        """
        Check if state object is available in the current context.

        Verifies if state object of the specified type is available the current context.
        Instantiates requested state if needed and possible.

        Parameters
        ----------
        state: type[StateType]
            The type of state to check

        instantiate_defaults: bool = False
            Control if default value should be instantiated during check.

        Returns
        -------
        bool
            True if state is available, otherwise False.
        """
        return StateContext.check_state(
            state,
            instantiate_defaults=instantiate_defaults,
        )

    @staticmethod
    def state[StateType: State](
        state: type[StateType],
        /,
        default: StateType | None = None,
    ) -> StateType:
        """
        Access state from the current scope context by its type.

        Retrieves state objects that have been propagated within the current execution context.
        State objects are automatically made available through context scopes and disposables.
        If no matching state is found, creates a default instance if possible.

        Parameters
        ----------
        state: type[StateType]
            The State class type to retrieve from the current context
        default: StateType | None, default=None
            Optional default instance to return if state is not found in context.
            If None and no state is found, a new instance will be created if possible.

        Returns
        -------
        StateType
            The state instance from the current context or a default/new instance

        Raises
        ------
        RuntimeError
            If called outside of any scope context
        TypeError
            If no state is found and no default can be created

        Examples
        --------
        Accessing configuration state:

        >>> from haiway import ctx, State
        >>>
        >>> class ApiConfig(State):
        ...     base_url: str = "https://api.example.com"
        ...     timeout: int = 30
        >>>
        >>> async def fetch_data():
        ...     config = ctx.state(ApiConfig)
        ...     # Use config.base_url and config.timeout
        >>>
        >>> async with ctx.scope("api", ApiConfig(base_url="https://custom.api.com")):
        ...     await fetch_data()  # Uses custom config

        Accessing state with default:

        >>> cache_config = ctx.state(CacheConfig, default=CacheConfig(ttl=3600))

        Within service classes:

        >>> class UserService(State):
        ...     @classmethod
        ...     async def get_user(cls, user_id: str) -> User:
        ...         config = ctx.state(DatabaseConfig)
        ...         # Use config to connect to database
        """
        return StateContext.state(
            state,
            default=default,
        )

    @staticmethod
    def log_error(
        message: str,
        /,
        *args: Any,
        exception: BaseException | None = None,
        **extra: Any,
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

        ObservabilityContext.record_log(
            ObservabilityLevel.ERROR,
            message,
            *args,
            exception=exception,
            **extra,
        )

    @staticmethod
    def log_warning(
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
        **extra: Any,
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

        ObservabilityContext.record_log(
            ObservabilityLevel.WARNING,
            message,
            *args,
            exception=exception,
            **extra,
        )

    @staticmethod
    def log_info(
        message: str,
        /,
        *args: Any,
        **extra: Any,
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

        ObservabilityContext.record_log(
            ObservabilityLevel.INFO,
            message,
            *args,
            exception=None,
            **extra,
        )

    @staticmethod
    def log_debug(
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
        **extra: Any,
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

        ObservabilityContext.record_log(
            ObservabilityLevel.DEBUG, message, *args, exception=exception, **extra
        )

    @overload
    @staticmethod
    def record(
        level: ObservabilityLevel = ObservabilityLevel.DEBUG,
        /,
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        """
        Record observability data within the current scope context.

        This method has three different forms:
        1. Record standalone attributes
        2. Record a named event with optional attributes
        3. Record a metric with a value and optional unit and attributes

        Parameters
        ----------
        level: ObservabilityLevel
            Severity level for the recording (default: DEBUG)
        attributes: Mapping[str, ObservabilityAttribute]
            Key-value attributes to record
        event: str
            Name of the event to record
        metric: str
            Name of the metric to record
        value: float | int
            Numeric value of the metric
        unit: str | None
            Optional unit for the metric
        """
        ...

    @overload
    @staticmethod
    def record(
        level: ObservabilityLevel = ObservabilityLevel.DEBUG,
        /,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @overload
    @staticmethod
    def record(
        level: ObservabilityLevel = ObservabilityLevel.DEBUG,
        /,
        *,
        metric: str,
        value: float | int,
        unit: str | None = None,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @staticmethod
    def record(
        level: ObservabilityLevel = ObservabilityLevel.DEBUG,
        /,
        *,
        event: str | None = None,
        metric: str | None = None,
        value: float | int | None = None,
        unit: str | None = None,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None:
        if event is not None:
            assert metric is None  # nosec: B101
            ObservabilityContext.record_event(
                level,
                event,
                attributes=attributes or {},
            )

        elif metric is not None:
            assert event is None  # nosec: B101
            assert value is not None  # nosec: B101
            ObservabilityContext.record_metric(
                level,
                metric,
                value=value,
                unit=unit,
                attributes=attributes or {},
            )

        else:
            ObservabilityContext.record_attributes(
                level,
                attributes=attributes or {},
            )
