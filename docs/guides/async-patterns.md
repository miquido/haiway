# Async Patterns

Haiway provides powerful utilities for structured concurrency and async programming. This guide covers common patterns and best practices.

## Timeout Management

### Basic Timeouts

```python
from haiway.helpers import timeout
import asyncio

async def slow_operation():
    await asyncio.sleep(5)
    return "completed"

async def main():
    try:
        # Timeout after 2 seconds
        result = await timeout(slow_operation(), seconds=2)
        print(f"Result: {result}")
    except asyncio.TimeoutError:
        print("Operation timed out")

asyncio.run(main())
```

### Context-based Timeouts

```python
from haiway import ctx
from haiway.helpers import timeout

async def network_request():
    # Simulate network request
    await asyncio.sleep(3)
    return {"status": "success"}

async def main():
    async with ctx.scope("app"):
        try:
            result = await timeout(network_request(), seconds=2)
            ctx.log_info(f"Request successful: {result}")
        except asyncio.TimeoutError:
            ctx.log_error("Network request timed out")

asyncio.run(main())
```

## Retry Logic

### Simple Retries

```python
from haiway.helpers import retry
import random

async def unreliable_operation():
    if random.random() < 0.7:  # 70% chance of failure
        raise ValueError("Operation failed")
    return "success"

async def main():
    try:
        result = await retry(
            unreliable_operation(),
            max_attempts=3,
            delay=1.0
        )
        print(f"Result: {result}")
    except ValueError as exc:
        print(f"All attempts failed: {exc}")

asyncio.run(main())
```

### Advanced Retry Patterns

```python
from haiway.helpers import retry
from haiway import ctx
import aiohttp

async def api_call(url: str):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            if response.status >= 500:
                raise aiohttp.ClientError(f"Server error: {response.status}")
            return await response.json()

async def robust_api_call(url: str):
    """API call with retry logic and logging"""
    async def attempt():
        ctx.log_info(f"Attempting API call to {url}")
        try:
            result = await api_call(url)
            ctx.log_info("API call successful")
            return result
        except Exception as exc:
            ctx.log_warning(f"API call failed: {exc}")
            raise

    return await retry(
        attempt(),
        max_attempts=3,
        delay=2.0,
        backoff_factor=2.0,  # Exponential backoff
        exceptions=(aiohttp.ClientError,)
    )

async def main():
    async with ctx.scope("api-client"):
        try:
            data = await robust_api_call("https://api.example.com/data")
            print(f"Data received: {data}")
        except Exception as exc:
            ctx.log_error(f"Failed to fetch data: {exc}")
```

## Concurrent Processing

### Process Items Concurrently

```python
from haiway.helpers import process_concurrently
from haiway import ctx
import asyncio

async def process_item(item: str) -> str:
    # Simulate processing
    await asyncio.sleep(1)
    return f"processed-{item}"

async def main():
    items = ["item1", "item2", "item3", "item4", "item5"]
    
    async with ctx.scope("batch-processing"):
        results = await process_concurrently(
            process_item,
            items,
            max_concurrency=3,  # Process 3 items at a time
            timeout_per_item=5.0
        )
        
        ctx.log_info(f"Processed {len(results)} items")
        for result in results:
            print(result)

asyncio.run(main())
```

### Error Handling in Concurrent Processing

```python
async def risky_operation(item: str) -> str:
    if item == "bad_item":
        raise ValueError(f"Cannot process {item}")
    await asyncio.sleep(0.5)
    return f"processed-{item}"

async def main():
    items = ["good1", "bad_item", "good2", "good3"]
    
    async with ctx.scope("concurrent-processing"):
        try:
            results = await process_concurrently(
                risky_operation,
                items,
                max_concurrency=2,
                stop_on_error=False  # Continue processing other items
            )
            
            # Filter out failed results
            successful = [r for r in results if not isinstance(r, Exception)]
            failed = [r for r in results if isinstance(r, Exception)]
            
            ctx.log_info(f"Successful: {len(successful)}, Failed: {len(failed)}")
            
        except Exception as exc:
            ctx.log_error(f"Batch processing failed: {exc}")
```

## Caching

### Simple Caching

```python
from haiway.helpers import cache
import asyncio

@cache(ttl=300)  # Cache for 5 minutes
async def expensive_computation(x: int, y: int) -> int:
    print(f"Computing {x} + {y}")
    await asyncio.sleep(2)  # Simulate expensive operation
    return x + y

async def main():
    # First call - computes the result
    result1 = await expensive_computation(1, 2)
    print(f"Result 1: {result1}")
    
    # Second call - returns cached result
    result2 = await expensive_computation(1, 2)
    print(f"Result 2: {result2}")
    
    # Different parameters - computes new result
    result3 = await expensive_computation(3, 4)
    print(f"Result 3: {result3}")

asyncio.run(main())
```

### Context-aware Caching

