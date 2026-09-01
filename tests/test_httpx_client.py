import asyncio
from collections.abc import AsyncIterator, MutableSequence
from typing import Any

import pytest

pytest.importorskip("httpx2")

from httpx2 import (
    AsyncBaseTransport,
    AsyncByteStream,
    CloseError,
    ConnectError,
    MockTransport,
    ReadTimeout,
    Request,
    Response,
    Timeout,
)
from pytest import mark, raises

from haiway import (
    HTTPBodyConsumedError,
    HTTPClientError,
    HTTPConnectionError,
    HTTPResponse,
    HTTPTimeoutError,
)
from haiway.httpx import HTTPXClient


def _transport(
    handler: Any,
    /,
) -> MockTransport:
    return MockTransport(handler)


class _TrackingStream(AsyncByteStream):
    """Byte stream recording whether the backend released it."""

    def __init__(
        self,
        chunks: tuple[bytes, ...],
        close_error: Exception | None = None,
    ) -> None:
        self.chunks = chunks
        self.closed = False
        self.close_error = close_error

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


class _BlockingStream(_TrackingStream):
    """Tracking stream which parks forever on a chosen chunk."""

    def __init__(
        self,
        chunks: tuple[bytes, ...],
        block_at: int,
    ) -> None:
        super().__init__(chunks)
        self.block_at = block_at
        self.parked = asyncio.Event()

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for index, chunk in enumerate(self.chunks):
            if index == self.block_at:
                self.parked.set()
                await asyncio.Event().wait()

            yield chunk


class _TrackingTransport(AsyncBaseTransport):
    def __init__(
        self,
        stream: _TrackingStream,
    ) -> None:
        self.stream = stream

    async def handle_async_request(
        self,
        request: Request,
    ) -> Response:
        return Response(200, stream=self.stream)


def _echo_handler(
    *,
    status_code: int = 200,
    chunks: tuple[bytes, ...] = (b"payload",),
    headers: Any = None,
    captured: MutableSequence[Request] | None = None,
) -> Any:
    def handler(request: Request) -> Response:
        if captured is not None:
            captured.append(request)

        async def stream() -> AsyncIterator[bytes]:
            for chunk in chunks:
                yield chunk

        return Response(
            status_code,
            content=stream(),
            headers=headers,
        )

    return handler


async def _request(
    client: HTTPXClient,
    /,
    **kwargs: Any,
) -> HTTPResponse:
    return await client.request("GET", url="/resource", **kwargs)


@mark.asyncio
async def test_unconfigured_client_applies_default_timeout() -> None:
    captured: MutableSequence[Request] = []
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(_echo_handler(captured=captured)),
    )

    async with client:
        response = await _request(client)
        await response.body()

    # an unset timeout bounds every phase with the 5s default - the exact key set is
    # owned by httpx2, so derive it from the library instead of hardcoding it
    assert captured[0].extensions["timeout"] == Timeout(5.0).as_dict()


@mark.asyncio
async def test_configured_timeout_is_forwarded() -> None:
    captured: MutableSequence[Request] = []
    client = HTTPXClient(
        base_url="https://api.test",
        timeout=1.5,
        transport=_transport(_echo_handler(captured=captured)),
    )

    async with client:
        response = await _request(client)
        await response.body()

    assert captured[0].extensions["timeout"]["read"] == 1.5


@mark.asyncio
async def test_request_timeout_overrides_client_default() -> None:
    captured: MutableSequence[Request] = []
    client = HTTPXClient(
        base_url="https://api.test",
        timeout=1.5,
        transport=_transport(_echo_handler(captured=captured)),
    )

    async with client:
        response = await _request(client, timeout=9.0)
        await response.body()

    assert captured[0].extensions["timeout"]["read"] == 9.0


@mark.asyncio
async def test_buffered_body_is_read_before_returning() -> None:
    stream = _TrackingStream((b"a", b"b"))
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client)
        # without stream=True the payload is read up front, freeing the connection
        assert stream.closed is True

        assert await response.body() == b"ab"
        # the buffered payload stays available for repeated reads
        assert await response.body() == b"ab"
        # streaming a buffered body yields it as a single chunk
        assert [chunk async for chunk in response.stream_body()] == [b"ab"]


