from asyncio import Queue, gather
from collections.abc import AsyncGenerator, Iterator, MutableSequence, Sequence

import pytest

pytest.importorskip("starlette")
pytest.importorskip("opentelemetry")
pytest.importorskip("httpx2")

from httpx2 import ConnectError, MockTransport
from httpx2 import Request as HTTPXRequest
from httpx2 import Response as HTTPXResponse
from opentelemetry import trace
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.sdk.trace.sampling import ALWAYS_ON
from opentelemetry.trace import SpanKind, StatusCode, Tracer
from pytest import MonkeyPatch, fixture, mark, raises
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route, WebSocketRoute
from starlette.types import Message
from starlette.websockets import WebSocket

from haiway import HTTPClient, HTTPConnectionError, ctx
from haiway.httpx import HTTPXClient
from haiway.opentelemetry import OpenTelemetry, OpenTelemetryException, observability
from haiway.starlette import (
    ServerContext,
    StreamResponse,
    application,
)
from haiway.starlette.middleware import _request_attributes
from haiway.starlette.observability import request_metric_attributes
from tests.asgi import (
    TRACE_ID_HEADER,
    Result,
    http_scope,
    receive_request,
    running,
    send_request,
    websocket_scope,
)

_CONFIGURATION_ATTRIBUTES: Sequence[str] = (
    "service",
    "version",
    "environment",
    "_logger",
    "_logger_provider",
    "_meter_provider",
    "_tracer_provider",
)

REMOTE_TRACE_ID: str = "4bf92f3577b34da6a3ce929d0e0e4736"
REMOTE_SPAN_ID: str = "00f067aa0ba902b7"
REMOTE_TRACEPARENT: str = f"00-{REMOTE_TRACE_ID}-{REMOTE_SPAN_ID}-01"
# every application here serves `/example`, which is what names its request scope
REQUEST_SPAN_NAME: str = "GET /example"


@fixture(autouse=True)
def isolated_configuration() -> Iterator[None]:
    """Keep `OpenTelemetry` process wide configuration from leaking between tests."""
    snapshot = {name: getattr(OpenTelemetry, name) for name in _CONFIGURATION_ATTRIBUTES}
    yield
    for name, value in snapshot.items():
        setattr(OpenTelemetry, name, value)


