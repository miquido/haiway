# State Management

Haiway's state management system is built around immutable data structures that provide type safety, validation, and predictable behavior in concurrent environments.

## Core Concepts

### Immutability by Default

All state objects in Haiway are immutable once created:

```python
from haiway import State

class UserProfile(State):
    name: str
    age: int
    email: str

profile = UserProfile(name="Alice", age=30, email="alice@example.com")

# This would raise an error:
# profile.name = "Bob"  # AttributeError!

# Instead, create new instances:
updated_profile = profile.updated(name="Alice Smith")
```

### Collection Types

**Always use abstract collection types** to ensure immutability:

```python
from typing import Sequence, Mapping, Set
from haiway import State

# ✅ Correct - use abstract types for immutability
class UserData(State):
    roles: Sequence[str]        # Lists become tuples (immutable)
    metadata: Mapping[str, Any] # Dicts remain dicts but interface is immutable
    tags: Set[str]              # Sets become frozensets (immutable)

# ❌ Incorrect - concrete types cause validation errors
class BadUserData(State):
    roles: list[str]           # Will cause validation error
    metadata: dict[str, Any]   # Will cause validation error
    tags: set[str]             # Will cause validation error
```

## State Definition Patterns

### Basic State Classes

```python
from haiway import State

class Configuration(State):
    database_url: str
    debug: bool = False
    max_connections: int = 10
    
class UserPreferences(State):
    theme: str = "light"
    language: str = "en"
    notifications_enabled: bool = True
```

### Generic State Classes

```python
from typing import TypeVar, Generic
from haiway import State

T = TypeVar('T')

class Container(State, Generic[T]):
    items: Sequence[T]
    count: int
    
    def __post_init__(self):
        # Validation can be added here
        assert len(self.items) == self.count

# Usage
numbers = Container[int](items=(1, 2, 3), count=3)
names = Container[str](items=("Alice", "Bob"), count=2)
```

### Nested State Structures

```python
class Address(State):
    street: str
    city: str
    country: str
    postal_code: str

class User(State):
    id: str
    name: str
    email: str
    address: Address | None = None
    preferences: UserPreferences = UserPreferences()

# Creating nested structures
user = User(
    id="123",
    name="Alice",
    email="alice@example.com",
    address=Address(
        street="123 Main St",
        city="New York",
        country="USA",
        postal_code="10001"
    )
)
```

## State Updates

### Simple Updates

```python
user = User(id="1", name="Alice", email="alice@example.com")

# Update single field
updated_user = user.updated(name="Alice Smith")

# Update multiple fields
updated_user = user.updated(
    name="Alice Smith",
    email="alice.smith@example.com"
)
```

### Nested Updates

```python
user_with_address = User(
    id="1",
    name="Alice",
    email="alice@example.com",
    address=Address(
        street="123 Main St",
        city="New York", 
        country="USA",
        postal_code="10001"
    )
)

# Update nested address
updated_user = user_with_address.updated(
    address=user_with_address.address.updated(city="Los Angeles")
)
```

### Collection Updates

```python
class TodoList(State):
    items: Sequence[str]
    completed: Set[str] = frozenset()

todo_list = TodoList(items=("Buy milk", "Walk dog", "Write code"))

# Add completed item
updated_list = todo_list.updated(
    completed=todo_list.completed | {"Buy milk"}
)

# Add new item
updated_list = updated_list.updated(
    items=updated_list.items + ("Review code",)
)
```

## Validation

### Built-in Validation

```python
from typing import Annotated
from haiway import State

class User(State):
    name: str
    age: int
    email: str
    
    def __post_init__(self):
        if self.age < 0:
            raise ValueError("Age cannot be negative")
        if "@" not in self.email:
            raise ValueError("Invalid email format")

# This will raise ValueError
# user = User(name="Alice", age=-5, email="invalid")
```

### Custom Validators

```python
import re
from haiway import State

def validate_email(email: str) -> str:
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    if not re.match(pattern, email):
        raise ValueError(f"Invalid email: {email}")
    return email

class User(State):
    name: str
    email: str
    
    def __post_init__(self):
        object.__setattr__(self, 'email', validate_email(self.email))
```

## Path-Based Updates

For complex nested structures, Haiway provides path-based updating:

