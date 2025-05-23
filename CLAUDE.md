# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

```bash
# Setup development environment
make venv
. ./.venv/bin/activate

# Sync dependencies
make sync

# Update dependencies
make update

# Run formatter - make sure to run with activated venv
make format

# Run linters and type checker - make sure to run with activated venv
make lint

# Run test suite - make sure to run with activated venv
make test

# Run a single test - make sure to run with activated venv
python -B -m pytest -v tests/test_file.py::test_function
```

## Architecture Overview

Haiway is a Python framework (3.12+) designed for functional programming with structured concurrency. It focuses on:

1. **Immutable State Management**: Using State classes for type-safe, immutable data structures
2. **Context-based Dependency Injection**: Propagating state through execution contexts
3. **Functional Approach**: Emphasizing pure functions over objects with methods

### Core Components

- **Context System** (`haiway.context`): Provides scoped execution environments with access to state
- **State Management** (`haiway.state`): Immutable data structures with validation
- **Helpers** (`haiway.helpers`): Utilities for async operations, caching, retries, etc.
- **Types** (`haiway.types`): Base type definitions

## Development Patterns

### Defining Types

```python
from typing import Protocol, runtime_checkable
from haiway import State

# Data structure
class UserData(State):
    id: str
    name: str
    email: str | None = None

# Function interface
@runtime_checkable
class UserFetching(Protocol):
    async def __call__(self, id: str) -> UserData: ...
```

### Managing State

```python
from haiway import State, ctx
from .types import UserFetching, UserData

# Configuration state
class UserServiceConfig(State):
    api_url: str = "https://api.example.com"
    timeout_seconds: int = 30

# Functionality container
class UserService(State):
    # Function implementations
    fetching: UserFetching

    # Class method interface
    @classmethod
    async def fetch_user(cls, id: str) -> UserData:
        return await ctx.state(cls).fetching(id)
```

### Implementation

```python
from haiway import ctx
from .types import UserData
from .state import UserService, UserServiceConfig

# Concrete implementation
async def http_user_fetching(id: str) -> UserData:
    config = ctx.state(UserServiceConfig)
    # Implementation using config.api_url
    return UserData(id=id, name="Example User")

# Factory function
def http_user_service() -> UserService:
    return UserService(fetching=http_user_fetching)
```

### Context Usage

```python
from haiway import ctx
from .implementation import http_user_service
from .state import UserServiceConfig

async def main():
    # Set up execution context
    async with ctx.scope(
        "main",
        http_user_service(),
        UserServiceConfig(api_url="https://custom-api.example.com")
    ):
        # Use functionality through class methods
        user = await UserService.fetch_user("user-123")
```

## Common Patterns

1. **Immutable Updates**:
   ```python
   # Create new instance with updated values
   updated_config = config.updated(api_url="https://new-api.example.com")
   ```

2. **Context Access**:
   ```python
   # Get state from current context
   config = ctx.state(ConfigType)
   ```

3. **Disposable Resources**:
   ```python
   # Resources that need cleanup
   async def create_resource() -> tuple[Resource, Disposable]:
      resource = Resource()
      async def cleanup(): await resource.close()
      return resource, ctx.disposable(cleanup)
   ```

## Testing Guidelines

- Tests use pytest with pytest-asyncio
- Mock context values with `ctx.updated`
- For async tests, use the `@pytest.mark.asyncio` decorator
- Immutable state makes testing simpler - no need to reset between tests

```python
import pytest
from haiway import ctx

@pytest.mark.asyncio
async def test_functionality():
    async with ctx.scope("test", TestState(value="test")):
        result = await some_function()
        assert result == expected_result
```
