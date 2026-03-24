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

Connection parameters are sourced from environment variables at import time. All values have sane
defaults so the driver works out of the box. Because these values are read when the module is
imported, changing the environment later does not affect already-imported defaults:

| Variable               | Default     | Description                           |
| ---------------------- | ----------- | ------------------------------------- |
| `POSTGRES_HOST`        | `localhost` | Server hostname                       |
| `POSTGRES_PORT`        | `5432`      | Server port                           |
| `POSTGRES_DATABASE`    | `postgres`  | Database name                         |
| `POSTGRES_USER`        | `postgres`  | Authentication user                   |
| `POSTGRES_PASSWORD`    | `postgres`  | Authentication password               |
| `POSTGRES_SSLMODE`     | `prefer`    | Value forwarded to the pool `ssl` arg |
| `POSTGRES_CONNECTIONS` | `1`         | Maximum number of open connections    |

Provide custom environment variables or pass explicit keyword arguments to `PostgresConnectionPool`
when instantiating it to tweak connection parameters.

To bootstrap from an existing connection string (DSN, or *Data Source Name*), use the `of`
constructor. The DSN can be provided positionally or as the `dsn` keyword:

```python
pool = PostgresConnectionPool.of(
    dsn="postgresql://analytics@db.internal:5432/events?sslmode=require&connections=6",
)
```

The helper parses the DSN, applies sane defaults for missing components, and respects query-string
overrides such as `sslmode`, `ssl`, `connections`, `connection_limit`, `maxsize`, or `max_size`.

## Working with Connections

`Postgres` is a `State` that exposes functional helpers: `fetch`, `fetch_one`, and `execute`. When
called outside an existing connection scope the helpers acquire and release a connection
automatically. Inside a scope that already provides a `PostgresConnection`, the helpers reuse the
instance and avoid nested acquisitions. Explicit recursive calls to `Postgres.acquire_connection()`
from inside an existing connection scope raise `RuntimeError`.

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
type assumptions honest at runtime.

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
changes.

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

Example package layout:

```text
my_app/db/migrations/
├── __init__.py
├── migration_0.py
└── migration_1.py
```

Each module should export an async `migration(connection: PostgresConnection) -> None` callable.

## Configuration Repository

`PostgresConfigurationRepository()` adapts Haiway's generic `ConfigurationRepository` to a
Postgres-backed store. It persists immutable configuration snapshots in a `configurations` table and
uses in-memory caching for listing and loading operations.

```python
from haiway import ConfigurationRepository, ctx
from haiway.postgres import PostgresConfigurationRepository, PostgresConnectionPool

async with ctx.scope(
    "config",
    PostgresConfigurationRepository(),
    disposables=(PostgresConnectionPool(),),
):
    available = await ConfigurationRepository.configurations()
```

The repository expects this schema to exist before use:

```sql
CREATE TABLE configurations (
    identifier TEXT NOT NULL,
    name TEXT NOT NULL,
    content JSONB NOT NULL,
    created TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (identifier, created)
);

CREATE INDEX IF NOT EXISTS configurations_idx
ON configurations (identifier, created DESC);
```

Behavior summary:

- `listing(...)` returns the newest distinct identifiers, optionally filtered by configuration type
- `loading(...)` fetches the newest row for an identifier and reconstructs it with `from_json(...)`
- `defining(...)` inserts a new snapshot row instead of updating in place
- `removing(...)` deletes all rows for the identifier
- successful writes clear the in-memory listing/loading caches
- cache behavior is configurable through `cache_limit` and `cache_expiration`

## Error Handling

Unexpected execution failures raise `PostgresException`:

```python
from haiway.postgres import PostgresException

try:
    await Postgres.fetch("SELECT * FROM missing_table")
except PostgresException as exc:
    ctx.log_error("Postgres query failed", exception=exc)
```

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
        async def execute(statement: str, /, *args):
            self.statements.append((statement, args))
            return tuple()

        return PostgresConnection(
            statement_executing=execute,
            transaction_preparing=lambda: _NoopTransaction(),
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
