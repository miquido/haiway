# Context Presets

Context presets provide a powerful way to package and reuse combinations of state objects and disposable resources in Haiway applications. They enable you to create reusable configurations that can be applied consistently across different parts of your application, promoting modularity and reducing code duplication.

## Overview

Context presets allow you to:

- **Package state and disposables** into reusable configurations
- **Apply presets directly** to context scopes without registry setup
- **Override preset state** with explicit parameters when needed
- **Compose complex configurations** from simpler building blocks
- **Maintain consistency** across different execution contexts

## Basic Usage

### Creating Context Presets

Define presets by combining state objects and disposables:

```python
from haiway import State, ctx
from haiway.context import ContextPreset

class DatabaseConfig(State):
    host: str
    port: int = 5432
    database: str = "app"

class ApiConfig(State):
    base_url: str
    timeout: int = 30
    api_key: str

# Create a preset for development environment
dev_preset = ContextPreset(
    name="development",
    state=[
        DatabaseConfig(host="localhost", database="app_dev"),
        ApiConfig(
            base_url="https://dev-api.example.com",
            timeout=60,
            api_key="dev-key-123"
        )
    ]
)

# Create a preset for production environment
prod_preset = ContextPreset(
    name="production",
    state=[
        DatabaseConfig(host="prod-db.example.com", database="app_prod"),
        ApiConfig(
            base_url="https://api.example.com",
            timeout=30,
            api_key="prod-key-456"
        )
    ]
)
```

### Using Presets Directly

Pass presets directly to `ctx.scope()` instead of string names:

```python
async def run_with_development():
    # Use development preset directly
    async with ctx.scope(dev_preset):
        db_config = ctx.state(DatabaseConfig)
        api_config = ctx.state(ApiConfig)

        print(f"Database: {db_config.host}:{db_config.port}/{db_config.database}")
        print(f"API: {api_config.base_url} (timeout: {api_config.timeout}s)")

async def run_with_production():
    # Use production preset directly
    async with ctx.scope(prod_preset):
        db_config = ctx.state(DatabaseConfig)
        api_config = ctx.state(ApiConfig)

        print(f"Database: {db_config.host}:{db_config.port}/{db_config.database}")
        print(f"API: {api_config.base_url} (timeout: {api_config.timeout}s)")
```

### Overriding Preset State

Override specific state objects from presets with explicit parameters:

```python
async def run_with_custom_timeout():
    # Use development preset but override API config
    async with ctx.scope(
        dev_preset,
        ApiConfig(
            base_url="https://dev-api.example.com",
            timeout=60,
            api_key="dev-key-123"
        )
    ):
        db_config = ctx.state(DatabaseConfig)  # From preset
        api_config = ctx.state(ApiConfig)      # Overridden

        print(f"Database: {db_config.host} (from preset)")
        print(f"API timeout: {api_config.timeout}s (overridden)")
        print(f"API URL: {api_config.base_url} (overridden)")
```

## Advanced Features

### Presets with Disposables

Include disposable resources in presets for complete environment setup:

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def database_connection():
    print("Opening database connection")
    try:
        # Setup database connection
        yield DatabaseConnection(pool=create_connection_pool())

    finally:
        print("Closing database connection")

class DatabaseConnection(State):
    pool: Any

# Preset with both state and disposables
full_dev_preset = ContextPreset(
    name="development_full",
    state=[
        DatabaseConfig(host="localhost", database="app_dev"),
    ],
    disposables=[
        database_connection(),
    ]
)

async def run_with_resources():
    async with ctx.scope(full_dev_preset):
        # State from preset
        db_config = ctx.state(DatabaseConfig)

        # Disposable resources from preset
        db_conn = ctx.state(DatabaseConnection)

        # Use resources
        await perform_database_operation(db_conn.pool)
```

### Nested Presets and Composition

Compose complex configurations by combining presets:

```python
# Base configuration preset
base_preset = ContextPreset(
    name="base",
    state=[
        LoggingConfig(level="INFO", format="json"),
        MetricsConfig(enabled=True, interval=60)
    ]
)

# Database-specific preset
database_preset = ContextPreset(
    name="database",
    state=[
        DatabaseConfig(host="localhost", port=5432),
        ConnectionPoolConfig(min_size=5, max_size=20)
    ]
)

async def run_composed_setup():
    # First apply base configuration
    async with ctx.scope(base_preset):
        # Then add database configuration
        async with ctx.scope(database_preset):
            # Both presets' state is available
            logging_config = ctx.state(LoggingConfig)    # From base_preset
            db_config = ctx.state(DatabaseConfig)        # From database_preset

            print(f"Logging level: {logging_config.level}")
            print(f"Database: {db_config.host}:{db_config.port}")
