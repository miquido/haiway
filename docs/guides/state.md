## State Management

Haiway's state management system is built around the `State` class, which provides immutable, type-safe data structures with validation. Unlike traditional mutable objects, State instances cannot be modified after creation, ensuring predictable behavior, especially in concurrent environments. This guide explains how to effectively use the State class to manage your application's data.

### Defining State Classes

State classes are defined by subclassing the `State` base class and declaring typed attributes:

```python
from haiway import State
from uuid import UUID
from datetime import datetime

class User(State):
    id: UUID
    name: str
    email: str | None = None
    created_at: datetime
```

Key features of State class definitions:

- **Type Annotations**: All attributes must have type annotations
- **Optional Attributes**: Provide default values for optional attributes
- **Immutability**: All instances are immutable once created
- **Validation**: Values are validated against their type annotations at creation time

### Creating State Instances

State instances are created using standard constructor syntax:

```python
from uuid import uuid4
from datetime import datetime

user = User(
    id=uuid4(),
    name="Alice Smith",
    created_at=datetime.now()
)
```

All required attributes must be provided, and all values are validated against their type annotations. If a value fails validation, an exception will be raised.

### Immutability and Updates

State instances are immutable, so you cannot modify them directly:

```python
user.name = "Bob"  # Raises AttributeError
```

Instead, create new instances with updated values using the `updated` method:

```python
updated_user = user.updated(name="Bob Smith")
```

This creates a new instance with the updated value, leaving the original instance unchanged. The `updated` method accepts keyword arguments for any attributes you want to change.

### Path-Based Updates

For nested updates, you can use the path-based `updating` method:

```python
class Address(State):
    street: str
    city: str
    postal_code: str

class Contact(State):
    name: str
    address: Address

# Create an instance
contact = Contact(
    name="Alice",
    address=Address(
        street="123 Main St",
        city="Springfield",
        postal_code="12345"
    )
)

# Update a nested value using path syntax
updated_contact = contact.updating(Contact._.address.city, "New City")
```

The `Class._.attribute` syntax creates an `AttributePath` that can be used to update nested attributes.

### Generic State Classes

State supports generic type parameters, allowing you to create reusable containers:

```python
from typing import Generic, TypeVar

T = TypeVar('T')

class Container(State, Generic[T]):
    value: T

# Create specialized instances
int_container = Container[int](value=42)
str_container = Container[str](value="hello")
```

The type parameter is enforced during validation:

```python
int_container.updated(value="string")  # Raises TypeError
```

### Conversion to Dictionary

You can convert a State instance to a dictionary using the `to_mapping` method:

```python
user_dict = user.to_mapping()
# {"id": UUID('...'), "name": "Alice Smith", "email": None, "created_at": datetime(...)}

# For nested conversion
user_dict = user.to_mapping(recursive=True)
```

This is useful for serialization or when you need to work with plain dictionaries.

### Type Validation

State classes perform thorough type validation for all supported Python types:

- **Basic Types**: int, str, bool, float, bytes
- **Container Types**: 
  - **Sequence[T]**: Use `Sequence[T]` instead of `list[T]` - converted to immutable tuples
  - **Mapping[K, V]**: Use `Mapping[K, V]` instead of `dict[K, V]` - remains as dict
  - **Set[T]**: Use `Set[T]` instead of `set[T]` - converted to immutable frozensets
  - **tuple[T, ...]**: Fixed or variable-length tuples
- **Special Types**: UUID, datetime, date, time, timedelta, timezone, Path, re.Pattern
- **Union Types**: str | None, int | float
- **Literal Types**: Literal["a", "b", "c"]
- **Enum Types**: Standard Enum and StrEnum classes
- **Callable Types**: Function types and Protocol interfaces
- **TypedDict**: Validates structure with Required/NotRequired fields
- **Nested State Classes**: Validates recursively including generic State types
- **Any Type**: Accepts any value without validation

#### Important Typing Requirements

**Always use abstract collection types instead of concrete types:**

```python
# ✅ Correct - Use abstract types
from collections.abc import Sequence, Mapping, Set

class Config(State):
    items: Sequence[str]        # Not list[str]
    data: Mapping[str, int]     # Not dict[str, int] 
    tags: Set[str]              # Not set[str]

# ✅ Lists are converted to tuples (immutable)
config = Config(
    items=["a", "b", "c"],      # Becomes ("a", "b", "c")
    data={"key": 1},            # Remains {"key": 1}
    tags={"tag1", "tag2"}       # Becomes frozenset({"tag1", "tag2"})
)

# ❌ Incorrect - Don't use concrete types
class BadConfig(State):
    items: list[str]            # Will cause validation errors
    data: dict[str, int]        # Will cause validation errors
    tags: set[str]              # Will cause validation errors
```

This requirement ensures immutability and type safety within the State system.

### Best Practices

1. **Use Immutability**: Embrace the immutable nature of State - never try to modify instances.
2. **Make Small States**: Keep State classes focused on a single concern.
3. **Provide Defaults**: Use default values for optional attributes to make creation easier.
4. **Use Type Annotations**: Always provide accurate type annotations for all attributes.
5. **Consistent Updates**: Always use `updated` or `updating` methods for changes.
6. **Composition**: Compose complex states from simpler ones.

### Example: Complex State Management

Here's a more complete example showing complex state management:

```python
from haiway import State
from uuid import UUID, uuid4
from datetime import datetime
from collections.abc import Sequence

class Address(State):
    street: str
    city: str
    country: str = "USA"

class Contact(State):
    email: str
    phone: str | None = None

class User(State):
    id: UUID
    name: str
    address: Address
    contact: Contact
    roles: Sequence[str] = ()
    active: bool = True
    created_at: datetime
    updated_at: datetime

# Create an instance
user = User(
    id=uuid4(),
    name="Alice Smith",
    address=Address(
        street="123 Main St",
        city="Springfield",
    ),
    contact=Contact(
        email="alice@example.com",
    ),
    created_at=datetime.now(),
    updated_at=datetime.now(),
)

# Update a simple attribute
user1 = user.updated(name="Alice Johnson")

# Update a nested attribute
user2 = user.updating(User._.address.city, "New City")

# Update multiple attributes
user3 = user.updated(
    active=False,
    updated_at=datetime.now(),
)

# Update a nested attribute directly
new_address = user.address.updated(street="456 Oak Ave")
user4 = user.updated(address=new_address)

# Chain updates
user5 = user.updated(name="Bob").updating(User._.contact.phone, "555-1234")
```

### Performance Considerations

While State instances are immutable, creating new instances for updates has minimal overhead as only the changed paths are reconstructed. The validation system is optimized to be fast for typical use cases.

For high-performance scenarios:
- Keep State classes relatively small and focused
- Consider using path-based updates for nested changes
- If needed, batch multiple updates into a single `updated` call

### Integration with Haiway Context

State classes are designed to work seamlessly with Haiway's context system:

```python
from haiway import ctx, State

class AppConfig(State):
    debug: bool = False
    log_level: str = "INFO"

async def main():
    # Provide state to context
    async with ctx.scope("main", AppConfig(debug=True)):
        # Access state from context
        config = ctx.state(AppConfig)
        
        # Create updated state in nested context
        async with ctx.scope("debug", config.updated(log_level="DEBUG")):
            # Use updated state
            debug_config = ctx.state(AppConfig)
            assert debug_config.log_level == "DEBUG"
```

This pattern enables effective dependency injection and state propagation throughout your application.