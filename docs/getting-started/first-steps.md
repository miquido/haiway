# First Steps

Now that you've seen Haiway in action, let's dive deeper into the core concepts that make it
powerful.

## Understanding State

In Haiway, everything is built around **immutable state objects** that ensure thread safety and
predictable behavior:

```python
from typing import Mapping, Sequence
from haiway import State

class UserPreferences(State):
    theme: str = "light"
    notifications: bool = True
    languages: Sequence[str] = ("en",)  # Becomes tuple
    metadata: Mapping[str, str] = {}    # Stays a dict (treat as read-only)

# State objects are immutable
prefs = UserPreferences()
# prefs.theme = "dark"  # This would raise an error!

# Instead, create updated instances through copies
dark_prefs: UserPreferences = prefs.updated(theme="dark")
print(f"Original: {prefs.theme}, Updated: {dark_prefs.theme}")
```

**What's happening here:**

- **Automatic Immutability**: `State` base class prevents modification after creation using
  `__setattr__` blocking
- **Type Conversion**: Sequences and sets are converted to immutable counterparts during validation
  (mappings stay as plain dicts)
- **Memory Sharing**: The `.updated()` method creates structural sharing - unchanged fields
  reference the same objects
- **Type Safety**: Field types are validated at runtime, ensuring data integrity
- **Default Values**: Fields can have defaults, and missing fields use type-appropriate defaults

### Important Collection Types

Always use **abstract collection types** to ensure immutability:

- `Sequence[T]` instead of `list[T]` (becomes tuple)
- `Mapping[K,V]` instead of `dict[K,V]` (remains a dict; treat it as read-only)
- `Set[T]` instead of `set[T]` (becomes frozenset)

**Why this matters:**

- **Automatic Conversion**: Haiway converts mutable sequences and sets (`list`, `set`) to immutable
  equivalents (`tuple`, `frozenset`) during validation
- **Interface Flexibility**: Abstract types allow callers to pass any compatible collection type
- **Memory Efficiency**: Immutable collections can be safely shared between state instances
- **Thread Safety**: Immutable collections eliminate race conditions in concurrent code

## Context System

The context system provides **scoped execution environments** that manage state and resources
automatically:

```python
from haiway import ctx
import asyncio

from haiway import ctx


async def main():
    # Contexts can be nested
    async with ctx.scope("database"):
        print("database context")
        
        async with ctx.scope("transaction"):
            print("Nested context")
            # Work happens here
            
        print(f"back to database context")

asyncio.run(main())
```

**What's happening here:**

- **Context Stack**: Each `ctx.scope()` creates a new context that inherits from its parent
- **Automatic Cleanup**: When a scope exits, all resources and tasks are automatically cleaned up
- **State Isolation**: Each context can contain its own state objects, accessed by type
- **Context Variables**: Uses Python's `contextvars` under the hood for async-safe state propagation
- **Nested Scopes**: Inner scopes can access outer scope state, but not vice versa

## Dependency Injection Pattern

Haiway uses **protocol-based dependency injection** to enable flexible, testable architectures:

```python
import asyncio
from typing import Protocol, runtime_checkable

from haiway import State, ctx, statemethod

@runtime_checkable
class EmailSending(Protocol):
    async def __call__(self, to: str, subject: str, body: str) -> bool: ...

class NotificationService(State):
    email_sending: EmailSending

    @statemethod
    async def notify_user(self, user_id: str, message: str) -> bool:
        return await self.email_sending(
            to=f"user-{user_id}@example.com",
            subject="Notification",
            body=message,
        )

# Implementation function
async def smtp_email_sending(to: str, subject: str, body: str) -> bool:
    # Send email logic here
    print(f"Sending email to {to}: {subject}")
    return True

# Factory function
def SMTPNotificationService() -> NotificationService:
    return NotificationService(email_sending=smtp_email_sending)

async def main():
    service = SMTPNotificationService()
    
    async with ctx.scope("app", service):
        success = await NotificationService.notify_user("123", "Welcome!")
        print(f"Email sent: {success}")

asyncio.run(main())
```

**What's happening here:**

- **Protocol Contract**: `EmailSending` defines the interface with a single `__call__` method for
  maximum flexibility
- **Service State**: `NotificationService` contains the function implementation and provides a clean
  API
- **Implementation Function**: `smtp_email_sending` is the concrete function that performs the
  actual email sending
- **Factory Pattern**: `SMTPNotificationService()` creates a pre-configured service with the
  implementation wired up
- **State Method**: `@statemethod` ensures the helper always receives an instance — class calls
  resolve from context while instance calls use that instance
- **Transparent Calling**: The method calls the implementation function seamlessly
- **Type Safety**: `@runtime_checkable` ensures implementations conform to the protocol at runtime

## Resource Management

Haiway provides **automatic resource cleanup** through disposable context managers:

```python
import asyncio
from collections.abc import Mapping
from contextlib import asynccontextmanager

from haiway import State, ctx


@asynccontextmanager
async def database_connection():
    print("Opening database connection")
    try:
        yield DatabaseState(connection={"status": "connected"})
    finally:
        print("Closing database connection")

class DatabaseState(State):
    connection: Mapping[str, str]

async def main():
    async with ctx.scope("app", disposables=(database_connection(),)):
        db = ctx.state(DatabaseState)
        print(f"Database status: {db.connection['status']}")
    # Connection automatically closed here

asyncio.run(main())
```

**What's happening here:**

- **Disposable Pattern**: `disposables=()` parameter accepts async context managers that need
  cleanup
- **Resource Lifecycle**: Resources are opened when entering the scope and closed when exiting
- **State Injection**: The yielded state object becomes available through `ctx.state()`
- **Exception Safety**: Resources are cleaned up even if exceptions occur within the scope
- **Concurrent Cleanup**: Multiple disposables are managed concurrently for efficient resource
  handling

## Next Steps

Now that you understand the basics:

1. Explore the [Functionalities](../guides/functionalities.md)
1. Learn about [State](../guides/state.md)
1. See how to structure [Packages](../guides/packages.md)
