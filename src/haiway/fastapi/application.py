from collections.abc import Callable, Coroutine, Iterable, Mapping
from typing import Any, cast

from fastapi import APIRouter, FastAPI, Request, Response
from starlette.middleware import Middleware
from starlette.types import StatelessLifespan

from haiway.fastapi.types import ExceptionHandling
from haiway.starlette import ContextMiddleware, ServerContext

__all__ = ("application",)


def application(
    context: ServerContext | None = None,
    /,
    *,
    routers: Iterable[APIRouter] = (),
    middleware: Iterable[Middleware] = (),
    exception_handlers: Mapping[int | type[Exception], ExceptionHandling] | None = None,
    lifespan: StatelessLifespan[FastAPI] | None = None,
    **extra: Any,
) -> FastAPI:
    """Prepare a FastAPI application handling requests within Haiway contexts.

    The FastAPI counterpart of ``haiway.starlette.application``, wiring the same
    two pieces of the integration into a regular FastAPI application: the
    lifespan of the given context, preparing the application resources, and
    ``ContextMiddleware``, entering a context scope for each request. Everything
    else is passed through to ``FastAPI`` unchanged, so an application prepared
    this way is served, tested, documented and extended like any other.

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
    exception_handlers : Mapping[int | type[Exception], ExceptionHandling] | None
        Handlers producing responses for exceptions and status codes,
        asynchronous or synchronous - a synchronous one is called in a worker
        thread. A handler nested below ``ContextMiddleware`` - anything but
        ``500`` or ``Exception``, the validation error handler of FastAPI
        included - answers within the scope of its request, so its response
        carries the trace headers. The two server error slots run above every other
        middleware, which is where Starlette places them, so what they answer
        with is outside of the request scope and carries none.
    lifespan : StatelessLifespan[FastAPI] | None
        Additional startup and shutdown steps, entered within the lifespan of
        the context, so the application state is prepared before they run.
        Startup work which owns a resource is better expressed as one of the
        context disposables.
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
    """
    resolved_context: ServerContext = context if context is not None else ServerContext()

    app: FastAPI = FastAPI(
        middleware=(
            Middleware(
                ContextMiddleware,
                context=resolved_context,
            ),
            *middleware,
        ),
        exception_handlers=cast(
            dict[int | type[Exception], Callable[[Request, Any], Coroutine[Any, Any, Response]]]
            | None,
            dict(exception_handlers) if exception_handlers else None,
        ),
        lifespan=resolved_context.composed_lifespan(lifespan),
        **extra,
    )

    for router in routers:
        app.include_router(router)

    return app
