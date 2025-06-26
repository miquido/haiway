# Examples

This section provides practical examples of using Haiway in real-world applications.

## Available Examples

- **[FastAPI Integration](fastapi-integration.md)** - Building web APIs with FastAPI and Haiway

## Example Repository

All examples are available in the [examples directory](https://github.com/miquido/haiway/tree/main/examples) of the main repository.

## Contributing Examples

Have a great example to share? We'd love to include it! Please:

1. Create a complete, working example
2. Include clear documentation and comments
3. Add tests where appropriate
4. Submit a pull request

## Common Patterns

### Basic Application Structure

Following Haiway's functional patterns with protocol-based dependency injection:

```python
from haiway import ctx, State
from typing import Protocol, runtime_checkable
from collections.abc import Sequence

# Define data structures using State
class User(State):
    id: str
    name: str
    email: str
    roles: Sequence[str] = ()  # Use Sequence, not list

# Define function interface with single __call__ method
@runtime_checkable
class UserFetching(Protocol):
    async def __call__(self, user_id: str) -> User | None: ...

# State container holding function implementations
class UserService(State):
    fetching: UserFetching
    
    @classmethod
    async def get_user(cls, user_id: str) -> User | None:
        """Class method interface to access functionality"""
        service = ctx.state(cls)
        return await service.fetching(user_id)

# Concrete implementation function
async def database_user_fetching(user_id: str) -> User | None:
    """Concrete implementation that could access database from context"""
    # In real implementation, would access database state from context
    # For demo, return example user
    return User(
        id=user_id, 
        name="Example User", 
        email="user@example.com",
        roles=("user", "member")  # Will become tuple
    )

# Factory function for service with implementation
def DatabaseUserService() -> UserService:
    """Factory that wires up implementation"""
    return UserService(fetching=database_user_fetching)

# Application setup and usage
async def main():
    service = DatabaseUserService()
    
    async with ctx.scope("app", service):
        user = await UserService.get_user("123")
        if user:
            print(f"Found user: {user.name} with roles: {user.roles}")

import asyncio
asyncio.run(main())
```

### Resource Management

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def database_connection():
    conn = await create_connection()
    try:
        yield DatabaseState(connection=conn)
    finally:
        await conn.close()

async def main():
    async with ctx.scope("app", disposables=(database_connection(),)):
        # Database connection is available here
        db = ctx.state(DatabaseState)
        # Connection is automatically closed when scope exits
```