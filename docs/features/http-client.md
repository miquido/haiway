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

The HTTPX integration requires the optional `httpx` extra:

```bash
pip install "haiway[httpx]"
# or manually:
pip install httpx
```

It provides a production-ready transport adapter that opens an `httpx.AsyncClient` and injects a
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
            body=json.dumps({"name": "Alice", "email": "alice@example.com"}),
            headers={"Content-Type": "application/json"},
        )

        updated = await HTTPClient.put(
            url="https://api.example.com/users/123",
            body=json.dumps({"status": "active"}),
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
    # Additional httpx.AsyncClient options
    verify=True,  # SSL verification
)
```

`HTTPXClient` always configures `follow_redirects=False` and disables cookies by default. Request
level `follow_redirects=` can override the redirect behavior per call, and additional `httpx`
keyword arguments are forwarded via `**extra`.

### Request-Level Options

Override client defaults per request:

```python
from haiway import HTTPClient

# Override timeout for slow endpoint
response = await HTTPClient.get(
    url="/slow-endpoint",
    timeout=60.0,
)

# Control redirect behavior
response = await HTTPClient.get(
    url="/redirect",
    follow_redirects=True,
)
```

## Error Handling

Transport and adapter-level failures are wrapped in `HTTPClientError`:

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
    body=json.dumps({"event": "user.created"}),
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
    # Handle other status codes
    raise HTTPClientError(f"Unexpected status: {response.status_code}")
```

`HTTPResponse` is immutable, but its body is consumed lazily. `await response.body()` reads the full
payload and caches it as `bytes`. For streaming use cases, iterate with `response.stream_body()` or
`response.iter_bytes()` instead of forcing the full body into memory.

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
instance after it has been closed creates a fresh internal `httpx.AsyncClient`.

## Testing

Mock HTTP clients for testing:

```python
import json

from haiway import HTTPClient, HTTPHeaders, HTTPQueryParams, HTTPResponse, ctx

async def mock_request(
    method: str,
    /,
    *,
    url: str,
    query: HTTPQueryParams | None = None,
    headers: HTTPHeaders | None = None,
    body: str | bytes | None = None,
    timeout: float | None = None,
    follow_redirects: bool | None = None,
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

1. **Use `HTTPXClient` as a scope disposable**: This ensures `httpx.AsyncClient` is opened and
   closed correctly.
1. **Set appropriate timeouts**: Prevent hanging requests and override per request only where
   needed.
1. **Handle transport failures separately from HTTP status codes**: Catch `HTTPClientError`, then
   validate `response.status_code` explicitly.
1. **Use `base_url` for related calls**: Keep request sites concise and consistent.
1. **Reuse a scope for batches**: Requests made inside one scope share the same connection pool.
1. **Choose between buffered and streamed body access intentionally**: `body()` buffers,
   `stream_body()` streams.
1. **Mock the `requesting` callable in tests**: Most unit tests do not need a real transport.

## Custom Implementations

Create custom HTTP client implementations by implementing the `HTTPRequesting` protocol:

```python
from haiway import HTTPClient, HTTPHeaders, HTTPQueryParams, HTTPResponse

class CustomHTTPClient:
    async def request(
        self,
        method: str,
        /,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
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
