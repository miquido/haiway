# Helpers API

The helpers module provides utilities for async programming, caching, retries, timeouts, and other common patterns used in concurrent applications.

## Async Utilities

Helper functions for managing asynchronous operations and concurrent execution patterns.

### Common Async Patterns

```python
import asyncio
from haiway import helpers

# Example async utility usage
async def process_items(items: list[str]) -> list[str]:
    """Process items concurrently with proper error handling"""
    
    async def process_single(item: str) -> str:
        # Simulate async processing
        await asyncio.sleep(0.1)
        return f"processed_{item}"
    
    # Process all items concurrently
    tasks = [process_single(item) for item in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    
    # Filter out exceptions if needed
    return [r for r in results if isinstance(r, str)]
```

### Task Management

```python
async def managed_background_task():
    """Example of structured task management"""
    
    async def background_worker():
        while True:
            print("Background work...")
            await asyncio.sleep(1)
    
    # Start task
    task = asyncio.create_task(background_worker())
    
    try:
        # Do other work
        await asyncio.sleep(5)
    finally:
        # Ensure cleanup
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
```

## Caching

Caching utilities for improving performance by storing computed results.

### Basic Caching Pattern

```python
from typing import Dict, Any
import asyncio

# Simple in-memory cache example
class SimpleCache:
    def __init__(self):
        self._cache: Dict[str, Any] = {}
    
    async def get(self, key: str) -> Any | None:
        return self._cache.get(key)
    
    async def set(self, key: str, value: Any, ttl: int = 300) -> None:
        self._cache[key] = value
        # In real implementation, handle TTL
    
    async def delete(self, key: str) -> None:
        self._cache.pop(key, None)

# Usage with Haiway context
from haiway import ctx, State

class CacheService(State):
    cache: SimpleCache
    
    @classmethod
    async def get_cached(cls, key: str) -> Any | None:
        service = ctx.state(cls)
        return await service.cache.get(key)
    
    @classmethod
    async def set_cached(cls, key: str, value: Any, ttl: int = 300) -> None:
        service = ctx.state(cls)
        await service.cache.set(key, value, ttl)
```

## Retries

Retry mechanisms for handling transient failures in distributed systems.

### Basic Retry Pattern

```python
import asyncio
from typing import TypeVar, Callable, Any

T = TypeVar('T')

async def retry_with_backoff(
    func: Callable[[], T],
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    backoff_factor: float = 2.0
) -> T:
    """
    Retry a function with exponential backoff.
    
    Args:
        func: Function to retry
        max_attempts: Maximum number of attempts
        base_delay: Initial delay between retries
        max_delay: Maximum delay between retries
        backoff_factor: Multiplier for delay after each failure
    """
    last_exception = None
    
    for attempt in range(max_attempts):
        try:
            return await func()
        except Exception as e:
            last_exception = e
            
            if attempt == max_attempts - 1:
                break
            
            # Calculate delay with exponential backoff
            delay = min(base_delay * (backoff_factor ** attempt), max_delay)
            await asyncio.sleep(delay)
    
    # Re-raise the last exception
    raise last_exception

# Usage example
async def unreliable_operation() -> str:
    """Simulates an operation that might fail"""
    import random
    if random.random() < 0.7:  # 70% chance of failure
        raise ConnectionError("Network error")
    return "Success!"

async def example_retry():
    try:
        result = await retry_with_backoff(unreliable_operation, max_attempts=3)
        print(f"Operation succeeded: {result}")
    except Exception as e:
        print(f"Operation failed after retries: {e}")
```

## Timeouts

Timeout utilities for preventing operations from running indefinitely.

### Basic Timeout Pattern

```python
import asyncio
from typing import TypeVar, Awaitable

T = TypeVar('T')

async def with_timeout(
    coro: Awaitable[T], 
    timeout_seconds: float
) -> T:
    """
    Run a coroutine with a timeout.
    
    Args:
        coro: Coroutine to run
        timeout_seconds: Maximum time to wait
        
    Raises:
        asyncio.TimeoutError: If operation exceeds timeout
    """
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        raise asyncio.TimeoutError(f"Operation timed out after {timeout_seconds} seconds")

# Usage example
async def slow_operation() -> str:
    await asyncio.sleep(5)  # Simulates slow operation
    return "Completed"

async def example_timeout():
    try:
        result = await with_timeout(slow_operation(), timeout_seconds=3.0)
        print(f"Operation completed: {result}")
    except asyncio.TimeoutError as e:
        print(f"Operation timed out: {e}")
```

### Context-based Timeout

```python
from haiway import ctx, State

class TimeoutConfig(State):
    default_timeout: float = 30.0
    database_timeout: float = 10.0
    api_timeout: float = 5.0

async def database_operation() -> str:
    """Example database operation"""
    config = ctx.state(TimeoutConfig)
    
    async def db_query():
        await asyncio.sleep(2)  # Simulate database query
        return "Database result"
    
    return await with_timeout(db_query(), config.database_timeout)
```

## Concurrent Operations

