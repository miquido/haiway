# Quick Start

Let's build your first Haiway application! This guide will walk you through creating a simple user management system that demonstrates Haiway's core features.

## Your First State

Haiway applications are built around **immutable state objects** that serve as data and dependency containers:

```python
from haiway import State
from typing import Sequence

class User(State):
    id: str
    name: str
    email: str | None = None
```

**What's happening here:**

- `State` is Haiway's base using dataclass like definitions that automatically makes objects immutable
- Fields are defined with type hints - Haiway validates types at runtime
- Once created, `User` objects cannot be modified (attempting `user.name = "new"` raises an error)
- Optional fields use union types (`str | None`) with default values

## Working with Context

Haiway uses **context scopes** to manage state and enable dependency injection:

```python
from haiway import ctx

async def main():
    # Create immutable user object
    alice = User(
        id="1",
        name="Alice",
        email="alice@example.com",
    )

    # Create a copy with updated fields (original unchanged)
    other_alice: User = alice.updated(
        id="2",
        email="other_alice@example.com",
    )

    # Create context scope and inject state
    async with ctx.scope("app", alice):
        # Access state from anywhere within this scope
        # Uses the type as a key to retrieve the correct state
        current_alice: User = ctx.state(User)
        print(f"Current user: {current_alice.name}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

**What's happening here:**

- `ctx.scope("app", alice)` creates an execution context named "app", containing the `alice` state
- State is **automatically propagated** to all code within the scope (including nested function calls)
- `ctx.state(User)` retrieves contextual state using its type as a key
- The `.updated()` method creates a copy of object with modified fields, leaving the original unchanged
- Context automatically manages the lifecycle - when the scope exits, resources are cleaned up

## Adding Functionality

Haiway implements **dependency injection** through function protocols and state containers:

```python
from typing import Protocol, runtime_checkable

# Function interface - single __call__ method only
@runtime_checkable
class UsersFetching(Protocol):
    async def __call__(self) -> Sequence[User]: ...

class UsersService(State):
    fetching: UsersFetching

    @classmethod
    async def fetch_users(cls) -> Sequence[User]:
        return await ctx.state(cls).fetching()

# Factory function for service implementation
def InMemoryUsersService() -> UsersService:

    # Implementation function
    async def in_memory_users_fetching() -> Sequence[User]:
        # In a real implementation, this would access configuration
        # or stored data from context state
        return (
            User(id="1", name="Alice", email="alice@example.com"),
            User(id="2", name="Bob", email="bob@example.com"),
        )

    return UsersService(fetching=in_memory_users_fetching)

async def main():
    # Create service with implementation
    service = InMemoryUsersService()

    # Use in context scope
    async with ctx.scope("app", service):
        # Access functionality through class methods
        users: Sequence[User] = await UsersService.fetch_users()
        print(f"Found {len(users)} users")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

**What's happening here:**

- **Protocol Interface**: `UsersFetching` defines a contract with a single `__call__` method - this ensures implementations are interchangeable
- **Service Container**: `UsersService` holds function implementations and provides a clean API through class methods
- **Implementation Function**: `in_memory_users_fetching` is the concrete implementation that returns actual data
- **Factory Pattern**: `InMemoryUsersService()` creates a configured service instance with the implementation wired up
- **Context Injection**: The service is injected into the context scope, making it available throughout the execution
- **Transparent Access**: `UsersService.fetch_users()` internally retrieves the service from context and calls the implementation
- **Type Safety**: `@runtime_checkable` enables runtime validation that implementations match the protocol

This pattern allows you to easily **swap implementations** (in-memory, database, API) without changing the calling code.

## Key Concepts

1. **Immutable State**: All state objects are immutable by default
2. **Type Safety**: Full type checking support with modern Python features
3. **Context Management**: Scoped execution with state propagation
4. **Dependency Injection**: Clean separation of concerns using function based state interfaces

## Advanced Context Usage

### Using Context Presets

For more advanced scenarios, you can use context presets to package state and disposables together:

```python
from haiway.context import ContextPreset

# Create a preset with predefined state
api_preset = ContextPreset(
    name="api_client",
    state=[
        UsersService(fetching=production_users_fetching),
        ApiConfig(base_url="https://api.example.com", timeout=60)
    ]
)

async def main():
    # Use preset directly - no need for preset registry
    async with ctx.scope(api_preset):
        users = await UsersService.fetch_users()
        config = ctx.state(ApiConfig)
        print(f"Using {config.base_url} with {len(users)} users")
    
    # Override preset state with explicit values
    async with ctx.scope(api_preset, ApiConfig(timeout=30)):
        config = ctx.state(ApiConfig)
        print(f"Timeout overridden to {config.timeout}")

if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
```

**What's happening here:**

- **Preset Definition**: `ContextPreset` packages multiple state objects together
- **Direct Usage**: Pass the preset directly to `ctx.scope()` instead of a string name
- **State Override**: Explicit state parameters override preset state by type
- **Priority System**: Explicit state (highest) > disposables > preset state > contextual state (lowest)

## What's Next?

1. Explore the [Functionalities](../guides/functionalities.md)
2. Learn about [State](../guides/state.md)
3. See how to structure [Packages](../guides/packages.md)
