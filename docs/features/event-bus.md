# Event Bus

The event bus in Haiway provides a type-safe, scoped publish-subscribe system for asynchronous event handling. It enables decoupled communication between different parts of your application while maintaining type safety and memory efficiency.

## Overview

The event bus allows you to:

- Send typed events to all active subscribers
- Subscribe to specific event types using async iteration
- Automatically manage event lifecycle and memory
- Ensure events are scoped to their context

## Basic Usage

### Defining Events

Events are regular State objects that carry your event data:

```python
from collections.abc import Sequence
from haiway import State

class UserLoggedIn(State):
    user_id: str
    timestamp: float
    ip_address: str

class OrderCreated(State):
    order_id: str
    customer_id: str
    total_amount: float
    items: Sequence[str]
```

### Sending Events

Use `ctx.send()` to publish events to all active subscribers:

```python
from haiway import ctx
import time

async def handle_login(user_id: str, ip: str):
    # Perform login logic
    await authenticate_user(user_id)

    # Send login event
    ctx.send(
        UserLoggedIn(
            user_id=user_id,
            timestamp=time.time(),
            ip_address=ip,
        )
    )
```

### Subscribing to Events

Use `ctx.subscribe()` to receive events of a specific type:

```python
async def monitor_logins():
    async for event in ctx.subscribe(UserLoggedIn):
        print(f"User {event.user_id} logged in from {event.ip_address}")
        await log_to_database(event)
```

## Common Patterns

### Background Event Processing

Start event processors as background tasks:

```python
async def main():
    async with ctx.scope("app"):
        # Start event processors in background
        login_monitor = ctx.spawn(monitor_logins)
        order_processor = ctx.spawn(process_orders)

        # Run main application logic
        await run_application()

        # Cancel processors when done
        login_monitor.cancel()
        order_processor.cancel()
```

### Multiple Subscribers

Multiple subscribers can listen to the same event type independently:

```python
async def alert_security_team():
    async for event in ctx.subscribe(UserLoggedIn):
        if is_suspicious_ip(event.ip_address):
            await send_security_alert(event)

async def update_user_stats():
    async for event in ctx.subscribe(UserLoggedIn):
        await increment_login_count(event.user_id)

async def main():
    async with ctx.scope("app"):
        # Both subscribers receive all events independently
        ctx.spawn(alert_security_team)
        ctx.spawn(update_user_stats)

        await run_application()
```

## Advanced Usage

### Request-Response Pattern

Implement request-response using events:

```python
class DataRequest(State):
    request_id: str
    query: str

class DataResponse(State):
    request_id: str
    result: Any

async def data_service():
    async for request in ctx.subscribe(DataRequest):
        result = await execute_query(request.query)
        ctx.send(
            DataResponse(
                request_id=request.request_id,
                result=result,
            )
        )

async def make_request(query: str) -> Any:
    request_id = generate_id()

    # Subscribe before sending to avoid race condition
    response_sub = ctx.subscribe(DataResponse)

    # Send request
    ctx.send(DataRequest(request_id=request_id, query=query))

    # Wait for matching response
    async for response in response_sub:
        if response.request_id == request_id:
            return response.result
```

## Best Practices

### Event Design

1. **Keep events focused**: Each event type should represent a single logical occurrence
2. **Use immutable data**: Events use Haiway's State objects which are immutable by design
3. **Include context**: Add relevant context like timestamps, user IDs, and correlation IDs

### Memory Management

The event bus is designed for memory efficiency:

- Events without subscribers are never stored and dropped immediately
- Events are garbage collected as soon as all subscribers consume them
- Abandoned subscriptions automatically clean up

### Error Handling

Always handle exceptions in event processors to prevent crashes:

```python
async def safe_event_processor():
    async for event in ctx.subscribe(CriticalEvent):
        try:
            await process_critical_event(event)
        except Exception as e:
            ctx.log_error(f"Failed to process event {event}", exception=e)
            # Event processing continues
```

### Scope Isolation

Events are scoped to their root context and don't leak:

```python
async def isolated_subsystem():
    async with ctx.scope("subsystem_a"):
        ctx.send(InternalEvent(data="A"))  # Only visible in subsystem_a

    async with ctx.scope("subsystem_b"):
        ctx.send(InternalEvent(data="B"))  # Only visible in subsystem_b
```

## Integration with Other Features

### With State Management

Combine events with state for reactive systems:

```python
class SystemStatus(State):
    healthy: bool = True
    last_check: float

async def health_monitor():
    async for event in ctx.subscribe(HealthCheckFailed):
        # Update system state
        current = ctx.state(SystemStatus)
        updated = current.updated(healthy=False, last_check=time.time())

        # Trigger recovery via another event
        ctx.send(SystemUnhealthy(reason=event.reason))
```

### With Task Management

Use Haiway's task management with events:

```python
async def event_driven_tasks():
    async for event in ctx.subscribe(TaskRequest):
        # Spawn a new task for each request
        ctx.spawn(handle_task, event.task_id, event.payload)
```

### With Observability

Events integrate with Haiway's observability:

```python
async def monitored_processor():
    async for event in ctx.subscribe(ImportantEvent):
        ctx.record(
            event="event_received",
            attributes={"event_type": type(event).__name__}
        )

        start = time.time()
        await process_event(event)

        ctx.record(
            metric="event_processing_time",
            value=time.time() - start,
            unit="seconds",
            kind="histogram",
        )
```

## Limitations and Considerations

1. **Type-based routing**: Events are routed by exact type match - inheritance is not considered
2. **No persistence**: Events are in-memory only and don't survive process restarts
3. **No ordering guarantees**: While events are generally delivered in order, this isn't guaranteed across multiple publishers
4. **Same event loop**: All operations must occur within the same asyncio event loop

For distributed event systems or persistent event stores, consider integrating with external message brokers while using Haiway's event bus for local, in-process events.
