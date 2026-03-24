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
    request_id: str = Default(default_factory=lambda: uuid4().hex)
    retries: int = Default(3)
```

Important behavior:

- Literal defaults are reused as-is
- Factories are called for each new instance
- Environment-backed defaults are read during instance construction
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
- convenience accessors such as `.kind`, `.name`, `.description`, `.identifier`, `.created`, and
  `.tags`
- builder-style methods such as `.with_tags(...)`, `.with_created(...)`, `.merged_with(...)`, and
  `.excluding(...)`

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
- invalid values raise `TypeError`

`Meta.empty` is a shared empty instance returned by `Meta.of(None)`.

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
