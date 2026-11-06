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
`Default` returns a `DefaultValue` container that the State runtime resolves while creating each
instance:

```python
from uuid import uuid4
from haiway import Default, State

class ServiceConfig(State):
    correlation_id: str = Default(default_factory=lambda: uuid4().hex)
    timeout_seconds: float = Default(1.5)  # literal defaults still work

config = ServiceConfig()
assert isinstance(config.correlation_id, str)
```

Factories are called every time a new instance is created, so each `ServiceConfig` receives its own
identifier. Environment-backed defaults are also read during construction, so tests stay
deterministic: set the environment variable before instantiating the state or pass an explicit value
in the constructor.

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
from haiway import Alias, Description, Sensitive, Specification, State, Validator

def ensure_positive(value: int) -> int:
    if value <= 0:
        raise ValueError("value must be positive")
    return value

class Invoice(State):
    # Externally exposed as "customer_id" when encoding/decoding mappings or JSON.
    customer: Annotated[str, Alias("customer_id"), Description("Public customer identifier")]
    total_cents: Annotated[int, Specification({"type": "integer", "minimum": 0}), Validator(ensure_positive)]
    notes: Annotated[str | None, Description("Free-form note about the invoice")] = None
    # Kept out of every rendered representation, including logs.
    payment_token: Annotated[str, Sensitive()] = ""
```

Supported annotations include:

- `Alias("external_name")` — maps the attribute to an alternate key when using `to_mapping`,
  `from_mapping`, JSON helpers, and `updating`. Both the name and the alias are accepted as input,
  but giving the same attribute under both of them is refused with a `TypeError`.
- `Description("text")` — surfaces in generated JSON schemas and downstream documentation.
- `Meta.of({...})` — attaches structured metadata that you can later inspect from field definitions.
- `Specification({...})` — overrides the JSON Schema fragment when the inferred schema is
  insufficient.
- `Validator(callable)` — applies additional validation or coercion logic before the base attribute
  validation runs. It always receives the raw input, including for nested State attributes and for
  custom classes providing their own `validate`, which validate the value afterwards.
- `Verifier(callable)` — applies an additional check after the base attribute has been validated and
  typed.
- `Sensitive` — marks the attribute as carrying a secret or personal data. `str()`, `repr()`, and
  `format_str` render `<redacted>` instead of the value, which keeps it out of logs and
  observability backends. The class and `Sensitive()` mark the attribute alike; pass a custom marker
  - `Sensitive(redaction="<api-key>")` - when the shape of the omission is worth keeping.

Marking an attribute sensitive covers rendering only: attribute access, `to_mapping`, `to_json`, and
the generated JSON schema all keep working on the real value, so persistence and outgoing requests
are unaffected. Review those call sites separately when handling secrets.

`typing.NotRequired[T]` is most useful inside `TypedDict` definitions embedded in State fields. For
regular State attributes, prefer an explicit default when a field may be omitted.

### Structured Metadata with `Meta`

Use the `Meta` container when you need immutable, JSON-compatible metadata on either a field itself
or on the annotations that describe it. Prefer `Meta.of(...)`, `Meta.from_mapping(...)`, or
`Meta.from_json(...)` when constructing metadata: those entry points validate values, convert
sequences to tuples, and convert nested mappings to immutable `Map` instances. `Meta` also exposes
convenience accessors such as `.kind`, `.tags`, `.has_tags(...)`, and `.with_identifier(...)`.

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

`Meta.of(...)` accepts either an existing mapping or keyword arguments, and you can combine
builders. For example, `Meta.of(kind="dataset").with_last_updated(timestamp)` returns a new instance
with the timestamp recorded. When no metadata is provided, Haiway uses the shared `Meta.empty`, so
`Meta.of(None) is Meta.empty`.

To inspect metadata added through annotations, read it from the resolved attribute definition:

```python
fields = Dataset.__SELF_ATTRIBUTE__.attributes
path_meta = fields["export_path"].meta
assert path_meta.has_tags(("pii",))
```

Metadata values are limited to strings, numbers, booleans, `None`, sequences, and nested mappings.
Passing unsupported objects through the validating constructors raises a `TypeError`, which keeps
emitted schemas and observability events consistent. Direct `Meta({...})` construction preserves the
underlying `dict` behavior and does not perform recursive validation on its own, so use the factory
helpers when the input is external or mutable.

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

Each specialization is a distinct class - `Container[int]` is not `Container[str]` - while the
relation between the arguments is preserved: `Container[Dog]` is a subclass of `Container[Animal]`
when `Dog` inherits from `Animal`, also through nested arguments such as
`Container[Container[Dog]]`. `isinstance(Container(value=1), Container[int])` checks the held values
of an unspecialized instance against the arguments.

Bounds and constraints of the type parameters are checked when the class is specialized:

```python
class Shelf[T: Animal](State):
    item: T

