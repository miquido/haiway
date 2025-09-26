# Caching Helper

Haiway's `haiway.helpers.caching.cache` decorator memoizes coroutine results. It is designed for
async-first services where work is driven through `async def` calls and relies on the current
context for background execution via `ctx.spawn`.

## Default In-Memory Cache

```python
from haiway.helpers.caching import cache

@cache
async def resolve_profile(user_id: str) -> dict[str, str]:
    print("Calling external service")
    return await fetch_profile(user_id)

async def handler(user_id: str) -> dict[str, str]:
    # First call executes the body; subsequent calls reuse the cached value.
    return await resolve_profile(user_id)
```

Key characteristics of the built-in cache:

- Only coroutine functions are supported. Decorating a synchronous callable raises an assertion.
- The cache keeps entries per decorated function and stores them in process memory.
- LRU eviction is applied with a default limit of a single entry; pass `limit` to increase it.
- Set `expiration` to a number of monotonic seconds to recompute stale entries automatically.

## Custom Cache Backends

When in-memory storage is not enough, supply a trio of async functions that operate on your cache
backend. The decorator returns a wrapper that still looks like the original coroutine while
retrieval and persistence are delegated to the custom backend.

```python
from haiway.helpers.caching import cache

async def read_from_store(cache_key: str) -> dict[str, str] | None:
    if raw := await redis.get(cache_key):
        return json.loads(raw)
    return None

async def write_to_store(cache_key: str, value: dict[str, str]) -> None:
    await redis.set(cache_key, json.dumps(value))

async def clear_from_store(cache_key: str | None) -> None:
    if cache_key is None:
        await redis.flushdb()
        return
    await redis.delete(cache_key)

@cache(
    make_key=lambda user_id: f"profile:{user_id}",
    read=read_from_store,
    write=write_to_store,
    clear=clear_from_store,
)
async def resolve_profile(user_id: str) -> dict[str, str]:
    return await fetch_profile(user_id)
```

`redis` denotes an async client instance and `json` comes from the standard library.

Guidelines for custom caches:

- `make_key` must convert arguments into a hashable key; provide one explicitly when using
  `read`/`write`.
- `write` is scheduled via `ctx.spawn`, so the call returns without waiting for persistence. Ensure
  your backend can handle eventual consistency or add your own synchronization.
- Provide a `clear` callable to make `clear_cache` and `clear_call_cache` work with the backend.

## Cache Invalidation

Every wrapped coroutine exposes two helpers:

- `await cached_fn.clear_cache()` removes every cached result when supported by the backend.
- `await cached_fn.clear_call_cache(*args, **kwargs)` recomputes the next call for a specific key.

The default in-memory cache clears entries immediately. Custom caches must include a `clear`
function that performs the removal.

## Operational Notes

- The decorator is per-process. Use an external cache backend if multiple workers must share
  results.
- Because writes happen asynchronously, ensure your context stays alive long enough for `ctx.spawn`
  tasks to complete.
- Combine cache metrics with `ctx.record_event` to instrument cache hits and misses when reporting
  to observability backends such as OpenTelemetry.
