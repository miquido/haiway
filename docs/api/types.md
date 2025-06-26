# Types API

The types module provides base type definitions, protocols, and utilities for handling missing values and type safety.

## Missing Values

Haiway provides utilities for handling missing or undefined values in a type-safe manner.

### Missing Type

The `Missing` type represents a value that is explicitly missing or undefined, distinct from `None`.

```python
from haiway.types import Missing, MISSING

# Example usage
def process_value(value: str | Missing = MISSING) -> str:
    if value is MISSING:
        return "No value provided"
    return f"Processing: {value}"

# Usage
result1 = process_value()  # "No value provided"
result2 = process_value("hello")  # "Processing: hello"
```

### Missing vs None

The distinction between `Missing` and `None` is important:

- `None` represents an explicit null value
- `Missing` represents the absence of a value entirely

```python
from haiway.types import Missing, MISSING
from haiway import State

class UserData(State):
    name: str
    email: str | None = None  # Explicit null value
    bio: str | Missing = MISSING  # Missing value

# Examples
user1 = UserData(name="Alice")  # bio is MISSING
user2 = UserData(name="Bob", email=None)  # email is explicitly None
user3 = UserData(name="Charlie", email="charlie@example.com", bio="Developer")
```

### Checking for Missing Values

```python
from haiway.types import Missing, MISSING

def handle_optional_param(param: str | Missing = MISSING) -> str:
    if param is MISSING:
        return "Parameter not provided"
    return f"Parameter value: {param}"

# Type-safe checking
def is_missing(value: any) -> bool:
    return value is MISSING

# Usage in State classes
class Config(State):
    database_url: str | Missing = MISSING
    debug: bool = False
    
    def has_database_url(self) -> bool:
        return self.database_url is not MISSING
```

## Default Values

Utilities for providing default values and handling initialization.

### Default Value Patterns

```python
from haiway import State
from haiway.types import Missing, MISSING

class ServiceConfig(State):
    host: str = "localhost"
    port: int = 8080
    timeout: float = 30.0
    api_key: str | Missing = MISSING
    
    @classmethod
    def production(cls) -> "ServiceConfig":
        """Factory method for production configuration"""
        return cls(
            host="prod.example.com",
            port=443,
            timeout=60.0,
            api_key="prod-api-key"
        )
    
    @classmethod
    def development(cls) -> "ServiceConfig":
        """Factory method for development configuration"""
        return cls(
            host="localhost",
            port=8080,
            timeout=10.0
            # api_key remains MISSING for dev
        )
```

### Optional Defaults with Factory Functions

```python
from typing import Callable
from datetime import datetime
from uuid import uuid4, UUID

class EntityData(State):
    id: UUID
    name: str
    created_at: datetime
    
    @classmethod
    def create(cls, name: str) -> "EntityData":
        """Create new entity with generated defaults"""
        return cls(
            id=uuid4(),
            name=name,
            created_at=datetime.utcnow()
        )

# Usage
entity = EntityData.create("My Entity")
```

## Protocol Definitions

Common protocols used throughout Haiway applications.

### Function Protocols

Following Haiway's pattern of single-method protocols:

```python
from typing import Protocol, runtime_checkable, Any
from collections.abc import Sequence, Mapping

# Basic function protocol
@runtime_checkable
class DataProcessing(Protocol):
    async def __call__(self, data: Any) -> Any: ...

# Parameterized function protocol
@runtime_checkable
class UserFetching(Protocol):
    async def __call__(self, user_id: str) -> UserData | None: ...

# Function with configuration
@runtime_checkable
class DataValidation(Protocol):
    async def __call__(self, data: Any, **kwargs: Any) -> bool: ...
```

### Service Protocols

Protocols for service interfaces:

```python
@runtime_checkable
class CacheAccess(Protocol):
    async def get(self, key: str) -> Any | None: ...
    async def set(self, key: str, value: Any, ttl: int = 300) -> None: ...
    async def delete(self, key: str) -> None: ...

@runtime_checkable
class DatabaseAccess(Protocol):
    async def execute(self, query: str, params: Mapping[str, Any] = {}) -> Sequence[Mapping[str, Any]]: ...
    async def execute_one(self, query: str, params: Mapping[str, Any] = {}) -> Mapping[str, Any] | None: ...
```

## Type Guards

Type guards for safe type checking at runtime.

### Missing Value Guards

```python
from typing import TypeGuard
from haiway.types import Missing, MISSING

def is_missing(value: Any) -> TypeGuard[Missing]:
    """Type guard for Missing values"""
    return value is MISSING

def is_not_missing(value: T | Missing) -> TypeGuard[T]:
    """Type guard for non-Missing values"""
    return value is not MISSING

# Usage
def process_optional_value(value: str | Missing) -> str:
    if is_not_missing(value):
        # TypeScript knows value is str here
        return value.upper()
    return "No value"
```

