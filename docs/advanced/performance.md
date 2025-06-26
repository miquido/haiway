# Performance Optimization

This guide covers advanced performance optimization techniques for Haiway applications.

## Memory Management

### State Object Optimization

```python
from haiway import State
from typing import Sequence

# ✅ Efficient - uses immutable collections
class EfficientState(State):
    items: Sequence[str]  # Becomes tuple - memory efficient
    metadata: dict[str, str] = {}  # Small dict is fine
    
# ❌ Inefficient - large mutable collections
class InefficientState(State):
    items: list[str] = []  # Mutable list
    large_data: dict[str, list[dict]] = {}  # Nested mutable structures
```

### Memory-Efficient State Updates

```python
# ✅ Efficient - single update
large_state = large_state.updated(
    field1="new_value",
    field2=new_sequence,
    field3=updated_mapping
)

# ❌ Inefficient - multiple intermediate objects
large_state = large_state.updated(field1="new_value")
large_state = large_state.updated(field2=new_sequence)
large_state = large_state.updated(field3=updated_mapping)
```

### Structural Sharing

Haiway automatically shares memory between state objects:

```python
class DataContainer(State):
    data: Sequence[dict]
    version: int = 1

# Original with large dataset
original = DataContainer(data=tuple(large_dataset), version=1)

# Updated version shares the data sequence
updated = original.updated(version=2)
# `data` is shared between original and updated
```

## Context Optimization

### Minimizing Context Depth

```python
# ✅ Good - shallow context hierarchy
async with ctx.scope("app", db_service, cache_service):
    async with ctx.scope("request"):
        # Handle request
        pass

# ❌ Avoid - deep nesting
async with ctx.scope("app"):
    async with ctx.scope("layer1"):
        async with ctx.scope("layer2"):
            async with ctx.scope("layer3"):
                # Too deep
                pass
```

### Efficient State Access

```python
# ✅ Efficient - access once, use multiple times
async def process_data():
    db = ctx.state(DatabaseService)
    cache = ctx.state(CacheService)
    
    # Use db and cache multiple times
    for item in items:
        cached = await cache.get(item.id)
        if not cached:
            data = await db.fetch(item.id)
            await cache.set(item.id, data)

# ❌ Inefficient - repeated context access
async def process_data_inefficient():
    for item in items:
        cached = await ctx.state(CacheService).get(item.id)
        if not cached:
            data = await ctx.state(DatabaseService).fetch(item.id)
            await ctx.state(CacheService).set(item.id, data)
```

## Concurrency Optimization

### Optimal Concurrency Limits

```python
import asyncio
from haiway.helpers import process_concurrently

async def optimized_batch_processing(items: list[str]):
    """Optimized batch processing with appropriate concurrency"""
    # Rule of thumb: 2-4x CPU cores for I/O bound tasks
    cpu_count = asyncio.current_task().get_loop()._executor._max_workers or 4
    optimal_concurrency = min(cpu_count * 3, len(items), 50)  # Cap at 50
    
    results = await process_concurrently(
        process_item,
        items,
        max_concurrency=optimal_concurrency
    )
    return results
```

### Connection Pool Sizing

```python
import asyncpg
from haiway import State

class DatabaseConfig(State):
    host: str
    port: int = 5432
    database: str
    # Pool size should match expected concurrency
    min_connections: int = 5
    max_connections: int = 20

async def create_optimized_pool():
    config = ctx.state(DatabaseConfig)
    
    pool = await asyncpg.create_pool(
        host=config.host,
        port=config.port,
        database=config.database,
        min_size=config.min_connections,
        max_size=config.max_connections,
        # Connection tuning
        command_timeout=10,
        server_settings={
            'application_name': 'haiway_app',
            'tcp_keepalives_idle': '600',
            'tcp_keepalives_interval': '30',
            'tcp_keepalives_count': '3',
        }
    )
    return pool
```

## Caching Strategies

### Multi-Level Caching

```python
from haiway.helpers import cache
from haiway import ctx, State

class CacheConfig(State):
    l1_ttl: int = 60  # Fast local cache
    l2_ttl: int = 300  # Slower distributed cache

# L1 Cache - In-memory
@cache(ttl=lambda: ctx.state(CacheConfig).l1_ttl, max_size=1000)
async def get_user_fast(user_id: str) -> dict:
    return await get_user_from_l2(user_id)

# L2 Cache - Redis/distributed
@cache(ttl=lambda: ctx.state(CacheConfig).l2_ttl, backend="redis")
async def get_user_from_l2(user_id: str) -> dict:
    return await get_user_from_database(user_id)

async def get_user_from_database(user_id: str) -> dict:
    # Expensive database query
    db = ctx.state(DatabaseService)
    return await db.fetch_user(user_id)
```

