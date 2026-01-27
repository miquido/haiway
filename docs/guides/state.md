# State Management

Haiway's state management system is built around the `State` class, which provides immutable,
type-safe data structures with validation. Unlike traditional mutable objects, State instances
cannot be modified after creation, ensuring predictable behavior, especially in concurrent
environments. This guide explains how to effectively use the State class to manage your
application's data.

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
- **Metadata Support**: Attach aliases, descriptions, specifications, and validators via
  `typing.Annotated`

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

All required attributes must be provided, and all values are validated against their type
annotations. If a value fails validation, an exception will be raised.

### Default Values and Lazy Initialization

Use the `Default` helper when a field needs to be computed lazily or resolved from the environment.
`Default` returns a `DefaultValue` container that the State runtime unwraps when the field is
accessed:

```python
from uuid import uuid4
from haiway import Default, State

class ServiceConfig(State):
    correlation_id: str = Default(default_factory=lambda: uuid4().hex)
    timeout_seconds: float = Default(1.5)  # literal defaults still work
    api_key: str | None = Default(env="SERVICE_API_KEY")  # read from environment when needed

config = ServiceConfig()
assert isinstance(config.correlation_id, str)
```

Factories are called every time a new instance is created, so each `ServiceConfig` receives its own
identifier. Environment defaults are resolved on demand, which keeps tests deterministic—set the
environment variable or pass an explicit value when constructing the state.

### Immutability and Updates

State instances are immutable, so you cannot modify them directly:

```python
user.name = "Bob"  # Raises AttributeError
```

Instead, create new instances with updated values using the `updating` method:

```python
updated_user = user.updating(name="Bob Smith")
```

This creates a new instance with the updated value, leaving the original instance unchanged. The
`updating` method accepts keyword arguments for any attributes you want to change.

### Attribute Metadata with `typing.Annotated`

Haiway uses `typing.Annotated` to attach field-level metadata that controls how attributes are
validated, exposed, and documented. Combine your base type with one or more annotations:

```python
from typing import Annotated
from haiway import Alias, Description, Specification, State, Validator

def ensure_positive(value: int) -> int:
    if value <= 0:
        raise ValueError("value must be positive")
    return value

class Invoice(State):
    # Externally exposed as "customer_id" when encoding/decoding mappings or JSON.
    customer: Annotated[str, Alias("customer_id"), Description("Public customer identifier")]
    total_cents: Annotated[int, Specification({"type": "integer", "minimum": 0}), Validator(ensure_positive)]
    notes: Annotated[str | None, Description("Free-form note about the invoice")] = None
```

Supported annotations include:

- `Alias("external_name")` — maps the attribute to an alternate key when using `to_mapping`,
  `from_mapping`, JSON helpers, and `updating`.
- `Description("text")` — surfaces in generated JSON schemas and downstream documentation.
- `Meta.of({...})` — attaches structured metadata that you can later inspect from field definitions.
- `Specification({...})` — overrides the JSON Schema fragment when the inferred schema is
  insufficient.
- `Validator(callable)` — applies additional validation logic after type checking succeeds.

Attributes annotated as `typing.NotRequired[T]` are treated as optional even without a default. This
is useful when mirroring typed dictionaries or validating payloads where the field may be omitted.

### Structured Metadata with `Meta`

Use the `Meta` container when you need immutable, JSON-compatible metadata on either a field itself
or on the annotations that describe it. `Meta` instances validate every value, expose convenience
accessors (such as `.kind`, `.tags`, `.has_tags(...)`, `.with_identifier(...)`), and are exported
directly from `haiway`.

```python
from typing import Annotated
from haiway import Description, Meta, State

class Dataset(State):
    meta: Meta = Meta.of(kind="dataset", tags=("exports",))
    export_path: Annotated[
        str,
        Description("S3 key for the generated export"),
        Meta.of(tags=("pii", "s3")),
    ]
```

`Meta.of(...)` accepts existing mappings or keyword arguments, and you can combine builders—for
example, `Meta.of(kind="dataset").with_last_updated(timestamp)` returns a new instance with the
timestamp recorded. When no metadata is provided, Haiway uses the singleton `META_EMPTY`, so you can
always compare with identity checks.

To inspect metadata added through annotations, read it from the resolved attribute definition:

```python
fields = Dataset.__SELF_ATTRIBUTE__.attributes
path_meta = fields["export_path"].meta
assert path_meta.has_tags(("pii",))
```

Metadata values are limited to strings, numbers, booleans, `None`, sequences, and nested mappings.
Passing unsupported objects raises a `TypeError`, which keeps emitted schemas and observability
events consistent.

### Generic State Classes

State supports generic type parameters, allowing you to create reusable containers:

```python
from typing import TypeVar
from haiway import State

T = TypeVar('T')

class Container[T](State):
    value: T

# Create specialized instances
int_container = Container[int](value=42)
str_container = Container[str](value="hello")
```

The type parameter is enforced during validation:

```python
int_container.updating(value="string")  # Raises TypeError
```

