# Utilities

Haiway exposes a small set of utility primitives for common application needs: data normalization,
environment loading, logging bootstrap, pagination state, and async producer-consumer coordination.

Most utilities described here are publicly exported from `haiway`. A few narrower helpers are kept
under submodules, such as `format_str` in `haiway.utils.formatting` and `AsyncQueueEmpty` in
`haiway.utils.queue`.

## Collection Helpers

The collection helpers normalize inputs into concrete container types while preserving `None`.

```python
from haiway import as_dict, as_list, as_map, as_set, as_tuple

values = as_list(range(3))          # [0, 1, 2]
names = as_tuple(["a", "b"])        # ("a", "b")
unique = as_set(["a", "a", "b"])    # {"a", "b"}
mapping = as_dict({"a": 1})         # {"a": 1}
immutable = as_map({"a": 1})        # Map({"a": 1})
```

### Available helpers

- `as_list(iterable)` converts any iterable to `list`, returning the original list unchanged.
- `as_tuple(iterable)` converts any iterable to `tuple`, returning the original tuple unchanged.
- `as_set(collection)` converts any iterable to `set`, returning the original set unchanged.
- `as_dict(mapping)` converts any mapping to `dict`, returning the original dict unchanged.
- `as_map(mapping)` converts any mapping to Haiway's immutable `Map`.

All five functions return `None` when passed `None`.

### Removing `MISSING` values

Use `without_missing()` when building payloads or state updates from optional values that may carry
Haiway's `MISSING` sentinel.

```python
from haiway import MISSING, without_missing

payload = without_missing(
    {
        "name": "alice",
        "nickname": MISSING,
        "age": 30,
    }
)

assert payload == {"name": "alice", "age": 30}
```

`without_missing()` only removes values equal to `MISSING`. It does not remove `None`, `False`, or
empty strings.

## Environment Helpers

The environment helpers provide typed access to `os.environ` and a minimal `.env` loader.

```python
from haiway import getenv_bool, getenv_int, getenv_str, load_env

load_env()  # reads .env if present

debug = getenv_bool("DEBUG", False)
port = getenv_int("PORT", 8080)
database_url = getenv_str("DATABASE_URL", required=True)
```

### Generic access

Use `getenv()` when you need a custom parser:

```python
from haiway import getenv

workers = getenv("WORKERS", int, default=4)
```

If the variable is set but parsing fails, `getenv()` raises `ValueError`.

### Specialized accessors

- `getenv_bool(key, default=None, required=False)` treats only `"true"`, `"1"`, and `"t"`
  case-insensitively as `True`. Any other present value becomes `False`.
- `getenv_int(...)` parses integers and raises `ValueError` on invalid input.
- `getenv_float(...)` parses floats and raises `ValueError` on invalid input.
- `getenv_str(...)` returns the raw string value.
- `getenv_base64(key, decoder=..., default=None, required=False)` validates base64 input, decodes it
  to bytes, then applies your decoder function.

### Loading `.env`

`load_env(path=None, override=True)` is intentionally minimal:

- It defaults to `.env` in the current working directory.
- It ignores lines starting with `#`.
- It expects `KEY=VALUE` per line.
- It silently does nothing when the file does not exist.
- It does not implement inline comments, shell expansion, or quoting rules.

That makes it suitable for lightweight app bootstrap, but not a drop-in replacement for more
feature-rich env parsers.

## Logging Bootstrap

`setup_logging()` configures standard-library logging to write to stdout.

```python
from haiway import setup_logging

setup_logging("uvicorn", "httpx")
```

### Behavior

- Configures the root logger and any explicitly named loggers.
- Uses `INFO` by default, or `DEBUG` when `DEBUG_LOGGING` is enabled.
- Includes timestamps by default and can disable them with `time=False`.
- Disables previously created loggers by default via `disable_existing_loggers=True`.

This helper should normally be called once during application startup.

## Pagination Primitives

Haiway provides immutable pagination objects for integrations and service layers.

### `Pagination`

`Pagination` is a `State` carrying:

