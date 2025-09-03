# Concurrent Processing

Haiway provides powerful tools for concurrent and parallel processing within its functional
programming paradigm. Built on Python's `asyncio`, the framework offers structured concurrency
through the context system (`ctx.spawn`) and advanced patterns through helper utilities. This guide
explains how to effectively use these tools to build high-performance concurrent applications.

## Core Concepts

### Structured Concurrency

Haiway follows the structured concurrency paradigm where all spawned tasks are tied to their parent
scope:

- Tasks spawned within a scope are automatically cancelled when the scope exits
- Exceptions in child tasks can propagate to parent scopes
- Resources are properly cleaned up through the scope lifecycle
- Task isolation ensures independent execution contexts

### Context Propagation

When spawning tasks, Haiway preserves the execution context:

- State objects remain accessible via `ctx.state()`
- Logging maintains proper scope identification
- Observability tracking continues across task boundaries
- Variables are isolated per task (not inherited from parent)

## Basic Task Spawning

The fundamental building block for concurrency in Haiway is `ctx.spawn()`, which creates tasks
within the current scope's task group:

```python
from haiway import ctx
import asyncio

async def process_item(item_id: str) -> dict:
    # Access context state within spawned task
    config = ctx.state(ProcessingConfig)
    ctx.log_info(f"Processing item {item_id}")
    
    # Simulate async work
    await asyncio.sleep(1)
    return {"id": item_id, "status": "completed"}

async def main():
    async with ctx.scope("processor"):
        # Spawn multiple tasks
        task1 = ctx.spawn(process_item, "item-1")
        task2 = ctx.spawn(process_item, "item-2")
        task3 = ctx.spawn(process_item, "item-3")
        
        # Tasks run concurrently
        results = await asyncio.gather(task1, task2, task3)
        ctx.log_info(f"Processed {len(results)} items")
```

### Fire-and-Forget Tasks

Tasks spawned with `ctx.spawn()` don't need to be awaited explicitly:

```python
async def background_task():
    while True:
        ctx.check_cancellation()  # Cooperative cancellation
        await process_queue_item()
        await asyncio.sleep(1)

async def main():
    async with ctx.scope("app"):
        # Spawn background task
        ctx.spawn(background_task)
        
        # Do other work
        await handle_requests()
        
        # Background task automatically cancelled when scope exits
```

### Task Cancellation

Tasks support cooperative cancellation through the context API:

```python
async def long_running_task():
    for i in range(100):
        ctx.check_cancellation()  # Raises CancelledError if cancelled
        await process_batch(i)

async def main():
    async with ctx.scope("batch"):
        task = ctx.spawn(long_running_task)
        
        # Cancel after timeout
        await asyncio.sleep(10)
        ctx.cancel()  # Cancels current task
        # or
        task.cancel()  # Cancel specific task
```

## Streaming with Context

The `ctx.stream()` method enables async generators to run in proper context:

```python
async def generate_items() -> AsyncGenerator[str, None]:
    for i in range(10):
        yield f"item-{i}"
        await asyncio.sleep(0.1)

async def main():
    async with ctx.scope("streamer"):
        # Stream runs in its own context
        async for item in ctx.stream(generate_items):
            ctx.log_info(f"Received: {item}")
```

## Concurrent Processing Helpers

The `haiway.helpers.concurrent` module provides four specialized functions for common concurrent
processing patterns. All functions integrate with Haiway's context system and provide controlled
parallelism.

### process_concurrently

Process elements from an iterable without collecting results. Ideal for side-effect operations like
notifications, logging, or data transformations.

```python
from haiway.helpers.concurrent import process_concurrently

async def send_notification(user_id: str) -> None:
    client = ctx.state(NotificationClient)
    await client.send(user_id, "Your order is ready!")

async def notify_users():
    user_ids = ["user-1", "user-2", "user-3", "user-4", "user-5"]
    
    # Process with concurrency limit
    await process_concurrently(
        user_ids,
        send_notification,
        concurrent_tasks=3,  # Max 3 concurrent notifications
        ignore_exceptions=True  # Continue on individual failures
    )
```