@mark.asyncio
async def test_response_body_is_streamed_in_chunks() -> None:
    chunks = (b"alpha", b"beta", b"gamma")
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(_echo_handler(chunks=chunks)),
    )

    async with client:
        response = await _request(client, stream=True)
        streamed = [chunk async for chunk in response.stream_body()]

    # chunk boundaries are preserved rather than collapsed into one buffer
    assert tuple(streamed) == chunks


@mark.asyncio
async def test_response_body_buffers_and_caches() -> None:
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(_echo_handler(chunks=(b"one", b"two"))),
    )

    async with client:
        response = await _request(client)
        assert await response.body() == b"onetwo"
        # cached, so a second read returns the same payload
        assert await response.body() == b"onetwo"


@mark.asyncio
async def test_consuming_body_releases_connection() -> None:
    stream = _TrackingStream((b"a", b"b"))
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        assert await response.body() == b"ab"

    assert stream.closed is True


@mark.asyncio
async def test_response_headers_expose_backend_lookups() -> None:
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(
            _echo_handler(
                headers=[
                    ("Content-Type", "application/json"),
                    ("X-Multi", "1"),
                    ("X-Multi", "2"),
                ],
            )
        ),
    )

    async with client:
        response = await _request(client)
        await response.body()

    # lookups stay case-insensitive, they are served by the backend mapping
    assert response.headers["content-type"] == "application/json"
    # repeated headers are joined rather than silently dropped
    assert response.headers["x-multi"] == "1, 2"


@mark.asyncio
async def test_timeout_is_translated_to_typed_error() -> None:
    def handler(request: Request) -> Response:
        raise ReadTimeout("too slow", request=request)

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        with raises(HTTPTimeoutError) as exc_info:
            await _request(client)

    error = exc_info.value
    assert isinstance(error, HTTPClientError)
    assert error.method == "GET"
    # the url is reported as requested, without resolving it against base_url
    assert error.url == "/resource"
    assert "HTTP request timed out" in str(error)
    # the driver error has to stay reachable for diagnostics
    assert isinstance(error.__cause__, ReadTimeout)


@mark.asyncio
async def test_network_failure_is_translated_to_typed_error() -> None:
    def handler(request: Request) -> Response:
        raise ConnectError("refused", request=request)

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        with raises(HTTPConnectionError) as exc_info:
            await _request(client)

    error = exc_info.value
    assert isinstance(error, HTTPClientError)
    assert error.method == "GET"
    assert "HTTP connection failed" in str(error)
    assert isinstance(error.__cause__, ConnectError)


@mark.asyncio
async def test_unexpected_failure_falls_back_to_base_error() -> None:
    def handler(request: Request) -> Response:
        raise RuntimeError("boom")

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        with raises(HTTPClientError) as exc_info:
            await _request(client)

    error = exc_info.value
    assert not isinstance(error, HTTPTimeoutError | HTTPConnectionError)
    assert "HTTP request failed" in str(error)
    assert isinstance(error.__cause__, RuntimeError)


@mark.asyncio
async def test_streaming_failure_is_translated() -> None:
    def handler(request: Request) -> Response:
        async def stream() -> AsyncIterator[bytes]:
            yield b"partial"
            raise ReadTimeout("dropped", request=request)

        return Response(200, content=stream())

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        response = await _request(client, stream=True)
        with raises(HTTPTimeoutError) as exc_info:
            await response.body()

    assert isinstance(exc_info.value.__cause__, ReadTimeout)


@mark.asyncio
async def test_abandoned_partial_stream_fails_later_buffered_read() -> None:
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(_echo_handler(chunks=(b"alpha", b"beta"))),
    )

    async with client:
        response = await _request(client, stream=True)
        async for chunk in response.stream_body():
            assert chunk == b"alpha"
            break  # abandon the stream with chunks still pending

        # a partially read stream is not resumable, so the re-read fails
        # rather than returning the unread remainder as the whole payload
        with raises(HTTPBodyConsumedError) as exc_info:
            await response.body()

    error = exc_info.value
    assert not isinstance(error, HTTPTimeoutError | HTTPConnectionError)
    assert isinstance(error, HTTPClientError)


@mark.asyncio
async def test_buffered_read_failure_is_translated() -> None:
    def handler(request: Request) -> Response:
        async def stream() -> AsyncIterator[bytes]:
            yield b"partial"
            raise ReadTimeout("dropped", request=request)

        return Response(200, content=stream())

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        # the buffered body is read within the request, so it fails there
        with raises(HTTPTimeoutError):
            await _request(client)


