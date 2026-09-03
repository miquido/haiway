# Types

Haiway's `types` package contains the low-level primitives that support `State`, schema generation,
metadata handling, and immutable value objects. Most applications use these types indirectly through
`State`, but understanding them helps when you need custom metadata, three-state optional values, or
standalone immutable containers.

## Overview

The package groups into four areas:

- JSON-like value aliases: `RawValue`, `BasicValue`, `BasicObject`, `FlatObject`
- Sentinel and defaults: `MISSING`, `Missing`, `Default`, `DefaultValue`
- Immutable containers: `Immutable`, `Map`, `Meta`
- Annotation metadata: `Alias`, `Description`, `Specification`, `TypeSpecification`

All of them are exported from `haiway`.

## JSON-Compatible Value Aliases

Use the aliases from `haiway.types.basic` when you need precise public types for JSON-like payloads:

- `RawValue` is `str | float | int | bool | None`
- `BasicValue` is recursive: raw values, nested mappings, and nested sequences
- `BasicObject` is `Mapping[str, BasicValue]`
- `FlatObject` is `Mapping[str, RawValue]`

`FlatObject` is useful for headers, message attributes, and similar payloads where nested structures
are intentionally forbidden.

### Converting to Basic Values

`State.to_basic_object()`, `State.to_json()`, and `Meta` validation all share one conversion into
this representation. Each leaf is encoded to the spelling the matching attribute validation reads
back, so a converted value can be validated into the type it came from:

| Type                               | Basic value          |
| ---------------------------------- | -------------------- |
| `str`, `int`, `float`, `bool`      | unchanged            |
| `None`, `StrEnum`, `IntEnum`       | unchanged            |
| `UUID`                             | `str(value)`         |
| `datetime`, `date`, `time`         | `value.isoformat()`  |
| `Path`                             | `value.as_posix()`   |
| `bytes`, `bytearray`, `memoryview` | base64 encoded `str` |

Nested `State` instances, dataclasses, mappings, sequences and sets are converted as the value graph
is traversed, into `Map` and `tuple` instances. Mapping keys go through the same table and are then
spelled the way JSON spells them - a `str` result is used as is, `int` and `float` become
`str(key)`, and `bool` and `None` become `"true"`, `"false"` and `"null"`. That is the spelling
`json.dumps` gives those keys on its own, so payloads which already encoded keep the output they
had.

```python
from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from haiway import State


class Event(State):
    identifier: UUID
    created: datetime


class Journal(State):
    events: Mapping[UUID, Event]


event = Event(identifier=UUID(int=1), created=datetime(2026, 1, 2, tzinfo=UTC))

event.to_basic_object()
# {"identifier": "00000000-0000-0000-0000-000000000001", "created": "2026-01-02T00:00:00+00:00"}

# keys are converted too, so a mapping json could not key encodes now
Journal(events={UUID(int=1): event}).to_json()
# '{"events": {"00000000-0000-0000-0000-000000000001": {"identifier": "000...", ...}}}'
```

Anything else - a plain `Enum`, a `Decimal`, `MISSING` - has no basic spelling the validation would
read back. Rather than encoding it into a value which could only fail on the way in,
`to_basic_object` raises `TypeError` naming the path and the type, never the value. `to_json` leaves
such values unchanged instead, so a custom `encoder_class` still gets to handle them.

## Missing Values with `MISSING`

`MISSING` is Haiway's explicit "not provided" sentinel. It is different from `None`:

- `None` means a value was provided and that value is null
- `MISSING` means no value was provided at all

```python
from haiway import MISSING, Missing, is_missing, not_missing, unwrap_missing

def normalize(value: str | Missing) -> str:
    if is_missing(value):
        return "fallback"

    assert not_missing(value)
    return value.upper()

assert unwrap_missing(MISSING, default="fallback") == "fallback"
```

Use `Missing` in state field types when you need three states: present, null, and omitted.

## Defaults with `Default`

`Default(...)` is a typed wrapper used by `Immutable` and `State` fields.

```python
from uuid import uuid4
from haiway import Default, State

class RequestContext(State):
    request_id: str = Default(factory=lambda: uuid4().hex)
    retries: int = Default(3)
```

Environment-backed defaults name the variable with `env`. `default` is the fallback used when the
variable is not set, and `mapping` converts the raw string to the type of the field:

```python
from haiway import Default, State, parse_bool

class ServiceConfig(State):
    host: str = Default(env="SERVICE_HOST", default="localhost")
    port: int = Default(env="SERVICE_PORT", mapping=int, default=8080)
    tracing: bool = Default(env="SERVICE_TRACING", mapping=parse_bool, default=False)
    token: str = Default(env="SERVICE_TOKEN")  # required - no fallback
```