```

### Dynamic Preset Creation

Create presets dynamically based on runtime conditions:

```python
def create_environment_preset(env: str) -> ContextPreset:
    if env == "development":
        return ContextPreset(
            name=f"dynamic_{env}",
            state=[
                DatabaseConfig(host="localhost", database="app_dev"),
                ApiConfig(base_url="https://dev-api.example.com", timeout=60),
                DebugConfig(enabled=True, verbose=True)
            ]
        )
    elif env == "production":
        return ContextPreset(
            name=f"dynamic_{env}",
            state=[
                DatabaseConfig(host="prod-db.example.com", database="app_prod"),
                ApiConfig(base_url="https://api.example.com", timeout=30),
                DebugConfig(enabled=False, verbose=False)
            ]
        )
    else:
        raise ValueError(f"Unknown environment: {env}")

async def run_dynamic_environment():
    import os
    env = os.getenv("APP_ENV", "development")

    # Create preset based on environment
    env_preset = create_environment_preset(env)

    async with ctx.scope(env_preset):
        db_config = ctx.state(DatabaseConfig)
        debug_config = ctx.state(DebugConfig)

        print(f"Running in {env} mode")
        print(f"Debug enabled: {debug_config.enabled}")
```

## State Priority System

**Priority order (highest to lowest):**
1. **Explicit state** - passed directly to `ctx.scope()`
2. **Explicit disposables** - from `disposables=` parameter
3. **Preset state** - from preset's state and disposables
4. **Contextual state** - inherited from parent contexts

## Preset Registry

Instead of directly providing presets you can use the preset registry approach allowing to resolve presets using scope names:

```python
# Register multiple presets by name
with ctx.presets(dev_preset, prod_preset, staging_preset):
    # Use by name lookup
    async with ctx.scope("development"):  # Matches dev_preset
        db_config = ctx.state(DatabaseConfig)

    async with ctx.scope("production"):   # Matches prod_preset
        db_config = ctx.state(DatabaseConfig)
```

## Best Practices

### 1. Descriptive Preset Names

Use clear, descriptive names that indicate the preset's purpose:

```python
# Good: Clear purpose
api_client_preset = ContextPreset(name="api_client", ...)
database_readonly_preset = ContextPreset(name="database_readonly", ...)

# Avoid: Generic names
config_preset = ContextPreset(name="config", ...)
preset1 = ContextPreset(name="preset1", ...)
```

### 2. Environment-Specific Presets

Create separate presets for different environments:

```python
dev_api_preset = ContextPreset(
    name="api_development",
    state=[ApiConfig(base_url="https://dev-api.example.com", debug=True)]
)

prod_api_preset = ContextPreset(
    name="api_production",
    state=[ApiConfig(base_url="https://api.example.com", debug=False)]
)
```

### 3. Minimal Preset Scope

Keep presets focused on specific concerns:

```python
# Good: Focused on database concerns
db_preset = ContextPreset(
    name="database",
    state=[DatabaseConfig(...), ConnectionPoolConfig(...)]
)

# Good: Focused on API concerns
api_preset = ContextPreset(
    name="api_client",
    state=[ApiConfig(...), RetryConfig(...)]
)

# Avoid: Mixed concerns
everything_preset = ContextPreset(
    name="everything",
    state=[DatabaseConfig(...), ApiConfig(...), LoggingConfig(...)]
)
```

### 4. Validation and Defaults

Ensure preset state objects have sensible defaults:

```python
class DatabaseConfig(State):
    host: str
    port: int = 5432        # Sensible default
    database: str = "app"   # Sensible default
    ssl: bool = True        # Secure by default

# Preset can rely on defaults
minimal_db_preset = ContextPreset(
    name="minimal_database",
    state=[DatabaseConfig(host="localhost")]  # Other fields use defaults
)
```

### 5. Testing with Presets

Create test-specific presets for consistent testing:

```python
test_preset = ContextPreset(
    name="testing",
    state=[
        DatabaseConfig(host="localhost", database="test_db"),
        ApiConfig(base_url="https://mock-api.test", timeout=5),
        LoggingConfig(level="DEBUG")
    ]
)

async def test_user_service():
    async with ctx.scope(test_preset):
        # Test with consistent configuration
        user_service = ctx.state(UserService)
        result = await user_service.fetch_user("test-id")
        assert result is not None
```

## Performance Considerations

- **Preset Creation**: Create presets once and reuse them - avoid creating new preset instances in hot paths
- **State Objects**: Preset state objects are shared (immutable), so there's no memory overhead for reuse
- **Disposables**: Each preset usage creates new disposable instances, so consider the cost of resource creation
- **Nested Contexts**: Deeply nested contexts with many presets may have slight overhead - profile if performance is critical
