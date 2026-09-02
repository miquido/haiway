from collections.abc import Mapping, MutableMapping
from typing import Any, final

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException, WebSocketException
from starlette.requests import ClientDisconnect
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from haiway.context import ObservabilityAttribute
from haiway.context.access import ctx
from haiway.starlette.context import ServerContext

__all__ = ("ContextMiddleware",)


@final
class ContextMiddleware:
    """ASGI middleware handling each request within a Haiway context scope.

    Enters a context scope around the rest of the application, which makes the
    state declared by a ``ServerContext`` - and the state prepared by its
    disposables - available to everything handling the request, including the
    middlewares nested below it, the endpoint, its background tasks and the
    generator of a streaming response.

    Requests are affected in four ways:

    - a scope is entered for each ``http`` and ``websocket`` request, recording
      it as one trace, and named after the request: the method and the requested
      path - ``"GET /users/12345"`` - with ``"WS"`` in place of the method for a
      websocket connection, which carries none. Other request types, ``lifespan``
      included, are passed through untouched.
    - the request is recorded into its scope as the HTTP semantic conventions of
      OpenTelemetry describe it - ``http.route``, ``url.path``,
      ``http.request.method`` and ``http.response.status_code`` among the
      attributes - once it was handled, which is when the route it matched and
      the status it was answered with are known. ``http.route`` is what a
      parameterized route is findable by, since the scope name carries the path
      which was actually requested rather than the template behind it.
    - a response carries the trace headers of its request scope, whether it was
      produced by an endpoint or by an exception handler nested below this
      middleware. For a websocket request that is the response denying its
      handshake - an accepted connection switches the protocol rather than
      answering, so it carries none. An entry a header can not hold is left out
      rather than failing the response it belongs to.
    - an exception which no handler answered propagates through the scope of its
      request, which is what records it as the failure of that request, and is
      reraised afterwards. Answering it is left to the application: the server
      error handling of the framework sits above this middleware, so the ``500``
      it produces - the plain one, the traceback page of a ``debug`` application,
      or a registered handler of ``Exception`` or ``500`` - is what the client
      receives, and carries no trace headers of its own.

    ``HTTPException``, ``WebSocketException`` and ``ClientDisconnect`` are not a
    failure of the request they end - the first two are how an application asks
    for a specific response, the third is a consumer which went away. They are
    withheld while the scope is left, so it does not record the request as
    failed, and reraised afterwards for whatever handles them upstream.

    Parameters
    ----------
    app : ASGIApp
        The application handling requests within the prepared scope.

    Examples
    --------
    >>> context = ServerContext(disposables=(HTTPXClient(),))
    >>> app = Starlette(
    ...     routes=[...],
    ...     middleware=[Middleware(ContextMiddleware, context=context)],
    ...     lifespan=context.lifespan,
    ... )

    Notes
    -----
    Placing it as the outermost middleware is what makes the context available
    to the other middlewares of the application - which is where
    ``application()`` puts it. State derived from a request, like the identity of
    its caller, can be added by a middleware nested below it through
    ``ctx.updating(...)``.
    """

    __slots__ = (
        "_app",
        "_context",
    )

    def __init__(
        self,
        app: ASGIApp,
        /,
        context: ServerContext,
    ) -> None:
        self._app: ASGIApp = app
        self._context: ServerContext = context

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        match scope["type"]:
            case "http":
                method: str = scope["method"]
                await self._handle(
                    scope=scope,
                    receive=receive,
                    send=send,
                    name=f"{method} {scope['path']}",
                    method=method,
                    response_start="http.response.start",
                )

            case "websocket":
                await self._handle(
                    scope=scope,
                    receive=receive,
                    send=send,
                    name=f"WS {scope['path']}",
                    # a websocket connection carries no method - the name of its
                    # scope says what it is instead, and nothing is recorded as
                    # the method it does not have
                    method=None,
                    response_start="websocket.http.response.start",
                )

            case _:
                await self._app(scope, receive, send)

    async def _handle(
        self,
        *,
        scope: Scope,
        receive: Receive,
        send: Send,
        name: str,
        method: str | None,
        response_start: str,
    ) -> None:
        with ctx.presets(*self._context.presets):
            async with ctx.scope(
                name,
                *self._context.request_state(),
                observability=self._context.request_observability(scope),
            ) as trace_id:
                # the status of the response, which only the message carrying it
                # reports - an aborted request is answered with none at all
                status: int | None = None

                async def traced_send(message: Message) -> None:
                    nonlocal status
                    if message["type"] == response_start:
                        status = message["status"]
                        # headers are optional in the message - a response without
                        # any is what an application sending raw messages can do
                        message.setdefault("headers", [])
                        MutableHeaders(scope=message).update(
                            {
                                "trace-id": trace_id,
                                **ctx.trace_context(),
                            }
                        )

                    await send(message)

                # an exception which does not fail the request is held here rather than
                # raised, so it does not travel through the scope - and is reraised once
                # the scope was left, for whatever handles it upstream
                withheld: BaseException | None = None
                try:
                    # errors are left to propagate through the scope, which is what
                    # records them as the failure of the request they belong to
                    await self._app(scope, receive, traced_send)

                except (HTTPException, WebSocketException, ClientDisconnect) as exc:
                    withheld = exc

                finally:
                    # recorded here rather than before the request - the route it
                    # matched is resolved by the routing below this middleware,
                    # and the status only by the response. Recorded even for a
                    # request which failed, which is where it is needed most
                    ctx.record_info(
                        attributes=_request_attributes(
                            scope,
                            method=method,
                            status=status,
                        )
                    )

        if withheld is not None:
            raise withheld


def _request_attributes(
    scope: Scope,
    /,
    *,
    method: str | None,
    status: int | None,
) -> Mapping[str, ObservabilityAttribute]:
    """Describe a request the way the HTTP semantic conventions of OpenTelemetry do.

    Recorded once the request was handled, which is when the route it matched
    and the status it was answered with are both available. ``http.route`` is
    the template behind the requested path, so it is what makes the requests of
    a parameterized route findable as one, while ``url.path`` keeps the path
    which was actually requested.

    The route is read from the request scope, which is where the routing leaves
    the route it matched - FastAPI puts it there, Starlette does not, so a plain
    Starlette application records no ``http.route`` unless it reports one itself
    through ``ctx.record_info(attributes={"http.route": ...})`` from within the
    request. Resolving it here instead would mean matching the route table a
    second time for every request, which is the cost the routing already paid.

    The method is recorded as received, and only for an ``http`` request - a
    websocket connection carries none, so reporting one would be inventing it.

    The query string is left out - it carries credentials often enough that
    recording it by default would leak them - and so is the address of the
    caller, which identifies it.
    """
    attributes: MutableMapping[str, ObservabilityAttribute] = {
        "url.path": scope["path"],
    }

    if method is not None:
        attributes["http.request.method"] = method

    scheme: Any | None = scope.get("scheme")
    if scheme:
        attributes["url.scheme"] = scheme

    # `path_format` is the template of the path a route matched, which is what a
    # FastAPI `APIRoute` left in the scope. Read defensively - the scope of a
    # request which matched nothing holds no route at all
    route_path: Any | None = getattr(scope.get("route"), "path_format", None)
    if isinstance(route_path, str):
        attributes["http.route"] = route_path

    protocol_version: Any | None = scope.get("http_version")
    if protocol_version:
        attributes["network.protocol.version"] = protocol_version

    if status is not None:
        attributes["http.response.status_code"] = status

    return attributes
