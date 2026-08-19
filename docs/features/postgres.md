# Postgres

Haiway ships with a context-aware Postgres integration that wraps `asyncpg`, exposes typed row
helpers, and coordinates schema migrations through the state system. The feature keeps the
framework's functional style while handling connection pooling and transactions for you.

## Overview

- **Context Managed**: Acquire connections through Haiway scopes to ensure cleanup
- **Typed Accessors**: `PostgresRow` (an immutable mapping) exposes helpers for UUIDs, datetimes,
  and primitive types
- **Protocol Driven**: Backends plug in via protocols, enabling custom clients in tests
- **Migrations Included**: Built-in runner discovers and executes ordered migration modules
- **Configuration Storage**: Optional `ConfigurationRepository` backed by versioned Postgres rows
- **Immutable State**: Connections are exposed as `State`; rows are immutable `Mapping` wrappers
  with strict typing helpers
- **Instrumented**: Operation, connection and transaction metrics recorded through `Observability`

## Quick Start

Install the Postgres extra to pull in `asyncpg`:

```bash
pip install haiway[postgres]
```

Use the provided `PostgresConnectionPool` as a disposable resource:

```python
from haiway import ctx
from haiway.postgres import Postgres, PostgresConnectionPool, PostgresRow

async with ctx.scope(
    "postgres",
    disposables=(PostgresConnectionPool(),),
):
    await Postgres.execute(
        "INSERT INTO users(email) VALUES($1)",
        email,
    )
    row: PostgresRow | None = await Postgres.fetch_one(
        "SELECT email FROM users WHERE id = $1",
        user_id,
    )
    return None if row is None else row.get_str("email")
```

## Configuration

Connection parameters are sourced from environment variables when a pool is constructed, not when
the module is imported, so `load_env()` applies regardless of import order. All values have sane
defaults so the driver works out of the box:

| Variable                   | Default     | Description                           |
| -------------------------- | ----------- | ------------------------------------- |
| `POSTGRES_HOST`            | `localhost` | Server hostname                       |
| `POSTGRES_PORT`            | `5432`      | Server port                           |
| `POSTGRES_DATABASE`        | `postgres`  | Database name                         |
| `POSTGRES_USER`            | `postgres`  | Authentication user                   |
| `POSTGRES_PASSWORD`        | unset       | Authentication password               |
| `POSTGRES_SSLMODE`         | `require`   | Value forwarded to the pool `ssl` arg |
| `POSTGRES_CONNECTIONS`     | `1`         | Maximum number of open connections    |
| `POSTGRES_ACQUIRE_TIMEOUT` | `30.0`      | Seconds to wait for a free connection |
| `POSTGRES_COMMAND_TIMEOUT` | `0.0`       | Per-statement limit; `0` disables it  |
| `POSTGRES_CLOSE_TIMEOUT`   | `30.0`      | Seconds to wait for pool shutdown     |
| `POSTGRES_STATEMENT_CACHE` | `100`       | Prepared statement cache size         |