**Parameters:**

- `source: AsyncIterable[Element] | Iterable[Element]` - Elements to process
- `handler: Callable[[Element], Coroutine[Any, Any, None]]` - Processing function
- `concurrent_tasks: int = 2` - Maximum concurrent tasks
- `ignore_exceptions: bool = False` - If True, log exceptions but continue processing

**Key Features:**

- Processes elements as they become available
- Maintains the specified concurrency limit
- Automatic task cancellation on errors or cancellation
- Optional exception tolerance for resilient processing

### execute_concurrently

Execute handler for each element and collect results in order. Perfect when you need to process
collections and gather the outcomes.

```python
from haiway.helpers.concurrent import execute_concurrently

async def fetch_user_data(user_id: str) -> dict:
    client = ctx.state(APIClient)
    return await client.get(f"/users/{user_id}")

async def fetch_all_users():
    user_ids = ["user-1", "user-2", "user-3"]
    
    # Execute concurrently and collect results
    results = await execute_concurrently(
        fetch_user_data,
        user_ids,
        concurrent_tasks=5
    )
    
    # Results maintain order: results[0] is for user_ids[0]
    for user_id, data in zip(user_ids, results):
        ctx.log_info(f"User {user_id}: {data}")
```

**Parameters:**

- `handler: Callable[[Element], Coroutine[Any, Any, Result]]` - Processing function that returns results
- `elements: AsyncIterable[Element] | Iterable[Element]` - Elements to process
- `concurrent_tasks: int = 2` - Maximum concurrent tasks
- `return_exceptions: bool = False` - Include exceptions in results instead of raising

**Key Features:**

- Results returned in same order as input elements
- Configurable exception handling via `return_exceptions`
- Works with both sync and async iterables
- Preserves result ordering for predictable processing

### concurrently

Execute pre-created coroutine objects with controlled parallelism. More flexible than
`execute_concurrently` when coroutines need different parameters or come from different sources.

```python
from haiway.helpers.concurrent import concurrently

async def fetch_with_timeout(url: str, timeout: float) -> dict:
    return await asyncio.wait_for(http_client.get(url), timeout)

async def fetch_different_endpoints():
    # Create coroutines with different parameters
    coroutines = [
        fetch_with_timeout("https://api.example.com/fast", 3.0),
        fetch_with_timeout("https://api.example.com/slow", 10.0), 
        fetch_with_timeout("https://api.example.com/medium", 5.0),
    ]
    
    results = await concurrently(
        coroutines,
        concurrent_tasks=2
    )
    
    # Results maintain order: results[0] from first coroutine, etc.
    return results
```

**Parameters:**

- `coroutines: AsyncIterable[Coroutine] | Iterable[Coroutine]` - Coroutine objects to execute
- `concurrent_tasks: int = 2` - Maximum concurrent tasks
- `return_exceptions: bool = False` - Include exceptions in results instead of raising

**Key Features:**

- Works directly with coroutine objects rather than applying a handler function
- Allows for different parameters per coroutine
- Maintains result ordering matching input coroutine order
- Flexible source of coroutines from different functions or generators

### stream_concurrently

Merge two async iterators into a single stream, yielding elements as they become available from
either source.

```python
from haiway.helpers.concurrent import stream_concurrently

async def sensor_readings() -> AsyncIterator[float]:
    while True:
        await asyncio.sleep(0.1)
        yield random.uniform(20.0, 25.0)

async def status_updates() -> AsyncIterator[str]:  
    while True:
        await asyncio.sleep(0.5)
        yield "System OK"

async def process_events():
    # Merge streams - yields events as they arrive from either source
    async for item in stream_concurrently(
        sensor_readings(), 
        status_updates()
    ):
        if isinstance(item, float):
            ctx.log_info(f"Temperature: {item}°C")
        else:
            ctx.log_info(f"Status: {item}")
```

**Parameters:**

