from collections.abc import AsyncGenerator, Collection, Iterable, Mapping, Sequence
from contextlib import (
    AbstractAsyncContextManager,
    asynccontextmanager,
)
from logging import Logger, getLogger
from typing import Final, final

from starlette.applications import Starlette
from starlette.types import Scope

from haiway.attributes import State
from haiway.context import (
    ContextMissing,
    ContextPresets,
    Disposable,
    Disposables,
    Observability,
    ctx,
)
from haiway.context.managed import TaskRuntime
from haiway.context.observability import LoggerObservability
from haiway.starlette.trace import request_trace_context
from haiway.starlette.types import ObservabilityPreparing

__all__ = ("ServerContext",)


# the root logger rather than the scope name based default of `ctx.scope` - scope
# names carry the request path, and requesting a logger per distinct path leaks one
# into the logging registry for each of them. Named rather than root it would be a
# logger created on import, which `setup_logging` disables along with every other
# logger predating it - silently dropping everything recorded through it
DEFAULT_LOGGER: Final[Logger] = getLogger()


@final
class _SharedBackend:
    __slots__ = ("backend",)

    def __init__(
        self,
        backend: Observability,
        /,
    ) -> None:
        self.backend: Observability = backend


@final
class _LoggedBackend:
    __slots__ = ("logger",)

    def __init__(
        self,
        logger: Logger,
        /,
    ) -> None:
        self.logger: Logger = logger


@final
class _PreparedBackend:
    __slots__ = ("preparing",)

    def __init__(
        self,
        preparing: ObservabilityPreparing,
        /,
    ) -> None:
        self.preparing: ObservabilityPreparing = preparing


type _ServerObservability = _SharedBackend | _LoggedBackend | _PreparedBackend


def _server_observability(
    observability: Observability | Logger | ObservabilityPreparing,
    /,
) -> _ServerObservability:
    if isinstance(observability, Observability):
        return _SharedBackend(observability)

    if isinstance(observability, Logger):
        return _LoggedBackend(observability)

    return _PreparedBackend(observability)


