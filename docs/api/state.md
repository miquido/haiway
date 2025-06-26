# State API

The state module provides immutable data structures with validation and type safety.

## State Base Class

The `State` class is the foundation for all immutable data structures in Haiway.

### Class Definition

```python
class State:
    """
    Base class for immutable, type-safe data structures.
    
    All State classes automatically become immutable after creation,
    with full runtime type validation and collection type conversion.
    """
```

### Key Methods

#### `updated(**kwargs) -> Self`

Create a new instance with updated field values.

**Parameters:**
- `**kwargs` - Field names and their new values

**Returns:**
- New State instance with updated values

**Example:**
```python
from haiway import State

class User(State):
    name: str
    age: int

user = User(name="Alice", age=30)
updated_user = user.updated(age=31)
# user.age is still 30, updated_user.age is 31
```

#### `updating(path, value) -> Self`

Update a nested field using path-based access.

**Parameters:**
- `path` - Attribute path (e.g., `User._.address.city`)
- `value` - New value for the field

**Returns:**
- New State instance with updated nested value

**Example:**
```python
class Address(State):
    city: str
    country: str

class User(State):
    name: str
    address: Address

user = User(
    name="Alice",
    address=Address(city="New York", country="USA")
)

# Update nested field
updated_user = user.updating(User._.address.city, "San Francisco")
```

#### `to_mapping(recursive: bool = False) -> dict`

Convert State instance to dictionary.

**Parameters:**
- `recursive: bool` - Whether to recursively convert nested State objects

**Returns:**
- Dictionary representation of the State

**Example:**
```python
user = User(name="Alice", age=30)
user_dict = user.to_mapping()
# {"name": "Alice", "age": 30}
```

## Type Validation

State classes perform comprehensive type validation for all supported Python types:

### Basic Types
- `int`, `str`, `bool`, `float`, `bytes`, `None`

### Collection Types
```python
from collections.abc import Sequence, Mapping, Set

class DataContainer(State):
    items: Sequence[str]        # Lists converted to tuples
    lookup: Mapping[str, int]   # Dicts stay as dicts (immutable interface)
    tags: Set[str]              # Sets converted to frozensets
```

### Special Types
- `UUID` - Universally unique identifiers
- `datetime`, `date`, `time`, `timedelta`, `timezone` - Date/time objects
- `pathlib.Path` - File system paths
- `re.Pattern` - Compiled regular expressions

### Union Types
```python
class OptionalData(State):
    value: str | None = None
    number: int | float = 0
```

### Literal Types
```python
from typing import Literal

class Config(State):
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
```

### Enum Types
```python
from enum import Enum, StrEnum

class Status(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"

class UserStatus(State):
    status: Status = Status.ACTIVE
```

### Callable Types
```python
from typing import Protocol, runtime_checkable, Callable

@runtime_checkable
class DataProcessor(Protocol):
    async def __call__(self, data: str) -> str: ...

class ProcessingService(State):
    processor: DataProcessor
    simple_func: Callable[[str], str]
```

### TypedDict Support
```python
from typing import TypedDict, Required, NotRequired

class UserDict(TypedDict):
    name: Required[str]
    email: Required[str]
    age: NotRequired[int]

class UserContainer(State):
    data: UserDict
```

### Nested State Classes
```python
class Address(State):
    street: str
    city: str

class User(State):
    name: str
    address: Address  # Validates recursively
```

### Generic State Classes
```python
from typing import TypeVar, Generic

T = TypeVar('T')

class Container(State, Generic[T]):
    value: T
    count: int

# Usage
int_container = Container[int](value=42, count=1)
str_container = Container[str](value="hello", count=1)
```

## Validation Rules

### Collection Type Requirements

**Critical Rule**: Always use abstract collection types in State classes:

```python
from collections.abc import Sequence, Mapping, Set

class CorrectState(State):
    # ✅ Correct - use abstract types
    items: Sequence[str]        # list → tuple (immutable)
    data: Mapping[str, int]     # dict → dict (immutable interface)
    tags: Set[str]              # set → frozenset (immutable)

class IncorrectState(State):
    # ❌ These will cause validation errors
    items: list[str]            # ValidationError!
    data: dict[str, int]        # ValidationError!
    tags: set[str]              # ValidationError!
```

### Custom Validation

Add custom validation in `__post_init__`:

```python
class User(State):
    name: str
    age: int
    email: str
    
    def __post_init__(self):
        if self.age < 0:
            raise ValueError("Age cannot be negative")
        if "@" not in self.email:
            raise ValueError("Invalid email format")
```

### Validation Errors

State validation can raise several types of errors:

- `TypeError` - Wrong type provided
- `ValueError` - Invalid value for the type
- `ValidationError` - Custom validation failure

## Best Practices

### 1. Use Abstract Collection Types
Always use `Sequence`, `Mapping`, `Set` instead of concrete types.

### 2. Provide Sensible Defaults
```python
class Config(State):
    debug: bool = False
    max_connections: int = 10
    allowed_hosts: Sequence[str] = ()
```

### 3. Keep States Focused
Create small, focused State classes for single concerns:
```python
# Good - focused states
class UserIdentity(State):
    id: str
    email: str

class UserProfile(State):
    name: str
    bio: str | None = None

# Better than one large User state with everything
```

### 4. Use Factory Methods
```python
class User(State):
    id: str
    name: str
    created_at: datetime
    
    @classmethod
    def create(cls, name: str) -> "User":
        from uuid import uuid4
        from datetime import datetime
        
        return cls(
            id=str(uuid4()),
            name=name,
            created_at=datetime.utcnow()
        )
```

### 5. Compose Complex States
```python
class UserAccount(State):
    identity: UserIdentity
    profile: UserProfile
    preferences: UserPreferences
```