### State Type Guards

```python
from typing import TypeGuard
from haiway import State

def is_state_instance(obj: Any) -> TypeGuard[State]:
    """Check if object is a State instance"""
    return isinstance(obj, State)

def has_attribute(obj: State, attr: str) -> bool:
    """Check if State has a specific attribute"""
    return hasattr(obj, attr)
```

## Generic Types

Generic type utilities for creating reusable components.

### Generic State Containers

```python
from typing import TypeVar, Generic
from haiway import State

T = TypeVar('T')
K = TypeVar('K')
V = TypeVar('V')

class Container(State, Generic[T]):
    """Generic container for any type"""
    value: T
    metadata: Mapping[str, str] = {}

class KeyValueStore(State, Generic[K, V]):
    """Generic key-value store"""
    data: Mapping[K, V]
    
    def get(self, key: K) -> V | Missing:
        """Get value by key"""
        return self.data.get(key, MISSING)

# Usage
string_container = Container[str](value="hello")
user_container = Container[UserData](value=user_data)

str_int_store = KeyValueStore[str, int](data={"count": 42})
```

### Generic Function Protocols

```python
from typing import TypeVar, Protocol, runtime_checkable

T = TypeVar('T')
R = TypeVar('R')

@runtime_checkable
class Transformer(Protocol, Generic[T, R]):
    """Generic transformation protocol"""
    async def __call__(self, input_data: T) -> R: ...

@runtime_checkable
class Validator(Protocol, Generic[T]):
    """Generic validation protocol"""
    async def __call__(self, data: T) -> bool: ...

# Usage in State
class ProcessingService(State, Generic[T, R]):
    transformer: Transformer[T, R]
    validator: Validator[T]
    
    @classmethod
    async def process(cls, data: T) -> R | None:
        service = ctx.state(cls)
        
        if await service.validator(data):
            return await service.transformer(data)
        return None
```

## Type Utilities

Utility functions for working with types.

### Type Inspection

```python
import inspect
from typing import get_type_hints, get_origin, get_args

def get_state_fields(state_class: type[State]) -> dict[str, type]:
    """Get field names and types from State class"""
    return get_type_hints(state_class)

def is_optional_field(field_type: type) -> bool:
    """Check if field type is optional (Union with None)"""
    origin = get_origin(field_type)
    if origin is not None:
        args = get_args(field_type)
        return type(None) in args
    return False

# Usage
user_fields = get_state_fields(UserData)
# {'name': <class 'str'>, 'email': str | None}

email_is_optional = is_optional_field(user_fields['email'])  # True
```

### Collection Type Helpers

```python
from collections.abc import Sequence, Mapping, Set
from typing import get_origin

def is_sequence_type(type_hint: type) -> bool:
    """Check if type is a Sequence"""
    origin = get_origin(type_hint)
    return origin is not None and issubclass(origin, Sequence)

def is_mapping_type(type_hint: type) -> bool:
    """Check if type is a Mapping"""
    origin = get_origin(type_hint)
    return origin is not None and issubclass(origin, Mapping)

def is_set_type(type_hint: type) -> bool:
    """Check if type is a Set"""
    origin = get_origin(type_hint)
    return origin is not None and issubclass(origin, Set)
```

## Best Practices

### 1. Use Missing for Optional Parameters

```python
from haiway.types import Missing, MISSING

# ✅ Good - distinguishes between None and missing
def create_user(name: str, email: str | None = None, bio: str | Missing = MISSING) -> UserData:
    # email=None means user explicitly has no email
    # bio=MISSING means bio was not provided
    pass

# ❌ Avoid - can't distinguish between explicit None and missing
def create_user_bad(name: str, email: str | None = None, bio: str | None = None) -> UserData:
    pass
```

### 2. Use Type Guards for Safety

```python
from haiway.types import is_not_missing

def process_config(config: Config) -> str:
    if is_not_missing(config.api_key):
        # Type checker knows api_key is str here
        return f"Using API key: {config.api_key[:8]}..."
    return "No API key configured"
```

### 3. Prefer Protocols for Interfaces

```python
# ✅ Good - single method protocol
@runtime_checkable
class UserFetching(Protocol):
    async def __call__(self, user_id: str) -> UserData | None: ...

# ❌ Avoid - multiple methods make protocols complex
class UserService(Protocol):
    async def get_user(self, user_id: str) -> UserData | None: ...
    async def create_user(self, data: UserData) -> UserData: ...
    async def update_user(self, user_id: str, data: UserData) -> UserData: ...
```

### 4. Use Generic Types for Reusability

```python
# ✅ Good - reusable generic container
class Result(State, Generic[T]):
    success: bool
    data: T | None = None
    error: str | None = None

# Usage
user_result = Result[UserData](success=True, data=user)
string_result = Result[str](success=False, error="Not found")
```