@final
class ServerContext:
    """Application wide context of a Starlette application.

    Declares what the context of a request looks like - which state it carries
    and how it is observed - and owns the resources that state is built from.
    Two parts of an application use it:

    - ``lifespan`` prepares the application disposables on startup and releases
      them on shutdown, so it has to be installed as the application lifespan.
    - ``ContextMiddleware`` enters a context scope for each request, using the
      state prepared by that lifespan.

    Both are wired automatically by ``application()``; installing them by hand
    is what allows an existing application - a FastAPI one, for instance - to be
    plugged into Haiway.

    Parameters
    ----------
    *state : State | None
        State propagated into every request scope. Suitable for state which is
        immutable and needs no cleanup - a client bound to a resource belongs in
        ``disposables`` instead. ``None`` values are ignored, which keeps a
        conditionally provided element from requiring a branch. Takes precedence
        over the state prepared by ``disposables`` on conflict.
    disposables : Iterable[Disposable | None]
        Disposables owned by the application, prepared on startup. State they
        produce is propagated into every request scope and the resources behind
        it are released on shutdown. ``None`` values are ignored the same way
        the declared state is. Requires ``lifespan`` to be installed as the
        application lifespan.
    presets : Iterable[ContextPresets]
        Context presets made available within request scopes, allowing a request
        handler to enter a nested scope by preset name.
    observability : Observability | Logger | ObservabilityPreparing
        Observability backend recording request scopes. A callable is invoked
        per request with the W3C trace context the request carries, which is
        what continues the trace of the caller instead of starting a new one -
        ``OpenTelemetry.observability`` implements it as it is. A single backend
        instance is used as provided, which records requests as their own traces.
        A ``Logger`` is turned into a logging backend of its own for each
        request, so what one recorded is released along with it - a backend
        shared by every request retains the scopes of those which never
        completed, an abandoned generator among them, for as long as the
        application runs.
        Defaults to logging through the root logger, which is where a logging
        setup always applies - a named one of ours would be silenced by a
        ``setup_logging`` call made after this module was imported.
    tasks : TaskRuntime | None
        Runtime of managed tasks, which the lifespan opens after the disposables
        are prepared and closes before they are released, and whose
        ``ManagedTasks`` is added to the state of every request scope, after the
        declared state. Tasks run in a context built from the state of the
        disposables and the declared state, and from the presets, and record
        through the observability of the runtime, else through the one declared
        here, which for a ``Logger`` is a logging backend shared by every task.
        Entering the block starts executing the due tasks stored by the store of
        the runtime, so a task can start before the first request is served.
        Requires ``lifespan`` to be installed as the application lifespan.
        Without it, no managed tasks are available in requests.

    Examples
    --------
    >>> context = ServerContext(
    ...     ExampleConfig(),
    ...     disposables=(HTTPXClient(),),
    ...     observability=getLogger("api"),
    ... )
    >>> app = application(context, routes=[...])

    Notes
    -----
    A single instance backs a single run of a single application. The declared
    disposables are the instances prepared on startup, not a factory producing
    fresh ones, so a lifespan which already ended can not be entered again - a
    test exercising more than one run declares a context per run.
    """

    __slots__ = (
        "_disposables",
        "_observability",
        "_state",
        "_tasks",
        "presets",
        "state",
    )

    def __init__(
        self,
        *state: State | None,
        disposables: Iterable[Disposable | None] = (),
        presets: Iterable[ContextPresets] = (),
        observability: Observability | Logger | ObservabilityPreparing = DEFAULT_LOGGER,
        tasks: TaskRuntime | None = None,
    ) -> None:
        self._state: Sequence[State] = tuple(element for element in state if element is not None)
        self.state: Sequence[State]
        self._disposables: Disposables = Disposables.of(*disposables)
        self.presets: Collection[ContextPresets] = tuple(presets)
        self._tasks: TaskRuntime | None = tasks
        self._observability: _ServerObservability = _server_observability(observability)

    def lifespan(
        self,
        application: Starlette | None = None,
        /,
    ) -> AbstractAsyncContextManager[None]:
        """Prepare the application resources for as long as it runs.

        Intended to be installed as the application lifespan, which is what
        makes the state of the prepared disposables available to requests::

            app = Starlette(lifespan=context.lifespan, middleware=[...])

        Parameters
        ----------
        application : Starlette | None
            The application being started, as passed by Starlette. Unused - the
            context is not bound to a single application - and optional, so the
            lifespan can also be entered directly, like in a test.

        Returns
        -------
        AbstractAsyncContextManager[None]
            Context manager preparing the application disposables when entered
            and releasing them on exit.

        Raises
        ------
        AssertionError
            When entered a second time - the disposables of the context are the
            instances it was declared with, so a lifespan which already ended
            has nothing left to prepare. Checked in debug builds only, where a
            wiring mistake is worth reporting rather than paying for at runtime.
        """
        return self._lifespan()

    @asynccontextmanager
    async def _lifespan(self) -> AsyncGenerator[None]:
        assert not hasattr(self, "state"), "Server context reentrance is not allowed"  # nosec: B101

        if __debug__:
            DEFAULT_LOGGER.warning("Starting DEBUG server...")

        else:
            DEFAULT_LOGGER.info("Starting server...")

        try:
            DEFAULT_LOGGER.info("...initializing server state...")
            async with self._disposables as disposable_state:
                # explicitly declared state goes last to take precedence
                # over the state prepared by the disposables
                declared: Sequence[State] = (*disposable_state, *self._state)
                try:
                    # startup work sees the declared state and the presets with or without
                    # tasks, and the managed tasks block takes its root snapshot of them, so
                    # they are current when it is entered and it is exited before them
                    with ctx.updating(*declared), ctx.presets(*self.presets):
                        if self._tasks is None:
                            self.state = declared
                            DEFAULT_LOGGER.info("...server state initialized...")
                            yield  # suspend until shutdown

                        else:
                            async with ctx.managed_tasks(
                                self._tasks,
                                observability=self._tasks_observability(),
                            ) as managed:
                                self.state = (*declared, managed)
                                DEFAULT_LOGGER.info("...server state initialized...")
                                yield  # suspend until shutdown

                finally:
                    DEFAULT_LOGGER.info("...closing server...")

        finally:
            DEFAULT_LOGGER.info("...server closed!")

    def _tasks_observability(self) -> Observability:
        # what records the tasks of a runtime which was given no backend: the one declared
        # for requests, a logger becoming a backend which is not meant for a single request
        match self._observability:
            case _SharedBackend(backend=backend):
                return backend

            case _LoggedBackend(logger=logger):
                return LoggerObservability(logger, debug_context=False)

            case _PreparedBackend(preparing=preparing):
                return preparing(traceparent=None, tracestate=None)

    @property
    def request_state(self) -> Sequence[State]:
        """Resolve the state propagated into a request scope.

        Returns
        -------
        Sequence[State]
            The state prepared by the disposables of the context, followed by
            the state it was declared with, which takes precedence over it, and
            the ``ManagedTasks`` of its runtime when it was given one.

        Raises
        ------
        ContextMissing
            When the lifespan of the context did not prepare the state - either
            it was not installed as the application lifespan, or the startup it
            belongs to has not completed yet.

        Notes
        -----
        Only meaningful for as long as the application runs. The state prepared
        on startup is reported as it is once shutdown released the resources
        behind it, which is not a usage a server produces - it stops serving
        before it shuts the application down - and is not guarded against.
        """
        try:
            return self.state

        except AttributeError:
            raise ContextMissing(
                "Server context state requested but not prepared -"
                " `ServerContext.lifespan` has to be installed as the application"
                " lifespan and its startup has to complete before requests"
                " are served"
            ) from None

    def request_observability(
        self,
        request_scope: Scope,
        /,
    ) -> Observability:
        """Resolve the observability backend recording a request scope.

        Parameters
        ----------
        request_scope : Scope
            ASGI scope of the incoming request, which is where the trace context
            handed to a backend factory is read from.

        Returns
        -------
        Observability
            The backend recording the request. A backend declared by the context
            is used as it is, while a ``Logger`` becomes a logging backend built
            here for each request. A callable is invoked per request with the
            W3C trace context the request carries, which is what continues the
            trace of its caller.
        """
        # the trace context of every request is resolved here, so a backend which
        # continues the trace of its caller needs no wiring of its own
        trace_context: Mapping[str, str] = request_trace_context(request_scope)
        match self._observability:
            case _SharedBackend(backend=backend):
                return backend

            case _LoggedBackend(logger=logger):
                return LoggerObservability(logger)

            case _PreparedBackend(preparing=preparing):
                return preparing(
                    traceparent=trace_context.get("traceparent"),
                    tracestate=trace_context.get("tracestate"),
                )
