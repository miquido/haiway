# Utilities

Haiway exposes a small set of utility primitives for common application needs: data normalization,
environment loading, logging bootstrap, pagination state, and async producer-consumer coordination.

Most utilities described here are publicly exported from `haiway`. A few narrower helpers are kept
under submodules, such as `format_str`, `format_log_message`, and `escape_controls` in
`haiway.utils.formatting`.

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

setup_logging("uvicorn", "httpx2")
```

### Behavior

- Configures the root logger and any explicitly named loggers.
- Emits `DEBUG` or `INFO` depending on the `debug=` flag. Its default is resolved once, when
  `haiway.utils.logs` is imported, from the `DEBUG_LOGGING` environment variable, falling back to
  `__debug__` - so unoptimized runs default to `DEBUG` and `python -O` defaults to `INFO`. Pass
  `debug=False` to pin the level regardless of the environment.
- Formats records with default formatter, which includes timestamps with the local timezone offset.
- Disables previously created loggers by default via `disable_existing_loggers=True`.

### Custom Formatter

Pass any `logging.Formatter` instance through `formatter=` to apply it to all configured loggers:

```python
from logging import Formatter

from haiway import setup_logging

setup_logging("uvicorn", formatter=Formatter("[%(levelname)-4s] [%(name)s] %(message)s"))
```

The example above is also how timestamps are omitted - there is no separate flag for it.

### JSON Logs

`JSONLogFormatter` renders every record as a single-line JSON object, suitable for log ingestion
pipelines:

```python
from haiway import JSONLogFormatter, setup_logging

setup_logging("uvicorn", formatter=JSONLogFormatter())
```

- Every record attribute becomes a field under its original name (`name`, `levelname`, `module`,
  `lineno`, `taskName`, ...), including everything passed through `extra=`. Nothing is filtered out
  or renamed, only `time` and `message` are rendered on top.
- `time` defaults to ISO-8601 with milliseconds and a timezone offset, or follows `datefmt` when
  provided.
- Fields holding `None` are omitted, keeping records free of empty noise like `exc_text` or
  `taskName` outside of a task.
- `exc_info` holds the formatted traceback instead of its raw contents.
- Values which are not JSON serializable are resolved to readable strings without memory addresses:
  exceptions to their message, types to their qualified name, tracebacks to their formatted frames
  and anything else through `str`. Logging never fails because of an unexpected payload.
- An `extra` field named like a record attribute is already a `KeyError` raised by `logging` itself.
  An `extra` field named `time` or `message` trips a debug-only assertion; optimized builds
  (`python -O`) carry none of that check and let the field win instead.

```python
from logging import getLogger

logger = getLogger("app")
logger.info("processed %d items", 42, extra={"request_id": "req-7"})
# {"time": "2026-08-20T13:09:56.123+02:00", "message": "processed 42 items", "name": "app",
#  "msg": "processed %d items", "args": [42], "levelname": "INFO", "levelno": 20,
#  "module": "app", "funcName": "run", "lineno": 12, ..., "request_id": "req-7"}
```

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

Haiway ships two related async generators for single-consumer workflows: `AsyncQueue` and
`AsyncStream`. Both implement the full async generator protocol - including `aclose()` ending the
iteration for good - so they can be passed anywhere a generator source is expected and closed with
`contextlib.aclosing`.

### `AsyncQueue`

`AsyncQueue` is a buffered async generator.

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
- `pending_next()` raises `AsyncQueueEmpty` when the queue is open but currently empty.
- `finish()` stops future `enqueue()` calls and ends iteration after buffered items are drained.
- `finish(exception)` re-raises that exception on the consumer after buffered items are drained.
- `cancel()` is shorthand for finishing with `CancelledError`.
- `clear()` drops currently buffered items, and does nothing at all while a consumer is waiting, so
  a queued wake-up is never disrupted.
- `aclose()` ends the iteration: it finishes the queue *and* drops what it still holds, so every
  later step raises the finish reason. Closing a generator ends it, and a closed one cannot hand out
  elements. That reason is `StopAsyncIteration` unless the queue was already finished, which keeps
  whichever reason ended it - a queue closed after `cancel()` keeps raising `CancelledError`. This
  is what `ctx.closing(queue)` and the generator consumers in `haiway.helpers.concurrent` call, so a
  queue closed early does not keep delivering.
- Reach for `finish()` rather than `aclose()` when the *producer* is done but the consumer should
  still drain what was accepted - that is the difference between the two, and it is why `finish()`
  keeps the buffer.
- `asend(None)` is `__anext__()`; sending any other value raises `TypeError`, since `enqueue()` is
  the producer side.
- `athrow()` finishes the queue with the exception and drops buffered items, as `aclose()` does -
  ending the iteration where it stands leaves nothing to deliver them to.

#### Bounding the Buffer

The buffer is unbounded by default. Pass `limit=` to cap it:

```python
queue: AsyncQueue[int] = AsyncQueue(limit=2)
queue.enqueue(1)
queue.enqueue(2)
queue.enqueue(3)  # drops 1 - the oldest buffered element
queue.finish()

