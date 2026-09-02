# Starlette

Haiway provides a Starlette integration plugging the context system into request handling. It
exposes an application wide declaration of the request context through `ServerContext`, an ASGI
middleware entering a context scope per request through `ContextMiddleware`, and an application
factory wiring both together through `application`.

## Overview

- **Context Managed**: application resources are prepared once, on startup, and their state is
  available to every request through `ctx`
- **Scope per Request**: each request is handled within its own context scope, recorded as one trace
  and described the way the HTTP semantic conventions of OpenTelemetry ask for
- **Traceable Responses**: responses carry the trace identifier of their request, so a client can
  report which one failed
- **Plug In Anywhere**: the middleware and the lifespan are plain Starlette pieces, so an existing
  application - a FastAPI one included - can be plugged in without being rewritten

## Installation

Install the Starlette extra:

```bash
pip install "haiway[starlette]"
```

## Quick Start

Declare the application context, then build the application from it:

```python
from haiway import State, ctx
from haiway.httpx import HTTPXClient
from haiway.starlette import ServerContext, application
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route


class ServiceConfig(State):
    upstream: str = "https://example.com"


async def status(request: Request) -> Response:
    # state declared by the application context, resolved from the request scope
    config: ServiceConfig = ctx.state(ServiceConfig)
    ctx.log_info("Checking %s", config.upstream)

    return JSONResponse({"upstream": config.upstream})


app = application(
    ServerContext(
        ServiceConfig(),
        disposables=(HTTPXClient(),),
    ),
    routes=[Route("/status", status)],
)
```

Serve it with any ASGI server:

```bash
uvicorn example:app
```

## Server Context

`ServerContext` describes what a request context looks like. It is used by two parts of the
application, both wired by `application(...)`:

- its `lifespan` prepares the declared disposables on startup and releases them on shutdown
- `ContextMiddleware` enters a context scope per request, carrying the prepared state

State comes from two places. Instances passed positionally are propagated as they are, which fits
configuration and other state owning no resources. Everything requiring setup or cleanup belongs in
`disposables`, prepared on startup and released on shutdown, whose state is propagated the same way:

```python
context = ServerContext(
    ServiceConfig(),  # propagated as provided
    disposables=(  # prepared on startup, released on shutdown
        HTTPXClient(),
        PostgresConnectionPool(),
    ),
)
```

Both accept `None` among their elements and ignore it, which keeps a conditionally provided element
from requiring a branch. State declared directly takes precedence over state prepared by the
disposables when both provide the same type.

The disposables are the instances declared here, prepared once, so a single context backs a single
run of a single application: a lifespan which already ended cannot be entered again, and a test
exercising more than one run declares a context per run. Resources which have to be created within
the running event loop belong inside a disposable's `__aenter__` rather than in its constructor.

The lifespan is what a request scope is built from, so it is not optional. Requests reaching an
application whose lifespan was not installed - or arriving before its startup completed - fail
rather than being served with a silently incomplete scope.

## Existing Applications

An application which is already built is plugged in by installing the same two pieces by hand:

```python
from haiway.starlette import ContextMiddleware, ServerContext
from starlette.applications import Starlette

context = ServerContext(disposables=(HTTPXClient(),))

app = Starlette(routes=[...], lifespan=context.lifespan)
app.add_middleware(ContextMiddleware, context=context)
```

`add_middleware` puts the middleware in front of the ones added before it, so adding it last keeps
it outermost - which is what makes the context available to the other middlewares as well. When the
application already has a lifespan of its own, compose the two with
`context.composed_lifespan(existing_lifespan)`.

FastAPI applications work the same way, and there is a factory building one directly - see
[FastAPI](fastapi.md).

## Request Handling

`ContextMiddleware` affects `http` and `websocket` requests, passing everything else - `lifespan`
included - through untouched. For each request it:

- enters a context scope named after the request - the method and the requested path, as in
  `GET /users/12345`, with `WS` in place of the method for a websocket connection, which carries
  none. The middleware runs before routing, so the route template behind that path is not available
  where the scope starts; it is recorded as an attribute once it is

- records the request into its scope as the HTTP semantic conventions of OpenTelemetry describe it,
  once it was handled and both the route it matched and the status it was answered with are known:

  | attribute                   | value                                                              |
  | --------------------------- | ------------------------------------------------------------------ |
  | `http.request.method`       | the method as received, for an `http` request only                 |
  | `http.route`                | the route template, when the routing left one in the request scope |
  | `url.path`                  | the requested path                                                 |
  | `url.scheme`                | the scheme of the request                                          |
  | `network.protocol.version`  | the HTTP version                                                   |
  | `http.response.status_code` | the status of the response, when one was started                   |

  `http.route` is what makes the requests of a parameterized route findable as one, since the scope
  name carries the path which was actually requested rather than the template behind it. FastAPI
  leaves the matched route in the request scope, so a FastAPI application records it; Starlette does
  not, so a plain Starlette application records none unless it reports one itself with
  `ctx.record_info(attributes={"http.route": ...})` from within the request. Resolving it in the
  middleware would mean matching the route table a second time for every request. The query string
  and the address of the caller are deliberately left out - the first carries credentials often
  enough that recording it by default would leak them, the second identifies the caller

