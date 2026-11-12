import re
from collections.abc import MutableMapping, MutableSequence, Sequence
from time import monotonic
from typing import Any, Final, final

from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException, WebSocketException
from starlette.requests import ClientDisconnect
from starlette.routing import BaseRoute, Host, Mount
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocketDisconnect

from haiway.context import ObservabilityAttribute
from haiway.context.access import ctx
from haiway.starlette.context import ServerContext
from haiway.starlette.observability import (
    CONNECTION_DURATION_METRIC,
    KNOWN_METHODS,
    OTHER_METHOD,
    REQUEST_DURATION_METRIC,
    RESPONSE_START_DURATION_ATTRIBUTE,
    request_metric_attributes,
)

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
      it as one ``server`` trace, and named after the request: the method and the
      requested path - ``"GET /users/12345"`` - with ``"WS"`` in place of the method for a
      websocket connection, which carries none. Other request types, ``lifespan``
      included, are passed through untouched.
    - the request is recorded into its scope as the HTTP semantic conventions of
      OpenTelemetry describe it - ``http.route``, ``url.path``,
      ``http.request.method`` and ``http.response.status_code`` among the
      attributes - once it was handled, which is when the route it matched and
      the status it was answered with are known. ``http.route`` is what a
      parameterized route is findable by, since the scope name carries the path
      which was actually requested rather than the template behind it. An OpenTelemetry span
      is renamed after the template, as ``"GET /users/{id}"``, once it is known. The wait
      for the headers of the response - what its consumer waited for an answer -
      is recorded among them as ``http.server.response.start.duration``, in
      seconds, when one was started.
    - how long the request took is recorded as one histogram, in seconds:
      ``http.server.request.duration``, or ``websocket.server.duration`` for a
      connection, which is no HTTP request. Every request is measured, the ones
      which failed or were never answered included. It is kept by the bounded
      attributes of the request only - see ``request_metric_attributes``.
    - a response carries the trace headers of its request scope, whether it was
      produced by an endpoint or by anything answering on its behalf below this
      middleware. For a websocket request that is the response denying its
      handshake - an accepted connection switches the protocol rather than
      answering, so it carries none. An entry a header can not hold is left out
      rather than failing the response it belongs to.
    - an exception which no handler answered propagates through the scope of its
      request, which is what records it as the failure of that request, and is
      reraised afterwards. Answering it is left to the application: the server
      error handling of the framework produces the ``500`` the client receives -
      the plain one, the traceback page of a ``debug`` application, or a
      registered handler of ``Exception`` or ``500``. A Starlette application
      installs that error handling above every middleware it is given, so its
      response is only within the request scope - and only carries its trace
      headers - when it is nested below this middleware instead, which is what
      ``application()`` arranges.

    ``HTTPException``, ``WebSocketException``, ``ClientDisconnect`` and
    ``WebSocketDisconnect`` are not a failure of the request they end - the first
    two are how an application asks for a specific response, the others are a
    consumer which went away. They are
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
    ...     middleware=[
    ...         Middleware(ContextMiddleware, context=context),
    ...         # the server error handling, below the scope rather than above it
    ...         Middleware(ServerErrorMiddleware, handler=None, debug=False),
    ...     ],
    ...     lifespan=context.lifespan,
    ... )

    Notes
    -----
    Placing it as the outermost middleware is what makes the context available
    to the other middlewares of the application - which is where
    ``application()`` puts it, followed by a ``ServerErrorMiddleware`` holding
    the server error handler of the application, so an unhandled failure is
    answered from within the scope of its request rather than above it. State
    derived from a request, like the identity of its caller, can be added by a
    middleware nested below it through ``ctx.updating(...)``.
    """

    __slots__ = (
        "_app",
        "_context",
        "_mounts",
    )

    def __init__(
        self,
        app: ASGIApp,
        /,
        context: ServerContext,
    ) -> None:
        self._app: ASGIApp = app
        self._context: ServerContext = context
        self._mounts: _MountTemplates = _MountTemplates()

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
                    name=f"{method} {_printable(scope['path'])}",
                    method=method,
                    response_start="http.response.start",
                    duration_metric=REQUEST_DURATION_METRIC,
                )

            case "websocket":
                await self._handle(
                    scope=scope,
                    receive=receive,
                    send=send,
                    name=f"WS {_printable(scope['path'])}",
                    # a websocket connection carries no method - the name of its
                    # scope says what it is instead, and nothing is recorded as
                    # the method it does not have
                    method=None,
                    response_start="websocket.http.response.start",
                    # a connection is no HTTP request - it switches the protocol
                    # and lasts until closed, so it is measured under its own name.
                    # the response denying a handshake is an HTTP response though
                    duration_metric=CONNECTION_DURATION_METRIC,
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
        duration_metric: str,
    ) -> None:
        # the earliest moment the application can observe the request. monotonic,
        # so a change of the system clock does not reach the measurement
        received_at: float = monotonic()

        with ctx.presets(*self._context.presets):
            async with ctx.scope(
                name,
                *self._context.request_state(),
                observability=self._context.request_observability(scope),
                kind="server",
            ) as trace_id:
                # the status of the response, which only the message carrying it
                # reports - an aborted request is answered with none at all
                status: int | None = None
                # only the moment is kept - nothing is measured while handling
                response_started_at: float | None = None

                async def traced_send(message: Message) -> None:
                    nonlocal response_started_at, status
                    if message["type"] == response_start:
                        # taken before the message is handed over, like the status
                        # it is paired with - a send failing on a gone consumer
                        # still started the response
                        response_started_at = monotonic()
                        status = message["status"]
                        # headers are optional in the message - a response without
                        # any is what an application sending raw messages can do
                        message.setdefault("headers", [])
                        headers: MutableHeaders = MutableHeaders(scope=message)
                        for header, value in {
                            "trace-id": trace_id,
                            **ctx.trace_context(),
                        }.items():
                            # an entry a header can not hold is left out - failing
                            # the response it was meant to accompany would be worse
                            # than a missing trace header
                            if _header_name_safe(header) and _header_value_safe(value):
                                headers[header] = value

                    await send(message)

                # an exception which does not fail the request is held here rather than
                # raised, so it does not travel through the scope - and is reraised once
                # the scope was left, for whatever handles it upstream
                withheld: BaseException | None = None
                try:
                    # errors are left to propagate through the scope, which is what
                    # records them as the failure of the request they belong to
                    await self._app(scope, receive, traced_send)

                except (
                    HTTPException,
                    WebSocketException,
                    ClientDisconnect,
                    WebSocketDisconnect,
                ) as exc:
                    withheld = exc

                finally:
                    # measured first, so what is done with it is not measured too
                    duration: float = monotonic() - received_at
                    # recorded here rather than before the request - the route it
                    # matched is resolved by the routing below this middleware,
                    # and the status only by the response. Recorded even for a
                    # request which failed, which is where it is needed most
                    attributes: MutableMapping[str, ObservabilityAttribute] = _request_attributes(
                        scope,
                        mounts=self._mounts,
                        method=method,
                        status=status,
                    )
                    if response_started_at is not None:
                        # the wait for an answer, which the span does not report on
                        # its own - unlike the duration, which is what the span is
                        attributes[RESPONSE_START_DURATION_ATTRIBUTE] = (
                            response_started_at - received_at
                        )

                    ctx.record_info(attributes=attributes)
                    ctx.record_info(
                        metric=duration_metric,
                        value=duration,
                        unit="s",
                        kind="histogram",
                        # bounded dimensions only - see `request_metric_attributes`
                        attributes=request_metric_attributes(attributes),
                    )

        if withheld is not None:
            raise withheld


def _request_attributes(
    scope: Scope,
    /,
    *,
    mounts: _MountTemplates,
    method: str | None,
    status: int | None,
) -> MutableMapping[str, ObservabilityAttribute]:
    """Describe a request the way the HTTP semantic conventions of OpenTelemetry do.

    Recorded once the request was handled, which is when the route it matched
    and the status it was answered with are both available. ``http.route`` is
    the template behind the requested path, so it is what makes the requests of
    a parameterized route findable as one, while ``url.path`` keeps the path
    which was actually requested.

    The route is read from the request scope, which is where the routing of both
    Starlette and FastAPI leaves the route it matched. A request which matched no
    route records no ``http.route``. Resolving it here instead would mean matching
    the route table a second time for every request, which is the cost the
    routing already paid. A route served from a mount is prefixed with the
    templates of the mounts leading to it, found by the route itself once per
    route - not with the root path, which holds the values a parameterized mount
    matched.

    The method is recorded only for an ``http`` request - a websocket connection
    carries none, so reporting one would be inventing it. A method outside the
    standard ones is recorded as ``_OTHER``, the received value moving to
    ``http.request.method_original`` on the trace: the method is chosen by the
    caller, and it is a dimension of the request metrics and the name of the
    span, where an unbounded set of values is how a metrics store is brought down.

    The query string is left out - it carries credentials often enough that
    recording it by default would leak them - and so is the address of the
    caller, which identifies it.
    """
    attributes: MutableMapping[str, ObservabilityAttribute] = {
        "url.path": _printable(scope["path"]),
    }

    if method is not None:
        if method in KNOWN_METHODS:
            attributes["http.request.method"] = method

        else:
            attributes["http.request.method"] = OTHER_METHOD
            attributes["http.request.method_original"] = method

    scheme: Any | None = scope.get("scheme")
    if scheme:
        attributes["url.scheme"] = scheme

    # `path_format` is the template of the path a route matched, which both a
    # Starlette `Route` and a FastAPI `APIRoute` carry. Read defensively - the scope
    # of a request which matched nothing holds no route at all
    route: Any | None = scope.get("route")
    route_path: Any | None = getattr(route, "path_format", None)
    if isinstance(route_path, str):
        # the template of a route is relative to the mount it is served from, so
        # the templates of the mounts are put in front of it - otherwise the routes
        # of different mounts would be reported as one. The root path can't serve
        # as the prefix, it holds the values a parameterized mount matched
        prefix: str | None = mounts.prefix(scope, route=route)
        if prefix is not None:
            attributes["http.route"] = prefix + route_path

    protocol_version: Any | None = scope.get("http_version")
    if protocol_version:
        attributes["network.protocol.version"] = protocol_version

    if status is not None:
        attributes["http.response.status_code"] = status

    return attributes


@final
class _MountTemplates:
    # the templates of the mounts leading to each route, found by the route itself -
    # routing keeps the route it ended at alone. Worked out once per route of the
    # application, a route added later is found when first requested. Keyed by
    # identity, which a route does not hash by, holding the route to tell it apart
    __slots__ = ("_known",)

    def __init__(self) -> None:
        self._known: MutableMapping[int, tuple[object, str | None]] = {}

    def prefix(
        self,
        scope: Scope,
        /,
        *,
        route: object,
    ) -> str | None:
        root_path: str = scope.get("root_path") or ""
        if root_path == scope.get("app_root_path", root_path):
            return ""  # served by no mount

        known: tuple[object, str | None] | None = self._known.get(id(route))
        if known is not None and known[0] is route:
            return known[1]

        prefix: str | None = _found_prefix(scope.get("router"), route=route)
        self._known[id(route)] = (route, prefix)
        return prefix


def _found_prefix(
    router: Any | None,
    /,
    *,
    route: object,
) -> str | None:
    # None when the route is not reached exactly one way
    found: MutableSequence[str] = []
    pending: MutableSequence[tuple[str, Sequence[BaseRoute]]] = [
        ("", getattr(router, "routes", ()))
    ]
    while pending:
        prefix, routes = pending.pop()
        for candidate in routes:
            if candidate is route:
                found.append(prefix)

            if isinstance(candidate, Mount):
                # the template routing normalized - `{tenant:int}` is `{tenant}`, as
                # in the template of every route
                template: str = candidate.path_format.removesuffix("/{path}")
                pending.append((prefix + template, candidate.routes))

            elif isinstance(candidate, Host):  # chosen by the host, adds no path
                pending.append((prefix, candidate.routes))

    return found[0] if len(found) == 1 else None


def _printable(
    value: str,
    /,
) -> str:
    # the path is decoded by the server, so a request can carry control characters
    # in it - escaped, so they can not forge lines of whatever renders the record
    if value.isprintable():
        return value

    return "".join(char if char.isprintable() else repr(char)[1:-1] for char in value)


# a token, the only form a header name takes (RFC 9110, section 5.1)
_HEADER_NAME: Final[re.Pattern[str]] = re.compile(r"[!#$%&'*+\-.^_`|~0-9A-Za-z]+")


def _header_name_safe(
    name: str,
    /,
) -> bool:
    return _HEADER_NAME.fullmatch(name) is not None


def _header_value_safe(
    value: str,
    /,
) -> bool:
    return value.isascii() and value.isprintable()
