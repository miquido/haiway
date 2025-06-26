# Core API

The core module contains the fundamental classes and functions that form the foundation of Haiway.

## State

The `State` class is the foundation of Haiway's immutable data structures. All application data should be defined using State classes.

### Key Features

- **Immutability**: State objects cannot be modified after creation
- **Type Safety**: Full runtime type validation for all Python types
- **Collection Conversion**: Automatic conversion of mutable collections to immutable equivalents
- **Path-based Updates**: Support for nested state updates using attribute paths

### Basic Usage

```python
from haiway import State
from typing import Sequence, Mapping, Set

class UserData(State):
    id: str
    name: str
    email: str | None = None
    roles: Sequence[str] = ()        # Becomes tuple
    metadata: Mapping[str, str] = {} # Stays as dict
    tags: Set[str] = frozenset()     # Becomes frozenset

# Create immutable instance
user = UserData(
    id="123",
    name="Alice",
    roles=["admin", "user"],  # Converted to tuple
    tags={"important", "vip"} # Converted to frozenset
)

# Update through copying
updated_user = user.updated(name="Alice Smith")
```

### Collection Type Requirements

**Always use abstract collection types:**

- `Sequence[T]` instead of `list[T]` - Lists are converted to tuples (immutable)
- `Mapping[K,V]` instead of `dict[K,V]` - Dicts remain as dicts but interface is read-only
- `Set[T]` instead of `set[T]` - Sets are converted to frozensets (immutable)

### Supported Types

State validates all Python types including:

- **Basic Types**: `int`, `str`, `bool`, `float`, `bytes`, `None`
- **Collection Types**: `Sequence[T]`, `Mapping[K,V]`, `Set[T]`, `tuple[T, ...]`
- **Special Types**: `UUID`, `datetime`, `date`, `time`, `timedelta`, `timezone`, `Path`, `re.Pattern`
- **Union Types**: `str | None`, `int | float`
- **Literal Types**: `Literal["a", "b", "c"]`
- **Enum Types**: Standard `Enum` and `StrEnum` classes
- **Callable Types**: Function types and `Protocol` interfaces
- **TypedDict**: Structure validation with `Required`/`NotRequired` fields
- **Nested State**: Recursive validation including generic State types
- **Any Type**: Accepts any value without validation

## Context

The `ctx` module provides context management for scoped execution environments with state propagation, dependency injection, and resource management.

### Key Features

- **State Access**: Type-safe dependency injection using `ctx.state(StateClass)`
- **Scoped Execution**: Hierarchical context scopes with automatic cleanup
- **Resource Management**: Automatic lifecycle management for disposable resources
- **Task Coordination**: Structured concurrency with automatic task cleanup

### Basic Usage

```python
from haiway import ctx, State

class DatabaseConfig(State):
    host: str = "localhost"
    port: int = 5432

async def main():
    config = DatabaseConfig(host="production.db.com")
    
    async with ctx.scope("application", config):
        # Access state from context
        db_config = ctx.state(DatabaseConfig)
        print(f"Database: {db_config.host}:{db_config.port}")

import asyncio
asyncio.run(main())
```

### Context Scopes

Create execution environments with `ctx.scope()`:

```python
async with ctx.scope("scope-name", *states, disposables=(*resources,)):
    # Code with access to states and resources
    current = ctx.current()  # Get current context info
    state = ctx.state(StateClass)  # Access injected state
```

### Resource Management

Use disposables for automatic resource cleanup:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def database_connection():
    conn = await create_connection()
    try:
        yield DatabaseState(connection=conn)
    finally:
        await conn.close()

async with ctx.scope("app", disposables=(database_connection(),)):
    db = ctx.state(DatabaseState)
    # Connection automatically closed when scope exits
```