- adds the trace headers to the response: `trace-id` holding the trace identifier of the request
  scope, accompanied by `traceparent` and `tracestate` when the observability backend provides them.
  An entry which a header cannot hold - a line break, a character outside latin-1 - is left out
  rather than written, since the trace context is partly continued from the request. For a websocket
  request the response is the one denying its handshake - an accepted connection switches the
  protocol rather than answering, so it carries no headers of its own

- lets an exception no handler answered propagate through the scope of its request, which is what
  records it as the failure of that request, then reraises it. Answering it is left to the
  application: the server error handling of the framework sits above the middleware, so the `500` it
  produces is what the client receives - the plain one, the traceback page of a `debug` application,
  or the response of a handler registered for `Exception` or `500`. None of them carry the trace
  headers, having been produced outside the scope of the request

`HTTPException`, `WebSocketException` and `ClientDisconnect` are not a failure of the request they
end - the first two are how an application asks for a specific response, the third is a consumer
which went away. They are withheld while the scope of the request is left, so it does not record
them as its failure, then reraised for whatever handles them upstream.

Everything handling the request runs within its scope: the middlewares nested below, the endpoint,
its background tasks and the generator of a streaming response.

## Request Derived State

State which depends on the request - the identity of its caller, a tenant, a locale - is added by a
middleware nested within the context:

```python
from starlette.datastructures import Headers
from starlette.middleware import Middleware
from starlette.types import ASGIApp, Receive, Scope, Send


class Caller(State):
    identifier: str


class CallerMiddleware:
    def __init__(self, app: ASGIApp, /) -> None:
        self.app: ASGIApp = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)

        # extends the state of the current request scope
        with ctx.updating(Caller(identifier=Headers(scope=scope).get("x-caller", "anonymous"))):
            await self.app(scope, receive, send)


app = application(
    context,
    routes=[...],
    middleware=[Middleware(CallerMiddleware)],
)
```

## Streaming Responses

The scope of a request stays entered until its response is finished, so a response streamed from an
async generator resolves state, records observability and reports the trace of the request it
belongs to while it is being produced - the middleware returns only once the last chunk was sent.

`StreamResponse` streams the chunks of a generator:

```python
from collections.abc import AsyncGenerator

from haiway.starlette import StreamResponse


async def export(request: Request) -> Response:
    async def rows() -> AsyncGenerator[bytes]:
        async for row in Postgres.fetch_rows(QUERY):  # state of the request
            yield row.get_str("payload").encode() + b"\n"

    return StreamResponse(rows(), media_type="application/x-ndjson")
```

Any live feed is streamed the same way - a server sent events feed by yielding the event stream
format and declaring the headers such a feed needs:

```python
async def updates(request: Request) -> Response:
    async def events() -> AsyncGenerator[bytes]:
        async for update in Updates.subscribe():
            yield f"event: update\ndata: {update.payload}\n\n".encode()

    return StreamResponse(
        events(),
        media_type="text/event-stream",
        headers={
            # a live feed is not cached, and not buffered by a reverse proxy
            "cache-control": "no-store",
            "x-accel-buffering": "no",
        },
    )
```

### Closing a Stream

The response takes a full async generator, not any async iterable, and closes it where the streaming
ends - when it ran out and when the consumer went away. That is what a generator holding resources
needs: an abandoned generator is otherwise finalized by the garbage collector, in a fresh context,
where a scope it opened can no longer be released.

Closing is what runs the cleanup of the generator, so it has to be able to await - releasing a
connection or leaving a scope usually does. That requires a server advertising ASGI spec version 2.4
or newer, the one reporting a gone consumer by failing the send: below that version the framework
ends a streamed response by cancelling it, and the cancellation would be delivered again at the
first await of the cleanup, leaving the generator suspended halfway through it.

A generator opening a scope of its own has to keep it inside itself, which is what `ctx.stream`
provides:

```python
async def produce() -> AsyncGenerator[bytes]:
    async with ctx.scope("updates", disposables=(Subscription(),)):
        async for update in Updates.subscribe():
            yield update.payload.encode()


async def updates(request: Request) -> Response:
    # the scope lives inside the generator, so it spans the whole response
    return StreamResponse(ctx.stream(produce))
```

A scope entered around *building* the generator is already released by the time the streaming
starts:

```python
async def updates(request: Request) -> Response:
    async with ctx.scope("updates", disposables=(Subscription(),)):
        content = produce()  # nothing was produced yet

    return StreamResponse(content)  # the subscription is already disposed
```

