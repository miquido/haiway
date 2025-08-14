# Configuration Management

Haiway provides a simple configuration system built on top of the State class. Configuration classes are immutable, type-safe, and can be loaded from various storage backends.

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

# Required loading - tries class defaults if not found in repository
config = await DatabaseConfig.load(required=True)

# Loading with explicit default
config = await DatabaseConfig.load(default=DatabaseConfig(
    database="myapp", username="admin", password="secret"
))

# Custom identifier
config = await DatabaseConfig.load(identifier="production_db")
```

### Automatic Default Fallback

When using `required=True`, the system will automatically try to create a configuration instance using class defaults if no data is found in the repository:

```python
class ServerConfig(Configuration):
    host: str = "0.0.0.0"
    port: int = 8000
    workers: int = 4
    
# This succeeds even if no configuration is stored,
# using the default values from the class
config = await ServerConfig.load(required=True)
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
    available = await ConfigurationRepository.available_configurations()
```

That's it! The configuration system is designed to be simple and integrate seamlessly with Haiway's context and state management.