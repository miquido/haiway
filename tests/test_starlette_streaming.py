from asyncio import sleep
from collections.abc import AsyncGenerator, Iterable, MutableSequence
from typing import Any
from uuid import UUID

import pytest

pytest.importorskip("starlette")

from pytest import mark, raises
from starlette.applications import Starlette
from starlette.requests import ClientDisconnect, Request
from starlette.responses import Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Message

from haiway import Observability, ObservabilityLevel, State, ctx
from haiway.starlette import (
    ServerContext,
    StreamResponse,
    application,
)
from tests.asgi import (
    TRACE_ID_HEADER,
    Result,
    http_scope,
    receive_request,
    running,
    send_request,
)


class ExampleState(State):
    value: str = "example"


@mark.asyncio
async def test_stream_response_streams_within_the_request_scope() -> None:
    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            for index in range(3):
                # the state and the trace of the request, resolved mid-stream
                yield f"{index}:{ctx.state(ExampleState).value}:{ctx.trace_id()}".encode()

        return StreamResponse(content())

    app: Starlette = application(
        ServerContext(ExampleState(value="streamed")),
        routes=[Route("/example", endpoint)],
    )

    async with running(app):
        result: Result = await send_request(app)

    trace_id: str = result.headers[TRACE_ID_HEADER]
    assert result.chunks == [f"{index}:streamed:{trace_id}".encode() for index in range(3)]


@mark.asyncio
async def test_stream_response_closes_the_body_when_it_ends() -> None:
    released: MutableSequence[str] = []

    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            try:
                yield b"first"
                yield b"second"

            finally:
                released.append("closed")

        return StreamResponse(content())

    app: Starlette = application(routes=[Route("/example", endpoint)])

    async with running(app):
        result: Result = await send_request(app)

    assert result.chunks == [b"first", b"second"]
    assert released == ["closed"]


@mark.asyncio
async def test_stream_response_closes_the_body_on_a_broken_connection() -> None:
    released: MutableSequence[str] = []

    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            try:
                yield b"first"
                yield b"second"  # never reaches the connection

            finally:
                released.append("closed")

        return StreamResponse(content())

    app: Starlette = application(routes=[Route("/example", endpoint)])
    sent: MutableSequence[Message] = []

    async def failing_send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body") == b"first":
            raise OSError("connection gone")  # the consumer is not there anymore

    async with running(app):
        with raises(ClientDisconnect):
            await app(http_scope(), receive_request, failing_send)

        # closed by the response, before the request was over - the generator was
        # left suspended at its yield, which the iteration alone does not close
        assert released == ["closed"]


@mark.asyncio
async def test_abandoned_stream_releases_its_own_scope() -> None:
    disposed: MutableSequence[str] = []

    class Resource:
        async def __aenter__(self) -> Iterable[State]:
            disposed.append("acquired")
            return (ExampleState(value="nested"),)

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: object,
        ) -> None:
            await sleep(0)  # releasing a real resource awaits
            disposed.append("released")

    async def produce() -> AsyncGenerator[bytes]:
        async with ctx.scope("producing", disposables=(Resource(),)):
            yield ctx.state(ExampleState).value.encode()
            await sleep(1)  # never reached by the consumer
            yield b"unreachable"

    async def endpoint(request: Request) -> Response:
        return StreamResponse(produce())

    app: Starlette = application(routes=[Route("/example", endpoint)])

    async def failing_send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body") == b"nested":
            raise OSError("connection gone")

    async with running(app):
        with raises(ClientDisconnect):
            await app(http_scope(), receive_request, failing_send)

        # the scope living inside the generator is released where the streaming
        # ended, not whenever the garbage collector reaches the generator
        assert disposed == ["acquired", "released"]


@mark.asyncio
async def test_failed_stream_is_reported_within_the_request() -> None:
    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            yield b"partial"
            raise ValueError("broken")

        return StreamResponse(content())

    app: Starlette = application(routes=[Route("/example", endpoint)])
    result = Result()

    async with running(app):
        with raises(ValueError):
            await app(http_scope(), receive_request, result.collecting())

    # a response which already started can not be replaced by an error
    assert result.status == 200
    assert result.body == b"partial"


@mark.asyncio
async def test_stream_response_carries_the_provided_media_type() -> None:
    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            yield b'{"row": 1}\n'

        return StreamResponse(content(), media_type="application/x-ndjson")

    app: Starlette = application(routes=[Route("/example", endpoint)])

    async with running(app):
        result: Result = await send_request(app)

    assert result.headers["content-type"] == "application/x-ndjson"
    assert result.body == b'{"row": 1}\n'


@mark.asyncio
async def test_framework_response_leaves_an_abandoned_body_open() -> None:
    # characterization of what `StreamResponse` is for - should the framework
    # start closing an abandoned body itself, the override becomes redundant
    released: MutableSequence[str] = []

    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            try:
                yield b"first"
                yield b"second"

            finally:
                released.append("closed")

        return StreamingResponse(content())

    app: Starlette = application(routes=[Route("/example", endpoint)])

    async def failing_send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body") == b"first":
            raise OSError("connection gone")

    async with running(app):
        with raises(ClientDisconnect):
            await app(http_scope(), receive_request, failing_send)

        assert released == []  # left for the garbage collector