@mark.asyncio
async def test_reentering_open_client_raises() -> None:
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(_echo_handler()),
    )

    async with client:
        # one instance owns a single pool, the backend refuses a second entry
        # (the error type is all that is contracted - its message belongs to httpx2)
        with raises(RuntimeError):
            async with client:
                pass  # pragma: no cover - entering must fail

        # the rejected re-entry left the already open client usable
        assert await (await _request(client)).body() == b"payload"

    # and a separate instance is what a second scope needs
    other = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(_echo_handler()),
    )
    async with other:
        assert await (await _request(other)).body() == b"payload"


@mark.asyncio
async def test_client_can_be_reused_sequentially() -> None:
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(_echo_handler(chunks=(b"first",))),
    )

    async with client:
        assert await (await _request(client)).body() == b"first"

    # a closed instance rebuilds its pool on the next entry
    async with client:
        assert await (await _request(client)).body() == b"first"


@mark.asyncio
async def test_cookies_are_not_persisted() -> None:
    captured: MutableSequence[Request] = []

    def handler(request: Request) -> Response:
        captured.append(request)

        async def stream() -> AsyncIterator[bytes]:
            yield b""

        return Response(
            200,
            content=stream(),
            headers=[("Set-Cookie", "session=secret; Path=/")],
        )

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        await (await _request(client)).body()
        await (await _request(client)).body()

    # the cookie from the first response must not be replayed on the second
    assert "cookie" not in captured[1].headers


@mark.asyncio
async def test_redirects_are_not_followed_by_default() -> None:
    def handler(request: Request) -> Response:
        if request.url.path == "/resource":
            return Response(302, headers=[("Location", "/moved")])

        return Response(200, content=b"final")  # pragma: no cover - not reached

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        response = await _request(client)
        # the unread body of the redirect needs no explicit release
        assert response.status_code == 302


@mark.asyncio
async def test_redirects_can_be_followed_per_request() -> None:
    def handler(request: Request) -> Response:
        if request.url.path == "/resource":
            return Response(302, headers=[("Location", "/moved")])

        return Response(200, content=b"final")

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        response = await _request(client, follow_redirects=True)
        assert response.status_code == 200
        assert await response.body() == b"final"


class _RequestStreamConsumingTransport(AsyncBaseTransport):
    """Transport consuming the request body the way a real one does.

    ``MockTransport`` calls ``Request.aread()``, which replaces the request
    stream with a replayable buffer - exactly what a streamed upload must not
    rely on. Iterating the stream instead keeps that distinction observable.
    """

    def __init__(
        self,
        *,
        redirect: bool = False,
    ) -> None:
        self.redirect = redirect
        self.received: MutableSequence[bytes] = []

    async def handle_async_request(
        self,
        request: Request,
    ) -> Response:
        assert isinstance(request.stream, AsyncByteStream)
        async for chunk in request.stream:
            self.received.append(chunk)

        if self.redirect and request.url.path == "/upload":
            # 307 preserves the method, so the body has to be sent again
            return Response(307, headers=[("Location", "/moved")])

        return Response(200, content=b"stored")


async def _chunks(
    *parts: bytes,
) -> AsyncIterator[bytes]:
    for part in parts:
        yield part


@mark.asyncio
async def test_streamed_request_body_is_sent_chunked() -> None:
    captured: MutableSequence[Request] = []

    def handler(request: Request) -> Response:
        captured.append(request)
        return Response(200, content=b"ok")

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        response = await client.request(
            "PUT",
            url="/upload",
            body=_chunks(b"first", b"second"),
        )
        assert await response.body() == b"ok"

    # a stream has no known length, so it goes out chunked instead of buffered
    assert captured[0].headers["Transfer-Encoding"] == "chunked"
    assert "Content-Length" not in captured[0].headers
    assert captured[0].content == b"firstsecond"


@mark.asyncio
async def test_buffered_request_body_is_sent_with_length() -> None:
    captured: MutableSequence[Request] = []

    def handler(request: Request) -> Response:
        captured.append(request)
        return Response(200, content=b"ok")

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        response = await client.request("PUT", url="/upload", body=b"payload")
        assert await response.body() == b"ok"

    assert captured[0].headers["Content-Length"] == "7"
    assert "Transfer-Encoding" not in captured[0].headers
    assert captured[0].content == b"payload"