### Cache Warming

```python
async def warm_cache():
    """Pre-populate cache with frequently accessed data"""
    # Get most frequently accessed user IDs
    popular_users = await get_popular_user_ids()
    
    # Warm cache concurrently
    await process_concurrently(
        get_user_fast,  # This will populate both cache levels
        popular_users,
        max_concurrency=10
    )
```

## Database Optimization

### Efficient Query Patterns

```python
from haiway import ctx, State
from typing import Sequence

class UserRepository(State):
    pool: asyncpg.Pool
    
    @classmethod
    async def get_users_by_ids(cls, user_ids: Sequence[str]) -> list[dict]:
        """Batch fetch users to avoid N+1 queries"""
        repo = ctx.state(cls)
        
        async with repo.pool.acquire() as conn:
            # Single query for multiple users
            rows = await conn.fetch(
                "SELECT * FROM users WHERE id = ANY($1)",
                user_ids
            )
            return [dict(row) for row in rows]
    
    @classmethod
    async def get_user_with_posts(cls, user_id: str) -> dict:
        """Efficient join query instead of separate queries"""
        repo = ctx.state(cls)
        
        async with repo.pool.acquire() as conn:
            # Single query with join
            rows = await conn.fetch("""
                SELECT u.*, p.id as post_id, p.title, p.content
                FROM users u
                LEFT JOIN posts p ON u.id = p.user_id
                WHERE u.id = $1
            """, user_id)
            
            # Group results
            if not rows:
                return None
                
            user = dict(rows[0])
            user['posts'] = [
                {"id": row['post_id'], "title": row['title'], "content": row['content']}
                for row in rows if row['post_id']
            ]
            return user
```

### Connection Management

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def database_transaction():
    """Efficient transaction management"""
    pool = ctx.state(DatabaseService).pool
    
    async with pool.acquire() as conn:
        async with conn.transaction():
            # Create temporary state with transaction connection
            tx_service = DatabaseService(connection=conn)
            
            # Replace service in context for transaction
            original_service = ctx.state(DatabaseService)
            ctx.update_state(tx_service)
            
            try:
                yield
            finally:
                # Restore original service
                ctx.update_state(original_service)

# Usage
async def transfer_funds(from_user: str, to_user: str, amount: float):
    async with database_transaction():
        await DatabaseService.debit_account(from_user, amount)
        await DatabaseService.credit_account(to_user, amount)
        # Transaction automatically committed/rolled back
```

## Monitoring and Profiling

### Performance Metrics

```python
import time
from haiway import ctx, State

class PerformanceTracker(State):
    request_times: dict[str, list[float]] = {}
    
    @classmethod
    async def track_operation(cls, operation_name: str, operation):
        """Track operation performance"""
        tracker = ctx.state(cls)
        
        start_time = time.time()
        try:
            result = await operation
            return result
        finally:
            duration = time.time() - start_time
            
            if operation_name not in tracker.request_times:
                tracker.request_times[operation_name] = []
            
            tracker.request_times[operation_name].append(duration)
            
            # Log slow operations
            if duration > 1.0:
                ctx.log_warning(f"Slow operation {operation_name}: {duration:.2f}s")

# Usage
async def slow_database_query():
    return await PerformanceTracker.track_operation(
        "user_query",
        DatabaseService.complex_user_query(user_id)
    )
```

### Memory Profiling

```python
import psutil
import os
from haiway import ctx

async def monitor_memory_usage():
    """Monitor memory usage during operations"""
    process = psutil.Process(os.getpid())
    
    initial_memory = process.memory_info().rss
    ctx.log_info(f"Initial memory: {initial_memory / 1024 / 1024:.2f} MB")
    
    # Perform memory-intensive operations
    await process_large_dataset()
    
    peak_memory = process.memory_info().rss
    memory_increase = peak_memory - initial_memory
    
    ctx.log_info(f"Peak memory: {peak_memory / 1024 / 1024:.2f} MB")
    ctx.log_info(f"Memory increase: {memory_increase / 1024 / 1024:.2f} MB")
    
    # Force garbage collection
    import gc
    gc.collect()
    
    final_memory = process.memory_info().rss
    ctx.log_info(f"Final memory: {final_memory / 1024 / 1024:.2f} MB")
```

## Async Optimization

### Efficient Task Management

```python
import asyncio
from haiway import ctx

