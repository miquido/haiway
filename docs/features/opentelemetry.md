# OpenTelemetry Integration

Haiway provides seamless integration with [OpenTelemetry](https://opentelemetry.io/) for distributed
tracing, metrics collection, and structured logging. This integration allows you to observe your
applications with industry-standard tooling while maintaining Haiway's functional programming
principles.

**Note:** `OpenTelemetry.configure()` currently provisions only OTLP gRPC exporters. If you need
OTLP HTTP/protobuf or other SDK-level configuration, initialize global OpenTelemetry providers
outside Haiway and bind Haiway with `OpenTelemetry.autoconfigure()`.

## Overview

The OpenTelemetry integration in Haiway bridges the framework's observability abstractions with the
OpenTelemetry SDK, enabling:

- **Distributed Tracing**: Automatic span creation and context propagation across async operations
- **Metrics Collection**: Counter, histogram, and gauge metrics with custom attributes
- **Structured Logging**: Context-aware logs correlated with traces
- **External Trace Linking**: Connect to existing distributed traces from other services

## Quick Start

### 1. Installation

The OpenTelemetry integration requires additional dependencies:

```bash
pip install haiway[opentelemetry]
# or manually:
pip install opentelemetry-api opentelemetry-sdk opentelemetry-exporter-otlp
```

### 2. Configuration

Configure OpenTelemetry once at application startup before creating any observability scopes:

```python
from haiway.opentelemetry import OpenTelemetry

# Configure for local development (console output)...
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="development"
)

# ...or for production (OTLP export)
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="production",
    otlp_endpoint="http://jaeger:4317",
    insecure=True,
    export_interval_millis=5000,
    attributes={
        "team": "backend",
        "component": "api"
    }
)
```

If your process already configures OpenTelemetry through SDK autoconfiguration, environment
variables, or auto-instrumentation, bind Haiway to those global providers instead:

```python
from haiway.opentelemetry import OpenTelemetry

OpenTelemetry.autoconfigure(
    service="my-service",
    version="1.0.0",
    environment="production",
)
```

OpenTelemetry providers can only be installed once per process, and each signal has its own global
slot. `configure()` resolves the tracer, meter, and logger providers independently: any provider
already installed - by auto-instrumentation, another library, or a previous call - is adopted, while
the remaining slots get providers built from the passed exporter configuration. This avoids both the
split state where traces and metrics keep going to existing providers while logs go to newly created
ones, and the opposite one where a single externally configured signal leaves the others on
non-exporting proxies.

A slot can also be held by a provider that is neither an SDK instance to adopt nor replaceable, such
as an explicitly installed no-op. `configure()` verifies that each installation actually took
effect; a signal whose slot could not be claimed is reported as an error and the provider built for
it is released right away, so its exporter threads and channels do not linger unused. Nothing is
retained for such a signal, which keeps `force_flush()` and `shutdown()` from acting on a provider
telemetry never reaches.

`autoconfigure()` adopts whatever is installed without building anything. When no signal has an SDK
provider yet, every global one is still a non-exporting proxy - that is reported as a warning rather
than raised, since providers may be installed later, but until then telemetry is discarded and
scopes report a zero trace identifier.

Both `configure()` and `autoconfigure()` raise `OpenTelemetryException` when `service` is empty, and
`observability()` raises it when neither has been called yet.

Telemetry is attributed to an instrumentation scope named after `service`, carrying `version`. The
service is identified by the resource attributes as well, per the OpenTelemetry specification, which
is what `autoconfigure()` relies on - there the resource belongs to the externally installed
providers.

### Flushing and Shutdown

Providers installed by `configure()` register interpreter exit hooks, so telemetry is normally
flushed automatically. Flush explicitly when exiting through a path that skips those hooks, or when
asserting on exported telemetry in tests:

```python
from haiway.opentelemetry import OpenTelemetry

OpenTelemetry.force_flush()          # export what is buffered
OpenTelemetry.force_flush(5000)      # with a 5 second budget per provider

OpenTelemetry.shutdown()             # flush and stop the providers
```

Both are safe to call when nothing was configured, and both log failures rather than raising, so
they never break a shutdown path. `shutdown()` leaves the integration unconfigured, so
`observability()` raises again afterwards instead of handing out adapters writing into providers
that no longer export anything.

`configure()` claims its provider slots once per process, and they are never released. Calling it
again - including after `shutdown()` - raises `OpenTelemetryException` rather than silently keeping
the earlier configuration or adopting providers which no longer export anything.

`autoconfigure()` stays repeatable, since binding again is how a process picks up providers which
were installed after the first call. It can not tell a provider which was shut down from a live one,
so avoid it after `shutdown()`.

## Configuration Options

### Basic Configuration

| Parameter     | Type          | Description                                             |
| ------------- | ------------- | ------------------------------------------------------- |
| `service`     | `str`         | Name of your service. Must not be empty.                |
| `version`     | `str`         | Version of your service                                 |
| `environment` | `str`         | Deployment environment (e.g., "production", "staging")  |
| `instance`    | `str \| None` | Instance identifier. Defaults to the current process id |

### OTLP Export Configuration

| Parameter                | Type                        | Default | Description                                                    |
| ------------------------ | --------------------------- | ------- | -------------------------------------------------------------- |
| `otlp_endpoint`          | `str \| None`               | `None`  | OTLP endpoint. Resolved from the environment when unset        |
| `insecure`               | `bool \| None`              | `None`  | Insecure connections. Resolved from the environment when unset |
| `export_interval_millis` | `int`                       | `5000`  | Metrics export interval in milliseconds                        |
| `attributes`             | `Mapping[str, Any] \| None` | `None`  | Additional resource attributes                                 |

### 3. Usage with Context

Use the configured OpenTelemetry observability in your Haiway contexts:

```python
from haiway import ctx
from haiway.opentelemetry import OpenTelemetry
import asyncio

async def main():
    # Use in context scope
    async with ctx.scope(
        "application",
        # Create an OpenTelemetry-backed observability adapter
        observability=OpenTelemetry.observability()
    ):
        await process_requests()

async def process_requests():
    # Automatic span creation and context propagation
    async with ctx.scope("request-processing"):
        ctx.log_info("Processing batch of requests")

        # Record custom metrics
        ctx.record_info(
            metric="requests.processed",
            value=10,
            kind="counter",
        )

        # Record custom events
        ctx.record_info(
            event="batch.started",
            attributes={
                "batch_size": 10,
                "priority": "high",
            },
        )

        await process_individual_requests()

async def process_individual_requests():
    # Nested spans are automatically created
    async with ctx.scope("individual-request"):
        ctx.record_info(
            attributes={
                "request.id": "req-123",
                "user.id": "user-456",
            },
        )

        # Simulated work
        await asyncio.sleep(0.1)

        ctx.record_info(
            metric="request.duration",
            value=100,
            unit="ms",
            kind="histogram",
        )
```

### Console vs OTLP Export

**Console Export**: When no OTLP endpoint is given and none is configured in the environment,
OpenTelemetry console exporters are used.

```python
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="development"
    # otlp_endpoint=None (default) uses console exporters
)
```

**OTLP Export**: With an OTLP endpoint provided, telemetry is sent to that endpoint and not mirrored
to the console. The endpoint may equally come from the standard `OTEL_EXPORTER_OTLP_ENDPOINT` or
per-signal `OTEL_EXPORTER_OTLP_{TRACES,METRICS,LOGS}_ENDPOINT` environment variables, so an
environment-configured process exports over OTLP without passing the argument.

```python
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="production",
    otlp_endpoint="http://collector:4317"
)
```

### Adapter Reuse

An `Observability` instance may back several independent root scopes, including concurrent ones.
Each root starts its own span tree and its own trace, and its state is released when that tree
completes:

```python
observability = OpenTelemetry.observability()

async def handle(request):
    # each request is an independent root trace
    async with ctx.scope("request", observability=observability):
        await process(request)

await asyncio.gather(*(handle(request) for request in requests))
```

Nested scopes inherit the span tree of the root they belong to, and share the tracer, meter, logger,
and metric instruments of the adapter. Entering a scope therefore costs one span and one context. An
adapter tracks its live scopes in plain dictionaries, so it may be shared across concurrent scopes
on one event loop, but not across threads - create one adapter per loop instead.

## Distributed Tracing

### Automatic Span Creation

Haiway automatically creates OpenTelemetry spans for each context scope that uses an
OpenTelemetry-backed observability instance:

```python
async def handle_request():
    async with ctx.scope("http-request"):  # Creates span "http-request"
        async with ctx.scope("database-query"):  # Creates child span "database-query"
            await query_database()

        async with ctx.scope("external-api"):  # Creates child span "external-api"
            await call_external_service()
```

### External Trace Linking

Connect to existing distributed traces from other services:

```python
# Continue an external trace from the W3C trace context headers
observability = OpenTelemetry.observability(
    traceparent=request.headers.get("traceparent"),
    tracestate=request.headers.get("tracestate"),
)

async with ctx.scope("service-handler", observability=observability):
    # This root span continues the caller's trace as a child of its span
    await handle_service_request()
```

`traceparent=` installs the decoded span context as the remote parent of the root span, so the
incoming trace continues as a single trace across services, and `tracestate=` carries the vendor
trace state along with it. Decoding is delegated to the OpenTelemetry trace context propagator, so
field lengths, reserved versions, and zeroed identifiers are validated exactly as the W3C
specification requires. Anything it rejects is ignored with a warning and the scope starts its own
trace instead. The value is decoded once, when the adapter is created.

The remote parent applies to root scopes only. A nested scope entered within a spawned task stays
under the span its task inherited, instead of being re-rooted at the remote parent.

Reading the headers and preparing a backend per request is what the Starlette and FastAPI
integrations do, so an ASGI application needs none of the wiring above - `observability` there takes
the callable itself:

```python
from haiway.starlette import ServerContext

context = ServerContext(observability=OpenTelemetry.observability)
```

See [Starlette](starlette.md#distributed-traces) for what it resolves from a request, and for the
cases a backend handed a single value cannot see - several `traceparent` headers, or a `tracestate`
split across a few.

To propagate the current active span to another system, use:

```python
headers = {"traceparent": OpenTelemetry.traceparent()}

if tracestate := OpenTelemetry.tracestate():
    headers["tracestate"] = tracestate
```

`traceparent()`/`tracestate()` and `observability(traceparent=..., tracestate=...)` are a matching
pair: the values produced by one service are consumed by the next. Both return `None` when no valid
span context is active.

`ctx.trace_context()` produces the same carrier without reaching for the integration directly:

```python
headers = {**ctx.trace_context()}
```

It encodes the position of the current scope, and returns an empty mapping when the observability
backend has no trace position to hand out - the default logger among them - or when used out of
context. That makes it the form to reach for in reusable code, which cannot assume OpenTelemetry is
the backend in use. It is what `HTTPClient` attaches to a request made with
`trace_propagation=True`.

### Trace Context Propagation

Traces automatically propagate across async operations:

```python
async def parent_operation():
    async with ctx.scope("parent"):
        # Start concurrent operations - they inherit trace context
        tasks = [
            asyncio.create_task(child_operation(i))
            for i in range(3)
        ]
        await asyncio.gather(*tasks)

async def child_operation(task_id: int):
    async with ctx.scope(f"child-{task_id}"):
        # Each child gets its own span under the parent trace
        await asyncio.sleep(0.1)
```

## Metrics Collection

### Metric Types

Haiway supports three metric kinds through the level-specific `ctx.record_*` helpers:

```python
# Counter: Monotonically increasing values
ctx.record_info(metric="requests.total", value=1, kind="counter")

# Histogram: Distribution of values (e.g., latencies, sizes)
ctx.record_info(metric="request.duration", value=150, unit="ms", kind="histogram")

# Gauge: Point-in-time values that can go up or down
ctx.record_info(metric="active_connections", value=42, kind="gauge")
```

### Metric Attributes

Add dimensional data to metrics:

```python
ctx.record_info(
    metric="requests.processed",
    value=1,
    kind="counter",
    attributes={
        "method": "POST",
        "endpoint": "/api/users",
        "status": "success",
    },
)
```

### Custom Units

An instrument is identified by its kind, name, and unit, and instruments are shared by every scope
of an adapter. Recording the same metric from a nested scope therefore contributes to one stream,
while changing the unit of a name records into an instrument of its own rather than being silently
dropped into the first one.

Specify units for better observability:

```python
ctx.record_info(metric="response.size", value=1024, unit="byte", kind="histogram")
ctx.record_info(metric="cpu.usage", value=75.5, unit="percent", kind="gauge")
ctx.record_info(metric="request.rate", value=150, unit="1/s", kind="gauge")
```

## Structured Logging

### Context-Aware Logging

Logs are automatically correlated with active spans:

```python
async with ctx.scope("user-service"):
    ctx.log_info("Processing user request")  # Correlated with span

    try:
        user = await fetch_user(user_id)
        ctx.log_debug("User fetched successfully: %s", user_id)
    except UserNotFound as e:
        ctx.log_error("User not found: %s", user_id, exception=e)
```

### Log Levels

Control the minimum observability level by setting `level=`. The threshold applies to logs, events,
metrics, and attributes:

```python
from haiway.context import ObservabilityLevel

# Only log warnings and errors
observability = OpenTelemetry.observability(level=ObservabilityLevel.WARNING)

async with ctx.scope("critical-operation", observability=observability):
    ctx.log_debug("This won't be recorded")  # Below threshold
    ctx.log_warning("This will be recorded")  # At or above threshold
```

## Event Recording

### Custom Events

Record significant events with structured attributes:

```python
ctx.record_info(
    event="user.login",
    attributes={
        "user_id": "user-123",
        "login_method": "oauth",
        "client_ip": "192.168.1.100",
        "success": True,
    },
)

ctx.record_info(
    event="cache.miss",
    attributes={
        "cache_key": "user:profile:123",
        "ttl": 3600,
    },
)
```

### Business Events

Track business-relevant events:

```python
ctx.record_info(
    event="order.created",
    attributes={
        "order_id": "ord-789",
        "customer_id": "cust-456",
        "total_amount": 99.99,
        "currency": "USD",
        "items_count": 3,
    },
)
```

## Advanced Usage

### Custom Resource Attributes

Add service-specific metadata:

```python
import os

OpenTelemetry.configure(
    service="payment-service",
    version="2.1.0",
    environment="production",
    instance=os.environ.get("INSTANCE_ID"),
    otlp_endpoint="http://collector:4317",
    attributes={
        "service.namespace": "payments",
        "deployment.version": "v2.1.0-rc.1",
        "team": "payments-team",
        "region": "us-east-1"
    }
)
```

`configure()` derives these resource attributes from its own parameters, following the current
OpenTelemetry semantic conventions:

| Parameter     | Resource attribute            |
| ------------- | ----------------------------- |
| `service`     | `service.name`                |
| `version`     | `service.version`             |
| `instance`    | `service.instance.id`         |
| `environment` | `deployment.environment.name` |

Anything passed through `attributes=` is applied last, so it can override these.

### Error Handling and Status

On scope exit, spans are marked `ERROR` - described as `TypeName: message` - when the scope exits
with a regular exception, and left `UNSET` otherwise. `OK` is deliberately not set on success: the
OpenTelemetry specification reserves it for explicitly asserted success, and the SDK treats it as
terminal, which would stop a later error from being recorded on the same span.

Cancellation is not an error. A scope exiting with `CancelledError` - or any other `BaseException`
which is not an `Exception` - leaves the status `UNSET`, since cancellation is routine control flow
under structured concurrency and marking it would paint whole subtrees red.

Logging with `exception=...` reports the failure through both signals - `exception.type`,
`exception.message` and `exception.stacktrace` on the log record, and an `exception` event on the
active span:

```python
async with ctx.scope("risky-operation"):
    try:
        await potentially_failing_operation()
        # Span status: UNSET
    except Exception as e:
        # Span status: ERROR, described as "TypeName: message"
        # Exception details attached to the log record and to the active span:
        ctx.log_error("Operation failed", exception=e)
        raise
```

## Span Completion

A span ends only once every scope nested below it has completed, which keeps parent-child lifetimes
intact regardless of the order they finish in. The span still reports the duration of its own scope:
the timestamp is captured when the scope exits, not when the last descendant finishes. Since every
scope joins the tasks spawned within it before leaving, a nested scope always ends before the one it
was spawned in.

## Attribute Normalization

Before sending data to OpenTelemetry, Haiway normalizes observability attributes:

- `None` and `MISSING` values are skipped
- sequences are filtered and exported as tuples
- mapping values are flattened into dotted keys such as `http.request_id`
- binary values - `bytes`, `bytearray`, `memoryview` - are rendered as a single string rather than
  exported byte by byte

Metric values are checked as well: `NaN`, both infinities, and integers too wide to be converted to
a float are skipped with a warning, since recording them would raise inside the SDK.

Example:

```python
ctx.record_info(
    attributes={
        "request": {
            "id": "req-123",
            "method": "POST",
        },
        "tags": ["api", "critical"],
        "optional": None,
    },
)
```

This produces span attributes equivalent to:

```text
request.id=req-123
request.method=POST
tags=("api", "critical")
```

## Integration with Popular Tools

### Jaeger

```python
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="production",
    otlp_endpoint="http://jaeger-collector:14250",
    insecure=True,
)
```

### Prometheus + Grafana

```python
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="production",
    otlp_endpoint="http://otel-collector:4317",
    export_interval_millis=10000,  # 10 second export interval
)
```

### SigNoz

For self-hosted SigNoz:

```python
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="production",
    otlp_endpoint="http://signoz-otel-collector:4317",
    insecure=True,
    export_interval_millis=5000,
)
```

## Best Practices

### 1. Service Naming

Use consistent service names across your organization:

```python
# Good: Consistent with service discovery
OpenTelemetry.configure(service="user-service", ...)

# Avoid: Inconsistent naming
OpenTelemetry.configure(service="userSvc", ...)
```

### 2. Meaningful Span Names

Use descriptive span names that indicate the operation:

```python
# Good: Descriptive operation names
async with ctx.scope("validate-user-permissions"):
    ...

async with ctx.scope("fetch-user-profile"):
    ...

# Avoid: Generic names
async with ctx.scope("operation"):
    ...
```

### 3. Attribute Consistency

Use consistent attribute names across your services:

```python
# Good: Consistent attribute naming
ctx.record_info(
    attributes={
        "user.id": user_id,
        "user.role": user_role,
        "request.id": request_id,
    },
)

# Establish naming conventions:
# - Use dots for namespacing
# - Use snake_case for attribute names
# - Use consistent prefixes (user., request., etc.)
```

## Troubleshooting

### Common Issues

**1. No telemetry data appearing**

- Verify OTLP endpoint is reachable
- Check if OpenTelemetry.configure() was called before creating observability, or if autoconfigure()
  was used when relying on externally configured global providers
- Look for a logged error naming provider slots held outside of the SDK, or a warning about
  autoconfigure finding no installed providers - both mean telemetry is being discarded
- Check that the `haiway[opentelemetry]` extra is installed
- Ensure proper network connectivity to your observability backend

**2. High memory usage**

- Consider increasing export intervals
- Check if you're creating too many unique metric label combinations
- Review span attribute cardinality

**3. Missing trace correlation**

- Ensure observability is properly passed through context scopes
- Verify the supplied `traceparent` value is a valid W3C traceparent string, and that `tracestate`
  is passed alongside it when the caller sends one
- Check that async context is properly propagated

## Further Reading

- [OpenTelemetry Official Documentation](https://opentelemetry.io/docs/)
- [Haiway Context Guide](../guides/functionalities.md)
- [Haiway State Management](../guides/state.md)
