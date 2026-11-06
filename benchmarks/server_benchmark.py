"""Micro benchmarks of the Starlette and FastAPI integrations.

Run with ``make benchmark-server`` or ``uv run python benchmarks/server_benchmark.py``.

    --json PATH      write the results as json
    --compare PATH   print the ratio against results written earlier
    --filter TEXT    run the cases whose name contains TEXT only
    --budget SECONDS time spent measuring each case (default 0.5)

Requests are driven through the ASGI interface in process - no network, no server - so
the numbers are the cost of the application stack alone. Every case reports the best of
several repeats in microseconds per request. Compare the ``haiway`` cases with their
``bare`` counterparts for the overhead of the integration. Run with ``python -O`` to
measure without the debug bookkeeping of observability, which is what production does.
"""

import argparse
import asyncio
import gc
import json
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from time import perf_counter
from typing import Any

from fastapi import FastAPI
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import ASGIApp, Message, Scope

from haiway.fastapi import application as fastapi_application
from haiway.starlette import ServerContext, StreamResponse
from haiway.starlette import application as starlette_application
from haiway.starlette.trace import request_trace_context

TRACEPARENT: bytes = b"00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"
CHUNKS: int = 10


def http_scope(
    path: str,
    headers: list[tuple[bytes, bytes]],
) -> Scope:
    return {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": headers,
        "server": ("benchmark", 80),
        "client": ("benchmark", 1),
    }


async def receive() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def send(message: Message) -> None:
    pass


async def chunks() -> AsyncGenerator[bytes]:
    for _ in range(CHUNKS):
        yield b"x" * 100


async def plain_endpoint(request: Request) -> Response:
    return PlainTextResponse("ok")


async def plain_stream_endpoint(request: Request) -> Response:
    return StreamingResponse(chunks())


async def haiway_stream_endpoint(request: Request) -> Response:
    return StreamResponse(chunks())


def starlette_app(
    *,
    haiway: bool,
    stream: bool,
) -> Starlette:
    endpoint = (
        (haiway_stream_endpoint if haiway else plain_stream_endpoint) if stream else plain_endpoint
    )
    routes = [Route("/", endpoint)]
    if haiway:
        return starlette_application(ServerContext(), routes=routes)

    return Starlette(routes=routes)


def fastapi_app(*, haiway: bool) -> FastAPI:
    application: FastAPI = fastapi_application(ServerContext()) if haiway else FastAPI()

    @application.get("/")
    async def endpoint() -> Response:
        return PlainTextResponse("ok")

    return application


async def run_requests(
    app: ASGIApp,
    scope: Scope,
    count: int,
    concurrency: int,
) -> None:
    async def worker(requests: int) -> None:
        for _ in range(requests):
            await app(dict(scope), receive, send)

    await asyncio.gather(*(worker(count // concurrency) for _ in range(concurrency)))


async def measure_requests(
    app: ASGIApp,
    scope: Scope,
    concurrency: int,
    budget: float,
    repeats: int = 5,
) -> float:
    await run_requests(app, scope, 200, concurrency)  # warm up
    started: float = perf_counter()
    await run_requests(app, scope, 200, concurrency)
    per_request: float = (perf_counter() - started) / 200
    count: int = max(concurrency, int(budget / repeats / per_request) // concurrency * concurrency)
    best: float = float("inf")
    for _ in range(repeats):
        gc.collect()
        started = perf_counter()
        await run_requests(app, scope, count, concurrency)
        best = min(best, perf_counter() - started)

    return best / count * 1e6


type Case = Callable[[float], Awaitable[float]]


def server_case(
    app: Starlette | FastAPI,
    headers: list[tuple[bytes, bytes]] | None = None,
    concurrency: int = 1,
) -> Case:
    async def run(budget: float) -> float:
        lifespan: AbstractAsyncContextManager[Any] = app.router.lifespan_context(app)
        async with lifespan:
            return await measure_requests(
                app,
                http_scope("/", headers or []),
                concurrency,
                budget,
            )

    return run


def trace_case(headers: list[tuple[bytes, bytes]]) -> Case:
    scope: Scope = http_scope("/", headers)

    async def run(budget: float) -> float:
        loops: int = 100_000
        best: float = float("inf")
        for _ in range(5):
            started: float = perf_counter()
            for _ in range(loops):
                request_trace_context(scope)

            best = min(best, perf_counter() - started)

        return best / loops * 1e6

    return run


def cases() -> Mapping[str, Case]:
    traced: list[tuple[bytes, bytes]] = [
        (b"traceparent", TRACEPARENT),
        (b"tracestate", b"vendor=value"),
    ]
    return {
        "starlette/bare": server_case(starlette_app(haiway=False, stream=False)),
        "starlette/haiway": server_case(starlette_app(haiway=True, stream=False)),
        "starlette/haiway, incoming traceparent": server_case(
            starlette_app(haiway=True, stream=False), traced
        ),
        "starlette/bare, 50 concurrent": server_case(
            starlette_app(haiway=False, stream=False), concurrency=50
        ),
        "starlette/haiway, 50 concurrent": server_case(
            starlette_app(haiway=True, stream=False), concurrency=50
        ),
        "starlette stream/bare": server_case(starlette_app(haiway=False, stream=True)),
        "starlette stream/haiway": server_case(starlette_app(haiway=True, stream=True)),
        "fastapi/bare": server_case(fastapi_app(haiway=False)),
        "fastapi/haiway": server_case(fastapi_app(haiway=True)),
        "fastapi/bare, 50 concurrent": server_case(fastapi_app(haiway=False), concurrency=50),
        "fastapi/haiway, 50 concurrent": server_case(fastapi_app(haiway=True), concurrency=50),
        "trace context/with traceparent": trace_case(traced),
        "trace context/without headers": trace_case([]),
    }


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
        line: str = f"{name:<{width}}  {microseconds:9.2f} us"
        if (previous := baseline.get(name)) is not None:
            line += f"   x{microseconds / previous:5.2f}  (was {previous:.2f} us)"

        print(line)

    if arguments.json:
        arguments.json.write_text(json.dumps(results, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