Provide custom environment variables or pass explicit keyword arguments to `PostgresConnectionPool`
when instantiating it to tweak connection parameters. The first six describe the connection target
and apply to a directly constructed pool only - a pool created from a connection string takes its
target from that string alone, see [Connection strings](#connection-strings) below.

There is deliberately no default password: an unset `POSTGRES_PASSWORD` is passed to the driver as
absent, which lets it resolve `PGPASSWORD` or a `.pgpass` entry and fail loudly when the server
expects credentials.

`POSTGRES_SSLMODE` defaults to `require`, which encrypts the connection and refuses a plaintext
fallback. Note that `require` does not validate the server certificate - use `verify-ca` or
`verify-full` where a CA bundle is available, and reserve `prefer` or `disable` for local
development, keeping in mind that `prefer` silently downgrades to plaintext when the server declines
TLS.

Both of these defaults changed and may break deployments that relied on the previous values.
`POSTGRES_PASSWORD` no longer defaults to `postgres`, so an unset value now defers to `PGPASSWORD`
or a `.pgpass` entry and fails to authenticate when neither is present. `POSTGRES_SSLMODE` no longer
defaults to `prefer`, so a server without TLS needs an explicit `POSTGRES_SSLMODE=prefer` or
`disable`.

### Connection strings

A connection target comes from exactly one source, never a mix of the two. Explicit arguments
configure a directly constructed pool, while a connection string (DSN, or *Data Source Name*) is
accepted only by the `of` factory method - which takes no connection arguments at all:

```python
pool = PostgresConnectionPool.of(
    "postgresql://analytics@db.internal:5432/events?sslmode=verify-full",
    connection_limit=6,
)
```

Nothing is merged into the DSN and nothing is taken out of it: it reaches `asyncpg` byte for byte.
Host lists for failover, unix socket directories, percent-encoded credentials, and unmodelled
options such as `sslrootcert`, `application_name`, `options` or `target_session_attrs` therefore all
keep working with full libpq fidelity:

```python
# both hosts are tried, the port binds to the host it follows
PostgresConnectionPool.of("postgresql://svc@primary.db,standby.db:5433/events")
# connects over the unix socket, not over TCP to localhost
PostgresConnectionPool.of("postgresql:///events?host=/var/run/postgresql")
```

Everything the DSN omits is resolved by `asyncpg` itself - from libpq's own environment (`PGHOST`,
`PGPORT`, `PGUSER`, `PGDATABASE`, `PGPASSWORD`, `.pgpass`, service files) - and **not** from the
`POSTGRES_*` connection defaults, which describe a directly constructed pool. `host`, `port`,
`database`, `user`, `password`, and `ssl` cannot be combined with a DSN, so no argument can silently
shadow what the connection string defines.

TLS is part of the connection specification, so the DSN owns it through `sslmode` and the `ssl*`
options. Mind the consequence: a DSN without `sslmode` connects under `asyncpg`'s default of
`prefer`, which silently downgrades to plaintext, rather than under the `require` default of the
`ssl` argument. State `sslmode` explicitly in every DSN.

Pool behavior is configured by arguments either way - a connection string cannot express it. `of`
accepts `connection_limit`, `acquire_timeout`, `command_timeout`, `close_timeout` and `initialize`.

The DSN itself is not inspected, which is what keeps it byte for byte faithful - but it also means a
pool setting left in one is not caught at construction time:

```python
PostgresConnectionPool.of("postgresql://db.internal/events?connections=6")
# accepted here; fails at connect time with:
# unrecognized configuration parameter "connections"
```

`asyncpg` forwards every query parameter it does not recognize to the server as a startup setting,
and it recognizes none of `connections`, `connection_limit`, `maxsize`, `max_size`, the non-libpq
`ssl`, or `connect_timeout` - so all of those have to stay out of the DSN and be passed as arguments
instead. An unsupported URL scheme likewise surfaces when connecting rather than in `of`.

### Timeouts

`POSTGRES_CONNECTIONS` defaults to `1`, so concurrent scopes contend for a single connection. Rather
than waiting indefinitely, acquisition is bounded by `acquire_timeout` and raises
`PostgresException` when it expires:

```python
pool = PostgresConnectionPool(
    connection_limit=10,
    acquire_timeout=5.0,    # give up waiting for a free connection
    command_timeout=30.0,   # cap any single statement
    close_timeout=15.0,     # bound pool shutdown, then terminate
)
```

`close_timeout` matters because closing a pool waits for every connection to be released, which
never completes if one was leaked. After the timeout the pool is terminated instead, and the failure
is reported through observability rather than raised, so it cannot mask an error leaving the scope.

Acquiring runs nothing against the server: the connection is taken from the pool and handed over as
is, so it costs no round trip. `asyncpg` replaces a connection it knows to be closed and recycles
the ones which sat idle longer than five minutes, which covers the socket dropped without a FIN by
an idle timeout on a proxy or NAT in between. A connection killed while pooled - a failover, or a
server restart - is reported as a `PostgresException` by the first statement that runs on it. That
failure is not classified as retriable, since `retries` covers transaction aborts rather than a
broken path to the server, so handle it where re-running the work is safe.

### Connection pooling proxies

`asyncpg` names and caches every prepared statement per connection, which is server-side session
state. A proxy such as PgBouncer pooling by *transaction* releases the server connection after each
transaction and may hand the next one to a different backend - which never saw the `PREPARE`. The
result is intermittent `prepared statement "__asyncpg_stmt_1__" does not exist` (or
`already exists`) failures that work in development, where you keep getting the same backend, and
break under production concurrency.

