from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.routing import BaseRoute
from starlette.types import ExceptionHandler, StatelessLifespan

from haiway.starlette.context import ServerContext
from haiway.starlette.middleware import ContextMiddleware

__all__ = ("application",)


def application(
    context: ServerContext | None = None,
    /,
    *,
    routes: Sequence[BaseRoute] = (),
    middleware: Iterable[Middleware] = (),
    exception_handlers: Mapping[Any, ExceptionHandler] | None = None,
    lifespan: StatelessLifespan[Starlette] | None = None,
    **extra: Any,
) -> Starlette:
    """Prepare a Starlette application handling requests within Haiway contexts.

    Wires the two parts of the integration into a regular Starlette
    application: the lifespan of the given context, preparing the application
    resources, and ``ContextMiddleware``, entering a context scope for each
    request. Everything else is passed through to ``Starlette`` unchanged, so an
    application prepared this way is served, tested and extended like any other.

    Parameters
    ----------
    context : ServerContext | None
        Declaration of the request context. Defaults to an empty context, which
        provides scopes and trace headers without any application state.
    routes : Sequence[BaseRoute]
        Routes serving the requests.
    middleware : Iterable[Middleware]
        Additional middlewares, nested below ``ContextMiddleware`` - each one
        runs within the context scope of the request and can extend its state
        through ``ctx.updating(...)``.
    exception_handlers : Mapping[Any, ExceptionHandler] | None
        Handlers producing responses for exceptions and status codes. A handler
        nested below ``ContextMiddleware`` - anything but ``500`` or
        ``Exception`` - answers within the scope of its request, so its response
        carries the trace headers. The two server error slots run above every
        other middleware, which is where Starlette places them, so what they
        answer with is outside of the request scope and carries none.
    lifespan : StatelessLifespan[Starlette] | None
        Additional startup and shutdown steps, entered within the lifespan of
        the context, so the application state is prepared before they run.
        Startup work which owns a resource is better expressed as one of the
        context disposables.
    **extra : Any
        Additional keyword arguments passed directly to ``Starlette`` - ``debug``
        and ``max_body_size`` among them.

    Returns
    -------
    Starlette
        The prepared application.

    Examples
    --------
    >>> app = application(
    ...     ServerContext(
    ...         ExampleConfig(),
    ...         disposables=(HTTPXClient(),),
    ...     ),
    ...     routes=[Route("/example", example_endpoint)],
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

    return Starlette(
        routes=routes,
        middleware=(
            Middleware(
                ContextMiddleware,
                context=resolved_context,
            ),
            *middleware,
        ),
        exception_handlers=exception_handlers,
        lifespan=resolved_context.composed_lifespan(lifespan),
        **extra,
    )
