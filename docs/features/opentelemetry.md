# OpenTelemetry Integration

Haiway provides seamless integration with [OpenTelemetry](https://opentelemetry.io/) for distributed tracing, metrics collection, and structured logging. This integration allows you to observe your applications with industry-standard tooling while maintaining Haiway's functional programming principles.

## Overview

The OpenTelemetry integration in Haiway bridges the framework's observability abstractions with the OpenTelemetry SDK, enabling:

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

Configure OpenTelemetry once at application startup (configuring more than once will cause an errror):

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

## Configuration Options

### Basic Configuration

| Parameter | Type | Description |
|-----------|------|-------------|
| `service` | `str` | Name of your service |
| `version` | `str` | Version of your service |
| `environment` | `str` | Deployment environment (e.g., "production", "staging") |

### OTLP Export Configuration

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `otlp_endpoint` | `str \| None` | `None` | OTLP endpoint URL. If None, uses console exporters |
| `insecure` | `bool` | `True` | Whether to use insecure connections |
| `export_interval_millis` | `int` | `5000` | Metrics export interval in milliseconds |
| `attributes` | `Mapping[str, Any] \| None` | `None` | Additional resource attributes |


### 3. Usage with Context

Use the configured OpenTelemetry observability in your Haiway contexts:

```python
from haiway import ctx
from haiway.opentelemetry import OpenTelemetry

async def main():
    # Use in context scope
    async with ctx.scope(
        "application",
        # Create observability instance
        observability=OpenTelemetry.observability()
    ):
        await process_requests()

async def process_requests():
    # Automatic span creation and context propagation
    async with ctx.scope("request-processing"):
        ctx.log_info("Processing batch of requests")

        # Record custom metrics
        ctx.record_metric("requests.processed", value=10, kind="counter")

        # Record custom events
        ctx.record_event("batch.started", attributes={
            "batch_size": 10,
            "priority": "high"
        })

        await process_individual_requests()

async def process_individual_requests():
    # Nested spans are automatically created
    async with ctx.scope("individual-request"):
        ctx.record_attributes({
            "request.id": "req-123",
            "user.id": "user-456"
        })

        # Simulated work
        await asyncio.sleep(0.1)

        ctx.record_metric("request.duration", value=100, unit="ms", kind="histogram")
```

### Console vs OTLP Export

**Console Export**:
When no otlp endpoint was specified all metrics and logs will be utilizing python logging system.
```python
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="development"
    # otlp_endpoint=None (default) uses console exporters
)
```

**OTLP Export**:
With otlp endpoint provided there will be no logs mirroring, all metrics and logs will be sent to the specified endpoint.
```python
OpenTelemetry.configure(
    service="my-service",
    version="1.0.0",
    environment="production",
    otlp_endpoint="http://collector:4317"
)
```

## Distributed Tracing

### Automatic Span Creation

Haiway automatically creates OpenTelemetry spans for each context scope:

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
# Link to external trace (e.g., from HTTP headers)
external_trace_id = request.headers.get("x-trace-id")

observability = OpenTelemetry.observability(
    external_trace_id=external_trace_id
)

async with ctx.scope("service-handler", observability=observability):
    # This span will be linked to the external trace
    await handle_service_request()
```

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

Haiway supports three OpenTelemetry metric types:

```python
# Counter: Monotonically increasing values
ctx.record_metric("requests.total", value=1, kind="counter")

# Histogram: Distribution of values (e.g., latencies, sizes)
ctx.record_metric("request.duration", value=150, unit="ms", kind="histogram")

# Gauge: Point-in-time values that can go up or down
ctx.record_metric("active_connections", value=42, kind="gauge")
```

### Metric Attributes

Add dimensional data to metrics:

```python
ctx.record_metric(
    "requests.processed",
    value=1,
    kind="counter",
    attributes={
        "method": "POST",
        "endpoint": "/api/users",
        "status": "success"
    }
)
```

### Custom Units

Specify units for better observability:

```python
ctx.record_metric("response.size", value=1024, unit="byte", kind="histogram")
ctx.record_metric("cpu.usage", value=75.5, unit="percent", kind="gauge")
ctx.record_metric("request.rate", value=150, unit="1/s", kind="gauge")
```

## Structured Logging

### Context-Aware Logging

Logs are automatically correlated with active spans:

```python
async with ctx.scope("user-service"):
    ctx.log_info("Processing user request")  # Correlated with span

    try:
        user = await fetch_user(user_id)
        ctx.log_debug("User fetched successfully", user_id=user_id)
    except UserNotFound as e:
        ctx.log_error("User not found", user_id=user_id, exception=e)
```

### Log Levels

Control log verbosity by setting the observability level:

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
ctx.record_event("user.login", attributes={
    "user_id": "user-123",
    "login_method": "oauth",
    "client_ip": "192.168.1.100",
    "success": True
})

ctx.record_event("cache.miss", attributes={
    "cache_key": "user:profile:123",
    "ttl": 3600
})
```

### Business Events

Track business-relevant events:

```python
ctx.record_event("order.created", attributes={
    "order_id": "ord-789",
    "customer_id": "cust-456",
    "total_amount": 99.99,
    "currency": "USD",
    "items_count": 3
})
```

## Advanced Usage

### Custom Resource Attributes

Add service-specific metadata:

```python
OpenTelemetry.configure(
    service="payment-service",
    version="2.1.0",
    environment="production",
    otlp_endpoint="http://collector:4317",
    attributes={
        "service.namespace": "payments",
        "service.instance.id": os.environ.get("INSTANCE_ID"),
        "deployment.version": "v2.1.0-rc.1",
        "team": "payments-team",
        "region": "us-east-1"
    }
)
```

### Error Handling and Status

Spans automatically record error status when exceptions occur:

```python
async with ctx.scope("risky-operation"):
    try:
        await potentially_failing_operation()
        # Span status: OK
    except Exception as e:
        # Span status: ERROR, exception recorded
        ctx.log_error("Operation failed", exception=e)
        raise
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
ctx.record_attributes({
    "user.id": user_id,
    "user.role": user_role,
    "request.id": request_id
})

# Establish naming conventions:
# - Use dots for namespacing
# - Use snake_case for attribute names
# - Use consistent prefixes (user., request., etc.)
```

## Troubleshooting

### Common Issues

**1. No telemetry data appearing**
- Verify OTLP endpoint is reachable
- Check if OpenTelemetry.configure() was called before creating observability
- Ensure proper network connectivity to your observability backend

**2. High memory usage**
- Consider increasing export intervals
- Check if you're creating too many unique metric label combinations
- Review span attribute cardinality

**3. Missing trace correlation**
- Ensure observability is properly passed through context scopes
- Verify external trace ID format is correct
- Check that async context is properly propagated

## Further Reading

- [OpenTelemetry Official Documentation](https://opentelemetry.io/docs/)
- [Haiway Context Guide](../guides/functionalities.md)
- [Haiway State Management](../guides/state.md)
