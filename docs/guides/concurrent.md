# Concurrent Processing

Haiway builds concurrency around `asyncio`, `ctx.spawn(...)`, and scope-bound task groups. The
helpers in `haiway.helpers.concurrent` add bounded fan-out and stream-merging patterns on top of
that model without leaving the framework's context and observability rules.

## Structured Concurrency Basics

Within Haiway, spawned tasks belong to the nearest isolated scope's task group.

- `ctx.spawn(...)` keeps work tied to the current scope.
- Scope exit waits for in-scope tasks to settle.
- Child task failures surface through the task group unless explicitly handled.
- State, logging, and observability remain available inside spawned tasks.

If work must outlive the current scope, use `ctx.spawn_background(...)` instead of `ctx.spawn(...)`.

## `process_concurrently`

Use `process_concurrently(...)` when you need bounded concurrent side effects and do not need
results back.

```python
from haiway import ctx, process_concurrently

async def send_notification(user_id: str) -> None:
    client = ctx.state(NotificationClient)
    await client.send(user_id, "ready")

await process_concurrently(
    ["u1", "u2", "u3", "u4"],
    send_notification,
    concurrent_tasks=3,
)
```

Behavior:

- Accepts `Iterable` and `AsyncIterable`.
- Runs at most `concurrent_tasks` handlers at once.
- Raises the first handler exception by default.
- With `ignore_exceptions=True`, logs handler failures and keeps going.

## `execute_concurrently`

Use `execute_concurrently(...)` when you have one async handler and want ordered results.

```python
from haiway import execute_concurrently

async def fetch_user(user_id: str) -> dict[str, object]:
    return await api.fetch_user(user_id)

results = await execute_concurrently(
    fetch_user,
    ["u1", "u2", "u3"],
    concurrent_tasks=2,
)
```

Key details:

- Result order matches input order, not completion order.
- Supports both `Iterable` and `AsyncIterable`.
- `return_exceptions=True` returns exception objects in-place instead of raising.

## `concurrently`

Use `concurrently(...)` when the work is already represented as coroutine objects and each coroutine
may have different parameters.

```python
from haiway import concurrently

coroutines = [
    fetch_user("u1"),
    fetch_account("u1"),
    fetch_permissions("u1"),
]

results = await concurrently(
    coroutines,
    concurrent_tasks=2,
)
```

This is similar to `execute_concurrently(...)`, but it consumes ready-made coroutines instead of
applying a single handler over elements.

## `stream_concurrently`

`stream_concurrently(...)` merges two async iterables and yields items as soon as either source
produces them.

```python
import asyncio

from haiway import stream_concurrently

async def numbers():
    for i in range(3):
        await asyncio.sleep(0.1)
        yield i

async def letters():
    for letter in "ab":
        await asyncio.sleep(0.15)
        yield letter

async for item in stream_concurrently(numbers(), letters(), exhaustive=True):
    print(item)
```

Important semantics:

- Default `exhaustive=False` stops the merged stream when either source finishes.
- `exhaustive=True` keeps yielding until both sources finish.
- Yielded order depends on arrival timing.
- Exceptions from either source are propagated.
- Cancelling the consumer cancels the producer tasks created for both sources.

## Cancellation and Failure Semantics

All four helpers are implemented with local `ContextTaskGroup`s plus `ctx.spawn(...)`.

That gives them predictable behavior:

- Cancellation propagates into spawned work.
- Uncaught task failures stop the operation unless you explicitly request exception-tolerant mode.
- Result-collecting helpers preserve order even when task completion order differs.

## Choosing the Right Helper

- `process_concurrently(...)`: side effects only
- `execute_concurrently(...)`: apply one handler and collect ordered results
- `concurrently(...)`: run pre-created coroutines and collect ordered results
- `stream_concurrently(...)`: merge two async iterables into one stream
