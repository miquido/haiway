# HTTP Client

Haiway provides a functional, context-aware HTTP client interface that integrates seamlessly with the framework's state management and observability features. The HTTP client supports async operations and flexible backend implementations.

## Overview

The HTTP client in Haiway follows the framework's core principles:

- **Functional Interface**: All operations are performed through class methods on the `HTTPClient` state
- **Context Integration**: HTTP implementations are injected through the context system
- **Protocol-Based**: Uses protocols for flexibility in backend implementations
- **Immutable Responses**: All responses are immutable state objects
- **Type-Safe**: Full type hints for request/response data

## Quick Start

### 1. Basic Usage with HTTPX

The HTTPX integration requires additional dependencies:

```bash
pip install haiway[httpx]
# or manually:
pip install httpx
```

it provides a production-ready HTTP client:

```python
from haiway import ctx
from haiway.httpx import HTTPXClient
from haiway.helpers import HTTPClient

async def fetch_user_data():
    # Create and use HTTP client in context
    async with ctx.scope(
        "api_request",
        disposables=(HTTPXClient(base_url="https://api.example.com"),)
    ):
        # Make GET request
        response = await HTTPClient.get(url="/users/123")
        # Access response data
        print(f"Status: {response.status_code}")
        print(f"Headers: {response.headers}")
        print(f"Body: {response.body.decode()}")
```

### 2. Making Different Request Types

The HTTP client provides convenience methods for common HTTP methods:

```python
import json

async def api_operations():
    async with ctx.scope("api", disposables=(HTTPXClient(),)):
        # GET request with query parameters
        users = await HTTPClient.get(
            url="https://api.example.com/users",
            query={"page": 1, "limit": 10}
        )
        # POST request with JSON body
        new_user = await HTTPClient.post(
            url="https://api.example.com/users",
            body=json.dumps({"name": "Alice", "email": "alice@example.com"}),
            headers={"Content-Type": "application/json"}
        )
        # PUT request to update
        updated = await HTTPClient.put(
            url="https://api.example.com/users/123",
            body=json.dumps({"status": "active"}),
            headers={"Content-Type": "application/json"}
        )
        # Generic request method for other verbs
        deleted = await HTTPClient.request(
            "DELETE",
            url="https://api.example.com/users/456"
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
        "Accept": "application/json"
    },
    timeout=30.0,  # Default timeout for all requests
    # Additional HTTPX options
    max_redirects=5,
    verify=True,  # SSL verification
)
```

### Request-Level Options

Override client defaults per request:

```python
# Override timeout for slow endpoint
response = await HTTPClient.get(
    url="/slow-endpoint",
    timeout=60.0  # Override default timeout
)

# Control redirect behavior
response = await HTTPClient.get(
    url="/redirect",
    follow_redirects=True  # Override client default
)
```

## Error Handling

All HTTP errors are wrapped in `HTTPClientError`:

```python
from haiway.helpers import HTTPClientError

async def safe_request():
    try:
        response = await HTTPClient.get(url="https://api.example.com/data")
        return json.loads(response.body)
    except HTTPClientError as e:
        print(f"HTTP request failed: {e}")
        # Original exception available as e.__cause__
        return None
```

## Advanced Usage

### Custom Headers

```python
# Per-request headers
response = await HTTPClient.post(
    url="/webhook",
    headers={
        "X-Webhook-Signature": "abc123",
        "X-Webhook-Timestamp": "1234567890"
    },
    body=json.dumps({"event": "user.created"})
)
```

### Working with Query Parameters

Query parameters support various types:

```python
# Multiple values for same parameter
response = await HTTPClient.get(
    url="/search",
    query={
        "tags": ["python", "async", "http"],  # ?tags=python&tags=async&tags=http
        "limit": 10,
        "active": True
    }
)
```

### Response Processing

```python
# Parse JSON response
response = await HTTPClient.get(url="/api/data")
data = json.loads(response.body)

# Check status codes
if response.status_code == 200:
    # Success
    process_data(response.body)
elif response.status_code == 404:
    # Not found
    return None
else:
    # Handle other status codes
    raise HTTPClientError(f"Unexpected status: {response.status_code}")
```

### Connection Pooling and Reuse

The HTTPX client maintains connection pools within context:

```python
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

## Testing

Mock HTTP clients for testing:

```python
from haiway import State
from haiway.helpers import HTTPResponse, HTTPRequesting

# Create mock implementation
async def mock_request(
    method: str,
    /,
    *,
    url: str,
    query = None,
    headers = None,
    body = None,
    timeout = None,
    follow_redirects = None,
) -> HTTPResponse:
    # Return mock response based on URL/method
    if url == "/users/123" and method == "GET":
        return HTTPResponse(
            status_code=200,
            headers={"Content-Type": "application/json"},
            body=b'{"id": 123, "name": "Test User"}'
        )

    return HTTPResponse(status_code=404, headers={}, body=b"Not Found")

# Use in tests
async def test_user_fetching():
    async with ctx.scope("test", HTTPClient(requesting=mock_request)):
        response = await HTTPClient.get(url="/users/123")
        assert response.status_code == 200
        data = json.loads(response.body)
        assert data["name"] == "Test User"
```

## Best Practices

1. **Always use context as disposable**: Ensures proper resource cleanup
2. **Set appropriate timeouts**: Prevent hanging requests
3. **Handle errors gracefully**: Wrap requests in try/except blocks
4. **Use base URLs**: Configure base URL to avoid repetition
5. **Pool connections**: Reuse HTTP client instances within a scope
6. **Validate responses**: Check status codes and handle errors appropriately
7. **Use type hints**: Leverage type safety for request/response data

## Custom Implementations

Create custom HTTP client implementations by implementing the `HTTPRequesting` protocol:

```python
from haiway.helpers import HTTPRequesting, HTTPResponse

class CustomHTTPClient:
    async def request(
        self,
        method: str,
        /,
        *,
        url: str,
        query = None,
        headers = None,
        body = None,
        timeout = None,
        follow_redirects = None,
    ) -> HTTPResponse:
        # Your custom implementation
        pass

    async def __aenter__(self):
        # Setup resources
        return HTTPClient(requesting=self.request)

    async def __aexit__(self, *args):
        # Cleanup resources
        pass
```

This allows integration with any HTTP library while maintaining the same interface.
