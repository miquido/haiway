from collections.abc import Iterable
from typing import Any

from fastapi import APIRouter, FastAPI
from starlette.middleware import Middleware
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.types import ExceptionHandler

from haiway.starlette import ServerContext
from haiway.starlette.middleware import ContextMiddleware

__all__ = ("application",)


def application(
    context: ServerContext | None = None,
    /,
    *,
    routers: Iterable[APIRouter] = (),
    middleware: Iterable[Middleware] = (),
    exception_handler: ExceptionHandler | None = None,
    **extra: Any,
) -> FastAPI:
    """Prepare a FastAPI application handling requests within Haiway contexts.

    The FastAPI counterpart of ``haiway.starlette.application``, wiring the same
    two pieces of the integration into a regular FastAPI application: the
    lifespan of the given context, preparing the application resources, and
    ``ContextMiddleware``, entering a context scope for each request. Everything
    else is passed through to ``FastAPI`` unchanged, so an application prepared
    this way is served, tested, documented and extended like any other.

    The server error handling of the application is nested below that scope
    rather than left above every middleware, where the framework installs it, so
    the ``500`` answering an unhandled failure is produced and sent from within
    the scope of its request and carries its trace headers - which is what
    correlates the report of a failed request with the trace recording why it
    failed.

    Parameters
    ----------
    context : ServerContext | None
        Declaration of the request context. Defaults to an empty context, which
        provides scopes and trace headers without any application state.
    routers : Iterable[APIRouter]
        Routers to include, in order. A router serving under a path prefix
        carries it itself - ``APIRouter(prefix="/api/v1")`` - and further
        routers can be added afterwards through ``app.include_router(...)``.
    middleware : Iterable[Middleware]
        Additional middlewares, nested below ``ContextMiddleware`` - each one
        runs within the context scope of the request and can extend its state
        through ``ctx.updating(...)``.
    exception_handler : ExceptionHandler | None
        Handler producing the response of an unhandled failure - the ``500`` of the
        application. It is nested below ``ContextMiddleware``, so it answers within
        the scope of its request and the response carries the trace headers. The
        plain ``500`` of the framework answers when none is given, and ``debug``
        keeps the debugging response in place of either. ``debug`` defaults to
        ``__debug__`` - on unless the interpreter runs optimized (``python -O``). Handlers of other
        exceptions and status codes are given as ``exception_handlers`` among the
        extra arguments and already answer within the scope. Do not register
        ``Exception`` or ``500`` there - the framework would install it above the
        scope, shadowed by this handler.
    **extra : Any
        Additional keyword arguments passed directly to ``FastAPI`` - the
        OpenAPI metadata (``title``, ``version``, ``description``), the
        documentation urls, global ``dependencies`` and everything else it
        accepts.

    Returns
    -------
    FastAPI
        The prepared application.

    Examples
    --------
    >>> app = application(
    ...     ServerContext(
    ...         ExampleConfig(),
    ...         disposables=(HTTPXClient(),),
    ...     ),
    ...     routers=(example_router,),
    ...     title="Example API",
    ...     version="1.0.0",
    ...     openapi_url="/openapi.json" if __debug__ else None,
    ... )

    Notes
    -----
    A provided ``lifespan`` must not hold a context scope open across its
    ``yield`` - a scope entered on startup and left open would leak its state
    into the context the server creates its request tasks in. Preparing state
    for requests is what ``ServerContext`` is for, while startup work which
    needs a context of its own - running migrations, for instance - belongs in a
    scope entered and exited before the ``yield``.

    A failure of the request scope itself - an observability backend refusing to
    prepare one, for instance - is answered with the plain ``500`` of the
    framework, not by ``exception_handler``: the error handling below the scope
    is only reached once that scope was entered.
    """
    server_context: ServerContext = context if context is not None else ServerContext()

    app: FastAPI = FastAPI(
        middleware=(
            Middleware(
                ContextMiddleware,
                context=server_context,
            ),
            Middleware(
                ServerErrorMiddleware,
                handler=exception_handler,
                debug=extra.get("debug", __debug__),
            ),
            *middleware,
        ),
        lifespan=server_context.lifespan,
        **extra,
    )

    for router in routers:
        app.include_router(router)

    return app
