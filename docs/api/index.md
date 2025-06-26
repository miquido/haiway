# API Reference

This section provides detailed API documentation for all Haiway modules and classes.

## Modules

- **[Core](core.md)** - Core framework functionality (`State`, `ctx`)
- **[Context](context.md)** - Context management and scoped execution
- **[State](state.md)** - Immutable state classes and utilities  
- **[Helpers](helpers.md)** - Async utilities and helper functions
- **[Types](types.md)** - Type definitions and protocols

## Essential Imports

```python
# Core functionality
from haiway import State, ctx

# Protocol definitions for dependency injection
from typing import Protocol, runtime_checkable

# Abstract collection types for immutability
from collections.abc import Sequence, Mapping, Set

# Context managers for resource management
from contextlib import asynccontextmanager
```

## Quick Start Reference

### Basic State Definition

```python
from haiway import State
from collections.abc import Sequence, Mapping

class UserData(State):
    id: str
    name: str
    email: str | None = None
    roles: Sequence[str] = ()        # Becomes tuple
    metadata: Mapping[str, str] = {} # Stays as dict
```

### Protocol and Service Pattern

```python
from typing import Protocol, runtime_checkable
from haiway import ctx, State

@runtime_checkable
class UserFetching(Protocol):
    async def __call__(self, user_id: str) -> UserData | None: ...

class UserService(State):
    fetching: UserFetching
    
    @classmethod
    async def get_user(cls, user_id: str) -> UserData | None:
        service = ctx.state(cls)
        return await service.fetching(user_id)
```

### Context Usage

```python
from haiway import ctx

# Implementation function
async def database_user_fetching(user_id: str) -> UserData | None:
    # Access other state from context if needed
    # config = ctx.state(DatabaseConfig)
    return UserData(id=user_id, name="Example User")

# Factory function
def DatabaseUserService() -> UserService:
    return UserService(fetching=database_user_fetching)

# Usage in context
async def main():
    service = DatabaseUserService()
    
    async with ctx.scope("app", service):
        user = await UserService.get_user("123")
        if user:
            print(f"User: {user.name}")
```

## Type Safety Guidelines

### Collection Types

**Always use abstract collection types in State classes:**

```python
from collections.abc import Sequence, Mapping, Set

class Config(State):
    # ✅ Correct - use abstract types
    items: Sequence[str]        # Lists → tuples (immutable)
    settings: Mapping[str, int] # Dicts → dicts (immutable interface)
    tags: Set[str]              # Sets → frozensets (immutable)
    
    # ❌ Incorrect - concrete types cause validation errors
    # bad_items: list[str]      # Will fail validation
    # bad_settings: dict[str, int]  # Will fail validation
    # bad_tags: set[str]        # Will fail validation
```

### Protocol Definitions

**Use single `__call__` method for maximum flexibility:**

```python
@runtime_checkable
class DataProcessing(Protocol):
    async def __call__(self, data: InputData, **kwargs) -> OutputData: ...

# Not multiple methods - keep protocols simple
```

## Error Handling

### State Validation Errors

```python
from haiway import State

class User(State):
    name: str
    age: int
    
    def __post_init__(self):
        if self.age < 0:
            raise ValueError("Age cannot be negative")

# This will raise ValueError during creation
# user = User(name="Alice", age=-5)
```

### Context State Access

```python
from haiway import ctx

# Handle missing state gracefully
def get_optional_config() -> Config | None:
    try:
        return ctx.state(Config)
    except StateNotFoundError:
        return None
```