Set `statement_cache` to `0` to disable the cache. `asyncpg` then prepares *unnamed* statements,
which leave no session state behind, at the cost of a parse per query:

```python
pool = PostgresConnectionPool(statement_cache=0)
```

This cannot be expressed in a connection string. `asyncpg` resolves `statement_cache_size` as a
client setting rather than a connection parameter, so putting it in a dsn sends it to the server as
a startup setting and the connection is rejected with `unrecognized configuration parameter` - the
argument above is the only way to reach it, `of()` included.

Prefer upgrading the proxy where you can: PgBouncer 1.21 and later can track protocol-level prepared
statements per server connection (`max_prepared_statements`), which makes transaction pooling work
without giving up the cache. Pooling by *session* also works unchanged, at the cost of most of the
multiplexing.

## Working with Connections

`Postgres` is a `State` that exposes functional helpers: `fetch`, `fetch_one`, and `execute`. When
called outside an existing connection scope the helpers acquire and release a connection
automatically. Inside a scope that already provides a `PostgresConnection`, the helpers reuse the
instance and avoid nested acquisitions. Explicit recursive calls to `Postgres.acquire_connection()`
from inside an existing connection scope raise `PostgresException`.

`fetch` and `fetch_one` load data; `execute` runs a statement without retrieving a result set and
returns the raw command tag the server reported:

```python
tag = await Postgres.execute("UPDATE users SET active = FALSE WHERE id = $1", user_id)
# "UPDATE 1"
```

The tag is passed through untouched - `"UPDATE 3"`, `"INSERT 0 1"`, `"DELETE 7"`, `"CREATE TABLE"` -
so callers that need the affected row count parse it themselves. A statement with a result set still
runs under `execute`, and the tag reports how many rows it produced, but those rows are never
transferred: use `fetch` to read them.

`execute` also decides its wire protocol from whether parameters were bound. **Without** parameters
it uses the simple query protocol, which carries a whole script - several semicolon-separated
commands run in one round trip, and nothing is prepared or cached:

```python
await Postgres.execute(
    """
    CREATE TABLE a (id INT);
    CREATE INDEX a_idx ON a (id);
    """
)
```

**With** parameters it uses the extended query protocol, which carries one command per request, so a
parameterized script has to be split into separate calls. `fetch` and `fetch_one` always use the
extended protocol and are always a single statement.

`fetch_one` reads the result set and keeps its first row, discarding the rest. It imposes no `LIMIT`
of its own, so a statement returning many rows transfers all of them - add `LIMIT 1` to the
statement when that matters, which also lets the planner optimize for it.

To run multiple statements on a single connection, acquire it explicitly:

```python
async with ctx.scope("postgres", disposables=(PostgresConnectionPool(),)):
    async with ctx.disposables(Postgres.acquire_connection()):
        await Postgres.execute("SET search_path TO app")
        rows = await Postgres.fetch("SELECT * FROM users")
```

## Typed Rows

Every result row is wrapped in `PostgresRow`, an immutable mapping that validates column access. Use
the helper methods to retrieve typed values:

```python
row: PostgresRow | None = await Postgres.fetch_one("SELECT id, joined_at FROM users WHERE email = $1", email)

if row is not None:
    user_id = row.get_int("id")
    joined = row.get_datetime("joined_at")
```

The helpers raise `TypeError` when the underlying value does not match the expected type, keeping
type assumptions honest at runtime. Nothing is coerced: `get_int` rejects a `bool` even though it
subclasses `int`, so a BOOLEAN column cannot silently become `0`, and `get_bool` does no truthiness
conversion.

Use `get_decimal` for `numeric` and `decimal` columns:

```python
amount = row.get_decimal("amount", required=True)
```

`get_float` deliberately refuses a `Decimal` and points at `get_decimal` instead. A binary float
cannot represent every decimal fraction, so widening `numeric` there would discard exactly the
exactness the column type was chosen for - the case that matters for money. `get_decimal` accepts a
native `Decimal`, widens an `int` (which is exact), and parses a `str`, but rejects a `float`: that
value has already lost its precision, and converting it would only make the loss harder to notice.

`get_float` still accepts an `int`, which is exact below `2**53`; a column holding larger integers
belongs to `get_int`.

## Transactions

`PostgresConnection.transaction()` returns an async context manager handling transaction
automatically:

