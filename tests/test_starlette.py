from asyncio import CancelledError, Event, Queue, Task, create_task
from collections.abc import AsyncGenerator, Iterable, Mapping, MutableSequence
from contextlib import asynccontextmanager
from logging import Logger, getLogger
from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("starlette")

from pytest import MonkeyPatch, mark, raises
from starlette.applications import Starlette
from starlette.exceptions import HTTPException, WebSocketException
from starlette.middleware import Middleware
from starlette.requests import ClientDisconnect, Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route, WebSocketRoute
from starlette.types import ASGIApp, Message, Receive, Scope, Send
from starlette.websockets import WebSocket

from haiway import ContextMissing, ContextPresets, LoggerObservability, Observability, State, ctx
from haiway.starlette import (
    ContextMiddleware,
    ServerContext,
    application,
    request_trace_context,
)

# where the backend recording a request is built out of a `Logger` - patched to
# count how many of them a run of an application builds
from haiway.starlette import context as server_context_module
from tests.asgi import (
    TRACE_ID_HEADER,
    LogCapture,
    Result,
    http_scope,
    receive_request,
    running,
    send_request,
    websocket_scope,
)

_TRACE_ID: UUID = UUID("0af7651916cd43dd8448eb211c80319c")


class ExampleState(State):
    value: str = "example"


class DisposableState(State):
    value: str = "disposable"


class PresetState(State):
    value: str = "preset"


class ExampleDisposable:
    def __init__(
        self,
        log: MutableSequence[str],
        /,
    ) -> None:
        self.log: MutableSequence[str] = log

    async def __aenter__(self) -> Iterable[State]:
        self.log.append("enter")
        return (DisposableState(),)

    async def __aexit__(
        self,
        exc_type: Any,
        exc_val: Any,
        exc_tb: Any,
    ) -> None:
        self.log.append("exit")