Shelf[Dog]  # fine
Shelf[int]  # raises TypeError - T is bound to Animal
```

#### Defaults of type parameters

A type parameter may declare a default (PEP 696). Leaving the argument out of a specialization uses
it, and the variant is the same class however it is spelled:

```python
class Pair[A, B = str](State):
    first: A
    second: B

assert Pair[int] is Pair[int, str]
```

An unspecialized generic stands for its defaults, so `Pair(first=1, second=2)` fails validation
because `second` is a `str`. A parameter without a default stands for its bound, or for any value.

#### Variadic parameters and parameter specifications

A `TypeVarTuple` binds the arguments the other parameters leave over, and each position of a
`tuple[*Ts]` attribute is validated against its own argument. A `ParamSpec` is given the signature
it stands for, written as a list of types or as `...`:

```python
class Row[*Ts](State):
    cells: tuple[*Ts]

Row[int, str](cells=(1, "a"))  # validated position by position

class Handler[**P, R](State):
    call: Callable[P, R]

Handler[[int], bool](call=lambda value: value > 0)
```

#### Variance

Whether `Box[Dog]` is a subclass of `Box[Animal]` follows the variance of the parameter. A declared
one is honored, so a `TypeVar("T", contravariant=True)` reverses the relation. A parameter whose
variance is inferred, as in `class Box[T]`, is worked out from the attributes using it: read from
only, it is covariant, which is all an immutable State can do with a value. Used as an argument of a
callable attribute it is contravariant, and used both ways it is invariant - neither variant is a
subclass of the other.

### State Classes Are Final

A `State` class that declares attributes cannot be inherited from:

```python
class User(State):
    name: str

class Admin(User):  # TypeError - User declares attributes
    privileges: Sequence[str]
```

Slots, defaults, annotations, and type arguments are all resolved once, when the class is created,
and the nominal identity that equality, instance checks, and schema generation rely on assumes they
describe the whole class. There are three ways to get what inheritance would have given you:

```python
# 1. parametrize a generic State to type a variant of it
class Container[T](State):
    value: T

IntContainer = Container[int]

# 2. inherit from an attribute-less base to share behavior - this is what
#    `Configuration` is, and why configuration classes may still subclass it
class Behavior(State):
    def described(self) -> str:
        return f"{self.__class__.__name__}"

class Concrete(Behavior):
    value: int

# 3. hold an instance as an attribute to reuse its attributes
class Admin(State):
    user: User
    privileges: Sequence[str]
```

A specialization such as `Container[int]` declares the attributes of its origin, so it is final too
\- it names a typed variant rather than opening a base to derive from.

### Copying, Pickling and Representation

`copy.copy` returns the same instance, `copy.deepcopy` creates an equal one, and `copy.replace` (or
`updating`) produces a validated variant. `pickle` is deliberately not supported and raises a
`TypeError` pointing at `to_json` / `to_basic_object` - rebuilding a State would have to skip
validation, and a specialized variant such as `Box[int]` has no importable name. This also applies
to values crossing a process pool.

`str(state)` renders values as they read (`User(name: Alice)`), while `repr(state)` spells each
value with its own `repr` (`User(name: 'Alice')`) so a string and a number can be told apart.
Attributes marked `Sensitive` render their redaction in both. `inspect.signature(User)` lists the
attributes as keyword-only parameters.

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
- `state.to_basic_object()` — convert to a mapping of JSON-compatible values, in memory. Where
  `to_mapping` keeps the live `UUID`, `datetime` or `bytes` values, this encodes every leaf and key
  to the spelling validation reads back, so the result round trips through `from_mapping`. An
  attribute with no basic spelling - a plain `Enum`, a `Decimal` - raises `TypeError` naming the
  path and the type. See [Converting to Basic Values](types.md#converting-to-basic-values).
- `State.validate(value)` — coerce an instance or compatible mapping into the target State type.
  This is handy when accepting heterogeneous inputs.

Aliases apply consistently across these helpers. For example, the `Invoice.customer` field above
accepts `customer` or `customer_id` when instantiating, and `invoice.to_mapping()` emits
`{"customer_id": "...", ...}`.

### Type Validation

State classes perform thorough type validation for all supported Python types:

- **Basic Types**: int, str, bool, float, bytes
- **Container Types**:
  - **Sequence[T]**: sequence-like inputs are normalized to immutable tuples
  - **Mapping[K, V]**: mappings are validated recursively and converted to immutable `Map` values
  - **Set[T]**: set-like inputs are normalized to immutable frozensets
  - **tuple[T, ...]**: Fixed or variable-length tuples
- **Special Types**: UUID, datetime, date, time, timedelta, timezone, Path, re.Pattern
- **Union Types**: str | None, int | float
- **Literal Types**: Literal["a", "b", "c"]
- **Enum Types**: Enum, StrEnum, and IntEnum
- **Callable Types**: Function types and Protocol interfaces
- **TypedDict**: Validates structure with Required/NotRequired fields
- **Nested State Classes**: Validates recursively including generic State types
- **Any Type**: Accepts any value without validation

#### Important Typing Requirements

**Prefer abstract collection types in public State definitions:**

```python
# ✅ Correct - Use abstract types
from collections.abc import Sequence, Mapping, Set

