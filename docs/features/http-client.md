# HTTP Client

Haiway provides a functional, context-aware HTTP client interface that integrates seamlessly with
the framework's state management and observability features. The HTTP client supports async
operations and flexible backend implementations.

## Overview

The HTTP client in Haiway follows the framework's core principles:

- **Functional Interface**: All operations are performed through class methods on the `HTTPClient`
  state
- **Context Integration**: HTTP implementations are injected into the current scope through the
  context system
- **Protocol-Based**: Uses protocols for flexibility in backend implementations
- **Immutable Responses**: All responses are immutable state objects
- **Type-Safe**: Full type hints for request/response data

## Quick Start

### 1. Basic Usage with HTTPX

The HTTPX integration requires the optional `httpx` extra, which installs the
[httpx2](https://github.com/pydantic/httpx2) package:

```bash
pip install "haiway[httpx]"
# or manually:
pip install httpx2
```

It provides a production-ready transport adapter that opens an `httpx2.AsyncClient` and injects a
bound `HTTPClient` state into the scope:

```python
from haiway import HTTPClient, ctx
from haiway.httpx import HTTPXClient

async def fetch_user_data():
    # HTTPXClient is consumed as a disposable and provides HTTPClient state
    async with ctx.scope(
        "api_request",
        disposables=(HTTPXClient(base_url="https://api.example.com"),),
    ):
        response = await HTTPClient.get(url="/users/123")

        print(f"Status: {response.status_code}")
        print(f"Headers: {response.headers}")
        print(f"Body: {(await response.body()).decode()}")
```

### 2. Making Different Request Types

The HTTP client provides convenience methods for common HTTP methods:

```python
import json

from haiway import HTTPClient, ctx
from haiway.httpx import HTTPXClient

async def api_operations():
    async with ctx.scope("api", disposables=(HTTPXClient(),)):
        users = await HTTPClient.get(
            url="https://api.example.com/users",
            query={"page": 1, "limit": 10},
        )

        new_user = await HTTPClient.post(
            url="https://api.example.com/users",
            body=json.dumps({"name": "Alice", "email": "alice@example.com"}).encode(),
            headers={"Content-Type": "application/json"},
        )

        updated = await HTTPClient.put(
            url="https://api.example.com/users/123",
            body=json.dumps({"status": "active"}).encode(),
            headers={"Content-Type": "application/json"},
        )

        # DELETE, PATCH, and other verbs use the generic request method
        deleted = await HTTPClient.request(
            "DELETE",
            url="https://api.example.com/users/456",
        )
```

## Configuration Options

### HTTPXClient Parameters

Configure the HTTPX client with various options:

```python
from haiway.httpx import HTTPXClient

# Configure with defaults
client = HTTPXClient(
    base_url="https://api.example.com",
    headers={
        "User-Agent": "MyApp/1.0",
        "Accept": "application/json",
    },
    timeout=30.0,  # Default timeout for all requests
    follow_redirects=False,  # Default redirect behavior
    max_redirects=20,  # Client-wide cap on redirect chain length
    # Additional httpx2.AsyncClient options
    verify=True,  # SSL verification
)
```

`HTTPXClient` disables cookies, and defaults `follow_redirects` to `False`. Request level
`follow_redirects=` can override the redirect behavior per call, and additional `httpx2` keyword
arguments are forwarded via `**extra`.

`max_redirects` bounds how far a redirect chain is followed. It is set once per client rather than
per request - httpx2 accepts the limit only at construction - so it applies to every request that
follows redirects, including one that opted in per call.

When `timeout=` is omitted it defaults to 5 seconds, matching the httpx2 default, and applies to
each of the connect, read, write and pool phases separately - not to the request as a whole. A
single `HTTPXClient` instance owns one connection pool and supports one active scope at a time; use
a separate instance per concurrent scope.

### Request-Level Options

Override client defaults per request:

```python
from haiway import HTTPClient

# Override timeout for slow endpoint
response = await HTTPClient.get(
    url="/slow-endpoint",
    timeout=60.0,
)

# Control redirect behavior - the chain stays bounded by the client's max_redirects
response = await HTTPClient.get(
    url="/redirect",
    follow_redirects=True,
)

# Keep the body as a stream instead of reading it up front
response = await HTTPClient.get(
    url="/large-export",
    stream=True,
)
```

## Error Handling

Transport and adapter-level failures are wrapped in `HTTPClientError`, or one of its more specific
subclasses:

```python
import json

from haiway import HTTPClient, HTTPClientError

async def safe_request():
    try:
        response = await HTTPClient.get(url="https://api.example.com/data")
        return json.loads(await response.body())
    except HTTPClientError as e:
        print(f"HTTP request failed: {e}")
        # Original exception available as e.__cause__
        return None
```

Catch `HTTPTimeoutError` or `HTTPConnectionError` to react to the two most common - and most
retryable - failure modes:

```python
from haiway import HTTPClient, HTTPConnectionError, HTTPTimeoutError, retry

# retry only the transient failure modes
@retry(
    limit=3,
    delay=1.0,
    catching=lambda exc: isinstance(exc, HTTPTimeoutError | HTTPConnectionError),
)
async def fetch_with_retries():
    return await HTTPClient.get(url="https://api.example.com/data")
```

Both derive from `HTTPClientError`, so `except HTTPClientError` still catches everything. Each error
carries the `method` and `url` of the request it belongs to - both are required, so a failure is
always attributable - and renders them as a `"{method} {url}|{message}"` prefix, which keeps the
context readable in logs. The originating backend exception is preserved as `__cause__`. Request
bodies and headers are deliberately left out so credentials cannot leak into a log line.

`HTTPClient` does not automatically raise on `4xx` or `5xx` responses. Those are returned as a
normal `HTTPResponse`; `HTTPClientError` is used for transport and adapter-level failures.

## Advanced Usage

### Custom Headers

```python
import json

from haiway import HTTPClient

# Per-request headers
response = await HTTPClient.post(
    url="/webhook",
    headers={
        "X-Webhook-Signature": "abc123",
        "X-Webhook-Timestamp": "1234567890",
    },
    body=json.dumps({"event": "user.created"}).encode(),
)
```

### Working with Query Parameters

Query parameters support various types:

```python
from haiway import HTTPClient

# Multiple values for same parameter
response = await HTTPClient.get(
    url="/search",
    query={
        "tags": ["python", "async", "http"],  # ?tags=python&tags=async&tags=http
        "limit": 10,
        "active": True,
    },
)
```

### Response Processing

```python
import json

from haiway import HTTPClient, HTTPClientError

# Parse JSON response
response = await HTTPClient.get(url="/api/data")
data = json.loads(await response.body())

# Check status codes
if response.status_code == 200:
    # Success
    process_data(await response.body())
elif response.status_code == 404:
    # Not found
    return None
else:
    # Handle other status codes - method and url are required
    raise HTTPClientError(
        f"Unexpected status: {response.status_code}",
        method="GET",
        url="/api/data",
    )
```

`HTTPResponse` is immutable. By default the payload is read before the request returns, which hands
the connection back to the pool right away:

```python
# buffered by default: the payload is already in memory
data = json.loads(await response.body())
```

Pass `stream=True` to keep the body as a stream instead - useful for payloads too large to hold in
memory. A streamed body keeps its connection checked out until it is consumed:

```python
response = await HTTPClient.get(url="/large-export", stream=True)
async for chunk in response.stream_body():
    await sink.write(chunk)
```

`stream_body()` hands each chunk over without retaining it, so peak memory stays at one chunk no
matter how large the payload is. Nothing is cached in exchange: a stream reads once, and reading it
again raises `HTTPBodyConsumedError`. Reach for `body()` instead when the payload has to stay
re-readable - it buffers the whole thing to cache it, and a buffered payload can then be read any
number of times through either accessor.

A stream is claimed before it is read, so a read that does not reach the end cannot be resumed by a
later one - that would hand back the unread remainder as though it were the whole payload:

```python
from contextlib import aclosing

response = await HTTPClient.get(url="/report", stream=True)
async with aclosing(response.stream_body()) as chunks:
    async for chunk in chunks:
        break  # gave up part way

await response.body()  # raises HTTPBodyConsumedError, rather than returning the remainder
```

`HTTPBodyConsumedError` derives from `HTTPClientError`, so coarse handling still catches it, but it
reports a caller mistake rather than a transport failure - keep it out of retry predicates, since
retrying cannot help. It carries no `method` or `url`: an `HTTPResponse` does not know the request
it came from.

Consuming a streamed body is the caller's responsibility, and it has to happen within the scope that
issued the request since the connection belongs to that scope's pool. A body that is never read
keeps its connection checked out until the scope exit closes the pool.

Requesting the stream is what claims it, so a stream that is closed without being read counts as
consumed just the same. Closing the generator releases the connection right away - exhausting it
does that, `contextlib.aclosing` does it when breaking out early, and so does closing before the
first chunk was requested:

```python
from contextlib import aclosing

async with aclosing(response.stream_body()) as chunks:
    async for chunk in chunks:
        if not await sink.write(chunk):
            break  # the connection goes back to the pool right here
```

Abandoning the iteration without closing it leaves the release to the garbage collector, and to the
pool closing on scope exit at the latest. Response headers come from the backend, so lookups are
case-insensitive and repeated headers are joined with `", "`.

### Streaming Request Bodies

`body` also accepts an async byte generator, which streams the payload to the server instead of
holding it in memory:

```python
import json
from collections.abc import AsyncGenerator

from haiway import HTTPClient

async def encoded_rows(rows: AsyncGenerator[dict[str, Any]]) -> AsyncGenerator[bytes]:
    async for row in rows:
        yield json.dumps(row).encode() + b"\n"

# the payload is never materialized as a whole, on either side of the call
response = await HTTPClient.put(
    url="/uploads/events",
    body=encoded_rows(pending_events()),
    headers={"Content-Type": "application/x-ndjson"},
)
```

A streamed payload has no known length, so it goes out with `Transfer-Encoding: chunked` rather than
a `Content-Length`. It can be consumed only once, which means it cannot be replayed: a redirect
preserving the method (307, 308) or a retry fails with `HTTPClientError`. Pass `bytes` instead when
the request has to survive either.

Buffered payloads are `bytes`, not `str` - `HTTPBody` is `AsyncGenerator[bytes] | bytes`, so text is
encoded at the call site and the charset is never guessed for you. It has to be a full async
generator rather than any async iterable, because a streamed body is closed once it is read or
abandoned and only a generator has `aclose`. Wrap a bare async iterator in an `async def` generator
that yields from it.

### Connection Pooling and Reuse

The HTTPX client maintains connection pools within context:

```python
from haiway import HTTPClient, ctx
from haiway.httpx import HTTPXClient

# Reuse connections for multiple requests
async with ctx.scope(
    "batch_operation",
    disposables=(HTTPXClient(base_url="https://example.com"),),
):
    # All requests share the same connection pool
    for user_id in user_ids:
        response = await HTTPClient.get(url=f"/users/{user_id}")
        process_user(response)
```

The connection pool lives for the lifetime of the entered scope. Re-entering the same `HTTPXClient`
instance after it has been closed creates a fresh internal `httpx2.AsyncClient`. Entering an
instance that is already open raises `RuntimeError` from the backend.

## Observability

Every request records events within the current scope, whichever backend is bound to
`HTTPClient.requesting` - including test doubles and custom implementations. Each event carries
`http.request.method` and `url`, plus:

| Event                | Level | Additional attributes                   |
| -------------------- | ----- | --------------------------------------- |
| `http.request`       | debug | `http.request.body.size`                |
| `http.response`      | debug | `http.response.status_code`, `duration` |
| `http.request.error` | error | `error.type`, `duration`                |

Each request is also measured into the `http.client.request.duration` histogram, in seconds - at
info level when it produced a response, at error level when it failed. Its attributes are only the
ones a metric can afford, since a separate stream is stored per combination of them:

| Attribute                   | Recorded                                      |
| --------------------------- | --------------------------------------------- |
| `http.request.method`       | always                                        |
| `http.response.status_code` | when the request produced a response          |
| `error.type`                | when it failed instead                        |
| `server.address`            | when the request URL names a host - see below |

A few properties worth knowing:

- **Credentials never reach the observability backend.** Recorded URLs are stripped of userinfo,
  query and fragment - all three routinely carry secrets - and neither headers nor bodies are
  recorded at all.
- **The URL is recorded as given.** A relative URL stays relative: the `base_url` it resolves
  against belongs to the backend, which the facade does not see. For the same reason a request made
  with a relative URL carries no `server.address` on the metric - with one `HTTPXClient` per API,
  the scope it was made in usually names the same thing.
- **Events are debug, the metric is info.** Successful traffic leaves no events at a production log
  level - the histogram is what stays on. Raising the backend level to error narrows it to failures,
  which are still measured.
- **`http.request.body.size` is missing for a streamed request body.** Measuring it would mean
  buffering the payload, which is what streaming avoids.
- **`duration` is the time until the response was returned.** With `stream=True` that is the time to
  its headers - the body is transferred afterwards, outside of the call.
- **4xx and 5xx are not errors here.** They are ordinary responses, recorded as `http.response` with
  their status code. `http.request.error` is reserved for transport failures, and carries the
  concrete error type - `HTTPTimeoutError`, `HTTPConnectionError` - rather than the coarse one.
- **Cancellation is not recorded.** It is routine control flow under structured concurrency, not a
  request failure.

### Trace Propagation

A request can carry the current trace position, so the called service continues this trace instead
of starting its own. It is asked for per request, and off by default:

```python
async with ctx.scope(
    "api",
    observability=OpenTelemetry.observability(),
    disposables=(HTTPXClient(base_url="https://internal.example.com"),),
):
    # this request carries `traceparent`, and `tracestate` when present
    response = await HTTPClient.get(url="/users", trace_propagation=True)

    # this one does not
    other = await HTTPClient.get(url="/public")
```

`trace_propagation` is per request, and defaults to `False`, because it exposes internal trace
identifiers to whoever is called - ask for it towards services you own, not towards third party
APIs. Since it is decided at the request site, one client can be used for both. Headers passed to
the request are never overridden, so a caller managing trace context itself keeps control, and a
backend with no trace position to hand out - the default logger among them - propagates nothing.

## Testing

Mock HTTP clients for testing:

```python
import json

from haiway import (
    HTTPClient,
    HTTPHeaders,
    HTTPQueryParams,
    HTTPBody,
    HTTPResponse,
    ctx,
)

async def mock_request(
    method: str,
    /,
    *,
    url: str,
    query: HTTPQueryParams | None = None,
    headers: HTTPHeaders | None = None,
    body: HTTPBody | None = None,
    timeout: float | None = None,
    follow_redirects: bool | None = None,
    stream: bool = False,
) -> HTTPResponse:
    if url == "/users/123" and method == "GET":
        return HTTPResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=b'{"id": 123, "name": "Test User"}',
        )

    return HTTPResponse(status_code=404, headers={}, body=b"Not Found")

async def test_user_fetching():
    async with ctx.scope("test", HTTPClient(requesting=mock_request)):
        response = await HTTPClient.get(url="/users/123")
        assert response.status_code == 200
        data = json.loads(await response.body())
        assert data["name"] == "Test User"
```

## Best Practices

1. **Use `HTTPXClient` as a scope disposable**: This ensures `httpx2.AsyncClient` is opened and
   closed correctly.
1. **Set appropriate timeouts**: Prevent hanging requests and override per request only where
   needed.
1. **Handle transport failures separately from HTTP status codes**: Catch `HTTPClientError`, then
   validate `response.status_code` explicitly.
1. **Use `base_url` for related calls**: Keep request sites concise and consistent.
1. **Reuse a scope for batches**: Requests made inside one scope share the same connection pool.
1. **Stream only large payloads**: The default buffers the body and frees the connection before
   returning; reach for `stream=True` when the payload should not be held in memory, and stream the
   request `body` when the payload being sent should not be either.
1. **Consume every streamed body inside the issuing scope**: An unread body holds a pool connection
   until the scope exits, and it cannot be read once the scope is gone.
1. **Close a streamed body you leave early**: `contextlib.aclosing` hands the connection back at the
   break instead of leaving it to the garbage collector.
1. **Do not retry `HTTPBodyConsumedError`**: It reports a body read twice - a caller mistake that a
   retry cannot fix. Exclude it from retry predicates that catch `HTTPClientError` broadly.
1. **Send replayable bodies where redirects or retries are expected**: A streamed request body is
   consumed once and cannot be sent again.
1. **Mock the `requesting` callable in tests**: Most unit tests do not need a real transport.
1. **Ask for `trace_propagation` only on requests towards services you own**: It hands internal
   trace identifiers to whoever is called.

## Custom Implementations

Create custom HTTP client implementations by implementing the `HTTPRequesting` protocol:

```python
from haiway import HTTPBody, HTTPClient, HTTPHeaders, HTTPQueryParams, HTTPResponse

class CustomHTTPClient:
    async def request(
        self,
        method: str,
        /,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        stream: bool = False,
    ) -> HTTPResponse:
        # Your custom implementation
        return HTTPResponse(status_code=200, headers={}, body=b"ok")

    async def __aenter__(self):
        return HTTPClient(requesting=self.request)

    async def __aexit__(self, *args):
        return None
```

Any implementation that can provide a callable matching the `HTTPRequesting` protocol can be bound
into `HTTPClient` state and used through the same context-aware API.