### A Failing Stream

A failing stream cannot be answered with an error - its response already started, and its status and
headers are long gone. The status and headers are also sent before the first element is asked for,
so this holds from the very first one: a producer which fails while setting itself up still produces
a `200` with nothing in it.

The failure is recorded where it happens, within the scope of the request, as
`Response streaming failed` carrying the exception - and only then reraised, which leaves the
response incomplete so the consumer can tell. Recording it there is what makes the actual failure
visible at all: on its way out it passes through the exception handling of the framework, which
replaces it with a `RuntimeError` about a response already started whenever a handler matches its
type, and that replacement is what the request scope would otherwise be recorded as failing with. A
failure to close the stream afterwards is recorded as a warning of its own rather than replacing
what is already on its way out.

The consumer sees a response which ends early. Where it has to know more than that, do the work
which can fail before returning the response, and send a failure which happens later as part of the
stream - a final event saying so - before letting it propagate.

A consumer which goes away mid-stream is not a failure of the response it ended, and is recorded as
`Response streaming ended by a disconnected consumer` at debug level rather than as an error. It
ends the request the same way whichever shape it arrives in - as a cancelled response below ASGI
spec version 2.4, and as a `ClientDisconnect` from the send above it - so a feed a consumer
eventually leaves does not turn every connection into a failed request. A gone consumer is the send
failing, which is what tells it apart from a body failing with an `OSError` of its own: the latter
is the failure of the response it was producing and is recorded as one, even though the framework
reports both to the server as a `ClientDisconnect`.

## Observability

Request scopes are recorded through the observability backend of the server context. It accepts a
`Logger`, an `Observability` instance, or a callable preparing one per request:

```python
from logging import getLogger

context = ServerContext(observability=getLogger("api"))
```

A logging backend of its own is built out of that `Logger` for each request, so what one recorded is
released along with it: a single backend shared by the application would keep holding the scopes of
every request which never completed - an abandoned generator among them - for as long as it runs. An
`Observability` instance is used as provided, and a callable is invoked per request - it has to
answer with a backend, which `LoggerObservability` builds out of a logger - which is what continuing
an incoming trace requires.

### Distributed Traces

The W3C trace context of every request is read from its headers and handed to that callable, as
`traceparent` and `tracestate` keyword arguments. `OpenTelemetry.observability` takes exactly those,
so continuing the trace of the caller is the whole wiring:

```python
from haiway.opentelemetry import OpenTelemetry

context = ServerContext(observability=OpenTelemetry.observability)
```

Each request then joins the trace it arrives with, or starts its own when it arrives with none, and
its response reports back which trace handled it - `trace-id`, plus `traceparent` and `tracestate`
identifying the position within it. Those two are a request header format, so nothing consumes them
from a response on its own; they are there for a caller which correlates the two sides itself, next
to the `trace-id` a user reports.

The values are handed over as received, apart from the whitespace surrounding them, which a header
carries without it being part of the value - an entry left empty by stripping it is reported as the
absent one it is. Validating the rest is the responsibility of the backend, which the specification
requires to reject a malformed value and start a new trace instead - what the OpenTelemetry
integration does, recording a warning. Two cases are resolved before that, because a backend given a
single value cannot see them:

- a request carrying several `traceparent` headers has no single position to continue, so its trace
  context is discarded and a new trace is started
- only the first `tracestate` header is read, so a caller splitting a long trace state across
  several of them has to join them itself

An outgoing HTTP request carries the trace onwards when it asks to, which is what makes a call chain
one trace end to end:

```python
context = ServerContext(
    observability=OpenTelemetry.observability,
    disposables=(HTTPXClient(base_url=INTERNAL_URL),),
)

# within an endpoint - a request towards a service you own continues the trace
# of its caller
response = await HTTPClient.get(url="/users", trace_propagation=True)
```

