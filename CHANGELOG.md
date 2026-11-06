# Changelog

Notable changes to Haiway. Versions follow the `version` in `pyproject.toml`.

## 0.60.0

### OpenTelemetry integration

The OpenTelemetry bridge was reviewed against the OpenTelemetry specification and semantic
conventions. Telemetry now lands where backends expect it - at the cost of a few changes in what is
exported. No public signature was changed incompatibly.

#### Breaking changes

- **Events and exceptions are exported as log records, not span events.** OpenTelemetry deprecated
  the span event API (`Span.add_event`, `Span.record_exception`) in favor of log based events.
  `ctx.record_*(event=...)` now emits a log record with `event_name`, the severity of its level and
  the scope context. A failed scope emits one `exception` log record. Logging with `exception=...`
  and then raising the same exception reports it once. Nothing is added to `span.events` any more.

  Migration: make sure the logs signal is exported and ingested by your backend (the `configure()`
  path does this, `autoconfigure()` uses whatever logger provider you installed). Dashboards, alerts
  or queries reading span events named after your events or `exception` must read log records
  instead - filter on `event_name`, and correlate with the span through `trace_id` and `span_id`.

- **Failed span status carries the exception type only.** The status description was
  `TypeName: message` and is now `TypeName`; the new `error.type` span attribute holds the same
  value. The message may carry sensitive data - it is available on the `exception` log record.

  Migration: alerts matching the status description text should match `error.type` or the exception
  log record instead.

- **Instrumentation scope is `haiway`, not the service.** The tracer, meter and logger are named
  `haiway` and carry the Haiway version. The service stays identified by the resource
  (`service.name`, `service.version`, ...).

  Migration: queries or views filtering on the instrumentation scope name (`otel.scope.name`,
  `scope_name`) must use `haiway`, or better the resource attributes.

- **`service.instance.id` defaults to a random UUID** instead of the process id, which collided
  across containers.

  Migration: none needed. Pass `instance=` to `configure()` for a stable identifier.

- **Log attribute `scope.name` is now `haiway.scope.name`**, so it does not read as the
  instrumentation scope name in backends.

  Migration: update log queries filtering on `scope.name`.

#### Added

- **Scope kind.** `ctx.scope(..., kind=...)` takes a `ScopeKind` - `"internal"` (default),
  `"server"`, `"client"`, `"producer"` or `"consumer"` - exported from `haiway` and `haiway.context`
  and available as `ContextIdentifier.kind`. The OpenTelemetry bridge maps it to the span kind.
  Starlette and FastAPI request scopes are `"server"`.
- **Route based server span names.** A `server` span is renamed `{method} {route}` once
  `http.request.method` and `http.route` are recorded, and `{method}` for an unrouted request. The
  raw path no longer ends up in span names (it stays in `url.path`); the scope name itself, and so
  logger output, is unchanged. Migration: span name based filters and dashboards for HTTP requests
  now see `GET /users/{id}` instead of `GET /users/12345`.
- **`make benchmark-otel`** and `benchmarks/opentelemetry_benchmark.py` measure the bridge against
  the logger backend.

#### Messaging

- **RabbitMQ publishing is traced and measured.** Each publish runs in a `producer` scope named
  `publish {destination}` with `messaging.*` attributes, injects the trace context into the message
  headers (explicit `attributes` win over a header of the same name), and records
  `messaging.client.operation.duration` and `messaging.client.sent.messages` - a publish which did
  not complete, cancelled ones included, is reported with `error.type`. Migration: none required;
  consumers on other stacks read `traceparent` from the headers.
- **Settling a message records an event.** `acknowledge()`, `reject()` and `async with message`
  record a `messaging.settle` event within the current scope - `ack` at debug level, `reject` at
  info level - with the system, queue, routing key, delivery tag, body size and the plain settle
  options. No scope is opened for the processing. `MQMessage` accepts an optional `source`
  (`MQMessageSource`) for custom queue adapters to describe their messages the same way. Migration:
  none required.

#### Semantic conventions

- **`error.type` is the exception class `__qualname__` everywhere.** The HTTP client used the class
  `__name__`, which differs for nested classes.
- **HTTP client events use `url.full`** instead of `url`. Migration: update queries on the
  `http.request`, `http.response` and `http.request.error` events.
- **Postgres no longer records the adapter method as `db.operation.name`**, which the database
  conventions reserve for what the database was asked to do (`SELECT`, ...). The adapter method
  (`fetch`, `fetch_one`, `execute`, `acquire_connection`) moved to `haiway.postgres.operation`.
  Migration: queries filtering `db.operation.name` on adapter method names must use
  `haiway.postgres.operation`.
- **`db.client.connection.count` is split by `db.client.connection.state`** instead of `state`.

#### Security

- **Unknown HTTP methods are bounded.** The request method is chosen by the caller and is a
  dimension of the request metrics and part of the server span name. A method outside the standard
  set is now recorded as `_OTHER` (span name `HTTP`), with the received value in
  `http.request.method_original` on the span, so a caller can not create unbounded metric series or
  span names. Migration: queries for custom methods must use `http.request.method_original`.
- **Scheme relative URLs are redacted.** The HTTP client recorded `//user:password@host/...` with
  its userinfo, as it recognised only `scheme://` URLs - and one quoting `https://` in its path
  slipped past the check as well. Userinfo is now replaced with `REDACTED` for those too.
