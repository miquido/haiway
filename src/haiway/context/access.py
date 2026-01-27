from asyncio import (
    CancelledError,
    Task,
    current_task,
)
from collections.abc import (
    AsyncGenerator,
    AsyncIterable,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
)
from contextlib import AbstractAsyncContextManager, AbstractContextManager
from logging import Logger
from typing import Any, NoReturn, final, overload

from haiway.attributes import State
from haiway.context.disposables import ContextDisposables, Disposable, Disposables, DisposableState
from haiway.context.events import ContextEvents, EventsSubscription
from haiway.context.observability import (
    ContextObservability,
    Observability,
    ObservabilityAttribute,
    ObservabilityLevel,
    ObservabilityMetricKind,
)

# Import after other imports to avoid circular dependencies
from haiway.context.presets import ContextPresets, ContextPresetsRegistry
from haiway.context.scope import ContextScope
from haiway.context.state import ContextState
from haiway.context.tasks import (
    BackgroundTaskGroup,
    ContextTaskGroup,
)

__all__ = ("ctx",)


@final  # static methods namespace
class ctx:
    """
    Static access to the current scope context.
    """

    @staticmethod
    def trace_id() -> str:
        """
        Get the trace identifier of the current scope.

        The trace identifier is a unique identifier that can be used to correlate
        logs, events, and metrics across different components and services.

        Returns
        -------
        str
            The string representation of the current trace ID
        """
        return ContextObservability.trace_id()

    @staticmethod
    def presets(
        *presets: ContextPresets,
    ) -> AbstractContextManager[None]:
        """
        Create a context manager for the presets registry.

        This method creates a registry of context presets that can be used within
        nested scopes. Presets allow you to define reusable combinations of state
        and disposables that can be referenced by name when creating scopes.

        When entering this context manager, the provided presets become available
        for use with ctx.scope(). The presets are looked up by their name when
        creating scopes.

        Note: For single preset usage, consider passing the preset directly to
        ctx.scope() instead of using this registry.

        Parameters
        ----------
        *presets: ContextPresets
            Variable number of preset configurations to register. Each preset
            must have a unique name within the registry.

        Returns
        -------
        AbstractContextManager[None]
            A context manager that makes the presets available in nested scopes

        Examples
        --------
        Basic preset usage:

        >>> from haiway import ctx, State, ContextPresets
        >>>
        >>> class ApiConfig(State):
        ...     base_url: str
        ...     timeout: int = 30
        >>>
        >>> # Define presets
        >>> dev_preset = ContextPresets.of(
        ...     "development",
        ...     ApiConfig(base_url="https://dev-api.example.com")
        ... )
        >>>
        >>> prod_preset = ContextPresets.of(
        ...     "production",
        ...     ApiConfig(base_url="https://api.example.com", timeout=60)
        ... )
        >>>
        >>> # Use presets
        >>> with ctx.presets(dev_preset, prod_preset):
        ...     async with ctx.scope("development"):
        ...         config = ctx.state(ApiConfig)
        ...         assert config.base_url == "https://dev-api.example.com"
        """
        return ContextPresetsRegistry(presets=presets)

    @staticmethod
    def scope(
        scope: ContextPresets | str,
        /,
        *state: State | None,
        disposables: Iterable[Disposable | None] | None = None,
        observability: Observability | Logger | None = None,
        isolated: bool = False,
    ) -> AbstractAsyncContextManager[str]:
        """
        Prepare scope context with given parameters.

        When called within an existing context, it becomes nested with current context
        as its parent.

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
        scope: ContextPresets | str
            Either a name of the scope context (can be associated with state presets with
            matching name from preset registry), or a context preset to be used directly
            within the scope context. When a preset is provided directly, its state and
            disposables will be applied to the scope with lower priority than explicit state.

        *state: State | None
            state propagated within the scope context, will be merged with current state by
            replacing current with provided on conflict.

        disposables: Iterable[Disposable | None] | None
            disposables consumed within the context when entered. Produced state will automatically
            be added to the scope state. Using asynchronous context is required if any disposables
            were provided.

        observability: Observability | Logger | None = None
            observability solution responsible for recording and storing metrics, logs and events.
            Assigning observability within existing context will result in an error.
            When not provided, logger with the scope name will be requested and used.

        isolated: bool = False
            control if scope inheritance and task group will be isolated from parent.
            When set to True, context will use a separate TaskGroup and will not propagate its
            context/state to the parent scope. Isolation does not affect event propagation within
            the context. Root scope is always isolated.

        Returns
        -------
        AbstractAsyncContextManager[str]
            context manager object intended to enter the scope with.
            context manager will provide trace_id of current scope.
        """

        name: str
        presets: ContextPresets | None
        if isinstance(scope, ContextPresets):
            name = scope.name
            presets = scope

        else:
            name = scope
            presets = None

        context_disposables: Disposables
        if disposables is None:
            context_disposables = Disposables.of(
                DisposableState.of(*(element for element in state if element is not None))
            )

        else:
            context_disposables = Disposables.of(
                *disposables,
                DisposableState.of(*(element for element in state if element is not None)),
            )

        return ContextScope(
            name=name,
            presets=presets,
            disposables=context_disposables,
            observability=observability,
            isolated=isolated,
        )

    @staticmethod
    def updating(
        *state: State | None,
    ) -> AbstractContextManager[None]:
        """
        Update scope context with given state.

        When called within an existing context, it becomes nested with current
        context as its parent.

        Parameters
        ----------
        *state: State | None
            state propagated within the updated scope context, will be merged with current if any
            by replacing current with provided on conflict

        Returns
        -------
        AbstractContextManager[None]
            context manager object intended to enter updated state context with it
        """

        return ContextState.updating(state)

    @staticmethod
    def disposables(
        *disposables: Disposable | None,
    ) -> AbstractAsyncContextManager[None]:
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
        AbstractAsyncContextManager[None]
            A context manager that manages the lifecycle of all provided disposables
            and propagates their state to the context, similar to ctx.scope()

        Examples
        --------
        Using disposables with database connections:

        >>> from haiway import ctx
        >>> async def main():
        ...
        ...     async with ctx.disposables(
        ...         database_connection(),
        ...     ):
        ...         # ConnectionState is now available in context
        ...         conn_state = ctx.state(ConnectionState)
        ...         await conn_state.connection.execute("SELECT 1")
        """

        return ContextDisposables(disposables)

    @overload
    @staticmethod
    def spawn[Result](
        coro: Coroutine[Any, Any, Result],
        /,
    ) -> Task[Result]: ...

    @overload
    @staticmethod
    def spawn[Result, **Arguments](
        coro: Callable[Arguments, Coroutine[Any, Any, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]: ...

    @staticmethod
    def spawn[Result, **Arguments](
        coro: Callable[Arguments, Coroutine[Any, Any, Result]] | Coroutine[Any, Any, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Spawn an async task within current scope context task group.

        When called outside of context, it will spawn a background task instead.

        Parameters
        ----------
        coro: Callable[Arguments, Coroutine[Any, Any, Result]] | Coroutine[Any, Any, Result]
            function or coroutine to be called within the task group

        *args: Arguments.args
            positional arguments passed to function call

        **kwargs: Arguments.kwargs
            keyword arguments passed to function call

        Returns
        -------
        Task[Result]
            task for tracking function execution and result
        """

        return ContextTaskGroup.run(coro, *args, **kwargs)

    @overload
    @staticmethod
    def spawn_background[Result](
        coro: Coroutine[Any, Any, Result],
        /,
    ) -> Task[Result]: ...

    @overload
    @staticmethod
    def spawn_background[Result, **Arguments](
        coro: Callable[Arguments, Coroutine[Any, Any, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]: ...

    @staticmethod
    def spawn_background[Result, **Arguments](
        coro: Callable[Arguments, Coroutine[Any, Any, Result]] | Coroutine[Any, Any, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Spawn an async task within background task group.

        Parameters
        ----------
        coro: Callable[Arguments, Coroutine[Any, Any, Result]] | Coroutine[Any, Any, Result]
            function or coroutine to be called within the task group

        *args: Arguments.args
            positional arguments passed to function call

        **kwargs: Arguments.kwargs
            keyword arguments passed to function call

        Returns
        -------
        Task[Result]
            task for tracking function execution and result
        """

        return ContextTaskGroup.background_run(coro, *args, **kwargs)

    @staticmethod
    def shutdown_background_tasks() -> None:
        """
        Cancel all background tasks created via ``ctx.spawn_background`` or fallback spawns.

        Intended for graceful shutdown and test teardown to avoid task leaks.
        """

        BackgroundTaskGroup.shutdown_all()

    @staticmethod
    def stream[Element, **Arguments](
        source: Callable[Arguments, AsyncGenerator[Element]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> AsyncIterable[Element]:
        """
        Stream results produced by a generator within the proper context state.

        Parameters
        ----------
        source: Callable[Arguments, AsyncGenerator[Element]]
            async generator used as the stream source

        *args: Arguments.args
            positional arguments passed to generator call

        **kwargs: Arguments.kwargs
            keyword arguments passed to generator call

        Returns
        -------
        AsyncIterable[Element]
            iterator for accessing generated elements
        """

        scope = ctx.scope("stream")  # prepare scope before generator

        async def stream() -> AsyncGenerator[Element]:
            async with scope:
                async for result in source(*args, **kwargs):
                    yield result

        return stream()

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
        task: Task[Any] | None = current_task()

        if task is not None and task.cancelling():
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

        task: Task[Any] | None = current_task()
        if task is not None:
            task.cancel()

        else:
            raise RuntimeError("Attempting to cancel context out of asyncio task")

    @staticmethod
    def contains_state[StateType: State](
        state: type[StateType],
        /,
    ) -> bool:
        """
        Check if state object is available in the current context.

        Verifies if state object of the specified type is available in the current context.

        Parameters
        ----------
        state: type[StateType]
            The type of state to check

        Returns
        -------
        bool
            True if state is available, otherwise False.
        """
        return ContextState.contains(state)

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
        ContextMissing
            If called outside of any scope context
        ContextStateMissing
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
        return ContextState.state(
            state,
            default=default,
        )

    @staticmethod
    def send(
        event: State,
        /,
    ) -> None:
        """
        Send an event to all active subscribers within the current context.

        Events are dispatched based on their exact type - subscribers must
        subscribe to the specific State type to receive events.

        Parameters
        ----------
        event : State
            The event payload to send. Must be a State instance.

        Raises
        ------
        ContextMissing
            If called outside of an ContextEvents

        Examples
        --------
        Basic event sending:

        >>> from haiway import ctx, State
        >>>
        >>> class OrderCreated(State):
        ...     order_id: str
        ...     amount: float
        >>>
        >>> async def process_order():
        ...     # Send event after order creation
        ...     ctx.send(OrderCreated(order_id="12345", amount=99.99))
        """
        ContextEvents.send(event)

    @staticmethod
    def subscribe[Event: State](
        event: type[Event],
    ) -> EventsSubscription[Event]:
        """
        Subscribe to events of a specific type within the current context.

        Creates a subscription that receives all events of the specified type
        sent after the subscription is created.

        Parameters
        ----------
        event : type[Event]
            The State type to subscribe to. Must be a State class.

        Returns
        -------
        EventsSubscription[Event]
            An async iterator that yields events of the specified type

        Examples
        --------
        Basic subscription:

        >>> from haiway import ctx, State
        >>>
        >>> class UserActivity(State):
        ...     user_id: str
        ...     action: str
        ...     timestamp: float
        >>>
        >>> async def monitor_activity():
        ...     async for activity in ctx.subscribe(UserActivity):
        ...         print(f"{activity.user_id} performed {activity.action}")

        With error handling:

        >>> async def process_events():
        ...     try:
        ...         async for event in ctx.subscribe(PaymentEvent):
        ...             await handle_payment(event)
        ...     except asyncio.CancelledError:
        ...         ctx.log_info("Payment processing stopped")
        ...         raise

        See Also
        --------
        ctx.send : For sending events
        EventsSubscription : The subscription iterator
        """
        return ContextEvents.subscribe(event)

    @staticmethod
    def log_error(
        message: str,
        /,
        *args: Any,
        exception: BaseException | None = None,
    ) -> None:
        """
        Log using ERROR level within current scope context.

        When there is no current scope, root logger will be used without additional details.

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

        ContextObservability.record_log(
            ObservabilityLevel.ERROR,
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
        Log using WARNING level within current scope context.

        When there is no current scope, root logger will be used without additional details.

        Parameters
        ----------
        message: str
            message to be written to log

        *args: Any
            message format arguments

        exception: Exception | None = None
            exception associated with log, when provided full stack trace will be recorded

        Returns
        -------
        None
        """

        ContextObservability.record_log(
            ObservabilityLevel.WARNING,
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
        Log using INFO level within current scope context.

        When there is no current scope, root logger will be used without additional details.

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

        ContextObservability.record_log(
            ObservabilityLevel.INFO,
            message,
            *args,
            exception=None,
        )

    @staticmethod
    def log_debug(
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
    ) -> None:
        """
        Log using DEBUG level within current scope context.

        When there is no current scope, root logger will be used without additional details.

        Parameters
        ----------
        message: str
            message to be written to log

        *args: Any
            message format arguments

        exception: Exception | None = None
            exception associated with log, when provided full stack trace will be recorded

        Returns
        -------
        None
        """

        ContextObservability.record_log(
            ObservabilityLevel.DEBUG,
            message,
            *args,
            exception=exception,
        )

    @overload
    @staticmethod
    def record_error(
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...

    @overload
    @staticmethod
    def record_error(
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @overload
    @staticmethod
    def record_error(
        *,
        metric: str,
        value: float | int,
        unit: str | None = None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @staticmethod
    def record_error(
        *,
        event: str | None = None,
        metric: str | None = None,
        value: float | int | None = None,
        unit: str | None = None,
        kind: ObservabilityMetricKind | None = None,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None:
        if event is not None:
            assert metric is None  # nosec: B101
            ContextObservability.record_event(
                ObservabilityLevel.ERROR,
                event,
                attributes=attributes or {},
            )

        elif metric is not None:
            assert event is None  # nosec: B101
            assert value is not None  # nosec: B101
            assert kind is not None  # nosec: B101
            ContextObservability.record_metric(
                ObservabilityLevel.ERROR,
                metric,
                value=value,
                unit=unit,
                kind=kind,
                attributes=attributes or {},
            )

        else:
            ContextObservability.record_attributes(
                ObservabilityLevel.ERROR,
                attributes=attributes or {},
            )

    @overload
    @staticmethod
    def record_warning(
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...

    @overload
    @staticmethod
    def record_warning(
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @overload
    @staticmethod
    def record_warning(
        *,
        metric: str,
        value: float | int,
        unit: str | None = None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @staticmethod
    def record_warning(
        *,
        event: str | None = None,
        metric: str | None = None,
        value: float | int | None = None,
        unit: str | None = None,
        kind: ObservabilityMetricKind | None = None,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None:
        if event is not None:
            assert metric is None  # nosec: B101
            ContextObservability.record_event(
                ObservabilityLevel.WARNING,
                event,
                attributes=attributes or {},
            )

        elif metric is not None:
            assert event is None  # nosec: B101
            assert value is not None  # nosec: B101
            assert kind is not None  # nosec: B101
            ContextObservability.record_metric(
                ObservabilityLevel.WARNING,
                metric,
                value=value,
                unit=unit,
                kind=kind,
                attributes=attributes or {},
            )

        else:
            ContextObservability.record_attributes(
                ObservabilityLevel.WARNING,
                attributes=attributes or {},
            )

    @overload
    @staticmethod
    def record_info(
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...

    @overload
    @staticmethod
    def record_info(
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @overload
    @staticmethod
    def record_info(
        *,
        metric: str,
        value: float | int,
        unit: str | None = None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @staticmethod
    def record_info(
        *,
        event: str | None = None,
        metric: str | None = None,
        value: float | int | None = None,
        unit: str | None = None,
        kind: ObservabilityMetricKind | None = None,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None:
        if event is not None:
            assert metric is None  # nosec: B101
            ContextObservability.record_event(
                ObservabilityLevel.INFO,
                event,
                attributes=attributes or {},
            )

        elif metric is not None:
            assert event is None  # nosec: B101
            assert value is not None  # nosec: B101
            assert kind is not None  # nosec: B101
            ContextObservability.record_metric(
                ObservabilityLevel.INFO,
                metric,
                value=value,
                unit=unit,
                kind=kind,
                attributes=attributes or {},
            )

        else:
            ContextObservability.record_attributes(
                ObservabilityLevel.INFO,
                attributes=attributes or {},
            )

    @overload
    @staticmethod
    def record_debug(
        *,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None: ...

    @overload
    @staticmethod
    def record_debug(
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @overload
    @staticmethod
    def record_debug(
        *,
        metric: str,
        value: float | int,
        unit: str | None = None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None: ...

    @staticmethod
    def record_debug(
        *,
        event: str | None = None,
        metric: str | None = None,
        value: float | int | None = None,
        unit: str | None = None,
        kind: ObservabilityMetricKind | None = None,
        attributes: Mapping[str, ObservabilityAttribute] | None = None,
    ) -> None:
        if event is not None:
            assert metric is None  # nosec: B101
            ContextObservability.record_event(
                ObservabilityLevel.DEBUG,
                event,
                attributes=attributes or {},
            )

        elif metric is not None:
            assert event is None  # nosec: B101
            assert value is not None  # nosec: B101
            assert kind is not None  # nosec: B101
            ContextObservability.record_metric(
                ObservabilityLevel.DEBUG,
                metric,
                value=value,
                unit=unit,
                kind=kind,
                attributes=attributes or {},
            )

        else:
            ContextObservability.record_attributes(
                ObservabilityLevel.DEBUG,
                attributes=attributes or {},
            )

    __slots__ = ()

    def __init__(self) -> NoReturn:
        raise RuntimeError("ctx instantiation is forbidden")
