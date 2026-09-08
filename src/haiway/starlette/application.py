from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.errors import ServerErrorMiddleware
from starlette.routing import BaseRoute
from starlette.types import ExceptionHandler, StatelessLifespan

from haiway.starlette.context import ServerContext
from haiway.starlette.middleware import ContextMiddleware

__all__ = ("application",)


# the arrangement both factories build - a request scope around the server error
# handling of the application, and the handlers the framework is left with
def scoped_middleware_stack[Handler: ExceptionHandler](
    context: ServerContext,
    /,
    *,
    middleware: Iterable[Middleware],
    exception_handlers: Mapping[Any, Handler] | None,
    debug: bool,
) -> tuple[Sequence[Middleware], dict[Any, Handler]]:
    # `500` and `Exception` resolve one server error handler, the last of the two
    # given winning - resolved here the way the framework resolves it, to install
    # it below the request scope instead of above every middleware, where the
    # framework would. Held in both places it would be called twice for a single
    # failure, only the first of its responses being sent
    server_error_handler: Handler | None = None
    remaining_handlers: dict[Any, Handler] = {}
    for key, value in (exception_handlers or {}).items():
        if key in (500, Exception):
            server_error_handler = value

        else:
            remaining_handlers[key] = value

    return (
        (
            Middleware(
                ContextMiddleware,
                context=context,
            ),
            # nested within the scope of the request instead of left above it -
            # the `500` of an unhandled failure is then produced and sent from
            # within that scope, which is what makes it carry the trace headers
            # correlating it with the trace recording the failure. The slot it
            # was taken out of stays empty, answering only a request which
            # failed before its scope was entered
            Middleware(
                ServerErrorMiddleware,
                handler=server_error_handler,
                debug=debug,
            ),
            *middleware,
        ),
        remaining_handlers,
    )


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
    routes : Sequence[BaseRoute]
        Routes serving the requests.
    middleware : Iterable[Middleware]
        Additional middlewares, nested below ``ContextMiddleware`` - each one
        runs within the context scope of the request and can extend its state
        through ``ctx.updating(...)``.
    exception_handlers : Mapping[Any, ExceptionHandler] | None
        Handlers producing responses for exceptions and status codes. Each one
        answers within the scope of its request, so its response carries the
        trace headers - the ``500`` and ``Exception`` slots included, which
        resolve one handler installed below that scope rather than above every
        middleware. Registering one afterwards through
        ``add_exception_handler(Exception, ...)`` installs it above instead,
        where the framework keeps that slot, so its response carries none.
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

    A failure of the request scope itself - an observability backend refusing to
    prepare one, for instance - is answered with the plain ``500`` of the
    framework, from the server error slot the handler was taken out of: the
    error handling below the scope is only reached once that scope was entered.
    """
    resolved_context: ServerContext = context if context is not None else ServerContext()
    middleware_stack: Sequence[Middleware]
    remaining_handlers: dict[Any, ExceptionHandler]
    middleware_stack, remaining_handlers = scoped_middleware_stack(
        resolved_context,
        middleware=middleware,
        exception_handlers=exception_handlers,
        # read here rather than from the application, which is only built below -
        # so a `debug` assigned to it afterwards reaches the error handling of the
        # framework alone, and not the one within the request scope
        debug=extra.get("debug", False),
    )

    return Starlette(
        routes=routes,
        middleware=middleware_stack,
        exception_handlers=remaining_handlers,
        lifespan=resolved_context.composed_lifespan(lifespan),
        **extra,
    )