- **`server.address` is read only from the authority of the URL itself.** It was taken from any
  `://` in the URL, a relative URL quoting another one in its path or query included
  (`/login?next=https://other.example`), which recorded a host the request was never addressed to
  and let the content of a path pick the values of a metric dimension. Migration: none required;
  relative URLs record no `server.address`, as documented.
- **Websocket spans are named after the route** - `WS /rooms/{room}` - instead of the requested
  path, which holds identifiers and tokens more often than not.
- **Exceptions are reported once across nested scopes**, not once per scope they travel through:
  each scope hands what it reported on to its parent.
- **Logged values from untrusted headers are bounded** - a malformed `traceparent` is truncated
  before it is warned about, in addition to being escaped.
- **Messages of exception causes are redacted in exported stack traces.** The chain of causes of an
  exception - what the integrations wrap with `raise ... from error`, a database driver message
  quoting a value of a statement among them - is exported by type and frames, with the messages
  replaced by `<redacted>`. The message of the exception itself is exported as it is. Migration:
  diagnose from the types and frames of a cause, or log its message yourself where it is safe to.

#### Changed

- Attribute and event sanitizing is skipped for spans which are not recording (sampled out).

#### Known limitations

- Exporters are gRPC only - `OTEL_EXPORTER_OTLP_PROTOCOL` is not honored.
- Some integration attributes still deviate from the semantic conventions: HTTP client events carry
  an unprefixed `duration`, and connection metrics have no pool name.
- RabbitMQ consuming records settle events only - the message carries the trace context of the
  publisher in its headers, but a scope nested within the one of the consumer loop stays in the
  trace of that loop.

### Native State (opt-in)

A C extension takes over the storage, validation, updating, equality, hashing, conversion to
mappings and JSON of `State`. The pure Python implementation is the default and stays complete -
nothing changes unless `HAIWAY_NATIVE=1` is set, which uses the extension and fails, rather than
falling back, when it is not built. Both implementations run the same test suite on Linux, macOS and
Windows with Python 3.14 and 3.15.

#### Added

- **Native `State`** (`HAIWAY_NATIVE=1`). Instances hold their attributes inline, with no `__dict__`
  or `__weakref__`, so an attribute can't be rebound through `object.__setattr__` either. Creating,
  updating, validating a mapping and comparing an instance is 4 to 10 times faster, converting to a
  mapping, a basic object or JSON up to 100 times for large states. Attribute reads and request
  handling are unchanged, and an instance is slightly larger than one with slots.
- **JSON in both directions.** `to_json`, `from_json` and `from_json_array` write and read the JSON
  without the intermediate Python objects, for the default `JSONEncoder` and `JSONDecoder` and no
  `indent`. Anything else - another encoder or decoder, a value with no native spelling, a malformed
  or unusual document - is handled by the Python implementation, which also reports every error.
- `make native` rebuilds the extension, `make test-native` runs the tests against it.
- Wheels carry the extension (`cp314` and `cp315`, Linux, macOS and Windows), the source
  distribution compiles it when there is a compiler and goes without it otherwise.

#### Changed

- **The build backend is `setuptools`**, as no backend of `uv` compiles C. `uv` stays the tool for
  everything else.
- `State.__init__` of the native implementation refuses to initialize an instance a second time,
  and reports a key which is not an attribute after the values before it were validated, not ahead
  of them. The Python implementation is unchanged.
- `Map` and `Meta` subclass the builtin `frozendict` where Python has one (3.15), so they can't be
  mutated through the `dict` methods either. They stay `dict` based on 3.14.

  Migration: `isinstance(map, dict)` is `False` on 3.15 - check against `Mapping`. `{} | map` and
  `{} | meta` are plain `dict`s, as `dict.__or__` handles a `frozendict` itself.

### Generics

- **Type parameter defaults (PEP 696).** A defaulted parameter may be left out of a specialization,
  and an unspecialized generic stands for its defaults, so `Box(value="x")` fails validation when
  `Box` is `class Box[T = int](State)`. A parameter without a default stands for its bound, or for
  anything, as before.
- **Variadic parameters (PEP 646) and parameter specifications (PEP 612)** are specialized and
  validated position by position, and given to a generic attribute of another generic class.
- **Variance** is honored when declared, and inferred for parameters of the `class Box[T]` syntax
  from where the attributes use them - read from only is covariant, used as an argument of a callable
  is contravariant, both is invariant. It is followed through nested generic States, type aliases
  and `Concatenate`. A parameter declared without a variance keeps being covariant.

  Migration: a generic `State` using its parameter as an argument of a callable attribute now
  accepts and rejects other variants than before, in `isinstance`, `issubclass` and validation.
- `class Box(State, Generic[T])` can be specialized. It was refused with "takes 0 type arguments".
- Specializing a generic looks up an existing variant first - `Box[int]` is about twice as fast.

### Other changes

- `AttributePath` keeps the path reached by an attribute or an item, up to 256 item paths for each
  path - `State._.attribute` is about four times faster.
- Identifiers of scopes and traces come from a generator of their own - a random version 4 UUID
  without a system call - which also reseeds in a forked process. They are not secrets, and are no
  longer drawn from the operating system. Request handling is about 8% cheaper.
- The license metadata is the SPDX expression `MIT`.