```python
from haiway.postgres import PostgresConnection

async with ctx.scope("postgres", disposables=(PostgresConnectionPool(),)):
    async with ctx.disposables(Postgres.acquire_connection()):
        async with PostgresConnection.transaction():
            await PostgresConnection.execute("DELETE FROM jobs WHERE finished")
            await PostgresConnection.execute("INSERT INTO audit(action) VALUES('cleanup')")
```

Any exception raised inside the block rolls back the transaction; successful execution commits the
changes. A transaction opened while another is already active becomes a savepoint.

Isolation and access mode can be selected per transaction:

```python
async with PostgresConnection.transaction(
    isolation="serializable",   # or "read_committed" / "repeatable_read"
    readonly=True,
    deferrable=True,            # only meaningful for serializable read-only
):
    await PostgresConnection.fetch("SELECT * FROM ledger")
```

Omitting these uses the server defaults.

## Migrations

The optional, lightweight migration runner executes callables conforming to `PostgresMigrating`. You
can pass either a sequence of migrations or a dotted module path where submodules named
`migration_<number>` expose a `migration` coroutine. Module names must use a continuous sequence
starting at `migration_0`; gaps or duplicate numbers raise `ValueError`.

```python
async with ctx.scope("migrations", disposables=(PostgresConnectionPool(),)):
    await Postgres.execute_migrations("my_app.db.migrations")
```

The runner ensures a `migrations` table exists, reads the current version, and applies any pending
entries in numeric order. Each migration executes inside its own transaction and appends an entry to
the table once complete.

```sql
CREATE TABLE migrations (
    id SERIAL PRIMARY KEY,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    identifier TEXT,
    checksum TEXT
);
```

`executed_at` is `TIMESTAMPTZ`, matching `configurations.created`. A table created before this used
`TIMESTAMP`, so the runner converts the column in place - guarded on the current type, so it neither
rewrites the table on later runs nor touches an already-correct schema. Both the conversion and the
version read happen while the advisory lock is held, so concurrent migrators cannot race.

`identifier` and `checksum` record which migration produced the row. Discovery from a package stores
the dotted module path together with the SHA-256 checksum of the module source file, while
explicitly passed callables store their qualified name and leave the checksum empty - there is no
file to hash. They are nullable and added to an existing table in place, so rows recorded before
they existed simply keep them empty.

Before anything runs, the already applied rows are compared position by position against the current
sequence, and drift is reported as a warning:

```text
...migration 2 was applied as 'my_app.db.migrations.migration_2' but position 2 now holds
'my_app.db.migrations.migration_two' - the sequence was renamed or reordered...
...migration 3 [my_app.db.migrations.migration_3] source changed since it was applied - the database
reflects the previous contents of the file...
```

Verification never fails the run. The applied count alone decides what still has to execute, and
once the database has moved on there is no automatic repair - renumbering the sequence would re-run
or skip migrations against a schema that already changed. The finding is reported so a human can
judge it. A missing `identifier` or `checksum` on either side means there is nothing to compare, not
a mismatch, so legacy rows and callable migrations stay quiet.

```sql
SELECT id, executed_at, identifier, checksum FROM migrations ORDER BY id;
```

Example package layout:

```text
my_app/db/migrations/
├── __init__.py
├── migration_0.py
└── migration_1.py
```

Each module should export an async `migration(connection: PostgresConnection) -> None` callable.

## Configuration Repository

`PostgresConfigurationRepository` adapts Haiway's generic `ConfigurationRepository` to a
Postgres-backed store. It persists immutable configuration snapshots in a `configurations` table and
uses in-memory caching for listing and loading operations.

```python
from haiway import ConfigurationRepository, ctx
from haiway.postgres import PostgresConfigurationRepository, PostgresConnectionPool

async with ctx.scope(
    "config",
    PostgresConfigurationRepository.prepare(),
    disposables=(PostgresConnectionPool(),),
):
    available = await ConfigurationRepository.configurations()
```

Before using the repository, create the backing table and index:

```python
async with ctx.scope("config.migrate", disposables=(PostgresConnectionPool(),)):
    await PostgresConfigurationRepository.migrate()
```

`prepare(...)` expects this schema to exist:

```sql
CREATE TABLE configurations (
    identifier TEXT NOT NULL,
    name TEXT NOT NULL,
    content JSONB NOT NULL,
    created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (identifier, created)
);

CREATE INDEX IF NOT EXISTS configurations_idx
ON configurations (identifier, created DESC);
```

