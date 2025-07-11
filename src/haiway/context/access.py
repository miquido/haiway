from asyncio import (
    CancelledError,
    Task,
    TaskGroup,
    current_task,
)
from collections.abc import (
    AsyncGenerator,
    AsyncIterator,
    Callable,
    Collection,
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
from haiway.context.presets import (
    ContextPresets,
    ContextPresetsRegistry,
    ContextPresetsRegistryContext,
)
from haiway.context.state import ScopeState, StateContext
from haiway.context.tasks import TaskGroupContext
from haiway.state import Immutable, State
from haiway.utils.stream import AsyncStream

__all__ = ("ctx",)


class ScopeContext(Immutable):
    """
    Context manager for executing code within a defined scope.

    ScopeContext manages scope-related data and behavior including identity, state,
    observability, and task coordination. It enforces immutability and provides both
    synchronous and asynchronous context management interfaces.

    This class should not be instantiated directly; use the ctx.scope() factory method
    to create scope contexts.
    """

    _identifier: ScopeIdentifier
    _state: Collection[State]
    _captured_state: Collection[State]
    _resolved_state_context: StateContext | None
    _disposables: Disposables | None
    _presets: ContextPresets | None
    _presets_disposables: Disposables | None
    _observability_context: ObservabilityContext
    _task_group_context: TaskGroupContext | None

    def __init__(
        self,
        name: str,
        task_group: TaskGroup | None,
        state: tuple[State, ...],
        disposables: Disposables | None,
        observability: Observability | Logger | None,
    ) -> None:
        object.__setattr__(
            self,
            "_identifier",
            ScopeIdentifier.scope(name),
        )
        # store explicit state separately for priority control
        object.__setattr__(
            self,
            "_state",
            state,
        )
        # capture current contextual state (without new additions)
        object.__setattr__(
            self,
            "_captured_state",
            StateContext.current_state(),
        )
        # placeholder for temporary, resolved state context
        object.__setattr__(
            self,
            "_resolved_state_context",
            None,
        )
        object.__setattr__(
            self,
            "_disposables",
            disposables,
        )
        object.__setattr__(
            self,
            "_presets",
            ContextPresetsRegistryContext.select(name),
        )
        object.__setattr__(
            self,
            "_presets_disposables",
            None,
        )
        object.__setattr__(
            self,
            "_observability_context",
            # pre-building observability context to ensure nested context registering
            ObservabilityContext.scope(
                self._identifier,
                observability=observability,
            ),
        )
        object.__setattr__(
            self,
            "_task_group_context",
            TaskGroupContext(task_group=task_group)
            if task_group is not None or self._identifier.is_root
            else None,
        )

    def __enter__(self) -> str:
        assert (  # nosec: B101
            self._task_group_context is None or self._identifier.is_root
        ), "Can't enter synchronous context with task group"
        assert self._disposables is None, "Can't enter synchronous context with disposables"  # nosec: B101
        assert self._resolved_state_context is None  # nosec: B101
        assert self._presets is None, "Can't enter synchronous context with presets"  # nosec: B101
        self._identifier.__enter__()
        self._observability_context.__enter__()
        # For sync context, only use explicit state (no presets or disposables allowed)
        resolved_state_context: StateContext = StateContext(
            _state=ScopeState((*self._captured_state, *self._state))
        )
        object.__setattr__(
            self,
            "_resolved_state_context",
            resolved_state_context,
        )
        resolved_state_context.__enter__()

        return str(self._observability_context.observability.trace_identifying(self._identifier))

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._resolved_state_context is not None  # nosec: B101
        self._resolved_state_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )
        object.__setattr__(
            self,
            "_resolved_state_context",
            None,
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
        assert self._presets_disposables is None  # nosec: B101
        assert self._resolved_state_context is None  # nosec: B101
        self._identifier.__enter__()
        self._observability_context.__enter__()

        if task_group := self._task_group_context:
            await task_group.__aenter__()

        # Collect all state sources in priority order (lowest to highest priority)
        collected_state: list[State] = []

        # 1. Add contextual state first (lowest priority)
        collected_state.extend(self._captured_state)

        # 2. Add preset state (low priority, overrides contextual)
        if self._presets is not None:
            presets_disposables: Disposables = await self._presets.prepare()
            object.__setattr__(
                self,
                "_presets_disposables",
                presets_disposables,
            )
            collected_state.extend(await presets_disposables.prepare())

        # 3. Add explicit disposables state (medium priority)
        if self._disposables is not None:
            collected_state.extend(await self._disposables.prepare())

        # 4. Add explicit state last (highest priority)
        collected_state.extend(self._state)
        # Create resolved state context with all collected state
        resolved_state_context: StateContext = StateContext(
            _state=ScopeState(tuple(collected_state))
        )

        resolved_state_context.__enter__()
        object.__setattr__(
            self,
            "_resolved_state_context",
            resolved_state_context,
        )

        return str(self._observability_context.observability.trace_identifying(self._identifier))

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._resolved_state_context is not None  # nosec: B101
        if self._disposables is not None:
            await self._disposables.dispose(
                exc_type=exc_type,
                exc_val=exc_val,
                exc_tb=exc_tb,
            )

        if self._presets_disposables is not None:
            await self._presets_disposables.dispose(
                exc_type=exc_type,
                exc_val=exc_val,
                exc_tb=exc_tb,
            )
            object.__setattr__(
                self,
                "_presets_disposables",
                None,
            )

        if task_group := self._task_group_context:
            await task_group.__aexit__(
                exc_type=exc_type,
                exc_val=exc_val,
                exc_tb=exc_tb,
            )

        self._resolved_state_context.__exit__(
            exc_type=exc_type,
            exc_val=exc_val,
            exc_tb=exc_tb,
        )
        object.__setattr__(
            self,
            "_resolved_state_context",
            None,
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
    def presets(
        *presets: ContextPresets,
    ) -> ContextPresetsRegistryContext:
        """
        Create a context manager for a preset registry.

        This method creates a registry of context presets that can be used within
        nested scopes. Presets allow you to define reusable combinations of state
        and disposables that can be referenced by name when creating scopes.

        When entering this context manager, the provided presets become available
        for use with ctx.scope(). The presets are looked up by their name when
        creating scopes.

        Parameters
        ----------
        *presets: ContextPresets
            Variable number of preset configurations to register. Each preset
            must have a unique name within the registry.

        Returns
        -------
        ContextPresetsRegistryContext
            A context manager that makes the presets available in nested scopes

        Examples
        --------
        Basic preset usage:

        >>> from haiway import ctx, State
        >>> from haiway.context import ContextPresets
        >>>
        >>> class ApiConfig(State):
        ...     base_url: str
        ...     timeout: int = 30
        >>>
        >>> # Define presets
        >>> dev_preset = ContextPresets(
        ...     name="development",
        ...     _state=[ApiConfig(base_url="https://dev-api.example.com")]
        ... )
        >>>
        >>> prod_preset = ContextPresets(
        ...     name="production",
        ...     _state=[ApiConfig(base_url="https://api.example.com", timeout=60)]
        ... )
        >>>
        >>> # Use presets
        >>> with ctx.presets(dev_preset, prod_preset):
        ...     async with ctx.scope("development"):
        ...         config = ctx.state(ApiConfig)
        ...         assert config.base_url == "https://dev-api.example.com"

        Nested preset registries:

        >>> base_presets = [dev_preset, prod_preset]
        >>> override_preset = ContextPresets(
        ...     name="development",
        ...     _state=[ApiConfig(base_url="https://staging.example.com")]
        ... )
        >>>
        >>> with ctx.presets(*base_presets):
        ...     # Outer registry has dev and prod presets
        ...     with ctx.presets(override_preset):
        ...         # Inner registry overrides dev preset
        ...         async with ctx.scope("development"):
        ...             config = ctx.state(ApiConfig)
        ...             assert config.base_url == "https://staging.example.com"

        See Also
        --------
        ContextPresets : For creating individual preset configurations
        ctx.scope : For creating scopes that can use presets
        """
        return ContextPresetsRegistryContext(
            registry=ContextPresetsRegistry(
                presets=presets,
            ),
        )

    @staticmethod
    def scope(
        name: str,
        /,
        *state: State | None,
        disposables: Disposables | Iterable[Disposable] | None = None,
        task_group: TaskGroup | None = None,
        observability: Observability | Logger | None = None,
    ) -> ScopeContext:
        """
        Prepare scope context with given parameters. When called within an existing context\
         it becomes nested with current context as its parent.

        State Priority System
        ---------------------
        State resolution follows a 4-layer priority system (highest to lowest):

        1. **Explicit state** (passed to ctx.scope()) - HIGHEST priority
        2. **Explicit disposables** (passed to ctx.scope()) - medium priority
        3. **Preset state** (from presets) - low priority
        4. **Contextual state** (from parent contexts) - LOWEST priority

        When state types conflict, higher priority sources override lower priority ones.
        State objects are resolved by type, with the highest priority instance winning.

        Parameters
        ----------
        name: str
            name of the scope context, can be associated with state presets

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
            name=name,
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
        stream_scope: ScopeContext = ctx.scope("stream")

        async def stream() -> None:
            async with stream_scope:
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