```python
from haiway.helpers import cache
from haiway import ctx, State

class CacheConfig(State):
    default_ttl: int = 300
    max_size: int = 1000

@cache(ttl=lambda: ctx.state(CacheConfig).default_ttl)
async def fetch_user_data(user_id: str) -> dict:
    ctx.log_info(f"Fetching data for user {user_id}")
    # Simulate database query
    await asyncio.sleep(1)
    return {"id": user_id, "name": f"User {user_id}"}

async def main():
    cache_config = CacheConfig(default_ttl=600, max_size=500)
    
    async with ctx.scope("app", cache_config):
        # First call - fetches from "database"
        user1 = await fetch_user_data("123")
        ctx.log_info(f"User 1: {user1}")
        
        # Second call - returns cached result
        user2 = await fetch_user_data("123")
        ctx.log_info(f"User 2: {user2}")
```

## Stream Processing

### Async Generators

```python
async def data_stream():
    """Generate a stream of data"""
    for i in range(10):
        await asyncio.sleep(0.1)
        yield f"data-{i}"

async def process_stream():
    """Process streaming data"""
    async for item in data_stream():
        print(f"Processing: {item}")
        # Process the item
        await asyncio.sleep(0.05)

async def main():
    async with ctx.scope("stream-processing"):
        ctx.log_info("Starting stream processing")
        await process_stream()
        ctx.log_info("Stream processing completed")
```

### Buffered Stream Processing

```python
from typing import AsyncIterator
import asyncio

async def buffered_stream(
    source: AsyncIterator[str], 
    buffer_size: int = 5
) -> AsyncIterator[list[str]]:
    """Buffer items from a stream"""
    buffer = []
    
    async for item in source:
        buffer.append(item)
        
        if len(buffer) >= buffer_size:
            yield buffer
            buffer = []
    
    # Yield remaining items
    if buffer:
        yield buffer

async def main():
    async with ctx.scope("buffered-processing"):
        async for batch in buffered_stream(data_stream(), buffer_size=3):
            ctx.log_info(f"Processing batch of {len(batch)} items")
            # Process batch
            await asyncio.sleep(0.2)
```

## Resource Pooling

### Connection Pool Pattern

```python
from contextlib import asynccontextmanager
from haiway import ctx, State
import asyncio
from typing import AsyncContextManager

class Connection:
    def __init__(self, connection_id: str):
        self.id = connection_id
        self.in_use = False
    
    async def query(self, sql: str) -> list[dict]:
        await asyncio.sleep(0.1)  # Simulate query
        return [{"result": f"data from {self.id}"}]

class ConnectionPool(State):
    connections: list[Connection]
    max_size: int = 10
    
    @classmethod
    async def acquire(cls) -> AsyncContextManager[Connection]:
        pool = ctx.state(cls)
        
        @asynccontextmanager
        async def connection_context():
            # Find available connection
            connection = None
            for conn in pool.connections:
                if not conn.in_use:
                    connection = conn
                    break
            
            if not connection:
                raise RuntimeError("No connections available")
            
            connection.in_use = True
            try:
                yield connection
            finally:
                connection.in_use = False
        
        return connection_context()

@asynccontextmanager
async def create_connection_pool():
    connections = [Connection(f"conn-{i}") for i in range(5)]
    pool = ConnectionPool(connections=connections, max_size=5)
    
    try:
        yield pool
    finally:
        # Cleanup connections
        for conn in connections:
            # Close connection
            pass

async def worker(worker_id: int):
    """Worker that uses the connection pool"""
    async with ConnectionPool.acquire() as conn:
        ctx.log_info(f"Worker {worker_id} using connection {conn.id}")
        result = await conn.query("SELECT * FROM users")
        await asyncio.sleep(0.5)  # Simulate work
        return result

async def main():
    async with ctx.scope("app", disposables=(create_connection_pool(),)):
        # Run multiple workers concurrently
        tasks = [worker(i) for i in range(10)]
        results = await asyncio.gather(*tasks)
        
        ctx.log_info(f"Completed {len(results)} tasks")
```

## Event-Driven Patterns

### Event Publisher/Subscriber

```python
from typing import Callable, Any
from haiway import ctx, State

class EventBus(State):
    subscribers: dict[str, list[Callable]] = {}
    
    @classmethod
    def subscribe(cls, event_type: str, handler: Callable):
        bus = ctx.state(cls)
        if event_type not in bus.subscribers:
            bus.subscribers[event_type] = []
        bus.subscribers[event_type].append(handler)
    
    @classmethod
    async def publish(cls, event_type: str, data: Any):
        bus = ctx.state(cls)
        if event_type in bus.subscribers:
            for handler in bus.subscribers[event_type]:
                await handler(data)

async def user_created_handler(user_data: dict):
    ctx.log_info(f"Sending welcome email to {user_data['email']}")
    # Send welcome email
    await asyncio.sleep(0.1)

async def user_analytics_handler(user_data: dict):
    ctx.log_info(f"Recording user analytics for {user_data['id']}")
    # Record analytics
    await asyncio.sleep(0.05)

async def create_user(name: str, email: str) -> dict:
    user_data = {"id": "123", "name": name, "email": email}
    
    # Publish user created event
    await EventBus.publish("user_created", user_data)
    
    return user_data

async def main():
    event_bus = EventBus()
    
    async with ctx.scope("app", event_bus):
        # Subscribe to events
        EventBus.subscribe("user_created", user_created_handler)
        EventBus.subscribe("user_created", user_analytics_handler)
        
        # Create user (triggers events)
        user = await create_user("Alice", "alice@example.com")
        ctx.log_info(f"User created: {user}")
```

