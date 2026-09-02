# FastAPI

Haiway provides a FastAPI variant of the [Starlette](starlette.md) integration. It is the same
integration with a FastAPI base: the request context is declared through the same `ServerContext`,
requests are handled within scopes by the same `ContextMiddleware`, and `haiway.fastapi.application`
wires both into a `FastAPI` application instead of a plain Starlette one.

## Overview

- **FastAPI Base**: returns a regular `FastAPI` application - routers, dependencies, OpenAPI docs
  and everything else work as they always do
- **Shared Context**: `ServerContext` and `ContextMiddleware` are the Starlette ones, re-exported
  from `haiway.fastapi` so a single import is enough
- **Scope per Request**: state declared by the server context is available to endpoints,
  dependencies, background tasks and nested middlewares

## Installation

Install the FastAPI extra, which brings Starlette along with it:

```bash
pip install "haiway[fastapi]"
```

## Quick Start

Declare the application context, then build the application from it:

```python
from fastapi import APIRouter
from haiway import State, ctx
from haiway.fastapi import ServerContext, application
from haiway.httpx import HTTPXClient


class ServiceConfig(State):
    upstream: str = "https://example.com"


router = APIRouter(prefix="/api/v1", tags=["example"])


@router.get("/status")
async def status() -> dict[str, str]:
    # state declared by the server context, resolved from the request scope
    config: ServiceConfig = ctx.state(ServiceConfig)
    ctx.log_info("Checking %s", config.upstream)

    return {"upstream": config.upstream}


app = application(
    ServerContext(
        ServiceConfig(),
        disposables=(HTTPXClient(),),
    ),
    routers=(router,),
    title="Example API",
    version="1.0.0",
    openapi_url="/openapi.json" if __debug__ else None,
    docs_url="/swagger" if __debug__ else None,
)
```

Serve it with any ASGI server:

```bash
uvicorn example:app
```

## Differences from the Starlette Factory

Everything about the request context - state, disposables, presets, observability, scope names and
trace headers - is described on the [Starlette](starlette.md) page and applies here unchanged. Only
the application factory differs:

- `routers` takes `APIRouter` instances, included in order, instead of `routes` taking Starlette
  routes. A router serving under a path prefix carries it itself, as `APIRouter(prefix="/api/v1")`,
  and further routers can be added afterwards through `app.include_router(...)`
- everything FastAPI accepts and Starlette does not - `title`, `version`, `description`,
  `openapi_url`, `docs_url`, global `dependencies`, `root_path` - is passed through as keyword
  arguments
- `exception_handlers` accepts async and sync handlers alike - a sync one is called in a worker
  thread - and the type `ExceptionHandling` names their signature. A handler nested below the
  middleware - the validation error handler of FastAPI included - answers within the scope of its
  request, so its response carries the trace headers, while the `Exception` and `500` slots run
  above it and carry none

`middleware`, `lifespan` and `debug` work exactly as in the Starlette factory, and
`ContextMiddleware` is installed as the outermost application middleware. There is no
`max_body_size` here - FastAPI does not accept one, so passing it through would fail; a body limit
takes a middleware of its own.

## Distributed Traces

Requests continue the trace they arrive with, which takes passing the OpenTelemetry backend factory
to the context - the W3C trace context of each request is read from its headers and handed to it:

```python
from haiway.opentelemetry import OpenTelemetry

context = ServerContext(observability=OpenTelemetry.observability)
```

Responses then report which trace handled them, through the `trace-id`, `traceparent` and
`tracestate` headers, and an outgoing request carries it onwards when it asks to:

```python
context = ServerContext(
    observability=OpenTelemetry.observability,
    disposables=(HTTPXClient(base_url=INTERNAL_URL),),
)

# within an endpoint - the trace continues into the service you own
response = await HTTPClient.get(url="/users", trace_propagation=True)
```

See [Observability](starlette.md#observability) for what is resolved before the backend sees it, and
for configuring the OpenTelemetry integration itself.

## Dependencies and Endpoints

The context scope is entered before routing, so everything FastAPI runs while handling a request
runs within it:

```python
from typing import Annotated

from fastapi import Depends, Header


class Caller(State):
    identifier: str


async def caller(authorization: Annotated[str, Header()]) -> Caller:
    # state resolved from the scope of the request being handled
    return await Authorization.verify(authorization)


@router.get("/profile")
async def profile(caller: Annotated[Caller, Depends(caller)]) -> dict[str, str]:
    return {"identifier": caller.identifier}
```

That covers dependencies, endpoints, background tasks added through `BackgroundTasks`, and the
generator of a streaming response. Synchronous endpoints are included as well - FastAPI runs them in
a worker thread with the context of the request copied into it, so `ctx.state(...)` resolves there
too. Long blocking work still belongs off the event loop path: prefer async endpoints and Haiway's
`@asynchronous` helper for the calls which cannot be.

## Streaming Responses

`StreamResponse` is re-exported here and returned from an endpoint like any other response. The
scope of the request stays entered until the last chunk was sent, so the producer resolves state and
reports the trace of the request it belongs to:

```python
from collections.abc import AsyncGenerator

from haiway.fastapi import StreamResponse


@router.get("/updates")
async def updates() -> StreamResponse:
    async def content() -> AsyncGenerator[bytes]:
        async for update in Updates.subscribe():  # state of the request
            yield update.payload.encode()

    return StreamResponse(content(), media_type="application/x-ndjson")
```

Returning a `Response` from a FastAPI endpoint skips its serialization, so annotate the return type
with the response itself - or with `Response` - rather than with the model of a chunk. See
[Streaming Responses](starlette.md#streaming-responses) for what closes a stream and for the scope a
producer of its own needs.

## Existing Applications

An application which is already built is plugged in by installing the same two pieces by hand:

```python
from fastapi import FastAPI
from haiway.fastapi import ContextMiddleware, ServerContext

context = ServerContext(disposables=(HTTPXClient(),))

app = FastAPI(lifespan=context.lifespan)
app.add_middleware(ContextMiddleware, context=context)
```

`add_middleware` puts the middleware in front of the ones added before it, so adding it last keeps
it outermost - which is what makes the context available to the other middlewares as well. When the
application already has a lifespan of its own, compose the two:

```python
app = FastAPI(lifespan=context.composed_lifespan(existing_lifespan))
```

## Testing

An application built this way is tested like any other FastAPI application, entering its lifespan
first so the context is prepared. A context backs a single run, so build the application within the
test - through a factory - rather than entering the lifespan of a shared one more than once.
`TestClient` requires `httpx2`, installed with the `httpx` extra:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient


def build_application() -> FastAPI:  # a context per run, so an application per test
    return application(
        ServerContext(ServiceConfig(), disposables=(HTTPXClient(),)),
        routers=(router,),
    )


def test_status() -> None:
    with TestClient(build_application()) as client:  # enters the lifespan
        response = client.get("/api/v1/status")

    assert response.status_code == 200
    assert response.headers["trace-id"]
```