@fixture
def spans(monkeypatch: MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """Install an in-memory tracer provider for the duration of one test."""
    monkeypatch.delenv("OTEL_SDK_DISABLED", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER", raising=False)
    monkeypatch.delenv("OTEL_TRACES_SAMPLER_ARG", raising=False)

    exporter = InMemorySpanExporter()
    provider = TracerProvider(sampler=ALWAYS_ON, shutdown_on_exit=False)
    provider.add_span_processor(SimpleSpanProcessor(exporter))

    def get_tracer_provider() -> TracerProvider:
        return provider

    def get_tracer(*args: object, **kwargs: object) -> Tracer:
        return provider.get_tracer("test")

    monkeypatch.setattr(trace, "get_tracer_provider", get_tracer_provider)
    monkeypatch.setattr(trace, "get_tracer", get_tracer)

    OpenTelemetry.autoconfigure(
        service="test-service",
        version="1.2.3",
        environment="test",
    )

    yield exporter

    exporter.clear()


def _request_span(
    spans: InMemorySpanExporter,
    /,
) -> ReadableSpan:
    matching: Sequence[ReadableSpan] = [
        # the method and the requested path, which is what the scope of a request
        # is named after - the middleware runs before routing, so the template
        # behind that path is carried by `http.route` rather than by the name
        span
        for span in spans.get_finished_spans()
        if span.name == REQUEST_SPAN_NAME
    ]
    assert len(matching) == 1, f"expected one request span, got {len(matching)}"
    return matching[0]


def _application() -> Starlette:
    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    return application(
        # the whole wiring - the trace context of a request is resolved and
        # handed over by the application context itself
        ServerContext(observability=OpenTelemetry.observability),
        routes=[Route("/example", endpoint)],
    )


@mark.asyncio
async def test_incoming_traceparent_is_continued(spans: InMemorySpanExporter) -> None:
    app: Starlette = _application()

    async with running(app):
        result: Result = await send_request(
            app,
            headers=(
                (b"traceparent", REMOTE_TRACEPARENT.encode()),
                (b"tracestate", b"vendor=value"),
            ),
        )

    span: ReadableSpan = _request_span(spans)
    assert span.parent is not None
    assert f"{span.parent.trace_id:032x}" == REMOTE_TRACE_ID
    assert f"{span.parent.span_id:016x}" == REMOTE_SPAN_ID
    assert f"{span.context.trace_id:032x}" == REMOTE_TRACE_ID  # pyright: ignore[reportOptionalMemberAccess]
    assert span.parent.trace_state.get("vendor") == "value"

    # the response reports the very same trace, in both forms
    assert result.headers[TRACE_ID_HEADER] == REMOTE_TRACE_ID
    assert result.headers["traceparent"].startswith(f"00-{REMOTE_TRACE_ID}-")
    assert result.headers["tracestate"] == "vendor=value"


@mark.asyncio
async def test_request_without_traceparent_starts_its_own_trace(
    spans: InMemorySpanExporter,
) -> None:
    app: Starlette = _application()

    async with running(app):
        result: Result = await send_request(app)

    span: ReadableSpan = _request_span(spans)
    assert span.parent is None
    assert result.headers[TRACE_ID_HEADER] != REMOTE_TRACE_ID
    assert result.headers["traceparent"].startswith(f"00-{result.headers[TRACE_ID_HEADER]}-")


@mark.asyncio
async def test_malformed_traceparent_starts_its_own_trace(spans: InMemorySpanExporter) -> None:
    app: Starlette = _application()

    async with running(app):
        result: Result = await send_request(
            app,
            headers=((b"traceparent", b"00-not-a-trace-01"),),
        )

    # rejected by the backend, as the specification requires
    span: ReadableSpan = _request_span(spans)
    assert span.parent is None
    assert result.status == 200
    assert result.headers["traceparent"].startswith(f"00-{result.headers[TRACE_ID_HEADER]}-")


@mark.asyncio
async def test_conflicting_traceparents_start_a_new_trace(spans: InMemorySpanExporter) -> None:
    app: Starlette = _application()

    async with running(app):
        result: Result = await send_request(
            app,
            headers=(
                (b"traceparent", REMOTE_TRACEPARENT.encode()),
                (b"traceparent", b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"),
            ),
        )

    span: ReadableSpan = _request_span(spans)
    assert span.parent is None
    assert result.headers[TRACE_ID_HEADER] != REMOTE_TRACE_ID


@mark.asyncio
async def test_concurrent_requests_keep_their_traces_apart(spans: InMemorySpanExporter) -> None:
    app: Starlette = _application()
    other_trace_id: str = "0af7651916cd43dd8448eb211c80319c"

    async with running(app):
        first, second = await gather(
            send_request(
                app,
                headers=((b"traceparent", REMOTE_TRACEPARENT.encode()),),
            ),
            send_request(
                app,
                headers=((b"traceparent", f"00-{other_trace_id}-b7ad6b7169203331-01".encode()),),
            ),
        )

    assert first.headers[TRACE_ID_HEADER] == REMOTE_TRACE_ID
    assert second.headers[TRACE_ID_HEADER] == other_trace_id
    assert {
        f"{span.context.trace_id:032x}"  # pyright: ignore[reportOptionalMemberAccess]
        for span in spans.get_finished_spans()
        if span.name == REQUEST_SPAN_NAME
    } == {REMOTE_TRACE_ID, other_trace_id}


@mark.asyncio
async def test_unconfigured_integration_fails_requests() -> None:
    # without the `spans` fixture the integration was never configured
    app: Starlette = _application()
    result = Result()

    async with running(app):
        with raises(OpenTelemetryException):
            await app(http_scope(), receive_request, result.collecting())

    assert result.status == 500
    assert TRACE_ID_HEADER not in result.headers  # there was no scope to report


@mark.asyncio
async def test_outgoing_request_continues_the_incoming_trace(spans: InMemorySpanExporter) -> None:
    captured: MutableSequence[HTTPXRequest] = []

    def downstream(request: HTTPXRequest) -> HTTPXResponse:
        captured.append(request)
        return HTTPXResponse(204)

    async def endpoint(request: Request) -> Response:
        # the request carries the trace of its caller onwards
        response = await HTTPClient.get(url="/downstream", trace_propagation=True)
        return PlainTextResponse(str(response.status_code))

    app: Starlette = application(
        ServerContext(
            observability=OpenTelemetry.observability,
            disposables=(
                HTTPXClient(
                    base_url="https://downstream.test",
                    transport=MockTransport(downstream),
                ),
            ),
        ),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        result: Result = await send_request(
            app,
            headers=(
                (b"traceparent", REMOTE_TRACEPARENT.encode()),
                (b"tracestate", b"vendor=value"),
            ),
        )

    assert result.status == 200
    assert result.body == b"204"

    span: ReadableSpan = _request_span(spans)
    assert span.context is not None
    # the downstream service is called within the very trace which arrived, and
    # continues from the span which handled the request
    assert captured[0].headers["traceparent"] == (
        f"00-{span.context.trace_id:032x}-{span.context.span_id:016x}-01"
    )
    assert captured[0].headers["traceparent"].startswith(f"00-{REMOTE_TRACE_ID}-")
    assert captured[0].headers["tracestate"] == "vendor=value"


@mark.asyncio
async def test_websocket_continues_the_incoming_trace(spans: InMemorySpanExporter) -> None:
    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.send_text(ctx.trace_id())
        await websocket.close()

    app: Starlette = application(
        ServerContext(observability=OpenTelemetry.observability),
        routes=[WebSocketRoute("/example", endpoint)],
    )

    incoming: Queue[Message] = Queue()
    sent: MutableSequence[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    await incoming.put({"type": "websocket.connect"})
    async with running(app):
        await app(
            websocket_scope(headers=((b"traceparent", REMOTE_TRACEPARENT.encode()),)),
            incoming.get,
            send,
        )

    matching: Sequence[ReadableSpan] = [
        span for span in spans.get_finished_spans() if span.name == "WS /example"
    ]
    assert len(matching) == 1
    assert matching[0].parent is not None
    assert f"{matching[0].parent.trace_id:032x}" == REMOTE_TRACE_ID
    assert sent[1]["text"] == REMOTE_TRACE_ID


@mark.asyncio
async def test_streamed_response_records_within_the_request_trace(
    spans: InMemorySpanExporter,
) -> None:
    captured: MutableSequence[HTTPXRequest] = []

    def downstream(request: HTTPXRequest) -> HTTPXResponse:
        captured.append(request)
        return HTTPXResponse(204)

    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            for index in range(2):
                # a scope of its own, entered while the response is streaming
                async with ctx.scope("chunk"):
                    await HTTPClient.get(url="/downstream", trace_propagation=True)
                    yield str(index).encode()

        return StreamResponse(content())

    app: Starlette = application(
        ServerContext(
            observability=OpenTelemetry.observability,
            disposables=(
                HTTPXClient(
                    base_url="https://downstream.test",
                    transport=MockTransport(downstream),
                ),
            ),
        ),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        result: Result = await send_request(
            app,
            headers=((b"traceparent", REMOTE_TRACEPARENT.encode()),),
        )

    assert result.chunks == [b"0", b"1"]

    request_span: ReadableSpan = _request_span(spans)
    assert request_span.context is not None
    chunk_spans: Sequence[ReadableSpan] = [
        span for span in spans.get_finished_spans() if span.name == "chunk"
    ]
    # the scope of the request was still entered while the body was produced, so
    # what the producer recorded belongs to the trace of the request
    assert len(chunk_spans) == 2
    for span in chunk_spans:
        assert span.parent is not None
        assert span.parent.span_id == request_span.context.span_id
        assert f"{span.context.trace_id:032x}" == REMOTE_TRACE_ID  # pyright: ignore[reportOptionalMemberAccess]

    # and the requests it made carry that same trace onwards
    assert [request.headers["traceparent"] for request in captured] == [
        f"00-{REMOTE_TRACE_ID}-{span.context.span_id:016x}-01"  # pyright: ignore[reportOptionalMemberAccess]
        for span in chunk_spans
    ]


@mark.asyncio
async def test_request_span_carries_the_conventional_attributes(
    spans: InMemorySpanExporter,
) -> None:
    app: Starlette = _application()

    async with running(app):
        assert (await send_request(app)).status == 200

    span: ReadableSpan = _request_span(spans)
    assert span.attributes is not None
    # the path the span name can not carry reaches the span as the attributes the
    # HTTP semantic conventions of OpenTelemetry define for it
    assert span.attributes["http.request.method"] == "GET"
    assert span.attributes["url.path"] == "/example"
    assert span.attributes["url.scheme"] == "http"
    assert span.attributes["network.protocol.version"] == "1.1"
    assert span.attributes["http.response.status_code"] == 200
    # the query string is left out - it carries credentials often enough that
    # recording it by default would leak them
    assert "url.query" not in span.attributes


@mark.asyncio
async def test_failed_request_span_records_its_exception(
    spans: InMemorySpanExporter,
) -> None:
    async def endpoint(request: Request) -> Response:
        raise ValueError("broken")

    app: Starlette = application(
        ServerContext(observability=OpenTelemetry.observability),
        routes=[Route("/example", endpoint)],
    )
    result = Result()

    async with running(app):
        with raises(ValueError):
            await app(http_scope(), receive_request, result.collecting())

    span: ReadableSpan = _request_span(spans)

    # the failure which needs correlating the most - the response answering it
    # carries the trace headers, the span the error type
    assert span.status.status_code is StatusCode.ERROR
    assert span.attributes is not None
    assert span.attributes["error.type"] == "ValueError"
    assert not span.events  # exceptions are reported as logs, not span events
    assert span.context is not None
    assert result.headers[TRACE_ID_HEADER] == f"{span.context.trace_id:032x}"


@mark.asyncio
async def test_failed_request_span_still_carries_its_attributes(
    spans: InMemorySpanExporter,
) -> None:
    async def endpoint(request: Request) -> Response:
        raise ValueError("broken")

    app: Starlette = application(
        ServerContext(observability=OpenTelemetry.observability),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        with raises(ValueError):
            await send_request(app)

    span: ReadableSpan = _request_span(spans)
    assert span.attributes is not None
    # recorded for a request which failed as well, which is where it is needed most
    assert span.attributes["url.path"] == "/example"
    assert span.attributes["http.request.method"] == "GET"
    # the error handling of the framework answered it within its scope
    assert span.attributes["http.response.status_code"] == 500


@mark.asyncio
async def test_request_span_is_a_server_span_named_after_the_route(
    spans: InMemorySpanExporter,
) -> None:
    async def endpoint(request: Request) -> Response:
        return PlainTextResponse("done")

    app: Starlette = application(
        ServerContext(observability=OpenTelemetry.observability),
        routes=[Route("/users/{user_id}", endpoint)],
    )

    async with running(app):
        await send_request(app, path="/users/12345")
        await send_request(app, path="/missing/12345")

    names: Sequence[str] = [span.name for span in spans.get_finished_spans()]
    # the route template, never the raw path - and just the method when unrouted
    assert sorted(names) == ["GET", "GET /users/{user_id}"]
    assert all(span.kind is SpanKind.SERVER for span in spans.get_finished_spans())


@fixture
def logs(
    spans: InMemorySpanExporter,
    monkeypatch: MonkeyPatch,
) -> Iterator[InMemoryLogRecordExporter]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider(shutdown_on_exit=False)
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    monkeypatch.setattr(observability, "get_logger_provider", lambda: provider)
    OpenTelemetry.autoconfigure(service="test-service", version="1.2.3", environment="test")

    yield exporter

    provider.shutdown()


@mark.asyncio
async def test_unknown_method_is_bounded_on_the_span_and_its_name(
    spans: InMemorySpanExporter,
) -> None:
    app: Starlette = _application()

    async with running(app):
        await send_request(app, method="X" * 500)

    (span,) = spans.get_finished_spans()
    # the method is chosen by the caller - an unbounded set of values would become
    # unbounded span names and metric series
    assert span.name == "HTTP /example"
    assert span.attributes is not None
    assert span.attributes["http.request.method"] == "_OTHER"
    assert span.attributes["http.request.method_original"] == "X" * 500


def test_unknown_method_is_a_bounded_metric_dimension() -> None:
    attributes = _request_attributes(http_scope(), method="BREW", status=200)

    assert request_metric_attributes(attributes)["http.request.method"] == "_OTHER"


@mark.asyncio
async def test_credentials_of_a_request_never_reach_telemetry(
    spans: InMemorySpanExporter,
    logs: InMemoryLogRecordExporter,
) -> None:
    async def endpoint(request: Request) -> Response:
        ctx.log_info("handling")
        ctx.record_info(event="handled")
        raise ValueError("boom")

    app: Starlette = application(
        ServerContext(observability=OpenTelemetry.observability),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        with raises(ValueError):
            await send_request(
                app,
                query="token=SECRET-QUERY",
                headers=(
                    (b"authorization", b"Bearer SECRET-AUTH"),
                    (b"cookie", b"session=SECRET-COOKIE"),
                    (b"x-api-key", b"SECRET-KEY"),
                ),
            )

    exported: str = repr(
        [
            (span.name, dict(span.attributes or {}), [event.name for event in span.events])
            for span in spans.get_finished_spans()
        ]
    ) + repr(
        [
            (entry.log_record.body, dict(entry.log_record.attributes or {}))
            for entry in logs.get_finished_logs()
        ]
    )

    assert "SECRET" not in exported


@mark.asyncio
async def test_hostile_trace_headers_do_not_break_the_response(
    spans: InMemorySpanExporter,
) -> None:
    app: Starlette = _application()

    async with running(app):
        result: Result = await send_request(
            app,
            headers=(
                (b"traceparent", REMOTE_TRACEPARENT.encode()),
                (b"tracestate", b"vendor=value\r\nset-cookie: pwned=1"),
            ),
        )

    assert result.status == 200
    assert not any("\r" in value or "\n" in value for value in result.headers.values())
    assert "set-cookie" not in result.headers


@mark.asyncio
async def test_websocket_span_is_named_after_the_route_not_the_path(
    spans: InMemorySpanExporter,
) -> None:
    async def endpoint(websocket: WebSocket) -> None:
        await websocket.accept()
        await websocket.close()

    app: Starlette = application(
        ServerContext(observability=OpenTelemetry.observability),
        routes=[WebSocketRoute("/rooms/{room}", endpoint)],
    )

    incoming: Queue[Message] = Queue()

    async def send(message: Message) -> None:
        pass

    await incoming.put({"type": "websocket.connect"})
    async with running(app):
        await app(websocket_scope(path="/rooms/SECRET-ROOM-TOKEN"), incoming.get, send)

    (span,) = spans.get_finished_spans()
    assert span.name == "WS /rooms/{room}"
    assert span.kind is SpanKind.SERVER


@mark.asyncio
async def test_exception_chain_of_a_failed_request_exposes_neither_url_nor_messages(
    spans: InMemorySpanExporter,
    logs: InMemoryLogRecordExporter,
) -> None:
    # assembled at runtime - a stack trace carries the source lines, which would
    # otherwise contain the very literals searched for
    token: str = "-".join(("hunter", "2"))
    password: str = "-".join(("pass", "word"))
    refusal: str = "-".join(("connection", "refused"))

    def downstream(request: HTTPXRequest) -> HTTPXResponse:
        raise ConnectError(refusal)

    async def endpoint(request: Request) -> Response:
        await HTTPClient.get(url=f"/downstream?token={token}")
        return PlainTextResponse("unreachable")

    app: Starlette = application(
        ServerContext(
            observability=OpenTelemetry.observability,
            disposables=(
                HTTPXClient(
                    base_url=f"https://user:{password}@downstream.test",
                    transport=MockTransport(downstream),
                ),
            ),
        ),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        with raises(HTTPConnectionError):
            await send_request(app)

    exported: str = repr(
        [
            (span.name, dict(span.attributes or {}), [event.name for event in span.events])
            for span in spans.get_finished_spans()
        ]
    ) + repr(
        [
            (entry.log_record.body, dict(entry.log_record.attributes or {}))
            for entry in logs.get_finished_logs()
        ]
    )

    # the chain is reported by its types, the messages of the causes are not
    assert "ConnectError: <redacted>" in exported
    assert refusal not in exported
    assert token not in exported
    assert password not in exported
