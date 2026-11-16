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
- `concurrently`, `execute_concurrently`, `process_concurrently`, `stream_concurrently` (with
  `stream2_concurrently`, `stream3_concurrently`, `stream4_concurrently`)
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

- `@asynchronous` runs a synchronous function in an executor and returns an awaitable wrapper. The
  function runs within a copy of the caller's context, so `ctx.state(...)` resolves there, while the
  threads of the executor never keep that context once the call is done.
- `@cache` memoizes async function results in-process with LRU eviction and optional expiration.
- `@retry` retries sync or async functions when handled exceptions occur.
- `@throttle` rate-limits async call starts within a time window.
- `@timeout` raises `TimeoutError` if an async function exceeds the configured duration.

These decorators do not install state by themselves; they wrap the target callable.

## Concurrency Helpers

The helpers in `haiway.helpers.concurrent` all integrate with Haiway task management via local
`ContextTaskGroup`s.

- `process_concurrently` is for side effects only.
- `execute_concurrently` applies one async handler to elements and returns results in input order.
- `concurrently` runs pre-created coroutine objects and also preserves input order.
- `stream_concurrently` merges any number of async generators and yields items as they arrive;
  `stream2_concurrently`, `stream3_concurrently` and `stream4_concurrently` are its typed variants
  keeping each source's type in the element union.

Important details:

- `concurrent_tasks` defaults to `2` for the bounded fan-out helpers.
- `execute_concurrently` and `concurrently` preserve input order, not completion order.
- `stream_concurrently(..., exhaustive=False)` stops when any source ends.
- `stream_concurrently(..., exhaustive=True)` continues until all sources end.
- Every helper closes an async generator source it consumed, however the consumption ended.

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

## URI Templates

`URITemplate` is an immutable URI template following
[RFC 6570](https://www.rfc-editor.org/rfc/rfc6570) that expands values into a URI and matches a URI
back into its parameters.

```python
from haiway import URITemplate

template = URITemplate.of("https://{tenant}.example.com/files{/name}{?page,tag*}")

template.expand({"tenant": "acme", "name": "a b.txt", "page": 2, "tag": ["x", "y"]})
# "https://acme.example.com/files/a%20b.txt?page=2&tag=x&tag=y"

template.match("https://acme.example.com/files/a%20b.txt?page=2")
# Map({"tenant": "acme", "name": "a b.txt", "page": "2"})
```

Supported are `{name}` (a path segment or host label), `{/name}`, `{?a,b*}` (`*` explodes a list
into repeated keys) and `{#name}`, with dotted names (`{user.id}`). Expansion follows the RFC
examples: unreserved characters only stay as they are (a fragment keeps the reserved ones too), a
list expands comma separated, an empty query value is kept (`x=`) and `None` is omitted. The
reserved, label, matrix and continuation operators (`+ . ; &`), prefix modifiers, several names
within an expression other than a query and mappings are not supported and refused when the template
is created.

Matching is what makes the template usable to guard lookups, and where it differs from the RFC,
which only defines expansion:

- Every decoded path or host value is held to its segment: values decoding to a path separator,
  `.`/`..`, a control character or invalid UTF-8 yield no match. A host value is held to the host
  label too - no delimiter (`? # @`), whitespace or character normalizing to one (a fullwidth `／`).
- Expansion refuses the same values (`ValueError`), so whatever a template expands also matches it.
  Path and host variables are required, the RFC would expand an undefined one to nothing.
- Only the declared query parameters are extracted, the first value of each.
- A query and a fragment end a template, and an empty segment (`/{/host}`) is refused. A segment
  holds at most one variable (`/{a}-{b}` is refused). Several of them, or anything following an
  optional query, would match a URI in many ways, which makes matching take time polynomial in the
  length of a URI an attacker controls.
- Values of an unsupported type (bytes, mappings, sets, nested sequences) raise `TypeError` instead
  of expanding their representation.

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

A recording at a level nobody keeps costs next to nothing: without a retained context the backend
tells `ctx` which levels its logger discards, so a `ctx.log_debug(...)` with `DEBUG` turned off
returns before its arguments are formatted or even passed on. A backend of your own gets the same by
giving `Observability` a `level_checking` function - `(scope, level) -> bool` - answering whether
anything recorded at that level would be kept. Leaving it out passes every recording on, for the
recording functions to decide.

```python
from haiway import LoggerObservability, ctx

async with ctx.scope("work", observability=LoggerObservability(debug_context=False)):
    ctx.log_debug("Skipped without formatting when DEBUG is off: %s", expensive)
```

One instance may back several independent scope trees, including concurrent ones, as long as they
run on a single event loop. Each tree is summarized and released on its own. A scope completes only
once every scope nested below it completed, so every scope keeps its place in the tree regardless of
the order they finish in.