```python
class Address(State):
    street: str
    city: str
    country: str = "USA"

class Contact(State):
    email: str
    phone: str | None = None

class User(State):
    id: str
    name: str
    address: Address
    contact: Contact
    active: bool = True

# Create a user
user = User(
    id="123",
    name="Alice Smith",
    address=Address(street="123 Main St", city="Springfield"),
    contact=Contact(email="alice@example.com")
)

# Update nested value using path syntax
updated_user = user.updating(User._.address.city, "New City")
updated_user = updated_user.updating(User._.contact.phone, "555-1234")

# The original user remains unchanged
assert user.address.city == "Springfield"
assert updated_user.address.city == "New City"
```

### Complex State Transformations

```python
from datetime import datetime
from uuid import UUID, uuid4

class UserProfile(State):
    name: str
    bio: str | None = None
    last_updated: datetime

class UserAccount(State):
    id: UUID
    email: str
    profile: UserProfile
    created_at: datetime
    updated_at: datetime

# Create factory methods for complex operations
@classmethod
def update_profile(cls, user: UserAccount, name: str | None = None, bio: str | None = None) -> UserAccount:
    """Update user profile and timestamps"""
    now = datetime.utcnow()
    
    profile_updates = {}
    if name is not None:
        profile_updates['name'] = name
    if bio is not None:
        profile_updates['bio'] = bio
    
    if profile_updates:
        profile_updates['last_updated'] = now
        updated_profile = user.profile.updated(**profile_updates)
        return user.updated(profile=updated_profile, updated_at=now)
    
    return user

# Usage
user = UserAccount(
    id=uuid4(),
    email="alice@example.com",
    profile=UserProfile(name="Alice", last_updated=datetime.utcnow()),
    created_at=datetime.utcnow(),
    updated_at=datetime.utcnow()
)

updated_user = UserAccount.update_profile(user, name="Alice Smith", bio="Software Developer")
```

## State Validation Patterns

### Runtime Type Validation

Haiway performs comprehensive runtime type validation for all supported Python types:

```python
from typing import Literal, Union
from enum import StrEnum
from collections.abc import Sequence, Mapping, Set

class Priority(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"

class TaskData(State):
    title: str
    description: str
    priority: Priority = Priority.MEDIUM
    tags: Sequence[str] = ()
    metadata: Mapping[str, str] = {}
    assignees: Set[str] = frozenset()
    status: Literal["pending", "in_progress", "completed"] = "pending"

# Valid creation
task = TaskData(
    title="Complete documentation",
    description="Write comprehensive docs",
    priority=Priority.HIGH,
    tags=["docs", "important"],  # List becomes tuple
    metadata={"project": "haiway"},  # Dict stays dict (immutable interface)
    assignees={"alice", "bob"},  # Set becomes frozenset
    status="in_progress"
)

# These would raise validation errors:
# TaskData(title=123)  # title must be str
# TaskData(title="test", priority="invalid")  # priority must be Priority enum
# TaskData(title="test", status="invalid")  # status must be literal value
```

### Custom Validation

```python
import re
from datetime import datetime, date

class EmailUser(State):
    name: str
    email: str
    birth_date: date
    registration_date: datetime
    
    def __post_init__(self):
        # Validate email format
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.match(email_pattern, self.email):
            raise ValueError(f"Invalid email format: {self.email}")
        
        # Validate age (must be at least 13)
        today = date.today()
        age = today.year - self.birth_date.year
        if today.month < self.birth_date.month or (today.month == self.birth_date.month and today.day < self.birth_date.day):
            age -= 1
        
        if age < 13:
            raise ValueError("User must be at least 13 years old")
        
        # Validate registration date is not in the future
        if self.registration_date.date() > today:
            raise ValueError("Registration date cannot be in the future")

# Valid user
user = EmailUser(
    name="Alice Smith",
    email="alice@example.com",
    birth_date=date(1990, 1, 1),
    registration_date=datetime.utcnow()
)

# These would raise validation errors:
# EmailUser(name="Bob", email="invalid-email", birth_date=date(1990, 1, 1), registration_date=datetime.utcnow())
# EmailUser(name="Charlie", email="charlie@example.com", birth_date=date(2020, 1, 1), registration_date=datetime.utcnow())
```

### Conditional Validation

