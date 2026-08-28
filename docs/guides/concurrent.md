# Concurrent Processing

Haiway builds concurrency around `asyncio`, `ctx.spawn(...)`, and scope-bound task groups. The
helpers in `haiway.helpers.concurrent` add bounded fan-out and stream-merging patterns on top of
that model without leaving the framework's context and observability rules.

## Structured Concurrency Basics

Within Haiway, every scope owns a task group, so spawned tasks belong to the scope they were spawned
in.

- `ctx.spawn(...)` keeps work tied to the current scope. A task is awaited by the scope it was
  spawned in - never by an enclosing one.
- Scope exit waits for in-scope tasks to settle.
- Child task failures surface through the task group unless explicitly handled.
- State, logging, and observability remain available inside spawned tasks, and stay valid for as
  long as the task runs.

Because scope exit joins the tasks spawned within it, such a task must be able to finish on its own.
Two shapes cannot:

- Waiting for something signalled only after the scope exits - the scope waits for the task while
  the task waits for the scope.
- Iterating a `ctx.subscribe(...)` subscription to exhaustion in a nested non-isolated scope. A
  subscription ends when the scope owning the event bus exits, and a nested non-isolated scope
  shares the bus of an ancestor, so consume it with a bound or spawn the consumer in the scope
  owning the bus - a root or `isolated=True` scope.

If work must outlive the current scope, use `ctx.spawn_background(...)` instead of `ctx.spawn(...)`.
Such a task is fully detached - it runs with an empty context, so `ctx.state(...)` and
`ctx.subscribe(...)` raise `ContextMissing` within it. Enter a scope inside the task, or pass what
it needs as arguments.

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

- Accepts `Iterable` and `AsyncGenerator` - not any async iterable: the source is closed when
  consumption ends, which needs `aclose`.
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
- Supports both `Iterable` and `AsyncGenerator`, on the same terms as `process_concurrently`.
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

`stream_concurrently(...)` merges two async generators and yields items as soon as either source
produces them.

```python
import asyncio

from haiway import ctx, stream_concurrently

async def numbers():
    for i in range(3):
        await asyncio.sleep(0.1)
        yield i

async def letters():
    for letter in "ab":
        await asyncio.sleep(0.15)
        yield letter

async with ctx.closing(stream_concurrently(numbers(), letters(), exhaustive=True)) as merged:
    async for item in merged:
        print(item)
```

Important semantics:

- Default `exhaustive=False` stops the merged stream when either source finishes.
- `exhaustive=True` keeps yielding until both sources finish.
- Yielded order depends on arrival timing.
- Exceptions from either source are propagated.
- Cancelling the consumer cancels the producer tasks created for both sources.
- Both sources are closed when the merged stream ends, however it ends - exhausted, failed, or
  abandoned. Close the merged stream itself with `contextlib.aclosing` when leaving early, so that
  happens at the break rather than whenever the garbage collector gets to it.

## Closing Generator Sources

Every helper here closes an async generator source it consumed, however the consumption ended -
exhausted, failed, or cancelled. That is why they take an `AsyncGenerator` rather than any async
iterable: without `aclose` there is no way to release the source. The generators these helpers
*return* are the caller's to close, and `ctx.closing(...)` does it where the iteration ends:

```python
from haiway import ctx

async with ctx.closing(stream_concurrently(numbers(), letters())) as merged:
    async for item in merged:
        if not await handle(item):
            break  # both sources are closed right here
```

This matters more than the usual "release resources promptly" argument. `ctx.stream(...)` and
`stream_concurrently(...)` open a context scope inside the generator, and an abandoned generator is
finalized by the garbage collector in a *fresh* context - one where the scope it opened can no
longer be released. The teardown fails there and the error is only logged, so an unclosed stream
degrades quietly rather than raising where the mistake was made:

```python
stream = ctx.stream(produce)
async for element in stream:
    break  # walking away here leaves the scope to the collector, which cannot release it
```

Closing is also what ends a pushed source: `ctx.closing(queue)` and `ctx.closing(stream)` end an
`AsyncQueue` or an `AsyncStream` for good, dropping whatever they still hold. When the *producer* is
done but the consumer should still drain what was accepted, call `queue.finish()` instead - that is
the one path which keeps the buffer.

`ctx.closing` is `contextlib.aclosing` typed for async generators; either works.

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
- `stream_concurrently(...)`: merge two async generators into one stream
