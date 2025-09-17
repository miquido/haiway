# Postgres

Haiway ships with a context-aware Postgres integration that wraps `asyncpg`, exposes typed row
helpers, and coordinates schema migrations through the state system. The feature keeps the
framework's functional style while handling connection pooling and transactions for you.

## Overview

- **Context Managed**: Acquire connections through Haiway scopes to ensure cleanup
- **Typed Accessors**: `PostgresRow` exposes helpers for UUIDs, datetimes, and primitive types
- **Protocol Driven**: Backends plug in via protocols, enabling custom clients in tests
- **Migrations Included**: Built-in runner discovers and executes ordered migration modules
- **Immutable State**: Connections and rows are immutable `State` objects with strict typing

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
defaults so the driver works out of the box:

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

## Working with Connections

`Postgres` is a `State` that exposes functional helpers: `fetch`, `fetch_one`, and `execute`. When
called outside an existing connection scope the helpers acquire and release a connection
automatically. Inside a scope that already provides a `PostgresConnection`, the helpers reuse the
instance and avoid nested acquisitions.

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
async with ctx.scope("postgres", disposables=(PostgresConnectionPool(),)):
    # make sure to acquire the connection for transaction and use PostgresConnection
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
`migration_<number>` expose a `migration` coroutine.

```python
async with ctx.scope("migrations", disposables=(PostgresConnectionPool(),)):
    await Postgres.execute_migrations("my_app.db.migrations")
```

The runner ensures a `migrations` table exists, reads the current version, and applies any pending
entries in numeric order. Each migration executes inside its own transaction and appends an entry to
the table once complete.

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
from haiway.postgres.state import Postgres, PostgresConnection

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

**Use scopes**: Always run database operations within a context that manages disposables **Group
statements**: Acquire a connection explicitly when running related queries **Validate data**: Prefer
`PostgresRow` accessors over direct subscripting **Log migrations**: Watch the logger output during
migration execution for progress tracking **Surface errors**: Catch `PostgresException` to translate
into application-level failures