```python
from typing import Optional

class ConditionalUser(State):
    name: str
    email: str
    is_premium: bool = False
    premium_features: Sequence[str] = ()
    subscription_id: str | None = None
    
    def __post_init__(self):
        if self.is_premium:
            # Premium users must have subscription ID
            if not self.subscription_id:
                raise ValueError("Premium users must have a subscription ID")
            
            # Premium users can have premium features
            if not self.premium_features:
                raise ValueError("Premium users should have at least one premium feature")
        else:
            # Non-premium users cannot have premium features
            if self.premium_features:
                raise ValueError("Non-premium users cannot have premium features")
            
            # Non-premium users should not have subscription ID
            if self.subscription_id:
                raise ValueError("Non-premium users should not have subscription ID")

# Valid premium user
premium_user = ConditionalUser(
    name="Alice",
    email="alice@example.com",
    is_premium=True,
    premium_features=["advanced_analytics", "priority_support"],
    subscription_id="sub_123"
)

# Valid free user
free_user = ConditionalUser(
    name="Bob",
    email="bob@example.com",
    is_premium=False
)
```

## Advanced Patterns

### State Composition

```python
class DatabaseConfig(State):
    host: str
    port: int
    username: str
    password: str

class CacheConfig(State):
    redis_url: str
    ttl: int = 3600

class APIConfig(State):
    rate_limit: int = 1000
    timeout: int = 30

class ApplicationConfig(State):
    database: DatabaseConfig
    cache: CacheConfig
    api: APIConfig
    debug: bool = False
```

### State with Computed Properties

```python
class Rectangle(State):
    width: float
    height: float
    
    @property
    def area(self) -> float:
        return self.width * self.height
    
    @property
    def perimeter(self) -> float:
        return 2 * (self.width + self.height)

rect = Rectangle(width=10, height=5)
print(f"Area: {rect.area}")  # Area: 50
print(f"Perimeter: {rect.perimeter}")  # Perimeter: 30
```

### State Factories

```python
from datetime import datetime
from typing import Optional

class User(State):
    id: str
    name: str
    email: str
    created_at: datetime
    updated_at: Optional[datetime] = None
    
    @classmethod
    def create(cls, name: str, email: str) -> "User":
        """Factory method to create a new user"""
        import uuid
        return cls(
            id=str(uuid.uuid4()),
            name=name,
            email=email,
            created_at=datetime.utcnow()
        )
    
    def touch(self) -> "User":
        """Update the updated_at timestamp"""
        return self.updated(updated_at=datetime.utcnow())

# Usage
user = User.create(name="Alice", email="alice@example.com")
updated_user = user.touch()
```

## Performance Considerations

### Memory Efficiency

Haiway's immutable structures share memory where possible:

```python
class LargeDataset(State):
    values: Sequence[int]
    metadata: Mapping[str, str]

# Original dataset
original = LargeDataset(
    values=tuple(range(1000000)),
    metadata={"source": "api", "version": "1.0"}
)

# Updated version shares the values sequence
updated = original.updated(
    metadata=original.metadata | {"processed": "true"}
)
# Values sequence is shared between original and updated
```

### Efficient Updates

```python
# ✅ Efficient - single update call
user = user.updated(
    name="New Name",
    email="new@example.com",
    preferences=user.preferences.updated(theme="dark")
)

# ❌ Less efficient - multiple intermediate objects
user = user.updated(name="New Name")
user = user.updated(email="new@example.com")  
user = user.updated(preferences=user.preferences.updated(theme="dark"))
```

## Integration with Context

State objects work seamlessly with Haiway's context system:

```python
from haiway import ctx

class DatabaseState(State):
    connection_string: str
    pool_size: int = 10

async def main():
    db_state = DatabaseState(
        connection_string="postgresql://localhost/mydb",
        pool_size=20
    )
    
    async with ctx.scope("app", db_state):
        # Access state from context
        db = ctx.state(DatabaseState)
        print(f"Using database: {db.connection_string}")
```

## Best Practices

1. **Use Abstract Types**: Always use `Sequence`, `Mapping`, `Set` for collections
2. **Validate Early**: Add validation in `__post_init__` method
3. **Keep States Small**: Prefer composition over large monolithic states
4. **Use Factory Methods**: Create convenient constructors for complex states
5. **Document State Contracts**: Use type hints and docstrings extensively

## Testing State Objects

```python
import pytest
from haiway import State

class Counter(State):
    value: int = 0
    
    def increment(self) -> "Counter":
        return self.updated(value=self.value + 1)

def test_counter_increment():
    counter = Counter()
    assert counter.value == 0
    
    incremented = counter.increment()
    assert incremented.value == 1
    assert counter.value == 0  # Original unchanged

def test_counter_immutability():
    counter = Counter(value=5)
    
    with pytest.raises(AttributeError):
        counter.value = 10  # Should raise error
```

## Next Steps

- Learn about the [Context System](context-system.md) for dependency injection
- Explore [Async Patterns](async-patterns.md) for concurrent programming
- See [Testing](testing.md) strategies for state-based applications