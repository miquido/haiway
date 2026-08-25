# Event Bus

The event bus in Haiway provides a type-safe, scoped publish-subscribe system for asynchronous event
handling. It enables decoupled communication between different parts of your application while
maintaining type safety and memory efficiency.

## Overview

The event bus allows you to:

- Send typed events to all active subscribers
- Subscribe to specific event types using async iteration
- Automatically manage event lifecycle and memory
- Ensure events are scoped to their context

Event routing is based on the exact `State` subclass. A subscription only receives events sent after
the subscription has been created.

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

Start event processors as scoped tasks:

```python
async def main():
    async with ctx.scope("app"):
        # Start event processors in background
        login_monitor = ctx.spawn(monitor_logins)
        order_processor = ctx.spawn(process_orders)

        # Run main application logic
        await run_application()

        # Cancel processors when done if they are open-ended loops
        login_monitor.cancel()
        order_processor.cancel()
```

`ctx.spawn()` ties tasks to the current scope's task group. Scope exit waits for unfinished tasks;
it does not automatically cancel healthy long-running processors unless the scope itself is
cancelled, fails, or you cancel the task explicitly.

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

Each subscriber receives the full event stream for the subscribed type.

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
1. **Use immutable data**: Events use Haiway's State objects which are immutable by design
1. **Include context**: Add relevant context like timestamps, user IDs, and correlation IDs

### Memory Management

- Events without subscribers are never stored and are dropped immediately.
- Events are garbage collected as soon as all subscribers consume them.
- Subscriptions are lightweight but currently keep an internal future alive; if you abandon a
  subscription without iterating it, the head entry stays in memory. Call `break`/`return` after the
  `async for` loop or drop the subscription only after finishing iteration.

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

### Delivery Scope

Delivery goes upwards through the scope tree. An event reaches the subscribers of the sending scope
and of every scope enclosing it, up to the nearest isolated context. Subscribers of sibling scopes -
and of scopes nested below the sender - do not receive it:

```python
async def per_request_isolation():
    async with ctx.scope("server"):
        # a subscriber here observes events of the whole subtree below it
        subscription = ctx.subscribe(OrderCreated)

        async with ctx.scope("request_a"):
            # reaches "request_a" and "server", not "request_b"
            ctx.send(OrderCreated(order_id="a", amount=1.0))

        async with ctx.scope("request_b"):
            # a subscriber of "request_b" never sees the event above
            ...
```

This is what keeps one request from observing another request's events while a service-wide
subscriber still sees everything happening beneath it.

When a producer and a consumer live in sibling scopes - a pipeline stage subscribing next to the
stage feeding it - send the event to every subscriber explicitly:

```python
ctx.send(OrderCreated(order_id="a", amount=1.0), broadcast=True)
```

`broadcast=True` ignores the scope tree, so unrelated scopes sharing the bus - other requests among
them - receive the payload as well. Reach for it deliberately, and prefer subscribing in a common
parent scope when the payload carries anything sensitive.

### Scope Isolation

The bus itself belongs to the nearest isolated context (root or `isolated=True`). Nested
non-isolated scopes share it:

```python
async def isolated_subsystem():
    async with ctx.scope("subsystem_a", isolated=True):
        ctx.send(InternalEvent(data="A"))  # Only visible in subsystem_a

    async with ctx.scope("subsystem_b", isolated=True):
        ctx.send(InternalEvent(data="B"))  # Only visible in subsystem_b
```

This means:

- A root scope always has an event bus.
- A nested non-isolated scope reuses the parent's bus, while delivery still follows the scope tree
  as described above.
- An `isolated=True` scope gets its own independent bus, so not even `broadcast=True` crosses it.
- Calling `ctx.send()` or `ctx.subscribe()` without an installed bus raises `ContextMissing`.

### Subscription Lifetime

A subscription ends when the scope owning its event bus exits. The events which already arrived are
delivered first, so the iteration finishes only once the queued events are drained and exiting the
scope never discards a payload the subscriber had not picked up yet.

Ownership is what matters here, not nesting. A nested non-isolated scope shares the bus of an
ancestor, so a subscription created within it stays open past that scope's exit:

```python
async with ctx.scope("server"):          # owns the bus
    async with ctx.scope("request"):     # shares it
        subscription = ctx.subscribe(OrderCreated)
        # this subscription ends when "server" exits, not when "request" does
```

Every scope also joins the tasks spawned within it before leaving, which makes the combination below
hang - `request` waits for the consumer, and the consumer waits for a bus which only `server` can
close:

```python
async with ctx.scope("server"):
    async with ctx.scope("request"):
        ctx.spawn(consumer)   # async for ... in ctx.subscribe(OrderCreated)
    # never returns
```

Consume a subscription unboundedly only in the scope owning the bus:

```python
async with ctx.scope("server"):
    ctx.spawn(consumer)       # ends when "server" exits
```

Within a nested scope, take a bounded number of events instead, so the iteration ends on its own:

```python
async with ctx.scope("server"):
    async with ctx.scope("request"):
        subscription = ctx.subscribe(OrderCreated)
        first = await asyncio.wait_for(anext(subscription), timeout=5.0)
```

A subscription created after its bus already closed is not an error - it finishes immediately
instead of waiting for an event which can never arrive.

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
        updated = current.updating(healthy=False, last_check=time.time())

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
        ctx.record_info(
            event="event_received",
            attributes={"event_type": type(event).__name__}
        )

        start = time.time()
        await process_event(event)

        ctx.record_info(
            metric="event_processing_time",
            value=time.time() - start,
            unit="seconds",
            kind="histogram",
        )
```

## Limitations and Considerations

1. **Type-based routing**: Events are routed by exact type match - inheritance is not considered
1. **No persistence**: Events are in-memory only and don't survive process restarts
1. **Ordering**: Events are delivered FIFO per event type within a context and event loop
1. **Same event loop**: All operations must occur within the same asyncio event loop

For distributed event systems or persistent event stores, consider integrating with external message
brokers while using Haiway's event bus for local, in-process events.