class Config(State):
    items: Sequence[str]        # Not list[str]
    data: Mapping[str, int]     # Not dict[str, int]
    tags: Set[str]              # Not set[str]

# Lists and sets are accepted at runtime and normalized
config = Config(
    items=["a", "b", "c"],      # Becomes ("a", "b", "c")
    data={"key": 1},            # Becomes immutable Map
    tags={"tag1", "tag2"}       # Becomes frozenset({"tag1", "tag2"})
)

# Concrete collection annotations also work, but are less flexible for public APIs
class ConcreteConfig(State):
    items: list[str]
    data: dict[str, int]
    tags: set[str]
```

This keeps interfaces flexible while preserving predictable runtime behavior. Sequences and sets are
wrapped in immutable containers, and mappings are converted to immutable `Map` objects, so treat
them as read-only once they cross a State boundary.

A few validation details matter in practice:

- Plain `Enum` fields accept enum members and do not coerce from raw values.
- `StrEnum` and `IntEnum` fields accept both enum members and matching raw string or integer forms.
- Callable and protocol fields validate correctly at runtime but do not produce JSON Schema
  specifications.
- Validation failures are wrapped in `ValidationError`, which includes a precise nested path.

### JSON Schema Generation

Every State exposes a JSON Schema fragment through the `__SPECIFICATION__` attribute. Call
`State.json_schema(indent=2)` when you need a ready-to-serialize schema definition:

```python
schema = Invoice.json_schema(indent=2)
```

Besides the primitives, the schema covers the standard value types: `UUID`, `datetime`, `date`,
`time`, `timedelta` and `Path` (as strings with a `format`), `bytes` (a base64 string,
`contentEncoding`) and sets (arrays with `uniqueItems`). A `timedelta` is spelled as an ISO 8601
duration such as `P1DT2H3M4.5S`; years and months are refused because their length depends on the
calendar date. Aliases and descriptions propagate into the schema automatically, and any custom
`Specification` overrides are respected. If a State cannot be represented as JSON Schema (for
example, when it holds callables), `json_schema(required=True)` raises a `TypeError` so you can
detect unsupported shapes early.

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

Paths support nested attribute traversal, indexing into sequences and tuples, mapping access for
`str` and `int` keys, and optional container access for unions shaped like `T | None`.

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

### Native Storage

Haiway ships a compiled extension which stores the attributes of a State inline in the instance,
validates the common types without calling back into Python, and converts to mappings and JSON
natively. It makes the immutability real - an attribute can't be rebound through
`object.__setattr__` either, and an instance has no `__dict__` or `__weakref__`. That holds for the
native storage only: the pure Python implementation refuses `setattr` and `delattr`, but
`object.__setattr__` can still rebind an attribute there.

The pure Python implementation is the default. The extension is opt-in: `HAIWAY_NATIVE=1` uses it
and fails, rather than falling back, when it is not built. It behaves the same for everything but a
few details: calling `__init__` a second time on an instance raises natively and re-initializes it
in Python, and a key which is not an attribute is reported after the values before it were validated
rather than ahead of them, so a validator or a default factory may have run by then.
`HAIWAY_NATIVE=1` fails instead of falling back when the extension is missing - that is what the
build uses. After changing the C source run `make native`, as `uv sync` does not recompile it.

`to_json`, `from_json` and `from_json_array` read and write the JSON natively as well, without the
intermediate Python objects - for the default `json.JSONEncoder` and `json.JSONDecoder`, and with no
`indent`. A document which is not the usual case, or a value with no native spelling, is handed to
the Python implementation, which is also what reports every error, so the result is the same either
way. Passing an `encoder_class`, a `decoder` or an `indent` uses the Python implementation directly.

Calling a class with keyword arguments, `Point(x=1, y=2)`, and updating, `point.updating(x=3)`, are
the fastest ways to build and change an instance natively. Unpacking a mapping into the call,
`Point(**values)`, costs a little more than it did - to build an instance from a mapping use
`Point.validate(values)` instead, which is faster than either.

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