It is off by default, and settable per request, because propagating hands internal trace identifiers
to whoever is called - see [Trace Propagation](http-client.md#trace-propagation).

`request_trace_context` exposes the inbound reading on its own, for an application which needs the
trace context for something else - propagating it over a protocol Haiway does not handle, for
instance.

To pass a recording level, apply it ahead of time:

```python
from functools import partial

from haiway import ObservabilityLevel

context = ServerContext(
    observability=partial(OpenTelemetry.observability, ObservabilityLevel.DEBUG),
)
```

An `Observability` instance provided directly is used as it is, which records every request as its
own trace - continuing incoming traces needs the callable.

### Configuring OpenTelemetry

`OpenTelemetry.configure(...)` has to run before the first request, which is what a disposable
expresses:

```python
from collections.abc import Iterable

from haiway import State
from haiway.opentelemetry import OpenTelemetry


class Telemetry:
    async def __aenter__(self) -> Iterable[State]:
        if not OpenTelemetry.configured():  # claimed once per process
            OpenTelemetry.configure(
                service="api",
                version="1.0.0",
                environment="production",
                otlp_endpoint="http://localhost:4317",
                insecure=True,
            )

        return ()  # provides no state, only the configuration

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: object,
    ) -> None:
        OpenTelemetry.force_flush()  # exports what the application recorded


context = ServerContext(
    observability=OpenTelemetry.observability,
    disposables=(Telemetry(), HTTPXClient()),
)
```

Requests fail while the integration is unconfigured - preparing an observability backend without it
raises - so the disposable belongs in the server context rather than somewhere later.

Note what the disposable does *not* do. The OpenTelemetry provider slots are process wide and
claimed once: `configure(...)` refuses a second call and `shutdown()` cannot be undone, while a
process can run more than one application - a test suite exercising several. Guarding on
`configured()` is what keeps the second of them from failing on startup, and flushing rather than
shutting down is what keeps it exporting. Shutting the providers down belongs at process exit, which
the SDK does on its own.

Startup and shutdown are outside of every request scope, so what the lifespan records - the context
being prepared and released, and a disposable which failed to prepare - goes to the root logger
rather than through this backend. There is no span for the startup of an application, and a failing
disposable is reported where the logging of the application goes. Startup work which has to be
traced belongs in a scope of its own, entered within an additional lifespan - see
[Startup Work](#startup-work).

When no observability is provided at all, request scopes are recorded through the root logger, so
they land wherever the logging of the application is configured to go. Providing one is still
preferable - `getLogger("api")`, or the OpenTelemetry factory - and gives the records a name to
filter by. Two things the default deliberately avoids: a logger named after the scope, which is what
`ctx.scope` would request and which allocates one per distinct request path, and a logger of our own
created on import, which `setup_logging` disables along with every other logger predating it.

## Context Presets

Presets declared by the application context are available within request scopes, which lets an
endpoint enter a nested scope by name:

```python
from haiway import ContextPresets

context = ServerContext(
    presets=(ContextPresets.of("summary", SummaryConfig(), disposables=(HTTPXClient,)),),
)


async def summarize(request: Request) -> Response:
    async with ctx.scope("summary"):  # resolved from the application presets
        ...
```

See [Context Presets](context-presets.md) for how presets are composed.

## Startup Work

Startup work owning a resource is best expressed as one of the disposables. Anything else - running
migrations, warming a cache - can be passed to `application(...)` as an additional lifespan, entered
within the lifespan of the context, so the disposables are already prepared when it runs:

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from haiway.postgres import Postgres, PostgresConnectionPool
from starlette.applications import Starlette


@asynccontextmanager
async def lifespan(app: Starlette) -> AsyncGenerator[None]:
    async with ctx.scope("migrations", disposables=(PostgresConnectionPool(),)):
        await Postgres.execute_migrations("example.migrations")

    yield  # suspend until shutdown


app = application(context, routes=[...], lifespan=lifespan)
```

Note the scope entered and exited before the `yield`. A scope held open across it would leak its
state into the context the server creates its request tasks in - preparing state for requests is
what the server context is for.

Startup runs outside of every context scope, the disposables of the context included: they are
prepared, but nothing entered a scope carrying their state, so `ctx.state(...)` resolves nothing
there. Startup work needing state enters a scope of its own, as above.

## Testing

An application built this way is tested like any other ASGI application, entering its lifespan first
so the context is prepared. `TestClient` requires `httpx2`, installed with the `httpx` extra:

```python
from starlette.testclient import TestClient


def build_application() -> Starlette:  # a context per run, so an application per test
    return application(
        ServerContext(ServiceConfig(), disposables=(HTTPXClient(),)),
        routes=[Route("/status", status)],
    )


def test_status() -> None:
    with TestClient(build_application()) as client:  # enters the lifespan
        response = client.get("/status")

    assert response.status_code == 200
    assert response.headers["trace-id"]
```

A context backs a single run, which is what the factory is for: each test builds its own context and
application rather than sharing one module level application between tests which each enter its
lifespan.

The lifespan of a `ServerContext` can also be entered directly, which is what a test exercising
state without an application needs - with a context of its own for the same reason:

```python
async def test_upstream() -> None:
    context = ServerContext(ServiceConfig(), disposables=(HTTPXClient(),))

    async with context.lifespan():
        ...
```

## Best Practices

- Declare every application resource in `disposables` - shutdown then releases them in the same
  place, whatever went wrong
- Declare a context per run - one instance backs one lifespan, so a test suite builds its
  application within each test rather than sharing one
- Keep the context middleware outermost so the rest of the application - middlewares included - runs
  within a scope
- Report the `trace-id` of a failed response back to your users; it is what correlates their report
  with the recorded trace
