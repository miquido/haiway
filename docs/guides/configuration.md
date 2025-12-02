# Configuration Management

Haiway provides a simple configuration system built on top of the State class. Configuration classes
are immutable, type-safe, and can be loaded from various storage backends.

## Basic Usage

### Defining Configuration Classes

Create configuration classes by inheriting from `Configuration`:

```python
from haiway.helpers import Configuration

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

# Required loading - tries contextual state then class defaults if not found in repository
config = await DatabaseConfig.load(required=True)

# Loading with explicit default
config = await DatabaseConfig.load(default=DatabaseConfig(
    database="myapp", username="admin", password="secret"
))

# Custom identifier
config = await DatabaseConfig.load(identifier="production_db")
```

### Contextual Fallback (no implicit defaults)

When using `required=True`, the system first looks for a contextual instance bound via
`ctx.scope(..., config_instance)`. This allows you to override repository values for the current
scope (handy in tests or temporary overrides). If no contextual instance is available and nothing is
found in the repository, `ConfigurationMissing` is raised—classes are **not** auto-instantiated with
defaults.

```python
from haiway import ctx

class ServerConfig(Configuration):
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4

# Uses contextual override when present
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
from haiway.helpers import ConfigurationRepository
from haiway import ctx

# Create repository with configurations
db_config = DatabaseConfig(
    database="myapp", username="admin", password="secret"
)

repo = ConfigurationRepository.volatile(db_config)

async with ctx.scope("app", repo):
    config = await DatabaseConfig.load(required=True)
```

### Custom Storage Backend

Implement storage protocols for persistent configuration:

```python
import json
from pathlib import Path

class FileStorage:
    def __init__(self, config_dir: Path):
        self.config_dir = config_dir

    async def load_config(self, identifier: str, **extra):
        config_file = self.config_dir / f"{identifier}.json"
        if config_file.exists():
            return json.loads(config_file.read_text())
        return None

    async def define_config(self, identifier: str, value, **extra):
        self.config_dir.mkdir(parents=True, exist_ok=True)
        config_file = self.config_dir / f"{identifier}.json"
        config_file.write_text(json.dumps(value, indent=2))

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
from haiway.helpers import ConfigurationMissing, ConfigurationInvalid

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

    # Remove configuration
    await ConfigurationRepository.remove(DatabaseConfig)

    # List available configurations
    available = await ConfigurationRepository.configurations()
```

That's it! The configuration system is designed to be simple and integrate seamlessly with Haiway's
context and state management.
