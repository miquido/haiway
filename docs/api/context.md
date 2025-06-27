# Context API

The context module provides scoped execution environments with state management, task coordination, and observability.

## Context Management Functions

### `ctx.scope(name, *states, disposables=())`

Create a new context scope with the given name, state objects, and disposable resources.

**Parameters:**
- `name: str` - Descriptive name for the context scope
- `*states` - State objects to inject into the context
- `disposables: tuple` - Tuple of async context managers for resource cleanup

**Returns:**
- `AsyncContextManager` - Context manager for the scope

**Example:**
```python
from haiway import ctx, State

class Config(State):
    debug: bool = False

async with ctx.scope("app", Config(debug=True)):
    config = ctx.state(Config)
    print(f"Debug mode: {config.debug}")
```

### `ctx.state(state_class)`

Retrieve a state object from the current context by its type.

**Parameters:**
- `state_class: type[State]` - The State class to retrieve

**Returns:**
- State instance from the current context

**Raises:**
- `StateNotFoundError` - If the state class is not available in the current context

**Example:**
```python
from haiway import ctx, State

class DatabaseConfig(State):
    host: str = "localhost"
    port: int = 5432

# Inside a context scope
db_config = ctx.state(DatabaseConfig)
print(f"Database: {db_config.host}:{db_config.port}")
```

### `ctx.current()`

Get information about the current context.

**Returns:**
- `ContextInfo` - Object containing context name, ID, and other metadata

**Example:**
```python
from haiway import ctx

# Inside a context scope
current = ctx.current()
print(f"Context: {current.name} (ID: {current.id})")
```

## Context Patterns

### Basic State Injection

```python
from haiway import ctx, State

class AppConfig(State):
    api_url: str = "https://api.example.com"
    timeout: int = 30

class UserService(State):
    max_users: int = 1000

async def main():
    config = AppConfig(api_url="https://prod-api.example.com")
    service = UserService(max_users=5000)
    
    async with ctx.scope("application", config, service):
        # Both states are available in this scope
        app_config = ctx.state(AppConfig)
        user_service = ctx.state(UserService)
        
        print(f"API URL: {app_config.api_url}")
        print(f"Max users: {user_service.max_users}")
```

### Resource Management with Disposables

```python
from contextlib import asynccontextmanager
from haiway import ctx, State

class DatabaseConnection(State):
    pool: object  # Database pool object

@asynccontextmanager
async def database_resource():
    # Create database connection pool
    pool = await create_database_pool()
    try:
        yield DatabaseConnection(pool=pool)
    finally:
        await pool.close()

async def main():
    async with ctx.scope("app", disposables=(database_resource(),)):
        # Database connection is available
        db = ctx.state(DatabaseConnection)
        # Use database connection
        # Pool is automatically closed when scope exits
```

### Nested Context Scopes

```python
from haiway import ctx, State

class GlobalConfig(State):
    app_name: str = "MyApp"

class RequestConfig(State):
    request_id: str
    user_id: str

async def main():
    global_config = GlobalConfig(app_name="Production App")
    
    async with ctx.scope("global", global_config):
        # Global config is available
        
        request_config = RequestConfig(request_id="req-123", user_id="user-456")
        async with ctx.scope("request", request_config):
            # Both global and request config are available
            global_cfg = ctx.state(GlobalConfig)
            request_cfg = ctx.state(RequestConfig)
            
            print(f"App: {global_cfg.app_name}")
            print(f"Request: {request_cfg.request_id} for {request_cfg.user_id}")
```

### Multiple Resources

```python
from contextlib import asynccontextmanager
from haiway import ctx, State

class DatabaseState(State):
    connection: object

class CacheState(State):
    client: object

@asynccontextmanager
async def database_resource():
    conn = await create_db_connection()
    try:
        yield DatabaseState(connection=conn)
    finally:
        await conn.close()

@asynccontextmanager
async def cache_resource():
    client = await create_cache_client()
    try:
        yield CacheState(client=client)
    finally:
        await client.close()

async def main():
    async with ctx.scope("app", disposables=(database_resource(), cache_resource())):
        # Both database and cache are available
        db = ctx.state(DatabaseState)
        cache = ctx.state(CacheState)
        # Both resources cleaned up automatically
```

## Error Handling

### StateNotFoundError

Raised when trying to access a state that hasn't been injected into the current context:

```python
from haiway import ctx, State

class MissingState(State):
    value: str

async with ctx.scope("app"):
    try:
        state = ctx.state(MissingState)  # This will raise StateNotFoundError
    except StateNotFoundError:
        print("MissingState was not injected into this context")
```

### Context Validation

Always ensure states are properly injected before accessing them:

```python
from haiway import ctx, State

class OptionalState(State):
    enabled: bool = False

def get_optional_state() -> OptionalState | None:
    try:
        return ctx.state(OptionalState)
    except StateNotFoundError:
        return None

async with ctx.scope("app"):
    optional = get_optional_state()
    if optional:
        print(f"Optional state enabled: {optional.enabled}")
    else:
        print("Optional state not available")
```