Important behavior:

- Literal defaults are reused as-is
- Factories are called for each new instance
- Environment-backed defaults are read during instance construction, not at import time, so
  `load_env()` applies regardless of import order
- Without a `default`, an unset variable makes the field required and construction reports the
  variable by name
- `Default(...)` is a field specifier, not a runtime descriptor

## `Immutable`

`Immutable` is Haiway's small frozen-record base class. Subclasses declare attributes with normal
type annotations, and the metaclass:

- collects annotated fields
- creates `__slots__`
- sets `__match_args__`
- resolves `Default(...)` values
- marks subclasses as `final`

```python
from haiway import Default, Immutable

class RetryPolicy(Immutable):
    attempts: int = Default(3)
    backoff_seconds: float
```

Instances cannot be modified after construction. `copy.copy()` and `copy.deepcopy()` return the same
instance because the object is immutable.

## `Map`

`Map[K, V]` is an immutable `dict` subclass with JSON helpers:

```python
from haiway import Map

mapping = Map({"a": 1})
merged = mapping | {"b": 2}

assert isinstance(merged, Map)
assert merged == {"a": 1, "b": 2}
```

Mutation methods such as `update`, `pop`, and item assignment raise `AttributeError`.

Use `Map` when you want an immutable mapping outside `State`, or when you want to document that a
function returns a read-only mapping value concretely.

## `Meta`

`Meta` is a specialized immutable metadata mapping built on the same JSON-compatible value model. It
adds:

- validation and normalization through `Meta.of(...)`, `Meta.from_mapping(...)`, and
  `Meta.from_json(...)`
- convenience accessors such as `.kind`, `.name`, `.description`, `.identifier`, `.error`,
  `.created`, `.last_updated`, and `.tags`
- typed getters for arbitrary keys - `.get_str(...)`, `.get_int(...)`, `.get_float(...)`,
  `.get_bool(...)`, `.get_uuid(...)`, and `.get_datetime(...)` - each raising `TypeError` when the
  stored value has a different type
- builder-style methods such as `.with_tags(...)`, `.with_error(...)`, `.with_created(...)`,
  `.merged_with(...)`, and `.excluding(...)`

```python
from haiway import Meta

meta = Meta.of(
    kind="dataset",
    tags=("exports", "pii"),
    payload={"owner": "ops", "versions": [1, 2]},
)

assert meta.tags == ("exports", "pii")
assert meta["payload"]["versions"] == (1, 2)
```

Normalization rules:

- lists become tuples
- nested mappings become `Map`
- values with a basic spelling are coerced to it - a `datetime` becomes its ISO string, a `UUID`
  becomes `str(value)` - see [Converting to Basic Values](#converting-to-basic-values)
- values without one raise `TypeError`
- keys stay strictly `str`, there is no spelling to coerce them to

`Meta.empty` is a shared empty instance returned by `Meta.of(None)`.

Accessors normalize on read rather than storing rich types: `.error` stores the stringified message
under `"error"` and returns it wrapped in a plain `Exception`, so the original exception type does
not survive a round trip.

```python
meta = Meta.empty.with_error(ValueError("quota exceeded"))

assert meta["error"] == "quota exceeded"
assert isinstance(meta.error, Exception)
```

One subtlety matters: direct `Meta({...})` construction does not recursively validate or normalize
values on its own. Use the factory helpers when the input comes from user code, JSON, or mutable
objects.

## Annotation Metadata

Haiway consumes several types through `typing.Annotated[...]` when resolving `State` fields:

- `Alias("external_name")` changes the externally exposed field name
- `Description("...")` adds human-readable schema/documentation text
- `Specification({...})` provides a manual JSON-schema-like override
- `Meta.of(...)` attaches structured metadata to the resolved attribute definition
- `Validator(callable)` applies an additional validation or coercion step before the base type
  validation runs
- `Verifier(callable)` applies an additional check after the base type has been validated

```python
from typing import Annotated
from haiway import Alias, Description, Meta, Specification, State

class Invoice(State):
    customer_id: Annotated[
        str,
        Alias("customer"),
        Description("Public customer identifier"),
        Meta.of(tags=("billing",)),
        Specification({"type": "string"}),
    ]
```

These annotations feed both runtime validation metadata and generated state schemas.

## Type Specifications

`TypeSpecification` is a typed union of JSON-schema-like `TypedDict` shapes. `Specification` is a
small immutable wrapper around one of those shapes so it can be attached inside
`typing.Annotated[...]`.

This is intentionally lightweight:

- Haiway keeps the structure typed
- schema fragments are composed by the attribute system
- `Specification(...)` does not deeply validate every schema keyword

Use it when inference is insufficient or when a type is intentionally represented differently in the
generated schema.
