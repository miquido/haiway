from collections.abc import AsyncGenerator, Iterable, MutableSequence
from contextlib import asynccontextmanager
from logging import Logger, getLogger
from typing import Annotated, Any

import pytest

pytest.importorskip("fastapi")

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pytest import mark, raises
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send

from haiway import ContextMissing, State, ctx
from haiway.fastapi import (
    ServerContext,
    StreamResponse,
    application,
)
from tests.asgi import (
    TRACE_ID_HEADER,
    LogCapture,
    Result,
    http_scope,
    receive_request,
    running,
    send_request,
)


class ExampleState(State):
    value: str = "example"


class DisposableState(State):
    value: str = "disposable"


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
    router = APIRouter(prefix="/api/v1")

    @router.get("/example")
    async def example() -> dict[str, str]:
        return {
            "declared": ctx.state(ExampleState).value,
            "prepared": ctx.state(DisposableState).value,
        }

    log: MutableSequence[str] = []
    app: FastAPI = application(
        ServerContext(
            ExampleState(),
            disposables=(ExampleDisposable(log),),
        ),
        routers=(router,),
    )

    async with running(app):
        assert log == ["enter"]
        result: Result = await send_request(app, path="/api/v1/example")

    assert result.status == 200
    assert result.body == b'{"declared":"example","prepared":"disposable"}'
    assert log == ["enter", "exit"]


@mark.asyncio
async def test_response_carries_trace_headers() -> None:
    router = APIRouter()

    @router.get("/example")
    async def example() -> dict[str, str]:
        return {"trace": ctx.trace_id()}

    app: FastAPI = application(routers=(router,))

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 200
    assert result.headers[TRACE_ID_HEADER] in result.body.decode()


@mark.asyncio
async def test_handled_exception_response_carries_trace_headers() -> None:
    router = APIRouter()

    @router.get("/example")
    async def example() -> dict[str, str]:
        raise HTTPException(status_code=404, detail="missing")

    app: FastAPI = application(routers=(router,))

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 404
    assert result.body == b'{"detail":"missing"}'
    assert TRACE_ID_HEADER in result.headers


@mark.asyncio
async def test_validation_error_response_carries_trace_headers() -> None:
    router = APIRouter()

    @router.get("/example")
    async def example(value: int) -> dict[str, int]:
        return {"value": value}

    app: FastAPI = application(routers=(router,))

    async with running(app):
        result: Result = await send_request(app, query="value=invalid")

    # the response of the FastAPI validation handler, nested within the context
    assert result.status == 422
    assert TRACE_ID_HEADER in result.headers


@mark.asyncio
async def test_unhandled_exception_is_answered_by_the_framework() -> None:
    router = APIRouter()

    @router.get("/example")
    async def example() -> dict[str, str]:
        raise ValueError("broken")

    app: FastAPI = application(routers=(router,))
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
async def test_request_without_lifespan_is_refused() -> None:
    router = APIRouter()

    @router.get("/example")
    async def example() -> dict[str, str]:
        return {"status": "done"}

    app: FastAPI = application(
        ServerContext(disposables=(ExampleDisposable([]),)),
        routers=(router,),
    )
    result = Result()

    # the state of a request scope is what the lifespan prepares, so there is
    # nothing to serve a request with before it ran
    with raises(ContextMissing):
        await app(http_scope(), receive_request, result.collecting())


