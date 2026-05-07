from collections.abc import Sequence
from contextlib import asynccontextmanager
from types import TracebackType

import pytest

from haiway import ctx
from haiway.postgres.state import (
    MIGRATION_COMPLETION_STATEMENT,
    MIGRATIONS_ADVISORY_LOCK_STATEMENT,
    MIGRATIONS_ADVISORY_UNLOCK_STATEMENT,
    MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT,
    MIGRATIONS_LOCK_TIMEOUT_STATEMENT,
    MIGRATIONS_TABLE_CREATE_STATEMENT,
    Postgres,
    PostgresConnection,
)
from haiway.postgres.types import PostgresRow, PostgresValue


class _FakeTransaction:
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
) -> PostgresConnection:
    async def execute(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]:
        executed.append(statement.strip())
        return ()

    return PostgresConnection(
        statement_executing=execute,
        transaction_preparing=_FakeTransaction,
    )


def _postgres(
    executed: list[str],
) -> Postgres:
    @asynccontextmanager
    async def acquire() -> PostgresConnection:
        yield _connection(executed)

    return Postgres(connection_acquiring=acquire)


@pytest.mark.asyncio
async def test_execute_migrations_uses_advisory_lock() -> None:
    executed: list[str] = []

    postgres = _postgres(executed)

    async def migration(
        connection: PostgresConnection,
    ) -> None:
        await connection.execute("SELECT 1;")

    async with ctx.scope("postgres-migrations-lock", postgres):
        await postgres.execute_migrations([migration])

    assert executed[0] == MIGRATIONS_LOCK_TIMEOUT_STATEMENT.format(300).strip()
    assert executed[1] == MIGRATIONS_ADVISORY_LOCK_STATEMENT.strip()
    assert MIGRATIONS_TABLE_CREATE_STATEMENT.strip() in executed
    assert MIGRATION_COMPLETION_STATEMENT.strip() in executed
    assert executed[-2] == MIGRATIONS_ADVISORY_UNLOCK_STATEMENT.strip()
    assert executed[-1] == MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT.strip()


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

    assert executed[0] == MIGRATIONS_LOCK_TIMEOUT_STATEMENT.format(300).strip()
    assert executed[1] == MIGRATIONS_ADVISORY_LOCK_STATEMENT.strip()
    assert executed[-2] == MIGRATIONS_ADVISORY_UNLOCK_STATEMENT.strip()
    assert executed[-1] == MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT.strip()