@mark.asyncio
async def test_streamed_request_body_reaches_the_transport_in_chunks() -> None:
    transport = _RequestStreamConsumingTransport()
    client = HTTPXClient(
        base_url="https://api.test",
        transport=transport,
    )

    async with client:
        response = await client.request(
            "POST",
            url="/upload",
            body=_chunks(b"alpha", b"beta", b"gamma"),
        )
        assert await response.body() == b"stored"

    # chunk boundaries survive rather than being collapsed into one buffer
    assert transport.received == [b"alpha", b"beta", b"gamma"]


@mark.asyncio
async def test_streamed_request_body_cannot_be_replayed_on_redirect() -> None:
    transport = _RequestStreamConsumingTransport(redirect=True)
    client = HTTPXClient(
        base_url="https://api.test",
        transport=transport,
    )

    async with client:
        # a consumed stream cannot be sent a second time
        with raises(HTTPClientError) as exc_info:
            await client.request(
                "POST",
                url="/upload",
                body=_chunks(b"alpha"),
                follow_redirects=True,
            )

    error = exc_info.value
    assert not isinstance(error, HTTPTimeoutError | HTTPConnectionError)
    assert error.method == "POST"


@mark.asyncio
async def test_streamed_response_body_is_not_buffered() -> None:
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(_echo_handler(chunks=(b"alpha", b"beta"))),
    )

    async with client:
        response = await _request(client, stream=True)
        assert [chunk async for chunk in response.stream_body()] == [b"alpha", b"beta"]

        # streaming retains nothing - which is what keeps a streamed body
        # bounded in memory - so a consumed stream has nothing left to give
        with raises(HTTPBodyConsumedError):
            await response.body()


@mark.asyncio
async def test_abandoned_stream_releases_connection_immediately() -> None:
    stream = _TrackingStream((b"alpha", b"beta", b"gamma"))
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        body = response.stream_body()
        async for chunk in body:
            assert chunk == b"alpha"
            break  # abandon the stream with chunks still pending

        await body.aclose()
        # released inside the scope rather than waiting for the pool to close
        assert stream.closed is True


@mark.asyncio
async def test_stream_closed_before_read_releases_connection() -> None:
    stream = _TrackingStream((b"alpha", b"beta", b"gamma"))
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        body = response.stream_body()
        # the connection is checked out by the response, not by reading it, so
        # closing without requesting a chunk has to release it as well
        await body.aclose()
        assert stream.closed is True


@mark.asyncio
async def test_exhausted_stream_releases_connection_within_scope() -> None:
    stream = _TrackingStream((b"alpha", b"beta", b"gamma"))
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        assert [chunk async for chunk in response.stream_body()] == [
            b"alpha",
            b"beta",
            b"gamma",
        ]
        # reaching the end runs the backend release inside the scope rather
        # than leaving it to the pool closing
        assert stream.closed is True


@mark.asyncio
async def test_streaming_network_failure_is_translated() -> None:
    def handler(request: Request) -> Response:
        async def stream() -> AsyncIterator[bytes]:
            yield b"partial"
            raise ConnectError("dropped", request=request)

        return Response(200, content=stream())

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        response = await _request(client, stream=True)
        # a connection lost mid-download is translated like one lost up front
        with raises(HTTPConnectionError) as exc_info:
            await response.body()

    error = exc_info.value
    assert isinstance(error.__cause__, ConnectError)
    # the request context comes from the response the stream belongs to
    assert error.method == "GET"
    assert error.url == "https://api.test/resource"


@mark.asyncio
async def test_streaming_close_failure_is_translated() -> None:
    stream = _TrackingStream(
        (b"alpha", b"beta", b"gamma"),
        close_error=CloseError("release failed"),
    )
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        body = response.stream_body()
        async for chunk in body:
            assert chunk == b"alpha"
            break  # abandon the stream so releasing happens on close

        # releasing the connection is part of reading the body, so failing to
        # release surfaces as a typed error rather than a backend one
        with raises(HTTPConnectionError) as exc_info:
            await body.aclose()

        # the backend release is still attempted despite failing
        assert stream.closed is True

    error = exc_info.value
    assert isinstance(error.__cause__, CloseError)
    assert error.method == "GET"
    assert error.url == "https://api.test/resource"