@mark.asyncio
async def test_additional_lifespan_runs_within_prepared_context() -> None:
    log: MutableSequence[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        log.append("startup")
        try:
            yield

        finally:
            log.append("shutdown")

    app: FastAPI = application(
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

    router = APIRouter()

    @router.get("/example")
    async def example() -> dict[str, str]:
        return {"value": ctx.state(ExampleState).value}

    app: FastAPI = application(
        ServerContext(ExampleState()),
        routers=(router,),
        middleware=[Middleware(NestedMiddleware)],
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.body == b'{"value":"middleware"}'


@mark.asyncio
async def test_extra_arguments_are_passed_through() -> None:
    router = APIRouter()

    @router.get("/example", summary="Example")
    async def example() -> dict[str, str]:
        return {"status": "done"}

    app: FastAPI = application(
        routers=(router,),
        title="Example API",
        version="2.1.0",
        openapi_url="/schema.json",
    )

    assert app.title == "Example API"
    assert app.version == "2.1.0"

    async with running(app):
        result: Result = await send_request(app, path="/schema.json")

    assert result.status == 200
    assert b'"title":"Example API"' in result.body
    assert b'"/example"' in result.body


@mark.asyncio
async def test_exception_handlers_are_installed() -> None:
    class ExampleError(Exception):
        pass

    router = APIRouter()

    @router.get("/example")
    async def example() -> dict[str, str]:
        raise ExampleError

    async def handle_example_error(
        request: Request,
        exc: Any,
    ) -> Response:
        return JSONResponse(
            {"detail": "handled"},
            status_code=418,
        )

    app: FastAPI = application(
        routers=(router,),
        exception_handlers={ExampleError: handle_example_error},
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 418
    assert TRACE_ID_HEADER in result.headers


@mark.asyncio
async def test_registered_server_error_handler_answers_the_request() -> None:
    router = APIRouter()

    @router.get("/example")
    async def example() -> dict[str, str]:
        raise ValueError("broken")

    async def handle_server_error(
        request: Request,
        exception: Any,
    ) -> Response:
        return JSONResponse({"detail": "handled"}, status_code=503)

    app: FastAPI = application(
        routers=(router,),
        # a server error handler answers above the middleware, in place of the
        # plain `500` the framework would produce
        exception_handlers={Exception: handle_server_error},
    )
    result = Result()

    async with running(app):
        with raises(ValueError):  # reraised for the server to report
            await app(http_scope(), receive_request, result.collecting())

    assert result.status == 503
    assert result.body == b'{"detail":"handled"}'


@mark.asyncio
async def test_dependency_teardown_runs_within_context() -> None:
    recorded: MutableSequence[str] = []

    async def scoped_value() -> AsyncGenerator[str]:
        yield ctx.state(ExampleState).value
        # the exit stack of the dependency is nested below the middleware, so the
        # teardown of a dependency still resolves the state of its request
        recorded.append(ctx.state(ExampleState).value)

    router = APIRouter()

    @router.get("/example")
    async def example(value: Annotated[str, Depends(scoped_value)]) -> dict[str, str]:
        return {"value": value}

    app: FastAPI = application(
        ServerContext(ExampleState(value="teardown")),
        routers=(router,),
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.body == b'{"value":"teardown"}'
    assert recorded == ["teardown"]


@mark.asyncio
async def test_dependencies_resolve_context_state() -> None:
    async def resolved_value() -> str:
        # dependencies are resolved within the scope of the request
        return ctx.state(ExampleState).value

    router = APIRouter()

    @router.get("/example")
    async def example(value: Annotated[str, Depends(resolved_value)]) -> dict[str, str]:
        return {"value": value}

    app: FastAPI = application(
        ServerContext(ExampleState(value="dependency")),
        routers=(router,),
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.body == b'{"value":"dependency"}'


@mark.asyncio
async def test_synchronous_endpoint_resolves_context_state() -> None:
    router = APIRouter()

    @router.get("/example")
    def example() -> dict[str, str]:  # runs in a worker thread
        return {"value": ctx.state(ExampleState).value}

    app: FastAPI = application(
        ServerContext(ExampleState(value="threaded")),
        routers=(router,),
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 200
    assert result.body == b'{"value":"threaded"}'


@mark.asyncio
async def test_background_task_runs_within_context() -> None:
    recorded: MutableSequence[str] = []

    router = APIRouter()

    @router.get("/example")
    async def example(background: BackgroundTasks) -> dict[str, str]:
        async def record() -> None:
            recorded.append(ctx.state(ExampleState).value)

        background.add_task(record)
        return {"status": "accepted"}

    app: FastAPI = application(
        ServerContext(ExampleState(value="background")),
        routers=(router,),
    )

    async with running(app):
        result: Result = await send_request(app)

    assert result.status == 200
    assert recorded == ["background"]


@mark.asyncio
async def test_stream_endpoint_streams_within_the_request_scope() -> None:
    router = APIRouter()

    @router.get("/stream")
    async def stream() -> StreamResponse:
        async def produce() -> AsyncGenerator[bytes]:
            # the state and the trace of the request, resolved mid-stream
            yield ctx.state(ExampleState).value.encode()
            yield ctx.trace_id().encode()

        return StreamResponse(produce(), media_type="application/x-ndjson")

    app: FastAPI = application(
        ServerContext(ExampleState(value="streamed")),
        routers=(router,),
    )

    async with running(app):
        result: Result = await send_request(app, path="/stream")

    assert result.headers["content-type"] == "application/x-ndjson"
    assert result.chunks == [b"streamed", result.headers[TRACE_ID_HEADER].encode()]


@mark.asyncio
async def test_request_is_recorded_with_its_route() -> None:
    logger: Logger = getLogger("test-fastapi-route")
    router = APIRouter()

    @router.get("/users/{identifier}/orders/{order}")
    async def endpoint(identifier: str, order: str) -> Response:
        return JSONResponse({"identifier": identifier, "order": order})

    app: FastAPI = application(
        ServerContext(observability=logger),
        routers=(router,),
    )

    with LogCapture(logger) as records:
        async with running(app):
            result: Result = await send_request(app, path="/users/12345/orders/7")

    assert result.status == 200
    # the requested path names the scope, the route template it matched does not -
    # the middleware runs before routing, which is what resolves the template
    assert any("Entering scope: GET /users/12345/orders/7" in record for record in records)

    recorded: str = "\n".join(record for record in records if "Attributes:" in record)
    # the routing of FastAPI leaves the route it matched in the scope, so the
    # template is what identifies the request rather than the path it carried
    assert '"http.route"]: "/users/{identifier}/orders/{order}"' in recorded
    assert '"url.path"]: "/users/12345/orders/7"' in recorded
    assert '"http.request.method"]: "GET"' in recorded
    assert '"http.response.status_code"]: 200' in recorded
