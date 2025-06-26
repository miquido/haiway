# Context System

Haiway's context system provides scoped execution environments that manage state, tasks, and resources automatically. This enables safe dependency injection and structured concurrency.

## Basic Concepts

### Context Scopes

A context scope is a bounded execution environment:

```python
from haiway import ctx
import asyncio

async def main():
    async with ctx.scope("application"):
        # Code within this scope has access to the context
        current = ctx.current()
        print(f"Context name: {current.name}")
        print(f"Context ID: {current.id}")

asyncio.run(main())
```

### Nested Contexts

Contexts can be nested to create hierarchies:

```python
async def main():
    async with ctx.scope("app"):
        print(f"Outer context: {ctx.current().name}")
        
        async with ctx.scope("request"):
            print(f"Inner context: {ctx.current().name}")
            # Inner context has access to outer context state
            
        print(f"Back to outer: {ctx.current().name}")
```

## State Management

### Adding State to Context

```python
from haiway import ctx, State

class DatabaseConfig(State):
    host: str = "localhost"
    port: int = 5432
    database: str = "myapp"

class UserService(State):
    max_connections: int = 10
    timeout: int = 30

async def main():
    db_config = DatabaseConfig(host="production.db.com")
    user_service = UserService(max_connections=20)
    
    async with ctx.scope("app", db_config, user_service):
        # Access state from context
        db = ctx.state(DatabaseConfig)
        service = ctx.state(UserService)
        
        print(f"Database: {db.host}:{db.port}")
        print(f"Max connections: {service.max_connections}")
```

### State Access Patterns

```python
from haiway import ctx, State
from typing import Protocol, runtime_checkable

@runtime_checkable
class EmailService(Protocol):
    async def send_email(self, to: str, subject: str, body: str) -> bool: ...

class NotificationService(State):
    email_service: EmailService
    
    @classmethod
    async def send_notification(cls, user_id: str, message: str) -> bool:
        # Access service from context
        service = ctx.state(cls)
        return await service.email_service.send_email(
            to=f"user-{user_id}@example.com",
            subject="Notification",
            body=message
        )

# Usage
async def main():
    email_service = SMTPEmailService()
    notification_service = NotificationService(email_service=email_service)
    
    async with ctx.scope("app", notification_service):
        success = await NotificationService.send_notification("123", "Hello!")
        print(f"Notification sent: {success}")
```

## Resource Management

### Disposable Resources

Resources that need cleanup can be managed automatically:

```python
from contextlib import asynccontextmanager
from haiway import ctx, State
import asyncpg

class DatabaseConnection(State):
    pool: asyncpg.Pool
    
    @classmethod
    async def execute(cls, query: str) -> list[dict]:
        db = ctx.state(cls)
        async with db.pool.acquire() as conn:
            result = await conn.fetch(query)
            return [dict(row) for row in result]

@asynccontextmanager
async def database_resource():
    """Create a disposable database connection"""
    pool = await asyncpg.create_pool(
        host="localhost",
        port=5432,
        database="myapp",
        user="postgres",
        password="password"
    )
    
    try:
        yield DatabaseConnection(pool=pool)
    finally:
        await pool.close()

async def main():
    async with ctx.scope("app", disposables=(database_resource(),)):
        # Database connection is available
        users = await DatabaseConnection.execute("SELECT * FROM users")
        print(f"Found {len(users)} users")
    # Connection automatically closed here
```

### Multiple Resources

```python
@asynccontextmanager
async def redis_resource():
    client = await aioredis.create_redis_pool("redis://localhost")
    try:
        yield CacheService(client=client)
    finally:
        client.close()
        await client.wait_closed()

async def main():
    async with ctx.scope(
        "app", 
        disposables=(database_resource(), redis_resource())
    ):
        # Both database and Redis are available
        db = ctx.state(DatabaseConnection)
        cache = ctx.state(CacheService)
        
        # Use both services
        cached_users = await cache.get("users")
        if not cached_users:
            users = await db.execute("SELECT * FROM users")
            await cache.set("users", users, ttl=300)
        # Both resources cleaned up automatically
```

## Task Management

### Automatic Task Cleanup

Tasks started within a context are automatically managed:

```python
import asyncio
from haiway import ctx

async def background_task():
    """Long-running background task"""
    try:
        while True:
            print("Background task running...")
            await asyncio.sleep(1)
    except asyncio.CancelledError:
        print("Background task cancelled")
        raise

async def main():
    async with ctx.scope("app"):
        # Start background task
        task = asyncio.create_task(background_task())
        
        # Do other work
        await asyncio.sleep(5)
        
        # Task is automatically cancelled when scope exits
        print("Exiting scope...")
    
    print("Background task has been cleaned up")

asyncio.run(main())
```

### Task Groups

```python
async def worker(worker_id: int):
    """Worker task"""
    for i in range(3):
        print(f"Worker {worker_id} processing item {i}")
        await asyncio.sleep(1)

async def main():
    async with ctx.scope("workers"):
        # Start multiple workers
        tasks = [
            asyncio.create_task(worker(i))
            for i in range(3)
        ]
        
        # Wait for all workers to complete
        await asyncio.gather(*tasks)
        
    print("All workers completed")
```

## Observability

### Logging Integration

```python
import logging
from haiway import ctx

async def main():
    logger = logging.getLogger("myapp")
    
    async with ctx.scope("app", logger=logger):
        # Log within context
        ctx.log_info("Application started")
        ctx.log_warning("This is a warning")
        
        try:
            raise ValueError("Something went wrong")
        except Exception as exc:
            ctx.log_error(f"Error occurred: {exc}")
```