## Circuit Breaker Pattern

```python
from enum import Enum
from datetime import datetime, timedelta
from haiway import ctx, State

class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"

class CircuitBreaker(State):
    failure_threshold: int = 5
    timeout: int = 60  # seconds
    failure_count: int = 0
    last_failure_time: datetime | None = None
    state: CircuitState = CircuitState.CLOSED
    
    @classmethod
    async def call(cls, operation: Callable) -> Any:
        breaker = ctx.state(cls)
        
        # Check if circuit should be reset
        if (breaker.state == CircuitState.OPEN and 
            breaker.last_failure_time and
            datetime.now() - breaker.last_failure_time > timedelta(seconds=breaker.timeout)):
            # Move to half-open state
            breaker = breaker.updated(state=CircuitState.HALF_OPEN)
            ctx.update_state(breaker)
        
        # If circuit is open, fail fast
        if breaker.state == CircuitState.OPEN:
            raise RuntimeError("Circuit breaker is open")
        
        try:
            result = await operation()
            
            # Success - reset failure count
            if breaker.failure_count > 0:
                ctx.update_state(breaker.updated(
                    failure_count=0,
                    state=CircuitState.CLOSED
                ))
            
            return result
            
        except Exception as exc:
            # Failure - increment count
            new_failure_count = breaker.failure_count + 1
            new_state = (CircuitState.OPEN 
                        if new_failure_count >= breaker.failure_threshold 
                        else breaker.state)
            
            ctx.update_state(breaker.updated(
                failure_count=new_failure_count,
                last_failure_time=datetime.now(),
                state=new_state
            ))
            
            ctx.log_error(f"Circuit breaker failure {new_failure_count}: {exc}")
            raise

async def unreliable_service():
    """Simulate an unreliable external service"""
    if random.random() < 0.8:  # 80% failure rate
        raise RuntimeError("Service unavailable")
    return "success"

async def main():
    circuit_breaker = CircuitBreaker()
    
    async with ctx.scope("app", circuit_breaker):
        for i in range(10):
            try:
                result = await CircuitBreaker.call(unreliable_service)
                ctx.log_info(f"Call {i}: {result}")
            except RuntimeError as exc:
                ctx.log_error(f"Call {i} failed: {exc}")
            
            await asyncio.sleep(0.5)
```

## Best Practices

1. **Use Timeouts**: Always set reasonable timeouts for external operations
2. **Implement Retries**: Add retry logic for transient failures
3. **Limit Concurrency**: Use semaphores or bounded queues to control resource usage
4. **Monitor Performance**: Log timing and success/failure metrics
5. **Handle Backpressure**: Implement flow control for high-throughput scenarios
6. **Graceful Degradation**: Design fallback mechanisms for service failures
7. **Test Failure Scenarios**: Use chaos engineering principles to test resilience

## Performance Optimization

### Efficient Batch Processing

```python
async def batch_process_efficiently(items: list[str], batch_size: int = 50):
    """Process items in efficient batches"""
    async with ctx.scope("batch-processing"):
        batches = [items[i:i + batch_size] for i in range(0, len(items), batch_size)]
        
        for i, batch in enumerate(batches):
            ctx.log_info(f"Processing batch {i + 1}/{len(batches)}")
            
            # Process batch concurrently
            results = await process_concurrently(
                process_item,
                batch,
                max_concurrency=min(10, len(batch))
            )
            
            ctx.log_info(f"Batch {i + 1} completed: {len(results)} items")
```

### Memory-Efficient Streaming

```python
async def process_large_dataset(data_source: AsyncIterator[dict]):
    """Process large dataset without loading all into memory"""
    processed_count = 0
    batch = []
    batch_size = 100
    
    async for item in data_source:
        batch.append(item)
        
        if len(batch) >= batch_size:
            # Process batch
            await process_batch(batch)
            processed_count += len(batch)
            batch = []
            
            if processed_count % 1000 == 0:
                ctx.log_info(f"Processed {processed_count} items")
    
    # Process remaining items
    if batch:
        await process_batch(batch)
        processed_count += len(batch)
    
    ctx.log_info(f"Total processed: {processed_count} items")
```

## Next Steps

- Learn about [Testing](testing.md) strategies for async code
- Explore [State Management](state-management.md) for complex data structures
- See [Examples](../examples/index.md) for real-world implementations