`created` uses `clock_timestamp()` rather than `CURRENT_TIMESTAMP`, which is the *transaction* start
time and would collide on the primary key when two snapshots of one configuration are stored inside
a single transaction. Inserts set the column explicitly, so tables created before this also behave
correctly.

Behavior summary:

- `listing(...)` returns the distinct identifiers, optionally filtered by configuration type
- `loading(...)` fetches the newest row for an identifier and reconstructs it with `from_json(...)`,
  raising `ConfigurationInvalid` when the stored name or content shape does not match
- `defining(...)` inserts a new snapshot row instead of updating in place
- `removing(...)` deletes all rows for the identifier
- successful writes clear the in-memory listing/loading caches
- cache behavior is configurable through `prepare(cache_limit=..., cache_expiration=...)`

## Error Handling

Driver failures are translated into `PostgresException` at every boundary - pool creation,
connection acquisition, statement execution, and transaction begin/commit/rollback:

```python
from haiway.postgres import PostgresException

try:
    await Postgres.fetch("SELECT * FROM missing_table")
except PostgresException as exc:
    ctx.log_error("Postgres query failed", exception=exc)
    exc.sqlstate  # PostgreSQL SQLSTATE, e.g. "42P01", when reported
```

`sqlstate` lets callers branch on specific conditions without importing `asyncpg`. Use
`PostgresErrorCode` rather than a literal - a mistyped code compiles fine and then silently never
matches, turning a handled condition into an unhandled failure:

```python
from haiway.postgres import PostgresErrorCode, PostgresException

try:
    await Postgres.execute("INSERT INTO users(email) VALUES($1)", email)
except PostgresException as exc:
    if exc.sqlstate == PostgresErrorCode.unique_violation:
        ...  # the address is already registered
    raise
```

`PostgresErrorCode` is a `StrEnum`, so a member compares equal to the raw `sqlstate` with no
conversion, and the two-character class prefix still works for handling a whole class at once
(`exc.sqlstate.startswith("23")` for any integrity constraint violation). It covers the codes
applications actually dispatch on, not all several hundred - anything missing is still readable as
the raw string.

The originating driver exception is preserved as `__cause__`. Statements and parameter values are
deliberately excluded from the message so credentials and personal data are never surfaced through
error handling.

### Retries

`PostgresException.retriable` reports whether re-running the same work is expected to succeed. It is
true for exactly two conditions - a serialization failure and a detected deadlock. PostgreSQL
resolves both by aborting one transaction rather than blocking, and both succeed when simply run
again.

The execution helpers take a `retries` argument that acts on that:

```python
rows = await Postgres.fetch(
    "UPDATE accounts SET balance = balance - $2 WHERE id = $1 RETURNING balance",
    account_id,
    amount,
    retries=3,
)
```

Anything not retriable is raised on the first attempt, since running it again would fail the same
way. Delays use full jitter, so concurrent retries spread out instead of realigning and conflicting
again in lockstep.

**`retries` repeats one statement.** That is the whole unit of work under autocommit, which is what
this covers. It is *not* the unit of work inside an explicit transaction: a serialization failure
aborts the entire transaction, every later statement in it fails with `in_failed_sql_transaction`,
and the block has to be re-entered from the beginning. Retrying at that level means re-running the
body, which `retries` cannot do for you - so build the loop around the `transaction()` block
instead.

Because that misuse is easy to reach, it is detected rather than left to confuse: when a retry is
rejected with `in_failed_sql_transaction`, the retry stops immediately and the original failure -
the one that actually explains the outcome - is raised, with the rejection kept as `__cause__`.

This matters most for `serializable`. PostgreSQL does not block to resolve snapshot conflicts under
that isolation level; it aborts one transaction with a serialization failure. Raising isolation
without retrying therefore converts silent anomalies into intermittent failures that only appear
under concurrency.

## Observability

The adapter records metrics and events through the contextual `Observability`, so they reach
whatever backend the scope was given - an OpenTelemetry exporter, or the logger fallback when none
was configured. Names follow OpenTelemetry's database semantic conventions where one exists, so
`db.response.status_code` carries the SQLSTATE those conventions specify for PostgreSQL.

