from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from types import TracebackType

import pytest

pytest.importorskip("asyncpg", reason="requires the postgres extra")

from haiway import ctx
from haiway.postgres.state import (
    MIGRATION_COMPLETION_STATEMENT,
    MIGRATIONS_ADVISORY_LOCK_STATEMENT,
    MIGRATIONS_ADVISORY_UNLOCK_STATEMENT,
    MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT,
    MIGRATIONS_LOCK_TIMEOUT_STATEMENT,
    MIGRATIONS_TABLE_CREATE_STATEMENT,
    MIGRATIONS_TABLE_UPGRADE_STATEMENT,
    Postgres,
    PostgresConnection,
)
from haiway.postgres.types import PostgresRow, PostgresValue


class _FakeTransaction:
    def __init__(
        self,
        *,
        isolation: str | None = None,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> None:
        self.isolation = isolation
        self.readonly = readonly
        self.deferrable = deferrable

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


def _connection(
    executed: list[str],
    arguments: list[tuple[PostgresValue, ...]] | None = None,
) -> PostgresConnection:
    def record(
        statement: str,
        args: tuple[PostgresValue, ...],
    ) -> None:
        executed.append(statement.strip())
        if arguments is not None:
            arguments.append(args)

    # fetch and execute are separate driver calls, but the migration runner uses
    # both - recording them together keeps the observed order the issued order
    async def fetch(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]:
        record(statement, args)
        return ()

    async def execute(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> str:
        record(statement, args)
        return "FAKE"

    return PostgresConnection(
        statement_fetching=fetch,
        statement_executing=execute,
        transaction_preparing=_FakeTransaction,
    )


def _postgres(
    executed: list[str],
    arguments: list[tuple[PostgresValue, ...]] | None = None,
) -> Postgres:
    @asynccontextmanager
    async def acquire() -> AsyncIterator[PostgresConnection]:
        yield _connection(executed, arguments)

    return Postgres(connection_acquiring=acquire)


@pytest.mark.asyncio
async def test_execute_migrations_uses_advisory_lock() -> None:
    executed: list[str] = []
    arguments: list[tuple[PostgresValue, ...]] = []

    postgres = _postgres(executed, arguments)

    async def migration(
        connection: PostgresConnection,
    ) -> None:
        await connection.execute("SELECT 1;")

    async with ctx.scope("postgres-migrations-lock", postgres):
        await postgres.execute_migrations([migration])

    assert executed[0] == MIGRATIONS_LOCK_TIMEOUT_STATEMENT.strip()
    # the timeout is passed as a parameter, never formatted into the SQL
    assert arguments[0] == ("300s",)
    assert executed[1] == MIGRATIONS_ADVISORY_LOCK_STATEMENT.strip()
    assert MIGRATIONS_TABLE_CREATE_STATEMENT.strip() in executed
    assert MIGRATION_COMPLETION_STATEMENT.strip() in executed
    assert executed[-2] == MIGRATIONS_ADVISORY_UNLOCK_STATEMENT.strip()
    assert executed[-1] == MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT.strip()


@pytest.mark.asyncio
async def test_execute_migrations_upgrades_the_table_under_the_lock() -> None:
    # the table shape is settled before the version is read and before any
    # migration runs, while the advisory lock keeps other migrators out
    executed: list[str] = []

    postgres = _postgres(executed)

    async def migration(
        connection: PostgresConnection,
    ) -> None:
        await connection.execute("SELECT 1;")

    async with ctx.scope("postgres-migrations-upgrade", postgres):
        await postgres.execute_migrations([migration])

    assert executed[1] == MIGRATIONS_ADVISORY_LOCK_STATEMENT.strip()
    assert executed[2] == MIGRATIONS_TABLE_CREATE_STATEMENT.strip()
    assert executed[3] == MIGRATIONS_TABLE_UPGRADE_STATEMENT.strip()
    assert executed.index("SELECT 1;") > 3


@pytest.mark.asyncio
async def test_execute_migrations_unlocks_on_migration_error() -> None:
    executed: list[str] = []

    postgres = _postgres(executed)

    async def failing_migration(
        connection: PostgresConnection,
    ) -> None:
        raise RuntimeError("boom")

    async with ctx.scope("postgres-migrations-lock-failure", postgres):
        with pytest.raises(RuntimeError, match="boom"):
            await postgres.execute_migrations([failing_migration])

    assert executed[0] == MIGRATIONS_LOCK_TIMEOUT_STATEMENT.strip()
    assert executed[1] == MIGRATIONS_ADVISORY_LOCK_STATEMENT.strip()
    assert executed[-2] == MIGRATIONS_ADVISORY_UNLOCK_STATEMENT.strip()
    assert executed[-1] == MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT.strip()