- `source_a: AsyncIterable[ElementA]` - First async iterable to consume
- `source_b: AsyncIterable[ElementB]` - Second async iterable to consume
- `exhaustive: bool = False` - If True, continue until both sources complete; if False (default),
  stop when either exhausts

**Key Features:**

- Elements yielded based on availability, not source order
- Maintains exactly one pending task per iterator for efficiency
- Default behavior stops when either source is exhausted
- Exhaustive mode continues until both sources complete

### AsyncStream

Push-based async stream with back-pressure, suitable for coordinating producers and a single
consumer.

```python
from haiway import AsyncStream, ctx

async def example_stream_usage() -> list[int]:
    stream: AsyncStream[int] = AsyncStream()
    results: list[int] = []

    async def producer() -> None:
        for i in range(5):
            await stream.send(i)  # waits until consumer is ready
        stream.finish()  # signal completion (or use stream.cancel())

    # Start producer in the current context
    ctx.spawn(producer)

    # Single consumer iterates values as they arrive
    async for value in stream:
        results.append(value)

    return results  # [0, 1, 2, 3, 4]
```

**Behavior:**

- Single-consumer: one active iteration is allowed; reuse raises an assertion.
- Back-pressure: `send()` suspends until the consumer accepts the element.
- Completion: `finish()` ends the stream; `finish(exc)` raises `exc` on the consumer.
- Cancellation: `cancel()` is equivalent to `finish(CancelledError())`.
- Post-finish sends: `send()` to a finished/failed stream is ignored.

**When to use:**

- Coordinating multiple producers that should not outpace the consumer.
- Bridging event callbacks into an async-iterable interface.
- As a building block for higher-level streaming utilities like `stream_concurrently()`.

## Error Handling Patterns

### Exception Tolerance in Processing

Handle exceptions gracefully in batch operations:

```python
async def resilient_batch_processing():
    urls = ["http://api1.com", "http://invalid", "http://api2.com"]
    
    # Collect exceptions as results
    results = await execute_concurrently(
        fetch_data,
        urls,
        concurrent_tasks=10,
        return_exceptions=True
    )
    
    for url, result in zip(urls, results):
        if isinstance(result, BaseException):
            ctx.log_error(f"Failed to fetch {url}", exception=result)
        else:
            ctx.log_info(f"Success: {url}")

# Similar pattern for concurrently()
async def handle_mixed_coroutines():
    results = await concurrently(
        [risky_operation(), safe_operation(), another_risky_operation()],
        concurrent_tasks=3,
        return_exceptions=True
    )
    
    successes = [r for r in results if not isinstance(r, BaseException)]
    failures = [r for r in results if isinstance(r, BaseException)]
    ctx.log_info(f"Processed: {len(successes)} successes, {len(failures)} failures")
```

## Advanced Patterns

### Chunked Processing

Process large datasets in chunks:

```python
async def process_large_dataset(items: list[str]):
    chunk_size = 100
    
    for i in range(0, len(items), chunk_size):
        chunk = items[i:i + chunk_size]
        
        # Process chunk concurrently
        await process_concurrently(
            chunk,
            process_item,
            concurrent_tasks=10
        )
        
        # Optional: Add delay between chunks
        await asyncio.sleep(1)
```

### Dynamic Concurrency

Adjust concurrency based on system load:

```python
async def adaptive_processing():
    items = await get_items()
    
    # Determine concurrency based on system resources
    cpu_count = os.cpu_count() or 1
    memory_available = get_available_memory_gb()
    
    # Scale concurrency with available resources
    concurrent_tasks = min(
        cpu_count * 2,  # 2x CPU cores
        int(memory_available / 0.5),  # 500MB per task
        50  # Hard limit
    )
    
    ctx.log_info(f"Processing with {concurrent_tasks} concurrent tasks")
    
    await process_concurrently(
        items,
        heavy_processing,
        concurrent_tasks=concurrent_tasks
    )
```

## Best Practices

### 1. Choose the Right Tool