| Metric                             | Kind      | Recorded by                   |
| ---------------------------------- | --------- | ----------------------------- |
| `db.client.operation.duration`     | histogram | `fetch`/`fetch_one`/`execute` |
| `db.client.response.returned_rows` | histogram | `fetch`/`fetch_one`           |
| `db.client.operation.retries`      | counter   | a retried statement           |
| `db.client.connection.wait_time`   | histogram | acquiring a connection        |
| `db.client.connection.count`       | gauge     | acquiring a connection        |
| `db.client.connection.timeouts`    | counter   | an acquire that gave up       |
| `db.client.transaction.duration`   | histogram | leaving a transaction         |

Events accompany them: `db.client.operation.retried` at debug level, plus
`db.client.connection.failed` at warning level, since a refused acquire means the path to the server
was disrupted.

`db.client.transaction.duration` carries a `db.transaction.outcome` attribute reporting what the
completion attempt actually did, not what it intended: `committed`, `rolled_back`, `commit_failed`,
or `rollback_failed`. The third is a transaction whose work is lost even though the block left
cleanly, and is the one worth alerting on.

Recorded attributes are `db.system.name`, `db.operation.name`, and for a failure
`db.response.status_code` and `error.type`. Statements and their parameters are not recorded.

### Reading the two durations

`db.client.operation.duration` starts once a connection is in hand, and spans every attempt plus the
delays between them - so a statement that only succeeded on its third try reports what that took. It
does **not** include acquiring the connection; `db.client.connection.wait_time` measures that.

Adding the two gives what the caller waited for, while keeping them separable - which matters
because `POSTGRES_CONNECTIONS` defaults to `1`, so a slow call is as likely to be a contended pool
as a slow statement. `db.client.connection.timeouts` counts the acquisitions that gave up entirely;
the wait histogram only records a wait that produced a connection, so exhaustion would otherwise be
invisible.

`db.client.response.returned_rows` counts what a fetch actually transferred, before `fetch_one`
discards anything. A large value on a `fetch_one` is the signal that the statement needs a
`LIMIT 1`.

Metrics are recorded at info level, because both the OpenTelemetry and logger backends filter
metrics by level and OpenTelemetry defaults to info - anything lower would be invisible in
production. Note that the logger fallback renders every metric as a log line, and under `__debug__`
also retains it for the scope tree summary, so a long-lived scope issuing many statements
accumulates them. Configure an OpenTelemetry backend, or raise the logger level, for anything
query-heavy.

## Testing

Swap the default connection acquisition with a stub that records executed statements or returns
prepared data. Implement the connection protocols from `haiway.postgres.types` to adapt in-memory
fixtures without touching a real database.

```python
from haiway import ctx
from haiway.postgres import Postgres, PostgresConnection

class _NoopTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_) -> None:
        return None

class FakeConnectionContext:
    def __init__(self):
        self.statements = []

    async def __aenter__(self) -> PostgresConnection:
        async def fetch(statement: str, /, *args):
            self.statements.append((statement, args))
            return tuple()

        async def execute(statement: str, /, *args):
            self.statements.append((statement, args))
            return "FAKE"

        def transaction(*, isolation=None, readonly=False, deferrable=False):
            return _NoopTransaction()

        return PostgresConnection(
            statement_fetching=fetch,
            statement_executing=execute,
            transaction_preparing=transaction,
        )

    async def __aexit__(self, *_) -> None:
        return None

class FakePostgres:
    def __init__(self, context: FakeConnectionContext):
        self._context = context

    async def __aenter__(self) -> Postgres:
        return Postgres(connection_acquiring=lambda: self._context)

    async def __aexit__(self, *_) -> None:
        return None

async def test_insert():
    connection_context = FakeConnectionContext()
    async with ctx.scope("test", disposables=(FakePostgres(connection_context),)):
        await Postgres.execute("INSERT INTO audit(action) VALUES($1)", "created")

    assert connection_context.statements == [
        ("INSERT INTO audit(action) VALUES($1)", ("created",)),
    ]
```

## Best Practices

- Use `ctx.scope(...)` or `ctx.disposables(...)` so pools and acquired connections are cleaned up.
- Acquire a connection explicitly when several statements must share one transaction or session.
- Prefer `PostgresRow` accessors over direct subscripting when the column type matters.
- Keep migration modules numbered continuously from `migration_0`.
- Catch `PostgresException` at the application boundary and translate it into domain-specific
  errors.