@mark.asyncio
async def test_state_is_available_within_endpoint() -> None:
    async def endpoint(request: Request) -> Response:
        return PlainTextResponse(
            f"{ctx.state(ExampleState).value}/{ctx.state(DisposableState).value}"
        )

    log: MutableSequence[str] = []
    app: Starlette = application(
        ServerContext(
            ExampleState(),
            disposables=(ExampleDisposable(log),),
        ),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        assert log == ["enter"]
        result: Result = await send_request(app)

    assert result.status == 200
    assert result.body == b"example/disposable"
    assert log == ["enter", "exit"]


@mark.asyncio
async def test_declared_state_takes_precedence_over_disposables() -> None:
    class Conflicting:
        async def __aenter__(self) -> Iterable[State]:
            return (ExampleState(value="disposable"),)

        async def __aexit__(
            self,
            exc_type: Any,
            exc_val: Any,
            exc_tb: Any,
        ) -> None:
            pass

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse(ctx.state(ExampleState).value)

    app: Starlette = application(
        ServerContext(
            ExampleState(value="declared"),
            disposables=(Conflicting(),),
        ),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.body == b"declared"


@mark.asyncio
async def test_response_carries_trace_headers() -> None:
    async def endpoint(request: Request) -> Response:
        return PlainTextResponse(ctx.trace_id())

    app: Starlette = application(routes=[Route("/example", endpoint)])

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 200
    assert result.headers[TRACE_ID_HEADER] == result.body.decode()


@mark.asyncio
async def test_handled_exception_response_carries_trace_headers() -> None:
    async def endpoint(request: Request) -> Response:
        raise HTTPException(status_code=404, detail="missing")

    app: Starlette = application(routes=[Route("/example", endpoint)])

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 404
    assert result.body == b"missing"
    assert TRACE_ID_HEADER in result.headers


@mark.asyncio
async def test_unhandled_exception_is_answered_by_the_framework() -> None:
    async def endpoint(request: Request) -> Response:
        raise ValueError("broken")

    app: Starlette = application(routes=[Route("/example", endpoint)])
    result = Result()

    async with running(app):
        with raises(ValueError):  # reraised for the server to report
            await app(http_scope(), receive_request, result.collecting())

    # answered by the server error handling of the framework, which sits above
    # the middleware - so outside of the scope of the request, without its headers
    assert result.status == 500
    assert result.body == b"Internal Server Error"
    assert TRACE_ID_HEADER not in result.headers


@mark.asyncio
async def test_debug_application_keeps_its_error_response() -> None:
    async def endpoint(request: Request) -> Response:
        raise ValueError("broken")

    app: Starlette = application(
        routes=[Route("/example", endpoint)],
        debug=True,
    )
    result = Result()

    async with running(app):
        with raises(ValueError):
            await app(http_scope(), receive_request, result.collecting())

    assert result.status == 500
    assert b"ValueError" in result.body  # the traceback rendered by Starlette
    assert TRACE_ID_HEADER not in result.headers


@mark.asyncio
async def test_error_within_started_response_is_not_replaced() -> None:
    async def endpoint(request: Request) -> Response:
        async def streaming() -> AsyncGenerator[bytes]:
            yield b"partial"
            raise ValueError("broken")

        return StreamingResponse(streaming())

    app: Starlette = application(routes=[Route("/example", endpoint)])
    result = Result()

    async with running(app):
        with raises(ValueError):
            await app(http_scope(), receive_request, result.collecting())

    assert result.status == 200
    assert result.body == b"partial"


@mark.asyncio
async def test_request_without_lifespan_is_refused() -> None:
    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    app: Starlette = application(
        ServerContext(disposables=(ExampleDisposable([]),)),
        routes=[Route("/example", endpoint)],
    )
    result = Result()

    # the state of a request scope is what the lifespan prepares, so there is
    # nothing to serve a request with before it ran
    with raises(ContextMissing):
        await app(http_scope(), receive_request, result.collecting())


@mark.asyncio
async def test_lifespan_can_not_be_entered_twice() -> None:
    log: MutableSequence[str] = []
    # the declared disposables are the instances prepared on startup, not a
    # factory producing fresh ones, so a single context backs a single run
    context = ServerContext(disposables=(ExampleDisposable(log),))

    async with context.lifespan():
        pass

    assert log == ["enter", "exit"]

    with raises(AssertionError):
        async with context.lifespan():
            pass

    assert log == ["enter", "exit"]


@mark.asyncio
async def test_failing_disposable_fails_startup() -> None:
    attempts: MutableSequence[str] = []

    class Failing:
        async def __aenter__(self) -> Iterable[State]:
            attempts.append("enter")
            if len(attempts) == 1:
                raise ValueError("broken")

            return ()

        async def __aexit__(
            self,
            exc_type: Any,
            exc_val: Any,
            exc_tb: Any,
        ) -> None:
            pass

    context = ServerContext(disposables=(Failing(),))

    with raises(ValueError):
        async with context.lifespan():
            pass

    # a failed startup prepared no state, so the next one is not refused
    async with context.lifespan():
        pass

    assert len(attempts) == 2


@mark.asyncio
async def test_additional_lifespan_runs_within_prepared_context() -> None:
    log: MutableSequence[str] = []

    @asynccontextmanager
    async def lifespan(app: Starlette) -> AsyncGenerator[None]:
        log.append("startup")
        try:
            yield

        finally:
            log.append("shutdown")

    app: Starlette = application(
        ServerContext(disposables=(ExampleDisposable(log),)),
        lifespan=lifespan,
    )

    async with running(app):
        pass

    assert log == ["enter", "startup", "shutdown", "exit"]


@mark.asyncio
async def test_nested_middleware_runs_within_context() -> None:
    class NestedMiddleware:
        def __init__(
            self,
            app: ASGIApp,
            /,
        ) -> None:
            self.app: ASGIApp = app

        async def __call__(
            self,
            scope: Scope,
            receive: Receive,
            send: Send,
        ) -> None:
            with ctx.updating(ExampleState(value="middleware")):
                await self.app(scope, receive, send)

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse(ctx.state(ExampleState).value)

    app: Starlette = application(
        ServerContext(ExampleState()),
        routes=[Route("/example", endpoint)],
        middleware=[Middleware(NestedMiddleware)],
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.body == b"middleware"


@mark.asyncio
async def test_presets_are_available_within_endpoint() -> None:
    async def endpoint(request: Request) -> Response:
        async with ctx.scope("example-preset"):
            return PlainTextResponse(ctx.state(PresetState).value)

    app: Starlette = application(
        ServerContext(
            presets=(ContextPresets.of("example-preset", PresetState(value="from-preset")),),
        ),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.body == b"from-preset"


@mark.asyncio
async def test_observability_preparing_receives_trace_context() -> None:
    recorded: MutableSequence[tuple[str | None, str | None]] = []

    def observability(
        *,
        traceparent: str | None,
        tracestate: str | None,
    ) -> None:
        recorded.append((traceparent, tracestate))

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    app: Starlette = application(
        ServerContext(observability=observability),
        routes=[Route("/example", endpoint)],
    )

    traceparent: str = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    async with running(app):
        await send_request(
            app,
            headers=(
                (b"traceparent", f" {traceparent} ".encode()),
                (b"tracestate", b"vendor=value"),
            ),
        )
        await send_request(app)  # nothing to continue

    assert recorded == [(traceparent, "vendor=value"), (None, None)]


@mark.asyncio
async def test_conflicting_trace_context_is_discarded() -> None:
    recorded: MutableSequence[tuple[str | None, str | None]] = []

    def observability(
        *,
        traceparent: str | None,
        tracestate: str | None,
    ) -> None:
        recorded.append((traceparent, tracestate))

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    app: Starlette = application(
        ServerContext(observability=observability),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        await send_request(
            app,
            headers=(
                (b"traceparent", b"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"),
                (b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
                (b"tracestate", b"vendor=value"),
            ),
        )

    assert recorded == [(None, None)]


@mark.asyncio
async def test_websocket_is_handled_within_context() -> None:
    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text(ctx.state(ExampleState).value)
        await websocket.close()

    app: Starlette = application(
        ServerContext(ExampleState(value="websocket")),
        routes=[WebSocketRoute("/example", endpoint)],
    )

    incoming: Queue[Message] = Queue()
    outgoing: MutableSequence[Message] = []

    async def send(message: Message) -> None:
        outgoing.append(message)

    await incoming.put({"type": "websocket.connect"})
    async with running(app):
        await app(
            websocket_scope(),
            incoming.get,
            send,
        )

    assert [message["type"] for message in outgoing] == [
        "websocket.accept",
        "websocket.send",
        "websocket.close",
    ]
    assert outgoing[1]["text"] == "websocket"


@mark.asyncio
async def test_denied_websocket_handshake_carries_trace_headers() -> None:
    class Rejected(Exception):
        pass

    async def socket(websocket: WebSocket) -> None:
        raise Rejected  # before the connection was accepted

    async def rejected(
        websocket: WebSocket,
        exception: Exception,
    ) -> Response:
        return PlainTextResponse("rejected", status_code=403)

    app: Starlette = application(
        ServerContext(),
        routes=[WebSocketRoute("/socket", socket)],
        exception_handlers={Rejected: rejected},
    )

    incoming: Queue[Message] = Queue()
    await incoming.put({"type": "websocket.connect"})
    outgoing: MutableSequence[Message] = []

    async def send(message: Message) -> None:
        outgoing.append(message)

    async with running(app):
        await app(websocket_scope(path="/socket"), incoming.get, send)

    # the denial of a handshake is the one response a websocket connection
    # carries - sent renamed under the websocket prefix, yet a response
    assert outgoing[0]["type"] == "websocket.http.response.start"
    assert outgoing[0]["status"] == 403
    headers: Mapping[str, str] = {
        name.decode(): value.decode() for name, value in outgoing[0]["headers"]
    }
    assert headers[TRACE_ID_HEADER]


@mark.asyncio
async def test_failing_websocket_error_is_reraised() -> None:
    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        raise ValueError("broken")

    app: Starlette = application(routes=[WebSocketRoute("/example", endpoint)])

    incoming: Queue[Message] = Queue()

    async def send(message: Message) -> None:
        pass

    await incoming.put({"type": "websocket.connect"})
    async with running(app):
        with raises(ValueError):
            await app(
                {
                    "type": "websocket",
                    "asgi": {"version": "3.0", "spec_version": "2.4"},
                    "path": "/example",
                    "raw_path": b"/example",
                    "query_string": b"",
                    "root_path": "",
                    "scheme": "ws",
                    "headers": [],
                    "client": ("127.0.0.1", 54321),
                    "server": ("testserver", 80),
                    "subprotocols": [],
                    "state": {},
                },
                incoming.get,
                send,
            )


@mark.asyncio
async def test_middleware_installed_by_hand_provides_scopes() -> None:
    async def endpoint(request: Request) -> Response:
        return PlainTextResponse(ctx.trace_id())

    # what plugging an existing application in looks like - the two pieces
    # installed separately rather than through the factory
    context = ServerContext()
    app: Starlette = Starlette(
        routes=[Route("/example", endpoint)],
        lifespan=context.lifespan,
        middleware=[Middleware(ContextMiddleware, context=context)],
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 200
    assert result.headers[TRACE_ID_HEADER] == result.body.decode()


def test_request_trace_context_reads_headers() -> None:
    traceparent: str = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert request_trace_context(
        http_scope(
            headers=(
                (b"traceparent", f" {traceparent} ".encode()),
                (b"tracestate", b"vendor=value"),
            )
        )
    ) == {"traceparent": traceparent, "tracestate": "vendor=value"}


def test_request_trace_context_ignores_incomplete_headers() -> None:
    assert request_trace_context(http_scope(headers=((b"tracestate", b"vendor=value"),))) == {}
    assert request_trace_context(http_scope(headers=((b"traceparent", b"  "),))) == {}
    assert request_trace_context(http_scope()) == {}
    assert request_trace_context({"type": "lifespan"}) == {}


def test_request_trace_context_discards_conflicting_traceparents() -> None:
    # no single position to continue - the trace has to be restarted instead
    assert (
        request_trace_context(
            http_scope(
                headers=(
                    (b"traceparent", b"00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"),
                    (b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
                    (b"tracestate", b"vendor=value"),
                )
            )
        )
        == {}
    )


def test_request_trace_context_reads_the_first_tracestate() -> None:
    # a caller splitting a long trace state across several headers has to join
    # them itself - only the first one is read
    traceparent: str = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    assert request_trace_context(
        http_scope(
            headers=(
                (b"traceparent", traceparent.encode()),
                (b"tracestate", b" vendor=value "),
                (b"tracestate", b"other=state"),
            )
        )
    ) == {"traceparent": traceparent, "tracestate": "vendor=value"}


def test_request_trace_context_passes_malformed_traceparent_on() -> None:
    # validation belongs to the observability backend, which the specification
    # requires to reject a malformed value and start its own trace
    assert request_trace_context(http_scope(headers=((b"traceparent", b"broken"),))) == {
        "traceparent": "broken"
    }


@mark.asyncio
async def test_registered_server_error_handler_answers_the_request() -> None:
    async def endpoint(request: Request) -> Response:
        raise ValueError("broken")

    async def handle_server_error(
        request: Request,
        exception: Exception,
    ) -> Response:
        return PlainTextResponse("handled", status_code=503)

    app: Starlette = application(
        routes=[Route("/example", endpoint)],
        # a server error handler answers above the middleware, in place of the
        # plain `500` the framework would produce
        exception_handlers={Exception: handle_server_error},
    )
    result = Result()

    async with running(app):
        with raises(ValueError):  # reraised for the server to report
            await app(http_scope(), receive_request, result.collecting())

    assert result.status == 503
    assert result.body == b"handled"
    # the handler runs above every middleware, so outside of the request scope
    assert TRACE_ID_HEADER not in result.headers


@mark.asyncio
async def test_registered_status_error_handler_answers_the_request() -> None:
    async def endpoint(request: Request) -> Response:
        raise ValueError("broken")

    async def handle_server_error(
        request: Request,
        exception: Exception,
    ) -> Response:
        return PlainTextResponse("handled", status_code=503)

    app: Starlette = application(
        routes=[Route("/example", endpoint)],
        # `500` resolves the same handler slot as `Exception` does
        exception_handlers={500: handle_server_error},
    )
    result = Result()

    async with running(app):
        with raises(ValueError):
            await app(http_scope(), receive_request, result.collecting())

    assert result.status == 503
    assert result.body == b"handled"


@mark.asyncio
async def test_handler_of_another_exception_keeps_the_error_response() -> None:
    class ExampleError(Exception):
        pass

    async def endpoint(request: Request) -> Response:
        raise ValueError("broken")

    async def handle_example_error(
        request: Request,
        exception: Exception,
    ) -> Response:
        return PlainTextResponse("handled", status_code=418)

    app: Starlette = application(
        routes=[Route("/example", endpoint)],
        # not a server error handler - the plain `500` of the framework is what
        # answers the failure of the request
        exception_handlers={ExampleError: handle_example_error},
    )
    result = Result()

    async with running(app):
        with raises(ValueError):
            await app(http_scope(), receive_request, result.collecting())

    assert result.status == 500
    assert result.body == b"Internal Server Error"
    assert TRACE_ID_HEADER not in result.headers


@mark.asyncio
async def test_http_exception_is_not_recorded_as_a_failure() -> None:
    recorded: MutableSequence[str | None] = []

    class Records:
        def observability(self) -> Observability:
            def scope_exiting(
                scope: Any,
                /,
                *,
                exception: BaseException | None,
            ) -> None:
                recorded.append(None if exception is None else type(exception).__name__)

            return Observability(
                trace_identifying=lambda scope, /: _TRACE_ID,
                log_recording=lambda scope, /, level, message, *args, exception: None,
                metric_recording=lambda scope, /, level, **kwargs: None,
                event_recording=lambda scope, /, level, **kwargs: None,
                attributes_recording=lambda scope, /, level, attributes: None,
                scope_entering=lambda scope, /: _TRACE_ID.hex,
                scope_exiting=scope_exiting,
                trace_context_encoding=lambda scope, /: {},
            )

    async def raising(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        raise HTTPException(status_code=409, detail="conflict")

    context = ServerContext(observability=Records().observability())
    app: ASGIApp = ContextMiddleware(raising, context=context)
    result = Result()

    async with context.lifespan():
        with raises(HTTPException):
            await app(http_scope(), receive_request, result.collecting())

    # an intended response is not the failure of the request asking for it
    assert recorded == [None]


@mark.asyncio
async def test_client_disconnect_is_not_recorded_as_a_failure() -> None:
    recorded: MutableSequence[str | None] = []

    def observability() -> Observability:
        def scope_exiting(
            scope: Any,
            /,
            *,
            exception: BaseException | None,
        ) -> None:
            recorded.append(None if exception is None else type(exception).__name__)

        return Observability(
            trace_identifying=lambda scope, /: _TRACE_ID,
            log_recording=lambda scope, /, level, message, *args, exception: None,
            metric_recording=lambda scope, /, level, **kwargs: None,
            event_recording=lambda scope, /, level, **kwargs: None,
            attributes_recording=lambda scope, /, level, attributes: None,
            scope_entering=lambda scope, /: _TRACE_ID.hex,
            scope_exiting=scope_exiting,
            trace_context_encoding=lambda scope, /: {},
        )

    async def endpoint(request: Request) -> Response:
        await request.body()  # the consumer went away instead of sending one
        return PlainTextResponse("unreachable")

    app: Starlette = application(
        ServerContext(observability=observability()),
        routes=[Route("/example", endpoint, methods=["POST"])],
    )
    result = Result()

    async def receive() -> Message:
        return {"type": "http.disconnect"}

    async with running(app):
        with raises(ClientDisconnect):
            await app(http_scope(method="POST"), receive, result.collecting())

    # a consumer which went away is not a failure of the request it abandoned
    assert recorded == [None]
    # nothing is answered here either - what reaches the connection which is
    # already gone is the response of the outer error handling of the framework
    assert TRACE_ID_HEADER not in result.headers


@mark.asyncio
async def test_http_exception_is_left_unanswered_without_handlers() -> None:
    async def raising(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        raise HTTPException(status_code=409, detail="conflict")

    context = ServerContext()
    app: ASGIApp = ContextMiddleware(raising, context=context)
    result = Result()

    async with context.lifespan():
        # an intended response, left to whatever handles it - not turned into a 500
        with raises(HTTPException):
            await app(http_scope(), receive_request, result.collecting())

    assert result.status == 0


@mark.asyncio
async def test_websocket_exception_is_left_unanswered_without_handlers() -> None:
    async def raising(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        raise WebSocketException(code=1008, reason="rejected")

    context = ServerContext()
    app: ASGIApp = ContextMiddleware(raising, context=context)
    sent: MutableSequence[Message] = []

    async def receive() -> Message:
        return {"type": "websocket.connect"}

    async def send(message: Message) -> None:
        sent.append(message)

    async with context.lifespan():
        with raises(WebSocketException):
            await app(
                websocket_scope(),
                receive,
                send,
            )

    assert sent == []


@mark.asyncio
async def test_provided_logger_records_request_scopes() -> None:
    logger: Logger = getLogger("test-application")

    async def endpoint(request: Request) -> Response:
        ctx.log_info("recorded")
        return PlainTextResponse("done")

    app: Starlette = application(
        ServerContext(observability=logger),
        routes=[Route("/example", endpoint)],
    )

    with LogCapture(logger) as records:
        async with running(app):
            assert (await send_request(app)).status == 200

    assert any("recorded" in record for record in records)
    assert any("Entering scope: GET" in record for record in records)


@mark.asyncio
async def test_provided_logger_gets_a_backend_per_request(
    monkeypatch: MonkeyPatch,
) -> None:
    logger: Logger = getLogger("test-backend-per-request")
    prepared: MutableSequence[Logger | None] = []

    def counted(
        logger: Logger | None = None,
        /,
        *,
        debug_context: bool = False,
    ) -> Observability:
        prepared.append(logger)
        return LoggerObservability(logger, debug_context=debug_context)

    # the backend recording a request is built out of the logger here
    monkeypatch.setattr(server_context_module, "LoggerObservability", counted)

    async def endpoint(request: Request) -> Response:
        ctx.log_info("recorded")
        return PlainTextResponse("done")

    context = ServerContext(observability=logger)
    # a backend built out of the logger for the request being handled, rather than
    # the logger itself or a single backend wrapping it when the context is declared
    first: Observability | Logger = context.request_observability(http_scope())
    second: Observability | Logger = context.request_observability(http_scope())
    assert first is not logger
    assert second is not first
    prepared.clear()

    app: Starlette = application(
        context,
        routes=[Route("/example", endpoint)],
    )

    with LogCapture(logger) as records:
        async with running(app):
            for _ in range(4):
                assert (await send_request(app)).status == 200

    # so each request records into a backend of its own, which is released along
    # with it - one backend shared by the application would instead retain the
    # scopes of every request which never completed, an abandoned generator
    # among them, for as long as it runs
    assert prepared == [logger, logger, logger, logger]
    assert len([record for record in records if "recorded" in record]) == 4


@mark.asyncio
async def test_prepared_logger_records_request_scopes() -> None:
    logger: Logger = getLogger("test-prepared-application")

    async def endpoint(request: Request) -> Response:
        ctx.log_info("recorded")
        return PlainTextResponse("done")

    app: Starlette = application(
        ServerContext(observability=lambda **_: LoggerObservability(logger)),
        routes=[Route("/example", endpoint)],
    )

    with LogCapture(logger) as records:
        async with running(app):
            assert (await send_request(app)).status == 200

    assert any("recorded" in record for record in records)
    assert any("Entering scope: GET" in record for record in records)


@mark.asyncio
async def test_cancelled_lifespan_is_not_a_failure() -> None:
    log: MutableSequence[str] = []
    context = ServerContext(disposables=(ExampleDisposable(log),))

    with raises(CancelledError):
        async with context.lifespan():
            raise CancelledError

    assert log == ["enter", "exit"]


@mark.asyncio
async def test_trace_headers_are_added_to_headerless_response() -> None:
    async def responding(
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        await send({"type": "http.response.start", "status": 201})
        await send({"type": "http.response.body", "body": b"raw"})

    context = ServerContext()
    app: ASGIApp = ContextMiddleware(responding, context=context)
    result = Result()

    async with context.lifespan():
        await app(http_scope(), receive_request, result.collecting())

    assert result.status == 201
    assert result.body == b"raw"
    assert TRACE_ID_HEADER in result.headers


@mark.asyncio
async def test_trace_context_headers_are_matched_case_insensitively() -> None:
    recorded: MutableSequence[tuple[str | None, str | None]] = []

    def observability(
        *,
        traceparent: str | None,
        tracestate: str | None,
    ) -> None:
        recorded.append((traceparent, tracestate))

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    app: Starlette = application(
        ServerContext(observability=observability),
        routes=[Route("/example", endpoint)],
    )

    traceparent: str = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
    async with running(app):
        # a server which does not lowercase header names, as ASGI requires it to,
        # must not silently cost the trace of the caller
        await send_request(
            app,
            headers=(
                (b"TraceParent", traceparent.encode()),
                (b"TraceState", b"vendor=value"),
            ),
        )

    assert recorded == [(traceparent, "vendor=value")]


@mark.asyncio
async def test_request_during_startup_is_refused() -> None:
    preparing: Event = Event()
    holding: Event = Event()

    class Slow:
        async def __aenter__(self) -> Iterable[State]:
            preparing.set()
            await holding.wait()  # startup is still running
            return ()

        async def __aexit__(
            self,
            exc_type: Any,
            exc_val: Any,
            exc_tb: Any,
        ) -> None:
            pass

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    context = ServerContext(disposables=(Slow(),))
    app: Starlette = application(context, routes=[Route("/example", endpoint)])
    startup: Task[None] = create_task(_entering(context))
    await preparing.wait()

    result = Result()
    # requests are not served with state which is not prepared yet - a server
    # holds them until startup completed, reaching one here fails naming what
    # is missing
    with raises(ContextMissing):
        await app(http_scope(), receive_request, result.collecting())

    holding.set()
    await startup


async def _entering(
    context: ServerContext,
    /,
) -> None:
    async with context.lifespan():
        pass


@mark.asyncio
async def test_default_observability_survives_reconfigured_logging() -> None:
    async def endpoint(request: Request) -> Response:
        ctx.log_info("recorded")
        return PlainTextResponse("done")

    app: Starlette = application(routes=[Route("/example", endpoint)])
    # `setup_logging` disables every logger predating it, so a default of our own
    # making would drop request records without a trace of having done so
    silenced: Logger = getLogger("haiway.starlette")
    silenced.disabled = True

    try:
        with LogCapture(getLogger()) as records:
            async with running(app):
                assert (await send_request(app)).status == 200

    finally:
        silenced.disabled = False

    assert any("recorded" in record for record in records)


@mark.asyncio
async def test_conditionally_provided_elements_are_ignored() -> None:
    async def endpoint(request: Request) -> Response:
        return PlainTextResponse(ctx.state(ExampleState).value)

    log: MutableSequence[str] = []
    app: Starlette = application(
        # `None` keeps a conditionally provided element from requiring a branch
        ServerContext(
            ExampleState(value="declared"),
            None,
            disposables=(ExampleDisposable(log), None),
        ),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.body == b"declared"
    assert log == ["enter", "exit"]


@mark.asyncio
async def test_composed_lifespan_prepares_the_context_first() -> None:
    log: MutableSequence[str] = []

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    @asynccontextmanager
    async def existing(app: Starlette) -> AsyncGenerator[None]:
        log.append("startup")
        try:
            yield

        finally:
            log.append("shutdown")

    context = ServerContext(disposables=(ExampleDisposable(log),))
    # what an application which already has a lifespan of its own installs
    app: Starlette = Starlette(
        routes=[Route("/example", endpoint)],
        lifespan=context.composed_lifespan(existing),
        middleware=[Middleware(ContextMiddleware, context=context)],
    )

    async with running(app):
        assert (await send_request(app)).status == 200

    # the disposables of the context are prepared around the additional lifespan
    assert log == ["enter", "startup", "shutdown", "exit"]


@mark.asyncio
async def test_startup_runs_outside_of_a_context_scope() -> None:
    @asynccontextmanager
    async def existing(app: Starlette) -> AsyncGenerator[None]:
        # the disposables are prepared, but nothing entered a scope with their
        # state - startup work which needs one enters it, and leaves it, itself
        with raises(ContextMissing):
            ctx.state(DisposableState)

        yield

    context = ServerContext(disposables=(ExampleDisposable([]),))
    app: Starlette = application(context, lifespan=existing)

    async with running(app):
        pass


@mark.asyncio
async def test_scopes_are_named_after_their_request() -> None:
    logger: Logger = getLogger("test-scope-naming")

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    async def socket(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close()

    app: Starlette = application(
        ServerContext(observability=logger),
        routes=[
            Route("/example", endpoint, methods=("POST",)),
            Route("/users/{identifier}", endpoint),
            WebSocketRoute("/socket", socket),
        ],
    )

    incoming: Queue[Message] = Queue()
    await incoming.put({"type": "websocket.connect"})

    async def send(message: Message) -> None:
        pass

    with LogCapture(logger) as records:
        async with running(app):
            await send_request(app, method="POST")
            await send_request(app, path="/users/12345")
            await app(websocket_scope(path="/socket"), incoming.get, send)

    # the method and the requested path - the middleware runs before routing, so
    # the template behind that path is recorded as an attribute instead
    assert any("Entering scope: POST /example" in record for record in records)
    assert any("Entering scope: GET /users/12345" in record for record in records)
    # a websocket connection carries no method, so the name says what it is
    assert any("Entering scope: WS /socket" in record for record in records)


@mark.asyncio
async def test_requests_are_recorded_as_the_conventions_describe_them() -> None:
    logger: Logger = getLogger("test-request-attributes")

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done", status_code=201)

    app: Starlette = application(
        ServerContext(observability=logger),
        routes=[Route("/users/{identifier}", endpoint)],
    )

    with LogCapture(logger) as records:
        async with running(app):
            assert (await send_request(app, path="/users/12345")).status == 201

    recorded: str = "\n".join(record for record in records if "Attributes:" in record)
    # the path the scope name can not carry is recorded instead, as the attributes
    # the HTTP semantic conventions of OpenTelemetry define for it
    assert '"url.path"]: "/users/12345"' in recorded
    assert '"http.request.method"]: "GET"' in recorded
    assert '"http.response.status_code"]: 201' in recorded
    assert '"url.scheme"]: "http"' in recorded
    # the routing of Starlette leaves no route in the scope, so there is no route
    # template to report - a FastAPI application is where one is available
    assert "http.route" not in recorded


@mark.asyncio
async def test_unknown_request_method_is_reported_as_received() -> None:
    logger: Logger = getLogger("test-unknown-method")

    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    app: Starlette = application(
        ServerContext(observability=logger),
        routes=[Route("/example", endpoint, methods=("BREW",))],
    )

    with LogCapture(logger) as records:
        async with running(app):
            await send_request(app, method="BREW")

    # a method the conventions do not know is neither replaced nor dropped - what
    # the request carried is what names its scope and what is recorded for it
    assert any("Entering scope: BREW /example" in record for record in records)
    recorded: str = "\n".join(record for record in records if "Attributes:" in record)
    assert '"http.request.method"]: "BREW"' in recorded
