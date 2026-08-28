from asyncio import (
    CancelledError,
    Task,
    current_task,
)
from collections.abc import (
    AsyncGenerator,
    Callable,
    Coroutine,
    Iterable,
    Mapping,
)
from contextlib import AbstractAsyncContextManager, AbstractContextManager, aclosing
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
from haiway.context.tasks import BackgroundTaskGroup, ContextTaskGroup

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

        The trace identifier is shared by the whole scope tree - a root creates
        it and every scope nested below it inherits it - so logs, events and
        metrics recorded anywhere within it correlate. It is rendered as unpadded
        lowercase hex, the form trace backends display and query by, and is the
        same value ``ctx.scope(...)`` yields when entered.

        Returns
        -------
        str
            The current trace identifier, as 32 lowercase hex characters. All
            zeros when the observability backend has no trace to report.
        """
        return ContextObservability.trace_id()

    @staticmethod
    def trace_context() -> Mapping[str, str]:
        """
        Encode the current trace position for propagation to another service.

        The result is intended to be attached to an outgoing request - as HTTP
        headers or message metadata - so the receiving service can continue this
        trace instead of starting its own. With the OpenTelemetry integration it
        contains the W3C ``traceparent``, and ``tracestate`` when vendor trace
        state is present.

        Returns
        -------
        Mapping[str, str]
            Carrier entries identifying the current scope within its trace.
            Empty when used out of context, or when the current observability
            backend has no trace position to hand out - propagation is best
            effort and never raises.
        """
        return ContextObservability.trace_context()

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
        creating scopes. Registry lookup is scoped: nested registries shadow outer
        registries for the duration of the nested ``with`` block.

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
            disposables will be applied to the scope with lower priority than explicit state and
            with higher priority than inherited parent state. Direct presets take precedence over
            registry lookup.

        *state: State | None
            state propagated within the scope context, will be merged with current state by
            replacing current with provided on conflict.

        disposables: Iterable[Disposable | None] | None
            disposables consumed within the context when entered. Produced state will automatically
            be added to the scope state. Using asynchronous context is required if any disposables
            were provided.

        observability: Observability | Logger | None = None
            observability solution responsible for recording and storing metrics, logs and events.
            When not provided, the current observability backend is reused if one exists;
            otherwise a logger with the scope name will be requested and used.

        isolated: bool = False
            controls whether event handling is isolated from the parent scope. When set to
            True, the scope uses its own event bus, so events sent within it do not reach
            the subscribers of enclosing scopes. Every scope owns its TaskGroup regardless
            of this flag. State inheritance still flows from parent to child when the scope
            is entered, but updates remain local to the child scope. Root scope is always
            isolated.

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
        coro: Coroutine[None, None, Result],
        /,
    ) -> Task[Result]: ...

    @overload
    @staticmethod
    def spawn[Result, **Arguments](
        coro: Callable[Arguments, Coroutine[None, None, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]: ...

    @staticmethod
    def spawn[Result, **Arguments](
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Spawn an async task within current scope context task group.

        Spawned tasks inherit the current context snapshot via ``contextvars`` at
        spawn time. When called outside of any scope, this falls back to the global
        background task group instead.

        Every scope owns a task group, so a task is awaited by the scope it was
        spawned in when that scope exits - never by an enclosing one. This keeps the
        scope, its state and its observability records alive for as long as the task
        runs. Tasks are not automatically cancelled on a healthy scope exit; use
        ``task.cancel()`` or ``ctx.spawn_background(...)`` when different lifetime
        semantics are required.

        A task consuming ``ctx.subscribe(...)`` ends together with the scope the
        subscription was created in, including a nested non isolated one sharing
        the event bus of an ancestor. That scope completes its closing future
        while unwinding, before joining its tasks, which ends the pending
        iteration - so such a task never blocks the scope it was spawned in.

        Parameters
        ----------
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result]
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
        coro: Coroutine[None, None, Result],
        /,
    ) -> Task[Result]: ...

    @overload
    @staticmethod
    def spawn_background[Result, **Arguments](
        coro: Callable[Arguments, Coroutine[None, None, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]: ...

    @staticmethod
    def spawn_background[Result, **Arguments](
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Spawn an async task within background task group.

        Background tasks are fully detached - they run with an empty context instead
        of a snapshot of the current one, and may outlive the scope they were spawned
        in. Inheriting that snapshot would bind them to a scope which is free to
        complete while they still run, silently voiding their state, events and
        observability records.

        As a consequence ``ctx.state(...)`` and ``ctx.subscribe(...)`` raise
        ``ContextMissing`` within a background task, and records go to the root
        logger. Enter a scope within the task, or pass what it needs as arguments,
        when either is required. Use ``ctx.shutdown_background_tasks()`` for
        best-effort cleanup during shutdown or tests.

        Parameters
        ----------
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result]
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
    ) -> AsyncGenerator[Element]:
        """
        Stream results produced by a generator within the proper context state.

        The source generator runs inside a dedicated ``"stream"`` child scope so
        that state, observability, and trace information remain available while the
        stream is consumed.

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
        AsyncGenerator[Element]
            generator for accessing produced elements

        Notes
        -----
        The scope lives inside the returned generator, so it is released when the
        generator ends - by exhausting it, or by closing it. Wrap it in
        ``ctx.closing`` when the iteration may be left early: an abandoned
        generator is finalized by the garbage collector in a fresh context, where
        the scope can no longer be released.
        """

        async def stream() -> AsyncGenerator[Element]:
            async with ctx.scope("stream"):
                generator: AsyncGenerator[Element] = source(*args, **kwargs)
                try:
                    async for result in generator:
                        yield result

                finally:
                    await generator.aclose()

        return stream()

    @staticmethod
    def closing[Element](
        generator: AsyncGenerator[Element],
        /,
    ) -> AbstractAsyncContextManager[AsyncGenerator[Element]]:
        """
        Ensure a generator is closed when leaving its iteration.

        Wraps an async generator in a context manager closing it on exit, so that
        its cleanup runs where the iteration ends instead of whenever the garbage
        collector reaches it. Leaving a generator to the collector is unreliable
        in general, and unsound for generators which manage a context scope -
        ``ctx.stream`` and ``stream_concurrently`` among them: the collector
        finalizes a generator in a fresh context, where the scope it opened can no
        longer be released, so the teardown fails and the error is only logged.

        Parameters
        ----------
        generator: AsyncGenerator[Element]
            generator to be closed on leaving the context

        Returns
        -------
        AbstractAsyncContextManager[AsyncGenerator[Element]]
            context manager providing the generator and closing it on exit

        Examples
        --------
        >>> async with ctx.closing(ctx.stream(produce)) as stream:
        ...     async for element in stream:
        ...         if not await handle(element):
        ...             break  # the stream scope is released right here
        """

        return aclosing(generator)

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
        Resolution is by exact concrete type. If no matching state is found inside a
        scope, a default instance is created lazily when possible.

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
            If called outside of any scope context and no explicit default was provided
        ContextStateMissing
            If no state is found in the current scope and no default can be created

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
        *,
        broadcast: bool = False,
    ) -> None:
        """
        Send an event to the subscribers of its type within the current scope tree.

        Events are dispatched based on their exact type - subscribers must
        subscribe to the specific State type to receive events. Only subscribers
        that already exist receive the event.

        Delivery goes upwards: an event reaches the subscribers of the sending
        scope and of every scope enclosing it, up to the nearest isolated scope.
        Subscribers of sibling scopes and of scopes nested below the sender do not
        receive it, so one request cannot observe the events of another.

        Parameters
        ----------
        event : State
            The event payload to send. Must be a State instance.
        broadcast : bool, default=False
            When ``True``, the event reaches every subscriber of its type sharing
            the event bus, regardless of the scope it was subscribed in. Use it
            for a producer and a consumer living in sibling scopes, keeping in
            mind that unrelated scopes - other requests among them - receive the
            payload as well.

        Raises
        ------
        ContextMissing
            If no event bus is installed in the current scope. Event buses exist in
            root scopes and in scopes created with ``isolated=True``; nested
            non-isolated scopes reuse the parent's bus.

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

        Reaching a consumer subscribed in a sibling scope:

        >>> async def notify_pipeline():
        ...     ctx.send(OrderCreated(order_id="12345", amount=99.99), broadcast=True)

        See Also
        --------
        ctx.subscribe : For receiving events
        """
        ContextEvents.send(
            event,
            broadcast=broadcast,
        )

    @staticmethod
    def subscribe[Event: State](
        event: type[Event],
    ) -> EventsSubscription[Event]:
        """
        Subscribe to events of a specific type within the current context.

        Creates a subscription that receives events of the specified type sent
        after the subscription is created. Delivery is FIFO for a given event type
        within the current scope and event loop.

        The subscription receives events sent within the scope it was created in
        and within every scope nested below it. Events of sibling scopes are not
        delivered unless they were sent with ``broadcast=True``.

        The subscription is bound to the scope which created it, captured on
        subscribing and never on iterating - a scope entered and left around the
        iteration does not affect it. It ends when that scope begins closing, or
        when the scope owning the event bus - a root or ``isolated=True`` scope -
        exits, whichever comes first, delivering the events which already arrived
        before finishing. A scope completes its closing before joining its tasks,
        so a task spawned there can iterate the subscription to exhaustion without
        ever blocking the scope it was spawned in from leaving.

        Iterating a subscription to exhaustion directly within the body of the scope
        which created it deadlocks instead - that scope begins closing only once its
        body returns, so the iteration waits for an end which can never come. Consume
        such a subscription in a spawned task, or leave the loop on a condition of
        its own.

        The subscription is an async generator, so closing it ends the iteration
        where it stands - releasing the events chain it holds and any ``__anext__``
        waiting on it. Wrap it in ``ctx.closing`` when the iteration is left before
        its scope ends, instead of leaving the chain pinned until the garbage
        collector reaches it.

        Only one iteration may run at a time, as with any async generator - a second
        concurrent ``__anext__`` raises ``RuntimeError`` instead of delivering the
        same event twice. Subscribe once per consumer to fan an event type out to
        several of them.

        Parameters
        ----------
        event : type[Event]
            The State type to subscribe to. Must be a State class.

        Returns
        -------
        EventsSubscription[Event]
            An async generator yielding events of the specified type

        Raises
        ------
        ContextMissing
            If no event bus is installed in the current scope

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

        Leaving the iteration early:

        >>> async def await_confirmation(order_id: str):
        ...     async with ctx.closing(ctx.subscribe(OrderConfirmed)) as confirmations:
        ...         async for confirmation in confirmations:
        ...             if confirmation.order_id == order_id:
        ...                 return confirmation  # the subscription ends right here

        See Also
        --------
        ctx.send : For sending events
        ctx.closing : For ending a subscription left before its scope
        EventsSubscription : The subscription generator
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
