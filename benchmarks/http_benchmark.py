"""Micro benchmarks of the HTTP client - the facade, the HTTPX backend and responses.

Run with ``make benchmark-http`` or ``uv run python -O benchmarks/http_benchmark.py``.

    --json PATH      write the results as json
    --compare PATH   print the ratio against results written earlier
    --filter TEXT    run the cases whose name contains TEXT only
    --budget SECONDS time spent measuring each case (default 0.5)

Requests go through ``httpx2.MockTransport`` in process - no network, no server - so the
numbers are the cost of the client stack alone, the part a real round trip adds to. Every
case reports the best of several repeats in microseconds per call; a ``haiway`` case has
a ``bare`` counterpart doing the same with ``httpx2`` alone, and the difference between
them is the overhead of the client. Stream cases are per chunk.
"""

import argparse
import asyncio
import gc
import json
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from pathlib import Path
from time import perf_counter

import httpx2

from haiway import ctx
from haiway.helpers.http_client import HTTPClient, HTTPResponse
from haiway.httpx import HTTPXClient

BASE_URL: str = "https://api.example.com"
PAYLOAD: bytes = b'{"status": "ok", "items": [1, 2, 3]}'
HEADERS: Mapping[str, str] = {"content-type": "application/json", "x-request-id": "abc"}
CHUNKS: int = 100
CHUNK: bytes = b"x" * 64


class _ChunkedBody(httpx2.AsyncByteStream):
    async def __aiter__(self) -> AsyncGenerator[bytes]:
        for _ in range(CHUNKS):
            yield CHUNK


def _respond(request: httpx2.Request) -> httpx2.Response:
    if request.url.path == "/stream":
        return httpx2.Response(200, stream=_ChunkedBody())

    return httpx2.Response(200, headers=HEADERS, content=PAYLOAD)


def _transport() -> httpx2.MockTransport:
    return httpx2.MockTransport(_respond)


async def _stub(
    method: str,
    /,
    *,
    url: str,
    query: object = None,
    headers: object = None,
    body: object = None,
    timeout: float | None = None,
    follow_redirects: bool | None = None,
    stream: bool = False,
) -> HTTPResponse:
    return HTTPResponse(status_code=200, headers=HEADERS, body=PAYLOAD)


async def measure(
    call: Callable[[], Awaitable[object]],
    budget: float,
    per: int = 1,
    repeats: int = 5,
) -> float:
    for _ in range(50):  # warm up
        await call()

    started: float = perf_counter()
    for _ in range(50):
        await call()

    single: float = (perf_counter() - started) / 50
    count: int = max(1, int(budget / repeats / single))
    best: float = float("inf")
    for _ in range(repeats):
        gc.collect()
        started = perf_counter()
        for _ in range(count):
            await call()

        best = min(best, perf_counter() - started)

    return best / count / per * 1e6


type Case = Callable[[float], Awaitable[float]]


def bare_case(
    request: Callable[[httpx2.AsyncClient], Awaitable[object]],
    per: int = 1,
) -> Case:
    async def run(budget: float) -> float:
        async with httpx2.AsyncClient(base_url=BASE_URL, transport=_transport()) as client:
            return await measure(lambda: request(client), budget, per)

    return run


def haiway_case(
    request: Callable[[], Awaitable[object]],
    per: int = 1,
) -> Case:
    async def run(budget: float) -> float:
        async with ctx.scope(
            "benchmark",
            disposables=(HTTPXClient(base_url=BASE_URL, transport=_transport()),),
        ):
            return await measure(request, budget, per)

    return run


def stub_case(request: Callable[[HTTPClient], Awaitable[object]]) -> Case:
    async def run(budget: float) -> float:
        client = HTTPClient(requesting=_stub)
        async with ctx.scope("benchmark"):
            return await measure(lambda: request(client), budget)

    return run


def sync_case(call: Callable[[], object]) -> Case:
    async def run(budget: float) -> float:
        async def awaitable() -> object:
            return call()

        return await measure(awaitable, budget)

    return run


async def _bare_stream(client: httpx2.AsyncClient) -> int:
    total: int = 0
    async with client.stream("GET", "/stream") as response:
        async for chunk in response.aiter_bytes():
            total += len(chunk)

    return total


async def _haiway_stream() -> int:
    response: HTTPResponse = await HTTPClient.get(url="/stream", stream=True)
    total: int = 0
    async for chunk in response.stream_body():
        total += len(chunk)

    return total


def cases() -> Mapping[str, Case]:
    return {
        "response/construct": sync_case(
            lambda: HTTPResponse(status_code=200, headers=HEADERS, body=PAYLOAD)
        ),
        "facade/get, stub backend": stub_case(
            lambda client: client.get(url=f"{BASE_URL}/v1/items?page=1")
        ),
        "facade/get and read the body, stub backend": stub_case(_body),
        "get/bare": bare_case(lambda client: client.get("/v1/items", params={"page": "1"})),
        "get/haiway": haiway_case(lambda: HTTPClient.get(url="/v1/items", query={"page": "1"})),
        "post/bare": bare_case(lambda client: client.post("/v1/items", content=PAYLOAD)),
        "post/haiway": haiway_case(lambda: HTTPClient.post(url="/v1/items", body=PAYLOAD)),
        "stream/bare, per chunk": bare_case(_bare_stream, per=CHUNKS),
        "stream/haiway, per chunk": haiway_case(_haiway_stream, per=CHUNKS),
    }


async def _body(client: HTTPClient) -> bytes:
    return await (await client.get(url=f"{BASE_URL}/v1/items")).body()


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--filter", default="")
    parser.add_argument("--budget", type=float, default=0.5)
    arguments = parser.parse_args()

    baseline: Mapping[str, float] = (
        json.loads(arguments.compare.read_text()) if arguments.compare else {}
    )
    results: dict[str, float] = {}
    selected = {name: case for name, case in cases().items() if arguments.filter in name}
    width: int = max((len(name) for name in selected), default=0)

    print(
        f"python {sys.version.split()[0]}{'' if __debug__ else ' -O'},"
        f" budget {arguments.budget}s per case"
    )
    for name, case in selected.items():
        microseconds: float = await case(arguments.budget)
        results[name] = microseconds
        line: str = f"{name:<{width}}  {microseconds:9.3f} us"
        if (previous := baseline.get(name)) is not None:
            line += f"   x{microseconds / previous:5.2f}  (was {previous:.3f} us)"

        print(line)

    if arguments.json:
        arguments.json.write_text(json.dumps(results, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
