# Caching Helper

Haiway ships two memoization helpers under `haiway.helpers.caching`:

- `cache` keeps coroutine results in-process with an LRU store.
- `cache_externally` coordinates reads and writes against a user-provided backend while preserving
  the coroutine signature.

Both decorators require `async def` targets and lean on `ctx.spawn` for background persistence work.

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
- Entries are stored per decorated function inside the process.
- The store evicts using LRU with a default limit of one entry; pass `limit` to increase it.
- Provide `expiration` in monotonic seconds to automatically recompute stale entries.

## External Cache Backends

`cache_externally` binds a coroutine to custom read/write logic. You supply the backend operations,
and the decorator mirrors the coroutine's interface while delegating persistence.

```python
from haiway.helpers.caching import cache_externally

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

@cache_externally(
    make_key=lambda user_id: f"profile:{user_id}",
    read=read_from_store,
    write=write_to_store,
    clear=clear_from_store,
)
async def resolve_profile(user_id: str) -> dict[str, str]:
    return await fetch_profile(user_id)
```

`redis` denotes an async client instance and `json` comes from the standard library.

Guidelines for external caches:

- `make_key` must deterministically transform call arguments into a hashable key.
- `read` returns `None` for cache misses; any other value is treated as a hit and returned.
- `write` runs via `ctx.spawn`, so the call returns before persistence completes. Make sure your
  backend tolerates eventual consistency or layer your own synchronization.
- Provide a `clear` callable if you need cache invalidation; omit it to disable `clear_cache`.

## Cache Invalidation

- `await cached_fn.clear_cache()` clears all entries for the in-memory decorator.
- `await cached_fn.clear_cache(key)` clears a specific entry when using `cache_externally`; omit
  `key` to flush the backend entirely. Calling `clear_cache` requires that `clear` was provided to
  `cache_externally`.

The default in-memory cache clears entries immediately, while external backends delegate to the
`clear` coroutine you supply.

## Operational Notes

- Decorated functions are per-process. Use `cache_externally` to share results across workers.
- Because writes happen asynchronously, ensure the current context stays alive long enough for
  spawned write tasks to complete.
- Combine cache metrics with `ctx.record_event` to instrument cache hits and misses before reporting
  to observability backends such as OpenTelemetry.