- `token: UUID | str | int | None`
- `limit: int`
- `arguments: Mapping[str, BasicValue]`

```python
from haiway import Pagination

pagination = Pagination.of(limit=50, region="eu")
next_page = pagination.with_token("cursor-2").with_arguments(sort="desc")
```

The `with_*()` methods always return updated immutable copies. `with_arguments()` merges new
arguments over existing ones and returns the same instance when no arguments are provided.

### `Paginated`

`Paginated[Element]` stores page items together with the `Pagination` metadata that produced or
describes the page.

```python
from haiway import Paginated, Pagination

page = Paginated.of(
    [1, 2, 3],
    pagination=Pagination(limit=3, arguments={}),
)

assert list(page) == [1, 2, 3]
assert page.items == (1, 2, 3)
```

`Paginated` behaves like a read-only sequence. Items are stored as an immutable tuple.

### Detecting continuation

`page.has_next_page` is intentionally permissive:

- it is `True` when a pagination token is present
- it is also `True` when the page size is greater than or equal to `pagination.limit`

This supports providers that do not return explicit continuation tokens and instead imply "more
results may exist" when a page is full.

## Async Producer-Consumer Primitives

Haiway ships two related async iterators for single-consumer workflows: `AsyncQueue` and
`AsyncStream`.

### `AsyncQueue`

`AsyncQueue` is a buffered async iterator.

```python
from haiway import AsyncQueue

queue: AsyncQueue[int] = AsyncQueue()
queue.enqueue(1)
queue.enqueue(2)
queue.finish()

items = [item async for item in queue]  # [1, 2]
```

Use `AsyncQueue` when producers may outpace the consumer and buffering is acceptable.

Key behavior:

- `enqueue()` immediately delivers to a waiting consumer or appends to an internal buffer.
- `pending_next()` returns a buffered item synchronously.
- `pending_next()` raises `AsyncQueueEmpty` from `haiway.utils.queue` when the queue is open but
  currently empty.
- `finish()` stops future `enqueue()` calls and ends iteration after buffered items are drained.
- `finish(exception)` re-raises that exception on the consumer after buffered items are drained.
- `cancel()` is shorthand for finishing with `CancelledError`.
- `clear()` drops only currently buffered items and leaves a waiting consumer intact.

### `AsyncStream`

`AsyncStream` is a flow-controlled async iterator with back-pressure.

```python
from haiway import AsyncStream, ctx

stream: AsyncStream[int] = AsyncStream()

async def producer() -> None:
    for i in range(3):
        await stream.send(i)
    stream.finish()

# `ctx.spawn(producer)` starts `producer()` concurrently while iteration continues below.
ctx.spawn(producer)

items = [item async for item in stream]  # [0, 1, 2]
```

Use `AsyncStream` when producers should wait for the consumer to accept each item.

Key behavior:

- `send()` suspends until the consumer takes the element if no consumer is currently waiting.
- `finish()` ends the stream immediately for future reads.
- `finish(exception)` re-raises that exception on the consumer.
- `cancel()` is shorthand for finishing with `CancelledError`.
- `send()` to a finished stream is ignored.
- Pending producers are released when the stream finishes.

### Choosing between them

- Use `AsyncQueue` for buffered handoff.
- Use `AsyncStream` for back-pressure and producer-consumer pacing.
- Both support exactly one active consumer at a time.

## Formatting Values for Diagnostics

`format_str()` recursively formats nested values into a readable string representation for logs,
errors, and observability output. It is available from `haiway.utils.formatting`.

```python
from haiway.utils.formatting import format_str

formatted = format_str(
    {
        "user": "alice",
        "roles": ["admin", "editor"],
    }
)
```

Formatting rules include:

- strings are quoted
- multiline strings use an indented triple-quoted block
- mappings and sequences are rendered with indentation
- bytes-like values are rendered as `<<<N bytes>>>`
- `datetime` values use ISO 8601
- `UUID` values use their canonical string form
- `MISSING` renders as an empty value and is skipped inside nested structures

This function is primarily useful for human-readable diagnostics rather than stable serialization.
