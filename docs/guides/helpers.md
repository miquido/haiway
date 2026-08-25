# Helpers

Haiway's `helpers` package contains two kinds of utilities:

- Decorators that wrap plain functions with async control-flow behavior.
- `State`-based facades that resolve implementations from the active `ctx` scope.

This split is the main pattern to understand when reading the package.

## Package Shape

The public helpers exported from `haiway.helpers` are:

- `asynchronous`
- `CacheMakeKey`, `CacheRead`, `CacheWrite`
- `cache`, `cache_externally`
- `concurrently`, `execute_concurrently`, `process_concurrently`, `stream_concurrently`
- `Configuration`, `ConfigurationRepository`, `ConfigurationMissing`, `ConfigurationInvalid`
- `File`, `Files`, `Directory`, `FileException`, `Paths`
- `HTTPClient`, `HTTPClientError`, `HTTPTimeoutError`, `HTTPConnectionError`,
  `HTTPBodyConsumedError`, `HTTPBody`, `HTTPHeaders`, `HTTPQueryParams`, `HTTPRequesting`,
  `HTTPResponse`, `HTTPStatusCode`
- `MQMessage`, `MQQueue`
- `LoggerObservability`
- `retry`
- `statemethod`
- `throttle`
- `timeout`

## Core Pattern

### Context-Bound Facades

Helpers such as `Configuration`, `ConfigurationRepository`, `HTTPClient`, `MQQueue`, `File`,
`Files`, and `Directory` expose methods through `@statemethod`.

That means:

- Calling the method on an instance uses that instance directly.
- Calling the method on the class resolves an instance from `ctx.state(...)`.

This is why code such as `await HTTPClient.get(...)` or `await MyConfig.load()` works without
manually passing a client or repository object around.

### Decorator Helpers

The decorator-based helpers wrap ordinary callables:

- `@asynchronous` runs a synchronous function in an executor and returns an awaitable wrapper.
- `@cache` memoizes async function results in-process with LRU eviction and optional expiration.
- `@retry` retries sync or async functions when handled exceptions occur.
- `@throttle` rate-limits async call starts within a time window.
- `@timeout` raises `TimeoutError` if an async function exceeds the configured duration.

These decorators do not install state by themselves; they wrap the target callable.

## Concurrency Helpers

The helpers in `haiway.helpers.concurrent` all integrate with Haiway task management via
`ctx.spawn(...)` and local `ContextTaskGroup`s.

- `process_concurrently` is for side effects only.
- `execute_concurrently` applies one async handler to elements and returns results in input order.
- `concurrently` runs pre-created coroutine objects and also preserves input order.
- `stream_concurrently` merges two async iterables and yields items as they arrive.

Important details:

- `concurrent_tasks` defaults to `2` for the bounded fan-out helpers.
- `execute_concurrently` and `concurrently` preserve input order, not completion order.
- `stream_concurrently(..., exhaustive=False)` stops when either source ends.
- `stream_concurrently(..., exhaustive=True)` continues until both sources end.

See [Concurrent Processing](concurrent.md) for usage details.

## Configuration and State Access

`Configuration` and `ConfigurationRepository` are the main typed configuration layer.

- `Configuration.load(...)` delegates to the current `ConfigurationRepository`.
- Repository loading happens first.
- When loading with `required=True`, missing repository values fall back to `ctx.state(ConfigType)`.
- Because `ctx.state(...)` lazily creates a default instance when possible, configuration classes
  with only defaulted fields can still load successfully without repository data.

See [Configuration](configuration.md) for the repository model and examples.

## Files, HTTP, and Queues

- `Files.access(...)` produces an async context manager that installs a `File` state with `read()`
  and `write()` methods.
- `HTTPClient` is a typed facade over an injected `requesting` implementation.
- `MQQueue` is a typed facade over publishing and consuming protocols, and `MQMessage` carries
  payload plus acknowledge/reject callbacks.

These helpers keep boundary integrations typed while still allowing adapters to be swapped through
context state.

## Observability

`LoggerObservability(...)` builds a Haiway `Observability` backend backed by the standard `logging`
module. It is also the implementation Haiway falls back to when a scope is given no observability at
all, or is given a `Logger` instead of an `Observability` - so there is a single logger backed
implementation rather than one for each case.

It tracks:

- trace identity
- scope entry/exit
- logs
- events
- metrics
- attributes

Scope lifecycle - entry, exit and the resulting duration - is recorded at `DEBUG`, since it
describes the shape of the execution rather than what the application did. Logs, events, metrics and
attributes are recorded at the level their call site asked for. A scope exiting with a regular
exception is reported at `ERROR`; cancellation is not, being routine control flow under structured
concurrency.

With `debug_context=True`, it also retains each scope tree and records a tree-style summary of it
when its root completes. Turning it off - which is what the implicit fallback uses - leaves only the
scopes which are still live tracked, so a long lived root scope does not accumulate the ones which
already finished.

One instance may back several independent scope trees, including concurrent ones, as long as they
run on a single event loop. Each tree is summarized and released on its own. A scope completes only
once every scope nested below it completed, so every scope keeps its place in the tree regardless of
the order they finish in.