@mark.asyncio
async def test_buffered_read_close_failure_is_translated() -> None:
    stream = _TrackingStream(
        (b"alpha", b"beta"),
        close_error=CloseError("release failed"),
    )
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        # the payload arrives in full, only releasing the connection fails
        with raises(HTTPConnectionError) as exc_info:
            await response.body()

        assert stream.closed is True

    assert isinstance(exc_info.value.__cause__, CloseError)


@mark.asyncio
async def test_unexpected_streaming_failure_falls_back_to_base_error() -> None:
    def handler(request: Request) -> Response:
        async def stream() -> AsyncIterator[bytes]:
            yield b"partial"
            raise RuntimeError("boom")

        return Response(200, content=stream())

    client = HTTPXClient(
        base_url="https://api.test",
        transport=_transport(handler),
    )

    async with client:
        response = await _request(client, stream=True)
        with raises(HTTPClientError) as exc_info:
            async for _ in response.stream_body():
                pass

    error = exc_info.value
    assert not isinstance(error, HTTPTimeoutError | HTTPConnectionError)
    assert isinstance(error.__cause__, RuntimeError)


@mark.asyncio
async def test_base_url_is_exposed() -> None:
    client = HTTPXClient(
        base_url="https://api.test/v1",
        transport=_transport(_echo_handler()),
    )

    # httpx2 normalizes the base URL with a trailing slash
    assert str(client.base_url) == "https://api.test/v1/"

    # and it survives the pool being rebuilt on a later scope
    async with client:
        assert str(client.base_url) == "https://api.test/v1/"


@mark.asyncio
async def test_thrown_exception_is_not_translated() -> None:
    stream = _TrackingStream((b"alpha", b"beta", b"gamma"))
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        body = response.stream_body()
        assert await anext(body) == b"alpha"

        # an exception thrown in comes from the consumer, not from the transport,
        # so reporting it as a request failure would misattribute it
        with raises(ValueError) as exc_info:
            await body.athrow(ValueError("consumer failed"))

        assert not isinstance(exc_info.value, HTTPClientError)
        # the connection is released regardless of where the failure came from
        assert stream.closed is True


@mark.asyncio
async def test_cancellation_is_not_translated_into_a_client_error() -> None:
    stream = _BlockingStream((b"alpha", b"beta"), block_at=1)
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        body = response.stream_body()
        assert await anext(body) == b"alpha"

        task = asyncio.ensure_future(anext(body))
        # wait until the read is actually parked on the blocked chunk, so the
        # cancellation lands on the pending read rather than before it started
        await stream.parked.wait()

        task.cancel()
        # cancellation is routine control flow under structured concurrency, not
        # a transport failure - translating it would hide it from the outer
        # timeout or task group it belongs to
        with raises(asyncio.CancelledError) as exc_info:
            await task

        assert not isinstance(exc_info.value, HTTPClientError)
        # ...and the connection is still released rather than held to pool close
        assert stream.closed is True


@mark.asyncio
async def test_cancelled_buffered_read_releases_the_connection() -> None:
    stream = _BlockingStream((b"alpha", b"beta"), block_at=1)
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        task = asyncio.ensure_future(response.body())
        # same as above - the buffered read has to be parked mid-body before
        # cancelling, otherwise the test would not exercise the abandoned read
        await stream.parked.wait()

        task.cancel()
        with raises(asyncio.CancelledError):
            await task

        assert stream.closed is True
        # the stream was claimed before it was read, so the abandoned read
        # cannot be resumed into a truncated payload
        with raises(HTTPBodyConsumedError):
            await response.body()


@mark.asyncio
async def test_timeout_around_a_streamed_read_releases_the_connection() -> None:
    stream = _BlockingStream((b"alpha", b"beta"), block_at=1)
    client = HTTPXClient(
        base_url="https://api.test",
        transport=_TrackingTransport(stream),
    )

    async with client:
        response = await _request(client, stream=True)
        # an outer deadline rather than the client's own - it arrives as
        # cancellation, and has to release the connection all the same
        with raises(TimeoutError):
            async with asyncio.timeout(0.01):
                await response.body()

        assert stream.closed is True