@mark.asyncio
async def test_context_stream_is_closed_when_abandoned() -> None:
    released: MutableSequence[str] = []

    async def produce() -> AsyncGenerator[bytes]:
        try:
            # the state of the request, resolved from the parent of the stream scope
            yield ctx.state(ExampleState).value.encode()
            await sleep(1)  # never reached by the consumer
            yield b"unreachable"

        finally:
            released.append("closed")

    async def endpoint(request: Request) -> Response:
        # the scope of `ctx.stream` lives inside its generator, so it spans the
        # whole response instead of being released before the streaming starts
        return StreamResponse(ctx.stream(produce))

    app: Starlette = application(
        ServerContext(ExampleState(value="from-stream")),
        routes=[Route("/example", endpoint)],
    )
    sent: MutableSequence[bytes] = []

    async def failing_send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body"):
            sent.append(message["body"])
            raise OSError("connection gone")

    async with running(app):
        with raises(ClientDisconnect):
            await app(http_scope(), receive_request, failing_send)

        # both the producer and the scope of the stream ended with the response -
        # the context checks of the suite catch a scope left behind
        assert released == ["closed"]

    assert sent == [b"from-stream"]


class _Records:
    """Observability capturing what a request scope recorded."""

    def __init__(self) -> None:
        self.logs: MutableSequence[tuple[ObservabilityLevel, str, str | None]] = []
        self.failures: MutableSequence[str] = []

    def observability(self) -> Observability:
        def scope_exiting(
            scope: Any,
            /,
            *,
            exception: BaseException | None,
        ) -> None:
            if exception is not None:
                self.failures.append(type(exception).__name__)

        def log_recording(
            scope: Any,
            /,
            level: ObservabilityLevel,
            message: str,
            *args: Any,
            exception: BaseException | None,
        ) -> None:
            self.logs.append(
                (level, message, None if exception is None else str(exception)),
            )

        return Observability(
            trace_identifying=lambda scope, /: UUID(int=1),
            log_recording=log_recording,
            metric_recording=lambda scope, /, level, **kwargs: None,
            event_recording=lambda scope, /, level, **kwargs: None,
            attributes_recording=lambda scope, /, level, attributes: None,
            scope_entering=lambda scope, /: "trace",
            scope_exiting=scope_exiting,
            trace_context_encoding=lambda scope, /: {},
        )


class ExampleError(Exception):
    pass


@mark.asyncio
async def test_failed_stream_is_recorded_with_the_actual_error() -> None:
    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            yield b"partial"
            raise ExampleError("stream broke")

        return StreamResponse(content())

    records = _Records()
    app: Starlette = application(
        ServerContext(observability=records.observability()),
        routes=[Route("/example", endpoint)],
        # a handler matching the error makes the framework replace it with a
        # `RuntimeError` about a response already started, which is all the
        # request would otherwise be recorded as failing with
        exception_handlers={ExampleError: _handled},
    )
    result = Result()

    async with running(app):
        with raises(RuntimeError):
            await app(http_scope(), receive_request, result.collecting())

    assert result.body == b"partial"
    assert (ObservabilityLevel.ERROR, "Response streaming failed", "stream broke") in records.logs


@mark.asyncio
async def test_failing_stream_close_does_not_replace_the_error() -> None:
    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            try:
                yield b"partial"  # the connection goes away here
                yield b"unreachable"

            except GeneratorExit:
                # what a scope failing to release within the generator looks like
                raise ExampleError("close broke") from None

        return StreamResponse(content())

    records = _Records()
    app: Starlette = application(
        ServerContext(observability=records.observability()),
        routes=[Route("/example", endpoint)],
    )

    async def failing_send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body") == b"partial":
            raise ExampleError("send broke")

    async with running(app):
        # the failure of the response, not the one from closing after it
        with raises(ExampleError) as failure:
            await app(http_scope(), receive_request, failing_send)

    assert str(failure.value) == "send broke"
    assert (
        ObservabilityLevel.WARNING,
        "Response stream failed to close",
        "close broke",
    ) in records.logs
    assert [message for _, message, _ in records.logs].count("Response streaming failed") == 1


@mark.asyncio
async def test_disconnected_consumer_is_not_a_stream_failure() -> None:
    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            yield b"partial"
            yield b"unreachable"

        return StreamResponse(content())

    records = _Records()
    app: Starlette = application(
        ServerContext(observability=records.observability()),
        routes=[Route("/example", endpoint)],
    )

    async def failing_send(message: Message) -> None:
        if message["type"] == "http.response.body" and message.get("body") == b"partial":
            raise OSError("connection gone")  # the consumer is not there anymore

    async with running(app):
        with raises(ClientDisconnect):
            await app(http_scope(), receive_request, failing_send)

    # a consumer which went away ends the response without failing it - recorded
    # as what happened rather than as an error, and not as a failed request
    assert (
        ObservabilityLevel.DEBUG,
        "Response streaming ended by a disconnected consumer",
        None,
    ) in records.logs
    assert [message for _, message, _ in records.logs].count("Response streaming failed") == 0
    assert records.failures == []


@mark.asyncio
async def test_body_failing_with_an_os_error_is_a_stream_failure() -> None:
    async def endpoint(request: Request) -> Response:
        async def content() -> AsyncGenerator[bytes]:
            yield b"partial"
            raise OSError("the resource behind the body died")

        return StreamResponse(content())

    records = _Records()
    app: Starlette = application(
        ServerContext(observability=records.observability()),
        routes=[Route("/example", endpoint)],
    )
    result = Result()

    async with running(app):
        # the framework turns an `OSError` reaching it into a `ClientDisconnect`,
        # which is what a gone consumer is reported with as well
        with raises(ClientDisconnect):
            await app(http_scope(), receive_request, result.collecting())

    # a gone consumer is the send failing - a body failing with an error of the
    # same type is the failure of the response it was producing
    assert (
        ObservabilityLevel.ERROR,
        "Response streaming failed",
        "the resource behind the body died",
    ) in records.logs
    assert [message for _, message, _ in records.logs].count(
        "Response streaming ended by a disconnected consumer"
    ) == 0


async def _handled(
    request: Request,
    exception: Exception,
) -> Response:
    return Response(status_code=400)