### Conversion to Mappings and JSON

You can convert a State instance to a dictionary using the `to_mapping` method:

```python
user_dict = user.to_mapping()
# {"id": UUID('...'), "name": "Alice Smith", "email": None, "created_at": datetime(...)}

# For nested conversion
user_dict = user.to_mapping(recursive=True)
```

This is useful for serialization or when you need to work with plain dictionaries.

When working with external payloads, use the complementary helpers:

- `State.from_mapping(mapping)` — accepts both attribute names and aliases.
- `State.from_json(payload)` / `State.from_json_array(payload)` — decode JSON strings into State
  instances (or tuples of instances). Validation errors are converted into informative exceptions.
- `state.to_json(indent=2)` — encode a State instance to JSON using the same alias behaviour as
  `to_mapping`.
- `State.validate(value)` — coerce an instance or compatible mapping into the target State type.
  This is handy when accepting heterogeneous inputs.

Aliases apply consistently across these helpers. For example, the `Invoice.customer` field above
accepts `customer` or `customer_id` when instantiating, and `Invoice.to_mapping(aliased=True)` emits
`{"customer_id": "...", ...}`.

### Type Validation

State classes perform thorough type validation for all supported Python types:

- **Basic Types**: int, str, bool, float, bytes
- **Container Types**:
  - **Sequence[T]**: Use `Sequence[T]` instead of `list[T]` — are converted to immutable tuples
  - **Mapping[K, V]**: Use `Mapping[K, V]` instead of `dict[K, V]` — remains a dict
  - **Set[T]**: Use `Set[T]` instead of `set[T]` — are converted to immutable frozensets
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

This requirement preserves type safety and predictable behavior. Sequences and sets are wrapped in
immutable containers, while mappings stay as plain dicts—treat them as read-only to avoid accidental
mutation.

### JSON Schema Generation

Every State exposes a JSON Schema fragment through the `__SPECIFICATION__` attribute. Call
`State.json_schema(indent=2)` when you need a ready-to-serialize schema definition:

```python
schema = Invoice.json_schema(indent=2)
```

Aliases and descriptions propagate into the schema automatically, and any custom `Specification`
overrides are respected. If a State cannot be represented as JSON Schema (for example, when it holds
callables), `json_schema(required=True)` raises a `TypeError` so you can detect unsupported shapes
early.

When serialization must be guaranteed, mark the class as serializable to enforce schema generation
at definition time:

```python
class SerializableInvoice(State, serializable=True):
    id: str
    total: int
```

### Best Practices

1. **Use Immutability**: Embrace the immutable nature of State - never try to modify instances.
1. **Make Small States**: Keep State classes focused on a single concern.
1. **Provide Defaults**: Use default values for optional attributes to make creation easier.
1. **Use Type Annotations**: Always provide accurate type annotations for all attributes.
1. **Consistent Updates**: Always use `updating` (or helper functions that call it) for changes.
1. **Composition**: Compose complex states from simpler ones.

### Path-Based Updates and Requirements

Every `State` subclass exposes an `AttributePath` builder via the class attribute `_`. Paths let you
read or update deeply nested values without manually rebuilding intermediate objects, and they work
with the same validation rules as regular constructors.

```python
from collections.abc import Mapping, Sequence

from haiway import AttributeRequirement, State

class Profile(State):
    name: str
    preferences: Mapping[str, str]

class User(State):
    profile: Profile
    roles: Sequence[str]

user = User(
    profile=Profile(name="Alice", preferences={"locale": "en"}),
    roles=("admin",),
)

# Read a nested value
assert User._.profile.name(user) == "Alice"

# Produce a new instance with an updated nested value
renamed = User._.profile.name(user, "Alicia")
assert renamed.profile.name == "Alicia"

# Combine with AttributeRequirement to assert invariants
AttributeRequirement.equal(
    value="admin",
    path=User._.roles[0],
).check(renamed)
```

`AttributeRequirement` instances raise a `ValueError` when a check fails, which makes them useful in
tests or guard clauses. Use paths for bulk updates in performance-sensitive code—they only rebuild
the segments that actually change.

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
user1 = user.updating(name="Alice Johnson")

# Update multiple attributes
user3 = user.updating(
    active=False,
    updated_at=datetime.now(),
)

# Update a nested attribute directly
new_address = user.address.updating(street="456 Oak Ave")
user4 = user.updating(address=new_address)
```

### Performance Considerations

While State instances are immutable, creating new instances for updates has minimal overhead as only
the changed paths are reconstructed. The validation system is optimized to be fast for typical use
cases.

For high-performance scenarios:

- Keep State classes relatively small and focused
- Consider using path-based updates for nested changes
- If needed, batch multiple updates into a single `updating` call

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
        async with ctx.scope("debug", config.updating(log_level="DEBUG")):
            # Use updated state
            debug_config = ctx.state(AppConfig)
            assert debug_config.log_level == "DEBUG"
```

This pattern enables effective dependency injection and state propagation throughout your
application.
