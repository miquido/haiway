# Architecture

Haiway is designed around four core principles that work together to create a robust, scalable framework for Python applications.

## Design Philosophy

Haiway emphasizes:

1. **Immutable State Management** - Type-safe, immutable data structures with validation
2. **Context-based Dependency Injection** - Safe state propagation in concurrent environments
3. **Functional Approach** - Pure functions over objects with methods
4. **Structured Concurrency** - Automatic task management and resource cleanup

## Core Components

### Context System

The context system provides scoped execution environments with:

- **State Access** - Dependency injection and state management
- **Task Management** - Automatic lifecycle management for concurrent operations
- **Observability** - Built-in tracing and monitoring hooks

```python
from haiway import ctx

async with ctx.scope("application"):
    # All operations within this scope have access to the context
    current = ctx.current()
    print(f"Context name: {current.name}")
```

### State Management

Immutable data structures that provide:

- **Validation** - Automatic type checking and data validation
- **Generic Type Support** - Full support for Python's generic types
- **Path-based Access** - Navigate complex nested structures safely

```python
from haiway import State
from typing import Sequence, Mapping

class Configuration(State):
    database_url: str
    feature_flags: Mapping[str, bool]
    allowed_hosts: Sequence[str]

config = Configuration(
    database_url="postgresql://localhost/db",
    feature_flags={"new_ui": True, "beta_features": False},
    allowed_hosts=["localhost", "127.0.0.1"]
)
```

### Helpers

Utility functions for common async patterns:

- **Async Utilities** - Concurrent operations and task management
- **Caching** - Built-in caching mechanisms
- **Retries** - Configurable retry logic
- **Timeouts** - Automatic timeout handling
- **Tracing** - Integration with observability tools

### Types

Base type definitions and utilities:

- **Base Types** - Common interfaces and abstract base classes
- **Missing Value Handling** - Safe handling of optional and nullable values

## Architectural Patterns

### State Definition Pattern

```python
from typing import Protocol, runtime_checkable
from haiway import State

# Basic data structure
class UserData(State):
    id: str
    name: str
    email: str | None = None

# Generic state classes
class Container[Element](State):
    items: Sequence[Element]
    metadata: Mapping[str, Any]

# Function protocol for dependency injection
@runtime_checkable
class UserFetching(Protocol):
    async def __call__(self, id: str) -> UserData: ...

# Service state pattern
class UserService(State):
    user_fetching: UserFetching

    @classmethod
    async def fetch_user(cls, *, id: str) -> UserData:
        return await ctx.state(cls).user_fetching(id)
```

### Resource Management Pattern

```python
from contextlib import asynccontextmanager
from haiway import ctx, State

class DatabaseAccess(State):
    connection: DatabaseConnection

    @classmethod
    async def query(cls, sql: str) -> list[dict]:
        db = ctx.state(cls)
        return await db.connection.execute(sql)

@asynccontextmanager
async def database_resource():
    connection = await create_database_connection()
    try:
        yield DatabaseAccess(connection=connection)
    finally:
        await connection.close()

# Usage
async with ctx.scope("app", disposables=(database_resource(),)):
    results = await DatabaseAccess.query("SELECT * FROM users")
    # Connection automatically closed when scope exits
```

## Immutability Rules

### Collection Types

**Always use abstract collection types in State classes:**

```python
from typing import Sequence, Mapping, Set
from haiway import State

class UserData(State):
    roles: Sequence[str]        # Becomes tuple (immutable)
    metadata: Mapping[str, Any] # Becomes immutable dict
    tags: Set[str]              # Becomes frozenset (immutable)
```

### State Updates

State objects are immutable. Create new instances using the `updated()` method:

```python
user = UserData(id="1", name="Alice", email="alice@example.com")

# Create updated version
updated_user = user.updated(name="Alice Smith")

# Original remains unchanged
assert user.name == "Alice"
assert updated_user.name == "Alice Smith"
```

## Dependency Injection

Haiway uses a context-based dependency injection system:

### Protocol Definition

```python
from typing import Protocol, runtime_checkable

@runtime_checkable
class EmailSending(Protocol):
    async def __call__(self, to: str, subject: str, body: str) -> bool: ...
```

### Service State

```python
class NotificationService(State):
    email_sending: EmailSending

    @classmethod
    async def notify_user(cls, user_id: str, message: str) -> bool:
        service = ctx.state(cls)
        return await service.email_sending(
            to=f"user-{user_id}@example.com",
            subject="Notification",
            body=message
        )
```

### Context Setup

```python
# Implementation function
async def smtp_email_sending(to: str, subject: str, body: str) -> bool:
    # Send email implementation
    print(f"Sending email to {to}: {subject}")
    return True

# Factory function
def SMTPNotificationService() -> NotificationService:
    return NotificationService(email_sending=smtp_email_sending)

# Usage
notification_service = SMTPNotificationService()

async with ctx.scope("app", notification_service):
    await NotificationService.notify_user("123", "Welcome!")
```

## Structured Concurrency

Haiway ensures that all tasks are properly managed and cleaned up:

```python
import asyncio
from haiway import ctx

async def background_task():
    while True:
        print("Background task running")
        await asyncio.sleep(1)

async with ctx.scope("app"):
    # Start background task
    task = asyncio.create_task(background_task())
    
    # Do other work
    await asyncio.sleep(5)
    
    # Task is automatically cancelled when scope exits
```

## Best Practices

1. **Use Protocols for Contracts** - Define clear interfaces using `typing.Protocol`
2. **Keep State Immutable** - Always use immutable collection types
3. **Scope Resources Properly** - Use context managers for resource lifecycle
4. **Test with Mocks** - Use dependency injection for easy testing
5. **Follow Type Hints** - Leverage Python's type system for better code quality

## Integration Points

### OpenTelemetry Integration

Optional distributed tracing support:

```python
# Enable OpenTelemetry tracing
async with ctx.scope("traced-operation", trace=True):
    # All operations within this scope are automatically traced
    result = await some_operation()
```

### Framework Integration

Haiway integrates well with popular Python frameworks:

- **FastAPI** - See [FastAPI Integration](../examples/fastapi-integration.md)
- **Django** - Compatible with Django's async views
- **Flask** - Works with Flask's async capabilities

## Next Steps

- Learn about [State Management](state-management.md) in detail
- Explore the [Context System](context-system.md)
- Master [Functionality Patterns](functionality-patterns.md) for implementing features
- Plan your project with [Package Organization](package-organization.md) guidelines
- Discover [Async Patterns](async-patterns.md) for concurrent programming
- See [Examples](../examples/index.md) for real-world usage