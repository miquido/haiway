import asyncio
from collections.abc import AsyncGenerator, Mapping
from types import TracebackType
from typing import Any, NamedTuple
from uuid import UUID, uuid4

from pytest import mark, raises

from haiway import MISSING, ctx
from haiway.context import (
    ContextIdentifier,
    Observability,
    ObservabilityAttribute,
    ObservabilityLevel,
    ObservabilityMetricKind,
)
from haiway.helpers.http_client import (
    HTTPBodyConsumedError,
    HTTPClient,
    HTTPClientError,
    HTTPConnectionError,
    HTTPHeaders,
    HTTPResponse,
    HTTPTimeoutError,
)


async def _aiter_bytes(chunks: list[bytes]) -> AsyncGenerator[bytes]:
    for chunk in chunks:
        await asyncio.sleep(0)
        yield chunk


class _CloseTrackingStream:
    """Body whose iteration records being closed, like a backend stream does."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def stream(self) -> AsyncGenerator[bytes]:
        async def generator() -> AsyncGenerator[bytes]:
            try:
                for chunk in self._chunks:
                    await asyncio.sleep(0)
                    yield chunk

            finally:
                self.closed = True

        return generator()


class _CloseTrackingBody:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self._index = 0
        self.closed = False

    def __aiter__(self) -> _CloseTrackingBody:
        return self

    async def __anext__(self) -> bytes:
        await asyncio.sleep(0)
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    async def asend(
        self,
        value: None = None,
        /,
    ) -> bytes:
        return await self.__anext__()

    async def athrow(
        self,
        typ: type[BaseException] | BaseException,
        val: object = None,
        tb: TracebackType | None = None,
        /,
    ) -> bytes:
        self.closed = True
        raise typ if isinstance(typ, BaseException) else typ()

    async def aclose(self) -> None:
        self.closed = True


@mark.asyncio
async def test_http_response_stream_body_streams_without_buffering() -> None:
    chunks = [b"hello", b"world"]
    response = HTTPResponse(status_code=200, headers={}, body=_aiter_bytes(chunks))

    collected = [part async for part in response.stream_body()]
    assert collected == chunks

    # streaming retains nothing - that is what bounds its memory use - so the
    # payload is gone once the stream has been consumed, and reading it again
    # fails rather than presenting an empty payload as the whole one
    with raises(HTTPBodyConsumedError):
        await response.body()


@mark.asyncio
async def test_http_response_stream_body_can_be_abandoned() -> None:
    body = _CloseTrackingBody([b"hello", b"world"])
    response = HTTPResponse(status_code=200, headers={}, body=body)

    stream = response.stream_body()
    async for part in stream:
        assert part == b"hello"
        break

    await stream.aclose()
    # closing the stream releases the backend body behind it
    assert body.closed is True
    # and the partially consumed stream is not resumable - reading it again
    # would return the unread remainder as if it were the whole payload
    with raises(HTTPBodyConsumedError):
        await response.body()


@mark.asyncio
async def test_http_response_stream_body_releases_backend_when_closed_before_read() -> None:
    body = _CloseTrackingBody([b"hello", b"world"])
    response = HTTPResponse(status_code=200, headers={}, body=body)

    stream = response.stream_body()
    # the backend holds its resources from the moment the response was made, so
    # closing the stream releases them even before a chunk was requested
    await stream.aclose()
    assert body.closed is True

    # requesting the stream claimed it, so the body is not readable afterwards
    with raises(HTTPBodyConsumedError):
        await response.body()


@mark.asyncio
async def test_http_response_stream_body_releases_backend_when_abandoned() -> None:
    body = _CloseTrackingStream([b"hello", b"world"])
    response = HTTPResponse(status_code=200, headers={}, body=body.stream())

    stream = response.stream_body()
    async for part in stream:
        assert part == b"hello"
        break

    # leaving early releases the backend resources right away rather than
    # leaving them to the garbage collector
    await stream.aclose()
    assert body.closed is True


@mark.asyncio
async def test_http_response_body_failing_midway_is_not_resumable() -> None:
    async def failing() -> AsyncGenerator[bytes]:
        yield b"head"
        raise RuntimeError("connection reset")

    response = HTTPResponse(status_code=200, headers={}, body=failing())

    with raises(RuntimeError):
        await response.body()

    # the failed read claimed the stream, so a retry cannot silently succeed
    # with the truncated remainder - or with nothing at all
    with raises(HTTPBodyConsumedError):
        await response.body()


@mark.asyncio
async def test_http_response_buffered_body_stays_re_readable() -> None:
    response = HTTPResponse(status_code=200, headers={}, body=b"payload")

    # a buffered payload is not a stream, so it never runs out
    assert await response.body() == b"payload"
    assert [part async for part in response.stream_body()] == [b"payload"]
    assert [part async for part in response.stream_body()] == [b"payload"]
    assert await response.body() == b"payload"


@mark.asyncio
async def test_http_response_consumed_error_is_an_http_client_error() -> None:
    response = HTTPResponse(status_code=200, headers={}, body=_aiter_bytes([b"payload"]))
    assert [part async for part in response.stream_body()] == [b"payload"]

    # coarse handling keeps working, and the error carries no request context
    with raises(HTTPClientError) as exc_info:
        async for _ in response.stream_body():
            pass  # pragma: no cover - the stream must not start

    error = exc_info.value
    assert isinstance(error, HTTPBodyConsumedError)
    assert error.method is None
    assert error.url is None
    assert str(error) == "HTTP response body was already consumed"


@mark.asyncio
async def test_http_response_stream_body_releases_backend_when_exhausted() -> None:
    body = _CloseTrackingStream([b"hello", b"world"])
    response = HTTPResponse(status_code=200, headers={}, body=body.stream())

    # reaching the end is what lets the backing iterator finalize itself
    assert [part async for part in response.stream_body()] == [b"hello", b"world"]
    assert body.closed is True


@mark.asyncio
async def test_http_response_body_releases_backend_when_read() -> None:
    body = _CloseTrackingStream([b"hello", b"world"])
    response = HTTPResponse(status_code=200, headers={}, body=body.stream())

    # `body()` reads to the end by definition, so it releases too - and caches
    assert await response.body() == b"helloworld"
    assert body.closed is True
    assert await response.body() == b"helloworld"


@mark.asyncio
async def test_http_client_forwards_streamed_body() -> None:
    captured: list[str | bytes | AsyncGenerator[bytes] | None] = []

    async def capturing_request(method: str, /, **kwargs: Any) -> HTTPResponse:
        captured.append(kwargs["body"])
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    client = HTTPClient(requesting=capturing_request)
    body = _aiter_bytes([b"chunk"])

    # a streamed body is handed over as-is rather than buffered on the way
    await client.post(url="/upload", body=body)
    await client.put(url="/upload", body=body)
    await client.request("PATCH", url="/upload", body=body)

    assert captured == [body, body, body]


@mark.asyncio
async def test_http_client_forwards_stream_option() -> None:
    captured: list[bool] = []

    async def capturing_request(method, /, **kwargs) -> HTTPResponse:
        captured.append(kwargs["stream"])
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    client = HTTPClient(requesting=capturing_request)

    await client.get(url="/resource")
    await client.post(url="/resource", body=b"payload")
    await client.get(url="/resource", stream=True)
    await client.request("PATCH", url="/resource", stream=True)

    # bodies are buffered unless streaming is requested explicitly
    assert captured == [False, False, True, True]


@mark.asyncio
async def test_http_client_error_contains_context() -> None:
    async def failing_request(*args, **kwargs):
        raise RuntimeError("boom")

    client = HTTPClient(requesting=failing_request)

    with raises(HTTPClientError) as exc_info:
        await client.get(url="http://example.com/test")

    error = exc_info.value
    assert error.method == "GET"
    assert error.url == "http://example.com/test"
    assert isinstance(error.__cause__, RuntimeError)
    assert "HTTP request failed" in str(error)


@mark.asyncio
async def test_http_client_request_preserves_context_for_custom_method() -> None:
    async def failing_request(*args, **kwargs):
        raise RuntimeError("boom")

    client = HTTPClient(requesting=failing_request)

    with raises(HTTPClientError) as exc_info:
        await client.request("PATCH", url="http://example.com/test")

    error = exc_info.value
    assert error.method == "PATCH"
    assert error.url == "http://example.com/test"
    assert isinstance(error.__cause__, RuntimeError)
    # context is rendered as a prefix rather than appended as keyword pairs
    assert str(error).startswith("PATCH http://example.com/test|")
    assert "HTTP request failed" in str(error)


@mark.asyncio
async def test_http_client_passes_typed_errors_through_unchanged() -> None:
    # retry predicates match on the error type, so the facade must not rewrap a
    # backend error into a plain HTTPClientError on its way out
    for error in (
        HTTPTimeoutError("timed out", method="GET", url="/resource"),
        HTTPConnectionError("refused", method="GET", url="/resource"),
        HTTPBodyConsumedError(),
    ):

        async def failing(*args: Any, _error: Exception = error, **kwargs: Any) -> HTTPResponse:
            raise _error

        client = HTTPClient(requesting=failing)

        for call in (
            client.get(url="/resource"),
            client.put(url="/resource", body=b"payload"),
            client.post(url="/resource", body=b"payload"),
            client.request("PATCH", url="/resource", body=b"payload"),
        ):
            with raises(type(error)) as exc_info:
                await call

            assert exc_info.value is error


@mark.asyncio
async def test_http_client_wraps_untyped_errors_for_every_method() -> None:
    async def failing(*args: Any, **kwargs: Any) -> HTTPResponse:
        raise RuntimeError("boom")

    client = HTTPClient(requesting=failing)

    for method, call in (
        ("PUT", client.put(url="/resource", body=b"payload")),
        ("POST", client.post(url="/resource", body=b"payload")),
    ):
        with raises(HTTPClientError) as exc_info:
            await call

        error = exc_info.value
        assert error.method == method
        assert error.url == "/resource"
        assert isinstance(error.__cause__, RuntimeError)


_TRACE_ID: UUID = uuid4()


class _Metric(NamedTuple):
    level: ObservabilityLevel
    name: str
    value: float | int
    unit: str | None
    kind: ObservabilityMetricKind
    attributes: Mapping[str, Any]


def _recording_observability(
    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]],
    /,
    *,
    metrics: list[_Metric] | None = None,
    trace_context: Mapping[str, str] = {},
) -> Observability:
    """Observability capturing recorded events and handing out a fixed trace context."""

    def event_recording(
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        event: str,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        events.append((level, event, dict(attributes)))

    def metric_recording(
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        *,
        metric: str,
        value: float | int,
        unit: str | None,
        kind: ObservabilityMetricKind,
        attributes: Mapping[str, ObservabilityAttribute],
    ) -> None:
        if metrics is not None:
            metrics.append(_Metric(level, metric, value, unit, kind, dict(attributes)))

    return Observability(
        trace_identifying=lambda scope, /: _TRACE_ID,
        log_recording=lambda scope, /, level, message, *args, exception: None,
        metric_recording=metric_recording,
        event_recording=event_recording,
        attributes_recording=lambda scope, /, level, attributes: None,
        scope_entering=lambda scope, /: "trace",
        scope_exiting=lambda scope, /, *, exception: None,
        trace_context_encoding=lambda scope, /: trace_context,
    )


@mark.asyncio
async def test_http_client_records_request_and_response_events() -> None:
    async def responding(method: str, /, **kwargs: Any) -> HTTPResponse:
        return HTTPResponse(status_code=201, headers={}, body=b"ok")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    async with ctx.scope("test", observability=_recording_observability(events)):
        await HTTPClient(requesting=responding).post(
            url="https://example.com/users",
            body=b"payload",
        )

    assert [(level, event) for level, event, _ in events] == [
        (ObservabilityLevel.DEBUG, "http.request"),
        (ObservabilityLevel.DEBUG, "http.response"),
    ]
    request_attributes = events[0][2]
    assert request_attributes["http.request.method"] == "POST"
    assert request_attributes["url"] == "https://example.com/users"
    assert request_attributes["http.request.body.size"] == len(b"payload")
    response_attributes = events[1][2]
    assert response_attributes["http.response.status_code"] == 201
    assert response_attributes["duration"] >= 0


@mark.asyncio
async def test_http_client_records_unknown_size_of_a_streamed_body() -> None:
    async def responding(method: str, /, **kwargs: Any) -> HTTPResponse:
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    async with ctx.scope("test", observability=_recording_observability(events)):
        await HTTPClient(requesting=responding).put(
            url="/upload",
            body=_aiter_bytes([b"chunk"]),
        )

    # measuring a streamed payload would mean buffering it, which is the very
    # thing streaming avoids - the size is reported as missing instead
    assert events[0][2]["http.request.body.size"] is MISSING


@mark.asyncio
async def test_http_client_records_error_event() -> None:
    async def failing(*args: Any, **kwargs: Any) -> HTTPResponse:
        raise HTTPTimeoutError("timed out", method="GET", url="/resource")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    async with ctx.scope("test", observability=_recording_observability(events)):
        with raises(HTTPTimeoutError):
            await HTTPClient(requesting=failing).get(url="/resource")

    assert [(level, event) for level, event, _ in events] == [
        (ObservabilityLevel.DEBUG, "http.request"),
        (ObservabilityLevel.ERROR, "http.request.error"),
    ]
    # the concrete error type is what a reader needs to tell a timeout from a
    # refused connection, not the coarse type the facade guarantees
    assert events[1][2]["error.type"] == "HTTPTimeoutError"
    assert events[1][2]["duration"] >= 0


@mark.asyncio
async def test_http_client_records_error_event_for_a_wrapped_failure() -> None:
    async def failing(*args: Any, **kwargs: Any) -> HTTPResponse:
        raise RuntimeError("boom")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    async with ctx.scope("test", observability=_recording_observability(events)):
        with raises(HTTPClientError):
            await HTTPClient(requesting=failing).get(url="/resource")

    # the backend error is recorded as it was raised, before being wrapped
    assert events[1][1] == "http.request.error"
    assert events[1][2]["error.type"] == "RuntimeError"


@mark.asyncio
async def test_http_client_does_not_record_cancellation() -> None:
    async def cancelling(*args: Any, **kwargs: Any) -> HTTPResponse:
        raise asyncio.CancelledError()

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    async with ctx.scope("test", observability=_recording_observability(events)):
        with raises(asyncio.CancelledError):
            await HTTPClient(requesting=cancelling).get(url="/resource")

    # cancellation is routine control flow under structured concurrency, not a
    # request failure - and it passes through unwrapped
    assert [event for _, event, _ in events] == ["http.request"]


@mark.asyncio
async def test_http_client_records_url_without_credentials_or_query() -> None:
    async def responding(method: str, /, **kwargs: Any) -> HTTPResponse:
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    async with ctx.scope("test", observability=_recording_observability(events)):
        await HTTPClient(requesting=responding).get(
            url="https://user:secret@example.com/files?token=leaked#fragment",
        )

    # the host and path identify the request, the rest can authorize it
    for _, _, attributes in events:
        assert attributes["url"] == "https://REDACTED@example.com/files"


@mark.asyncio
async def test_http_client_records_relative_url_as_given() -> None:
    async def responding(method: str, /, **kwargs: Any) -> HTTPResponse:
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    async with ctx.scope("test", observability=_recording_observability(events)):
        await HTTPClient(requesting=responding).get(url="/files?token=leaked")

    # the base URL belongs to the backend, so the facade records what it has
    assert events[0][2]["url"] == "/files"


@mark.asyncio
async def test_http_client_does_not_propagate_trace_context_by_default() -> None:
    captured: list[HTTPHeaders | None] = []

    async def capturing(method: str, /, **kwargs: Any) -> HTTPResponse:
        captured.append(kwargs["headers"])
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    observability = _recording_observability(
        events,
        trace_context={"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"},
    )
    async with ctx.scope("test", observability=observability):
        await HTTPClient(requesting=capturing).get(url="/resource")
        await HTTPClient(requesting=capturing).get(
            url="/resource",
            headers={"Accept": "application/json"},
        )

    # trace identifiers are internal - reaching whoever is called has to be asked for
    assert captured == [None, {"Accept": "application/json"}]


@mark.asyncio
async def test_http_client_propagates_trace_context_when_enabled() -> None:
    captured: list[HTTPHeaders | None] = []

    async def capturing(method: str, /, **kwargs: Any) -> HTTPResponse:
        captured.append(kwargs["headers"])
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    traceparent: str = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    observability = _recording_observability(
        events,
        trace_context={"traceparent": traceparent, "tracestate": "vendor=value"},
    )
    client = HTTPClient(requesting=capturing)
    async with ctx.scope("test", observability=observability):
        await client.get(url="/resource", trace_propagation=True)
        await client.get(
            url="/resource",
            headers={"Accept": "application/json"},
            trace_propagation=True,
        )
        # an explicitly provided value is not overridden, whichever case it uses
        await client.get(
            url="/resource",
            headers={"Traceparent": "provided"},
            trace_propagation=True,
        )
        # headers covering the whole trace context are handed over untouched
        await client.get(
            url="/resource",
            headers={"Traceparent": "provided", "TraceState": "managed"},
            trace_propagation=True,
        )

    assert captured == [
        {"traceparent": traceparent, "tracestate": "vendor=value"},
        {
            "Accept": "application/json",
            "traceparent": traceparent,
            "tracestate": "vendor=value",
        },
        {"Traceparent": "provided", "tracestate": "vendor=value"},
        {"Traceparent": "provided", "TraceState": "managed"},
    ]


@mark.asyncio
async def test_http_client_propagates_trace_context_when_the_request_asks_for_it() -> None:
    captured: list[HTTPHeaders | None] = []

    async def capturing(method: str, /, **kwargs: Any) -> HTTPResponse:
        captured.append(kwargs["headers"])
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    traceparent: str = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    observability = _recording_observability(
        events,
        trace_context={"traceparent": traceparent},
    )
    client = HTTPClient(requesting=capturing)
    async with ctx.scope("test", observability=observability):
        await client.get(url="/resource", trace_propagation=True)  # asks for it
        await client.get(url="/resource", trace_propagation=False)  # opts out
        await client.get(url="/resource")  # propagation is not the default

    assert captured == [{"traceparent": traceparent}, None, None]


@mark.asyncio
async def test_http_client_propagates_trace_context_from_every_method() -> None:
    captured: list[HTTPHeaders | None] = []

    async def capturing(method: str, /, **kwargs: Any) -> HTTPResponse:
        captured.append(kwargs["headers"])
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    traceparent: str = "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    observability = _recording_observability(
        events,
        trace_context={"traceparent": traceparent},
    )
    client = HTTPClient(requesting=capturing)
    async with ctx.scope("test", observability=observability):
        await client.get(url="/resource", trace_propagation=True)
        await client.post(url="/resource", body=b"payload", trace_propagation=True)
        await client.put(url="/resource", body=b"payload", trace_propagation=True)
        await client.request("DELETE", url="/resource", trace_propagation=True)

    assert captured == [{"traceparent": traceparent}] * 4


@mark.asyncio
async def test_http_client_propagation_tolerates_a_backend_without_trace_context() -> None:
    captured: list[HTTPHeaders | None] = []

    async def capturing(method: str, /, **kwargs: Any) -> HTTPResponse:
        captured.append(kwargs["headers"])
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    client = HTTPClient(requesting=capturing)
    # the logger backend has no trace position to hand out, and out of context
    # there is none at all - asking for propagation stays harmless either way
    async with ctx.scope("test", observability=_recording_observability(events)):
        await client.get(
            url="/resource",
            headers={"Accept": "application/json"},
            trace_propagation=True,
        )

    await client.get(url="/resource", trace_propagation=True)

    assert captured == [{"Accept": "application/json"}, None]


@mark.asyncio
async def test_http_client_measures_request_duration() -> None:
    async def responding(method: str, /, **kwargs: Any) -> HTTPResponse:
        return HTTPResponse(status_code=204, headers={}, body=b"")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    metrics: list[_Metric] = []
    async with ctx.scope("test", observability=_recording_observability(events, metrics=metrics)):
        await HTTPClient(requesting=responding).get(
            url="https://user:secret@example.com:8443/items?token=leaked",
        )

    assert len(metrics) == 1
    metric = metrics[0]
    # the aggregate is what stays on in production, so it is recorded at info
    assert metric.level == ObservabilityLevel.INFO
    assert metric.name == "http.client.request.duration"
    assert metric.kind == "histogram"
    assert metric.unit == "s"
    assert metric.value >= 0
    # a stream is stored per combination of attributes, so the URL - unbounded -
    # is not one of them, and the host is recorded without userinfo or port
    assert metric.attributes == {
        "http.request.method": "GET",
        "http.response.status_code": 204,
        "server.address": "example.com",
    }


@mark.asyncio
async def test_http_client_measures_failed_requests_at_error_level() -> None:
    async def failing(*args: Any, **kwargs: Any) -> HTTPResponse:
        raise HTTPConnectionError("refused", method="GET", url="https://example.com/items")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    metrics: list[_Metric] = []
    async with ctx.scope("test", observability=_recording_observability(events, metrics=metrics)):
        with raises(HTTPConnectionError):
            await HTTPClient(requesting=failing).get(url="https://example.com/items")

    assert len(metrics) == 1
    metric = metrics[0]
    # recorded at error level, so a backend filtering out everything below it
    # still measures the failures - and only those
    assert metric.level == ObservabilityLevel.ERROR
    assert metric.name == "http.client.request.duration"
    assert metric.attributes == {
        "http.request.method": "GET",
        "error.type": "HTTPConnectionError",
        "server.address": "example.com",
    }


@mark.asyncio
async def test_http_client_measures_relative_url_without_a_host() -> None:
    async def responding(method: str, /, **kwargs: Any) -> HTTPResponse:
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    metrics: list[_Metric] = []
    async with ctx.scope("test", observability=_recording_observability(events, metrics=metrics)):
        await HTTPClient(requesting=responding).get(url="/items")

    # the base URL it resolves against belongs to the backend - reporting the
    # facade's own view of it would be a guess
    assert metrics[0].attributes["server.address"] is MISSING


@mark.asyncio
async def test_http_client_measures_an_ipv6_host_without_brackets() -> None:
    async def responding(method: str, /, **kwargs: Any) -> HTTPResponse:
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    metrics: list[_Metric] = []
    async with ctx.scope("test", observability=_recording_observability(events, metrics=metrics)):
        await HTTPClient(requesting=responding).get(url="http://[::1]:8080/items")

    assert metrics[0].attributes["server.address"] == "::1"


@mark.asyncio
async def test_http_client_does_not_measure_cancellation() -> None:
    async def cancelling(*args: Any, **kwargs: Any) -> HTTPResponse:
        raise asyncio.CancelledError()

    events: list[tuple[ObservabilityLevel, str, Mapping[str, Any]]] = []
    metrics: list[_Metric] = []
    async with ctx.scope("test", observability=_recording_observability(events, metrics=metrics)):
        with raises(asyncio.CancelledError):
            await HTTPClient(requesting=cancelling).get(url="https://example.com/items")

    # a cancelled request never completed - measuring it would report a latency
    # that nothing actually waited for
    assert metrics == []


def test_http_client_error_redacts_credentials_from_its_message() -> None:
    error = HTTPClientError(
        "HTTP request failed",
        method="GET",
        url="https://user:secret@example.com/files?token=leaked#fragment",
    )

    # an error is logged far more often than a metric is recorded, so it must
    # not carry what the recorded url deliberately drops
    assert "secret" not in str(error)
    assert "leaked" not in str(error)
    assert str(error) == "GET https://REDACTED@example.com/files|HTTP request failed"
    # the attribute is used for programmatic handling and is redacted alike
    assert error.url == "https://REDACTED@example.com/files"


def test_http_client_error_keeps_a_url_without_credentials_intact() -> None:
    error = HTTPClientError(
        "HTTP request failed",
        method="POST",
        url="https://example.com/v1/items",
    )

    assert error.url == "https://example.com/v1/items"
    assert str(error) == "POST https://example.com/v1/items|HTTP request failed"


def test_http_client_error_subclasses_redact_alike() -> None:
    leaking = "https://user:secret@example.com/x?token=leaked"

    for error in (
        HTTPTimeoutError("timed out", method="GET", url=leaking),
        HTTPConnectionError("connection failed", method="GET", url=leaking),
    ):
        assert "secret" not in str(error)
        assert "leaked" not in str(error)


def test_http_client_error_without_a_url_renders_the_message_alone() -> None:
    assert str(HTTPClientError("body already consumed")) == "body already consumed"


@mark.asyncio
async def test_http_client_wrapped_failure_does_not_leak_credentials() -> None:
    async def failing(method: str, /, **kwargs: Any) -> HTTPResponse:
        raise RuntimeError("backend exploded")

    async with ctx.scope("test"):
        with raises(HTTPClientError) as exc_info:
            await HTTPClient(requesting=failing).get(
                url="https://user:secret@example.com/files?token=leaked",
            )

    assert "secret" not in str(exc_info.value)
    assert "leaked" not in str(exc_info.value)


@mark.asyncio
async def test_http_response_stream_body_of_an_empty_payload_yields_no_chunks() -> None:
    buffered = HTTPResponse(status_code=204, headers={}, body=b"")

    # no chunks rather than one empty chunk, so a consumer counting them - or
    # forwarding them into another request - sees it the way a streamed empty
    # payload arrives
    assert [chunk async for chunk in buffered.stream_body()] == []

    async def empty() -> AsyncGenerator[bytes]:
        return
        yield b""  # pragma: no cover

    streamed = HTTPResponse(status_code=204, headers={}, body=empty())

    assert [chunk async for chunk in streamed.stream_body()] == []


@mark.asyncio
async def test_http_response_releasing_a_buffered_body_leaves_it_readable() -> None:
    response = HTTPResponse(status_code=200, headers={}, body=b"buffered")

    # the path a discarded response is released through - a buffered payload
    # holds nothing, so releasing it must not turn a re-readable body into a
    # consumed one
    await response.stream_body().aclose()

    assert await response.body() == b"buffered"
    assert [chunk async for chunk in response.stream_body()] == [b"buffered"]
