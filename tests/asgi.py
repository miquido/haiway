from asyncio import Queue, Task, create_task
from collections.abc import AsyncGenerator, MutableMapping, MutableSequence, Sequence
from contextlib import asynccontextmanager
from logging import Handler, Logger, LogRecord
from types import TracebackType
from typing import Any, Final

from starlette.types import ASGIApp, Message, Send

__all__ = (
    "TRACE_ID_HEADER",
    "LogCapture",
    "Result",
    "http_scope",
    "running",
    "send_request",
    "websocket_scope",
)


# the header `ServerContext.response_headers` reports the trace identifier through
TRACE_ID_HEADER: Final[str] = "trace-id"


def http_scope(
    *,
    method: str = "GET",
    path: str = "/example",
    query: str = "",
    headers: Sequence[tuple[bytes, bytes]] = (),
) -> MutableMapping[str, Any]:
    return {
        "type": "http",
        # 2.4 is what reports a gone consumer by failing the send, which is what
        # a streamed response requires
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode(),
        "query_string": query.encode(),
        "root_path": "",
        "headers": list(headers),
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "state": {},
    }


def websocket_scope(
    *,
    path: str = "/example",
    headers: Sequence[tuple[bytes, bytes]] = (),
) -> MutableMapping[str, Any]:
    return {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.4"},
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "root_path": "",
        "scheme": "ws",
        "headers": list(headers),
        "client": ("127.0.0.1", 54321),
        "server": ("testserver", 80),
        "subprotocols": [],
        "state": {},
    }


class Result:
    """Collector of the messages of a single response."""

    def __init__(self) -> None:
        self.status: int = 0
        self.headers: MutableMapping[str, str] = {}
        self.body: bytes = b""
        self.chunks: MutableSequence[bytes] = []

    def collecting(self) -> Send:
        async def send(message: Message) -> None:
            if message["type"] == "http.response.start":
                self.status = message["status"]
                for name, value in message.get("headers", ()):
                    self.headers[name.decode()] = value.decode()

            elif message["type"] == "http.response.body":
                chunk: bytes = message.get("body", b"")
                self.body += chunk
                if chunk:
                    self.chunks.append(chunk)

        return send


async def receive_request() -> Message:
    return {"type": "http.request", "body": b"", "more_body": False}


async def send_request(
    app: ASGIApp,
    /,
    *,
    method: str = "GET",
    path: str = "/example",
    query: str = "",
    headers: Sequence[tuple[bytes, bytes]] = (),
) -> Result:
    result = Result()
    await app(
        http_scope(method=method, path=path, query=query, headers=headers),
        receive_request,
        result.collecting(),
    )

    return result


@asynccontextmanager
async def running(
    app: ASGIApp,
    /,
) -> AsyncGenerator[None]:
    """Drive the ASGI lifespan of an application for the duration of the block."""
    incoming: Queue[Message] = Queue()
    outgoing: Queue[Message] = Queue()
    task: Task[None] = create_task(
        app(
            {"type": "lifespan", "asgi": {"version": "3.0"}, "state": {}},
            incoming.get,
            outgoing.put,
        )
    )

    await incoming.put({"type": "lifespan.startup"})
    startup: Message = await outgoing.get()
    if startup["type"] != "lifespan.startup.complete":
        await task
        raise AssertionError(startup)

    try:
        yield

    finally:
        await incoming.put({"type": "lifespan.shutdown"})
        await outgoing.get()
        await task


class LogCapture(Handler):
    def __init__(
        self,
        logger: Logger,
        /,
    ) -> None:
        super().__init__()
        self.logger: Logger = logger
        self.records: MutableSequence[str] = []

    def emit(
        self,
        record: LogRecord,
        /,
    ) -> None:
        self.records.append(record.getMessage())

    def __enter__(self) -> MutableSequence[str]:
        self.logger.addHandler(self)
        self.logger.setLevel(1)
        return self.records

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.logger.removeHandler(self)