- **`ctx.spawn()`**: For fire-and-forget tasks or when you need task handles
- **`process_concurrently()`**: For side-effect operations without collecting results
- **`execute_concurrently()`**: For applying a handler function to elements and collecting results
- **`concurrently()`**: For executing pre-created coroutine objects with different parameters
- **`stream_concurrently()`**: For merging two async iterators into a single stream

### 2. Resource Management

Always consider resource limits:

```python
async def resource_aware_processing():
    # Limit based on external resources
    db_pool_size = ctx.state(DatabaseConfig).pool_size
    concurrent_tasks = min(db_pool_size // 2, 20)
    
    await process_concurrently(
        items,
        database_operation,
        concurrent_tasks=concurrent_tasks
    )
```

### 3. Error Boundaries

Isolate failures with proper error handling:

```python
async def resilient_processing():
    async def safe_process(item: str) -> Result | None:
        try:
            return await risky_operation(item)
        except Exception as e:
            ctx.log_error(f"Failed processing {item}", exception=e)
            return None
    
    results = await execute_concurrently(
        safe_process,
        items,
        concurrent_tasks=10
    )
    
    # Filter out failures
    successful = [r for r in results if r is not None]
```

### 4. Monitoring and Observability

Track concurrent operations:

```python
async def monitored_processing():
    start_time = time.time()
    
    ctx.record(
        event="batch_processing_started",
        attributes={"item_count": len(items)}
    )
    
    try:
        results = await execute_concurrently(
            process_item,
            items,
            concurrent_tasks=20
        )
        
        duration = time.time() - start_time
        ctx.record(
            metric="batch_processing_duration",
            value=duration,
            kind=ObservabilityMetricKind.HISTOGRAM,
            attributes={"status": "success"}
        )
        
    except Exception as e:
        ctx.record(
            event="batch_processing_failed",
            attributes={"error": str(e)}
        )
        raise e
```

### 5. Context Isolation

Remember that spawned tasks have isolated variable contexts:

```python
async def context_isolation_example():
    # Set variable in parent
    ctx.variable(RequestID("parent-123"))
    
    async def child_task():
        # Variable is NOT inherited
        request_id = ctx.variable(RequestID)  # None
        
        # Set new variable in child
        ctx.variable(RequestID("child-456"))
    
    await ctx.spawn(child_task)
    
    # Parent variable unchanged
    assert ctx.variable(RequestID).value == "parent-123"
```

## Performance Considerations

### Concurrency vs Parallelism

Haiway's concurrency is based on `asyncio`, which provides:

- **Concurrency**: Multiple tasks make progress by interleaving execution
- **Not true parallelism**: Single thread, so CPU-bound tasks don't run in parallel
- **Ideal for I/O-bound operations**: Network requests, database queries, file operations

For CPU-bound parallel processing, consider:

```python
import asyncio
from concurrent.futures import ProcessPoolExecutor

async def parallel_cpu_processing():
    executor = ProcessPoolExecutor(max_workers=4)
    loop = asyncio.get_event_loop()
    
    # Run CPU-bound task in process pool
    results = await loop.run_in_executor(
        executor,
        cpu_intensive_function,
        large_dataset
    )
```

### Memory Considerations

Be mindful of memory usage with large concurrent operations:

```python
async def memory_efficient_processing():
    # Process in batches to control memory usage
    batch_size = 1000
    concurrent_tasks = 20
    
    async with ctx.scope("batch_processor"):
        for batch in iterate_batches(large_dataset, batch_size):
            await process_concurrently(
                batch,
                process_item,
                concurrent_tasks=concurrent_tasks
            )
            
            # Allow garbage collection between batches
            await asyncio.sleep(0)
```

## Summary

Haiway's concurrent processing tools provide:

- **Structured concurrency** through scope-based task management
- **Context preservation** across task boundaries
- **High-level patterns** for common concurrent operations
- **Resource control** through concurrency limits
- **Error resilience** with proper exception handling

By combining `ctx.spawn()` for basic task management with the specialized helpers in
`haiway.helpers.concurrent`, you can build efficient, maintainable concurrent applications that
fully leverage Python's async capabilities while maintaining the safety and structure of Haiway's
functional programming model.