async def optimized_background_tasks():
    """Efficiently manage background tasks"""
    
    # Use task groups for better resource management
    async with asyncio.TaskGroup() as tg:
        # CPU-bound tasks - limit to CPU count
        cpu_tasks = [
            tg.create_task(cpu_intensive_work(data))
            for data in cpu_work_items[:os.cpu_count()]
        ]
        
        # I/O-bound tasks - can have more concurrency
        io_tasks = [
            tg.create_task(io_intensive_work(item))
            for item in io_work_items[:50]  # Reasonable limit
        ]
    
    ctx.log_info(f"Completed {len(cpu_tasks)} CPU tasks and {len(io_tasks)} I/O tasks")
```

### Stream Processing Optimization

```python
async def optimized_stream_processing(stream):
    """Memory-efficient stream processing"""
    buffer = []
    buffer_size = 100
    
    async for item in stream:
        buffer.append(item)
        
        if len(buffer) >= buffer_size:
            # Process buffer concurrently
            await process_concurrently(
                process_item,
                buffer,
                max_concurrency=10
            )
            buffer.clear()  # Free memory immediately
    
    # Process remaining items
    if buffer:
        await process_concurrently(process_item, buffer, max_concurrency=10)
```

## Production Optimization

### Resource Limits

```python
import resource
from haiway import ctx

def configure_resource_limits():
    """Configure system resource limits"""
    
    # Set memory limit (in bytes)
    memory_limit = 1024 * 1024 * 1024  # 1GB
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))
    
    # Set file descriptor limit
    fd_limit = 10000
    resource.setrlimit(resource.RLIMIT_NOFILE, (fd_limit, fd_limit))
    
    ctx.log_info(f"Resource limits configured: memory={memory_limit}, fds={fd_limit}")
```

### Graceful Shutdown

```python
import signal
import asyncio
from haiway import ctx

class ApplicationState(State):
    shutdown_requested: bool = False

async def graceful_shutdown():
    """Handle graceful application shutdown"""
    
    def signal_handler(signum, frame):
        ctx.log_info(f"Received signal {signum}, initiating shutdown")
        app_state = ctx.state(ApplicationState)
        ctx.update_state(app_state.updated(shutdown_requested=True))
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    
    while not ctx.state(ApplicationState).shutdown_requested:
        await asyncio.sleep(1)
        # Check for work to do
        if await has_pending_work():
            await process_pending_work()
    
    ctx.log_info("Graceful shutdown completed")
```

## Performance Testing

### Load Testing

```python
import asyncio
import time
from haiway import ctx

async def load_test(concurrent_requests: int, duration_seconds: int):
    """Simple load testing"""
    
    start_time = time.time()
    request_count = 0
    error_count = 0
    
    async def make_request():
        nonlocal request_count, error_count
        try:
            # Simulate request
            await process_request()
            request_count += 1
        except Exception:
            error_count += 1
    
    # Start initial batch of requests
    tasks = set()
    for _ in range(concurrent_requests):
        task = asyncio.create_task(make_request())
        tasks.add(task)
    
    # Keep running until duration expires
    while time.time() - start_time < duration_seconds:
        # Wait for any task to complete
        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        
        # Remove completed tasks and start new ones
        for task in done:
            tasks.remove(task)
            if time.time() - start_time < duration_seconds:
                new_task = asyncio.create_task(make_request())
                tasks.add(new_task)
    
    # Wait for remaining tasks
    await asyncio.gather(*tasks, return_exceptions=True)
    
    total_time = time.time() - start_time
    rps = request_count / total_time
    
    ctx.log_info(f"Load test results:")
    ctx.log_info(f"  Duration: {total_time:.2f}s")
    ctx.log_info(f"  Requests: {request_count}")
    ctx.log_info(f"  Errors: {error_count}")
    ctx.log_info(f"  RPS: {rps:.2f}")
    ctx.log_info(f"  Error rate: {error_count/request_count*100:.2f}%")
```

## Best Practices Summary

1. **Memory Management**
   - Use immutable collections in state
   - Batch state updates
   - Monitor memory usage

2. **Concurrency**
   - Tune concurrency limits based on workload
   - Use connection pools appropriately
   - Avoid deep context nesting

3. **Caching**
   - Implement multi-level caching
   - Warm caches proactively
   - Set appropriate TTLs

4. **Database**
   - Use batch queries to avoid N+1 problems
   - Implement efficient connection management
   - Monitor query performance

5. **Monitoring**
   - Track key metrics
   - Profile memory usage
   - Implement proper logging

6. **Testing**
   - Load test under realistic conditions
   - Monitor resource usage
   - Test failure scenarios

## Next Steps

- Review [Troubleshooting](troubleshooting.md) for common performance issues
- Implement monitoring in your application
- Set up performance testing in CI/CD
- Profile your specific use cases