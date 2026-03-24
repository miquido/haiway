# Configuration Management

Haiway provides a simple configuration system built on top of the State class. Configuration classes
are immutable, type-safe, and can be loaded from various storage backends.

For process environment access, Haiway also exposes standalone helpers such as `load_env()`,
`getenv_str()`, `getenv_int()`, and `getenv_bool()`. Those functions are not part of the
`Configuration` repository model, but they are commonly used during bootstrap to assemble initial
settings or defaults.

## Environment Bootstrap

For lightweight startup configuration, load a `.env` file and parse individual values directly from
the environment:

```python
from haiway import getenv_bool, getenv_int, getenv_str, load_env

load_env()

app_env = getenv_str("APP_ENV", "development")
port = getenv_int("APP_PORT", 8080)
debug = getenv_bool("DEBUG", False)
database_url = getenv_str("DATABASE_URL", required=True)
```

Notes:

- `load_env()` silently skips missing files.
- It only supports simple `KEY=VALUE` lines and comments starting with `#`.
- `getenv_bool()` treats only `"true"`, `"1"`, and `"t"` as `True`; any other present value is
  `False`.
- Use `getenv()` when you need a custom parser function.

## Basic Usage

### Defining Configuration Classes

Create configuration classes by inheriting from `Configuration`:

```python
from haiway import Configuration

class DatabaseConfig(Configuration):
    host: str = "localhost"
    port: int = 5432
    database: str
    username: str
    password: str
    timeout: float = 30.0
```

All attributes must have type annotations. Provide default values where appropriate.

### Loading Configurations

```python
# Optional loading - returns None if not found
config = await DatabaseConfig.load()

# Required loading - repository first, then ctx.state(DatabaseConfig)
config = await DatabaseConfig.load(required=True)

# Loading with explicit default
config = await DatabaseConfig.load(default=DatabaseConfig(
    database="myapp", username="admin", password="secret"
))

# Custom identifier
config = await DatabaseConfig.load(identifier="production_db")
```

### Contextual and Class-Default Fallback

When using `required=True`, Haiway first asks the current `ConfigurationRepository` for the
configuration. Only when the repository returns no value does it fall back to `ctx.state(...)`.

That fallback matters because `ctx.state(ConfigType)` behaves in two useful ways:

- it returns a contextual instance already bound in the current scope, if one exists
- otherwise it lazily creates a default instance when the state type can be constructed with no
  arguments

This means configuration classes with fully defaulted fields can still load successfully even when
the repository has no stored value. `ConfigurationMissing` is raised only when the repository misses
and `ctx.state(...)` cannot supply an instance.

```python
from haiway import Configuration, ConfigurationMissing, ctx

class ServerConfig(Configuration):
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4

# Uses contextual override when repository misses
async with ctx.scope("server", ServerConfig(port=9001)):
    config = await ServerConfig.load(required=True)
    assert config.port == 9001

try:
    await ServerConfig.load(required=True)
except ConfigurationMissing:
    ...
```

## Storage and Repository

### Volatile Repository (In-Memory)

For testing and development:

```python
from haiway import ConfigurationRepository, ctx

# Create repository with configurations
db_config = DatabaseConfig(
    database="myapp", username="admin", password="secret"
)

repo = ConfigurationRepository.volatile(db_config)

async with ctx.scope("app", repo):
    config = await DatabaseConfig.load(required=True)
```

`ConfigurationRepository.volatile(...)` stores configuration instances in memory and keys unnamed
entries by `type(config).__qualname__`.

### Custom Storage Backend

Implement storage protocols for persistent configuration:

```python
import json
from pathlib import Path

from haiway import asynchronous


@asynchronous
def _path_exists(path: Path) -> bool:
    return path.exists()


@asynchronous
def _read_text(path: Path) -> str:
    return path.read_text()


@asynchronous
def _mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


@asynchronous
def _write_text(path: Path, content: str) -> None:
    path.write_text(content)


class FileStorage:
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir

    async def load_config(self, identifier: str, **extra):
        config_file = self.config_dir / f"{identifier}.json"
        if await _path_exists(config_file):
            content = await _read_text(config_file)
            return json.loads(content)
        return None

    async def define_config(self, identifier: str, value, **extra):
        await _mkdir(self.config_dir)
        config_file = self.config_dir / f"{identifier}.json"
        content = json.dumps(value, indent=2)
        await _write_text(config_file, content)

# Use custom storage
storage = FileStorage(Path("./configs"))
repo = ConfigurationRepository(
    loading=storage.load_config,
    defining=storage.define_config
)

async with ctx.scope("app", repo):
    config = await DatabaseConfig.load(required=True)
```

## Error Handling

```python
from haiway import ConfigurationInvalid, ConfigurationMissing

try:
    config = await DatabaseConfig.load(required=True)
except ConfigurationMissing as exc:
    print(f"Configuration '{exc.identifier}' not found")
except ConfigurationInvalid as exc:
    print(f"Invalid configuration: {exc.reason}")
```

## Repository Operations

```python
async with ctx.scope("app", repo):
    # Load configuration
    config = await ConfigurationRepository.load(DatabaseConfig)

    # Store configuration
    await ConfigurationRepository.define(config)
    await ConfigurationRepository.define(
        "production_db",
        DatabaseConfig(
            database="myapp",
            username="admin",
            password="secret",
        ),
    )

    # Remove configuration
    await ConfigurationRepository.remove(DatabaseConfig)
    await ConfigurationRepository.remove("production_db")

    # List available configurations
    available = await ConfigurationRepository.configurations()
    only_databases = await ConfigurationRepository.configurations(DatabaseConfig)
```

That's it! The configuration system is designed to be simple and integrate seamlessly with Haiway's
context and state management.