### Metrics Collection

```python
from haiway import ctx, MetricsLogger

async def process_request():
    """Process a request with metrics"""
    ctx.log_info("Processing request")
    
    # Simulate work
    await asyncio.sleep(0.1)
    
    ctx.log_info("Request processed successfully")

async def main():
    metrics = MetricsLogger.handler()
    
    async with ctx.scope("app", metrics=metrics):
        await process_request()
        
        # Metrics are automatically collected
        print("Request processed with metrics")
```

### Distributed Tracing

```python
from haiway import ctx

async def service_a():
    """First service in the chain"""
    ctx.log_info("Service A called")
    await service_b()

async def service_b():
    """Second service in the chain"""
    ctx.log_info("Service B called")
    await service_c()

async def service_c():
    """Third service in the chain"""
    ctx.log_info("Service C called")
    # Simulate work
    await asyncio.sleep(0.1)

async def main():
    async with ctx.scope("request") as trace_id:
        print(f"Trace ID: {trace_id}")
        await service_a()
        # All services share the same trace context
```

## Advanced Patterns

### Context Inheritance

```python
class BaseService(State):
    name: str
    
    @classmethod
    def get_name(cls) -> str:
        service = ctx.state(cls)
        return service.name

class UserService(BaseService):
    max_users: int = 1000

class AdminService(BaseService):
    admin_level: int = 1

async def main():
    user_service = UserService(name="users", max_users=500)
    admin_service = AdminService(name="admin", admin_level=2)
    
    async with ctx.scope("app", user_service, admin_service):
        # Access inherited behavior
        print(f"User service: {UserService.get_name()}")
        print(f"Admin service: {AdminService.get_name()}")
```

### Context Factories

```python
from typing import AsyncContextManager

def create_database_context(
    database_url: str,
    pool_size: int = 10
) -> AsyncContextManager[None]:
    """Factory for database context"""
    
    @asynccontextmanager
    async def database_context():
        pool = await asyncpg.create_pool(database_url, max_size=pool_size)
        try:
            yield DatabaseConnection(pool=pool)
        finally:
            await pool.close()
    
    return database_context()

async def main():
    db_context = create_database_context("postgresql://localhost/myapp")
    
    async with ctx.scope("app", disposables=(db_context,)):
        # Database is available
        result = await DatabaseConnection.execute("SELECT 1")
        print(f"Database test: {result}")
```

## Testing with Contexts

### Mock Dependencies

```python
import pytest
from haiway import ctx

class MockEmailService:
    def __init__(self):
        self.sent_emails = []
    
    async def send_email(self, to: str, subject: str, body: str) -> bool:
        self.sent_emails.append({"to": to, "subject": subject, "body": body})
        return True

@pytest.mark.asyncio
async def test_notification_service():
    mock_email = MockEmailService()
    service = NotificationService(email_service=mock_email)
    
    async with ctx.scope("test", service):
        result = await NotificationService.send_notification("123", "Test")
        
        assert result is True
        assert len(mock_email.sent_emails) == 1
        assert mock_email.sent_emails[0]["to"] == "user-123@example.com"
```

### Test Contexts

```python
@pytest.fixture
async def test_context():
    """Test fixture providing a configured context"""
    mock_db = MockDatabase()
    mock_cache = MockCache()
    
    db_service = DatabaseService(db=mock_db)
    cache_service = CacheService(cache=mock_cache)
    
    async with ctx.scope("test", db_service, cache_service) as scope:
        yield scope

@pytest.mark.asyncio
async def test_user_creation(test_context):
    async with test_context:
        user = await create_user("Alice", "alice@example.com")
        assert user.name == "Alice"
```

## Best Practices

1. **Use Descriptive Names**: Give contexts meaningful names for better observability
2. **Minimize Scope Depth**: Avoid deeply nested contexts when possible
3. **Clean Resource Management**: Always use disposables for resources that need cleanup
4. **State Composition**: Prefer multiple small states over large monolithic ones
5. **Test with Mocks**: Use dependency injection for easy testing
6. **Monitor Context Lifecycle**: Use logging and metrics to track context behavior

## Common Patterns

### Request-Response Pattern

```python
async def handle_request(request_id: str):
    """Handle a single request"""
    async with ctx.scope(f"request-{request_id}"):
        ctx.log_info(f"Handling request {request_id}")
        
        # Process request
        result = await process_request_data()
        
        ctx.log_info(f"Request {request_id} completed")
        return result
```

### Batch Processing Pattern

```python
async def process_batch(items: list[str]):
    """Process a batch of items"""
    async with ctx.scope("batch-processing"):
        ctx.log_info(f"Processing batch of {len(items)} items")
        
        results = []
        for item in items:
            async with ctx.scope(f"item-{item}"):
                result = await process_item(item)
                results.append(result)
        
        ctx.log_info(f"Batch processing completed: {len(results)} results")
        return results
```

### Service Layer Pattern

```python
class BusinessLogicService(State):
    repository: Repository
    cache: Cache
    
    @classmethod
    async def get_user(cls, user_id: str) -> User | None:
        service = ctx.state(cls)
        
        # Try cache first
        cached_user = await service.cache.get(f"user:{user_id}")
        if cached_user:
            return cached_user
        
        # Fall back to repository
        user = await service.repository.get_user(user_id)
        if user:
            await service.cache.set(f"user:{user_id}", user, ttl=300)
        
        return user
```

## Next Steps

- Learn about [Async Patterns](async-patterns.md) for advanced concurrency
- Explore [Testing](testing.md) strategies for context-based applications
- See [Examples](../examples/index.md) for real-world usage patterns