items = [item async for item in queue]  # [2, 3]
```

- Overflow drops the oldest buffered element silently; `enqueue()` never blocks or raises.
- Elements handed straight to a waiting consumer never enter the buffer, so they never count against
  the limit.
- `limit` must be positive when provided, and the effective value is readable through the `.limit`
  property (`None` when unbounded).
- Reach for `AsyncStream` instead when producers must be slowed down rather than have their oldest
  items discarded.

### `AsyncStream`

`AsyncStream` is a flow-controlled async generator with back-pressure.

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
- `aclose()` is `finish()` without an exception - not yet consumed elements are dropped and further
  iteration ends.
- `asend(None)` is `__anext__()`; sending any other value raises `TypeError`, since `send()` is the
  producer side. `athrow()` finishes the stream with the exception.

### Choosing between them

- Use `AsyncQueue` for buffered handoff, optionally bounded with `limit=` when dropping the oldest
  items is preferable to unbounded growth.
- Use `AsyncStream` for back-pressure and producer-consumer pacing.
- Both support exactly one active consumer at a time.
- Both are async generators, so wrap either in `ctx.closing(...)` when the iteration may be left
  early. See [Closing Generator Sources](concurrent.md#closing-generator-sources).

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
- control characters within strings are escaped, keeping the line feeds of multiline blocks
- mappings and sequences are rendered with indentation
- bytes-like values are rendered as `<<<N bytes>>>`
- `datetime` values use ISO 8601
- `UUID` values use their canonical string form
- `MISSING` renders as an empty value and is skipped inside nested structures

This function is primarily useful for human-readable diagnostics rather than stable serialization.

Attributes annotated with `Sensitive` are rendered as their redaction instead of their value, so a
`State` carrying credentials can be formatted without leaking them.

### Keeping Log Records Intact

Untrusted text reaching a log line can otherwise forge additional records or inject terminal escape
sequences. Two helpers from `haiway.utils.formatting` guard against it, and both built-in
observability backends already apply them:

```python
from haiway.utils.formatting import escape_controls, format_log_message

escape_controls("alice\n2026-08-20 [ERROR] AUDIT: root logged in")
# 'alice\\n2026-08-20 [ERROR] AUDIT: root logged in'

format_log_message("processed %d of %s", (3, "items"))
# 'processed 3 of items'
```

- `escape_controls(text, allow_newlines=False)` escapes control characters. Pass
  `allow_newlines=True` for text rendered within an already multiline structure - carriage returns
  and escape sequences are escaped either way.
- `format_log_message(message, args)` interpolates `%`-style arguments and escapes the result. A
  mismatched format string keeps the message and appends the arguments instead of losing the record,
  which is what the standard library does when interpolation fails.
