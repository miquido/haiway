import asyncio
from collections.abc import AsyncIterator

from pytest import mark, raises

from haiway.helpers.http_client import HTTPClient, HTTPClientError, HTTPResponse


async def _aiter_bytes(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        await asyncio.sleep(0)
        yield chunk


class _CloseTrackingBody:
    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = list(chunks)
        self._index = 0
        self.closed = False

    def __aiter__(self) -> "_CloseTrackingBody":
        return self

    async def __anext__(self) -> bytes:
        await asyncio.sleep(0)
        if self._index >= len(self._chunks):
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    async def aclose(self) -> None:
        self.closed = True


@mark.asyncio
async def test_http_response_iter_bytes_streams() -> None:
    chunks = [b"hello", b"world"]
    response = HTTPResponse(status_code=200, headers={}, body=_aiter_bytes(chunks))

    collected = [part async for part in response.iter_bytes()]
    assert collected == chunks

    # body() should still buffer and return full payload
    assert await response.body() == b"helloworld"


@mark.asyncio
async def test_http_response_iter_bytes_closes_on_early_exit() -> None:
    body = _CloseTrackingBody([b"hello", b"world"])
    response = HTTPResponse(status_code=200, headers={}, body=body)

    stream = response.iter_bytes()
    async for part in stream:
        assert part == b"hello"
        break

    await stream.aclose()
    assert body.closed is True


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
    assert "HTTP request failed" in str(error)