Utilities for managing concurrent operations safely and efficiently.

### Concurrent Processing

```python
import asyncio
from typing import List, TypeVar, Callable, Awaitable
from collections.abc import Sequence

T = TypeVar('T')
R = TypeVar('R')

async def process_concurrently(
    items: Sequence[T],
    processor: Callable[[T], Awaitable[R]],
    max_concurrency: int = 10
) -> List[R]:
    """
    Process items concurrently with limited concurrency.
    
    Args:
        items: Items to process
        processor: Async function to process each item
        max_concurrency: Maximum number of concurrent operations
    """
    semaphore = asyncio.Semaphore(max_concurrency)
    
    async def process_with_semaphore(item: T) -> R:
        async with semaphore:
            return await processor(item)
    
    tasks = [process_with_semaphore(item) for item in items]
    return await asyncio.gather(*tasks)

# Usage example
async def process_user(user_id: str) -> str:
    """Process a single user"""
    await asyncio.sleep(0.5)  # Simulate processing
    return f"processed_user_{user_id}"

async def example_concurrent():
    user_ids = [f"user_{i}" for i in range(20)]
    results = await process_concurrently(
        user_ids,
        process_user,
        max_concurrency=5
    )
    print(f"Processed {len(results)} users")
```

### Rate Limiting

```python
import asyncio
import time
from typing import List

class RateLimiter:
    """Simple rate limiter using token bucket algorithm"""
    
    def __init__(self, max_rate: float, time_window: float = 1.0):
        self.max_rate = max_rate
        self.time_window = time_window
        self.tokens = max_rate
        self.last_refill = time.time()
        self._lock = asyncio.Lock()
    
    async def acquire(self, tokens: int = 1) -> None:
        """Acquire tokens from the bucket"""
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_refill
            
            # Refill tokens based on elapsed time
            self.tokens = min(
                self.max_rate,
                self.tokens + elapsed * (self.max_rate / self.time_window)
            )
            self.last_refill = now
            
            # Wait if not enough tokens
            if self.tokens < tokens:
                wait_time = (tokens - self.tokens) * (self.time_window / self.max_rate)
                await asyncio.sleep(wait_time)
                self.tokens = 0
            else:
                self.tokens -= tokens

# Usage with Haiway
class RateLimitedService(State):
    rate_limiter: RateLimiter
    
    @classmethod
    async def make_api_call(cls, endpoint: str) -> str:
        service = ctx.state(cls)
        await service.rate_limiter.acquire()
        
        # Make actual API call
        await asyncio.sleep(0.1)  # Simulate API call
        return f"Response from {endpoint}"
```

## Observability

Utilities for monitoring, logging, and tracing application behavior.

### Basic Logging Pattern

```python
import logging
from haiway import ctx, State

class LoggingConfig(State):
    level: str = "INFO"
    format: str = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

def setup_logging():
    """Set up logging configuration"""
    config = ctx.state(LoggingConfig)
    
    logging.basicConfig(
        level=getattr(logging, config.level),
        format=config.format
    )
    
    return logging.getLogger(__name__)

# Usage in context
async def logged_operation():
    logger = setup_logging()
    
    logger.info("Starting operation")
    try:
        # Do work
        await asyncio.sleep(1)
        logger.info("Operation completed successfully")
        return "Success"
    except Exception as e:
        logger.error(f"Operation failed: {e}")
        raise
```

### Performance Monitoring

```python
import time
import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

@asynccontextmanager
async def monitor_performance(operation_name: str) -> AsyncIterator[None]:
    """Context manager for monitoring operation performance"""
    start_time = time.time()
    
    try:
        yield
    finally:
        duration = time.time() - start_time
        print(f"Operation '{operation_name}' took {duration:.3f} seconds")

# Usage
async def monitored_operation():
    async with monitor_performance("database_query"):
        await asyncio.sleep(0.5)  # Simulate work
        return "Query result"
```

## Best Practices

### 1. Error Handling
Always handle exceptions appropriately in async operations:

```python
async def robust_operation():
    try:
        result = await some_async_operation()
        return result
    except SpecificError as e:
        # Handle specific errors
        logger.warning(f"Specific error: {e}")
        return default_value
    except Exception as e:
        # Handle unexpected errors
        logger.error(f"Unexpected error: {e}")
        raise
```

### 2. Resource Cleanup
Use context managers for proper resource cleanup:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def managed_resource():
    resource = await acquire_resource()
    try:
        yield resource
    finally:
        await resource.cleanup()
```

### 3. Concurrency Limits
Always limit concurrency to prevent resource exhaustion:

```python
async def limited_concurrent_processing():
    semaphore = asyncio.Semaphore(10)  # Limit to 10 concurrent operations
    
    async def process_with_limit(item):
        async with semaphore:
            return await process_item(item)
```

### 4. Timeout Everything
Set appropriate timeouts for all network operations:

```python
async def api_call_with_timeout():
    async with asyncio.timeout(5.0):  # 5 second timeout
        return await make_api_call()
```