from collections.abc import Sequence
from types import TracebackType
from typing import Any

import pytest
from pytest import mark, raises

pytest.importorskip("asyncpg", reason="requires the postgres extra")

from asyncpg.exceptions import UniqueViolationError

from haiway import ctx
from haiway.postgres import (
    Postgres,
    PostgresConnection,
    PostgresConnectionPool,
    PostgresException,
)

# the connection context is where driver calls are actually translated, so the
# translation tests below drive it directly rather than through a stub
from haiway.postgres.client import (
    _ConnectionContext as _PoolConnectionContext,  # pyright: ignore[reportPrivateUsage]
)
from haiway.postgres.client import (
    _TransactionContext,  # pyright: ignore[reportPrivateUsage]
)
from haiway.postgres.configuration import PostgresConfigurationRepository
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
    transactions: list[_FakeTransaction] | None = None,
) -> PostgresConnection:
    async def fetch(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]:
        executed.append(statement.strip())
        return ()

    async def execute(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> str:
        executed.append(statement.strip())
        return "FAKE"

    def transaction(
        *,
        isolation: str | None = None,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> _FakeTransaction:
        prepared = _FakeTransaction(
            isolation=isolation,
            readonly=readonly,
            deferrable=deferrable,
        )
        if transactions is not None:
            transactions.append(prepared)

        return prepared

    return PostgresConnection(
        statement_fetching=fetch,
        statement_executing=execute,
        transaction_preparing=transaction,
    )


class _FakePool:
    """Hands out prepared acquire contexts in order, like ``Pool.acquire`` does."""

    def __init__(
        self,
        *pool_contexts: Any,
    ) -> None:
        self.pool_contexts: list[Any] = list(pool_contexts)
        self.acquired: int = 0

    def acquire(
        self,
        *,
        timeout: float | None = None,
    ) -> Any:
        self.acquired += 1
        return self.pool_contexts.pop(0)

    # occupancy introspection, so the gauge path runs instead of being skipped
    def get_size(self) -> int:
        return 2

    def get_idle_size(self) -> int:
        return 1


def _pool_connection_context(
    *pool_contexts: Any,
) -> Any:
    """Build the real connection context over fake acquire contexts."""
    return _PoolConnectionContext(
        _pool=_FakePool(*pool_contexts),
        _acquire_timeout=None,
    )


class _ConnectionContext:
    def __init__(
        self,
        connection: PostgresConnection,
    ) -> None:
        self._connection = connection

    async def __aenter__(self) -> PostgresConnection:
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


def _postgres(
    executed: list[str],
    transactions: list[_FakeTransaction] | None = None,
) -> Postgres:
    def acquire() -> _ConnectionContext:
        return _ConnectionContext(_connection(executed, transactions))

    return Postgres(connection_acquiring=acquire)


@mark.asyncio
async def test_execute_acquires_a_temporary_connection() -> None:
    executed: list[str] = []

    async with ctx.scope("test", _postgres(executed)):
        await Postgres.execute("CREATE TABLE a (id INT)")

    assert executed == ["CREATE TABLE a (id INT)"]


@mark.asyncio
async def test_fetch_acquires_a_temporary_connection() -> None:
    executed: list[str] = []

    async with ctx.scope("test", _postgres(executed)):
        assert await Postgres.fetch("SELECT 1") == ()

    assert executed == ["SELECT 1"]


@mark.asyncio
async def test_configuration_migrate_runs_without_a_contextual_connection() -> None:
    # only the pool is installed, matching the documented usage
    executed: list[str] = []

    async with ctx.scope("test", _postgres(executed)):
        await PostgresConfigurationRepository.migrate()

    assert len(executed) == 2
    assert "CREATE TABLE IF NOT EXISTS configurations" in executed[0]
    assert "CREATE INDEX IF NOT EXISTS" in executed[1]


@mark.asyncio
async def test_transaction_forwards_isolation_options() -> None:
    executed: list[str] = []
    transactions: list[_FakeTransaction] = []

    async with ctx.scope("test", _postgres(executed, transactions)):
        async with Postgres.acquire_connection() as connection:
            async with connection.transaction(
                isolation="serializable",
                readonly=True,
                deferrable=True,
            ):
                pass

    assert len(transactions) == 1
    assert transactions[0].isolation == "serializable"
    assert transactions[0].readonly is True
    assert transactions[0].deferrable is True


@mark.asyncio
async def test_transaction_defaults_to_server_settings() -> None:
    executed: list[str] = []
    transactions: list[_FakeTransaction] = []

    async with ctx.scope("test", _postgres(executed, transactions)):
        async with Postgres.acquire_connection() as connection:
            async with connection.transaction():
                pass

    assert transactions[0].isolation is None
    assert transactions[0].readonly is False
    assert transactions[0].deferrable is False


@mark.asyncio
async def test_recursive_acquire_raises_typed_error() -> None:
    # nesting an acquisition would deadlock against a single connection pool,
    # and is reported through the same exception type as every other boundary
    executed: list[str] = []

    async with ctx.scope("test", _postgres(executed)):
        # only a disposable installs PostgresConnection into the scope, which is
        # what makes a second acquisition recursive
        async with ctx.disposables(Postgres.acquire_connection()):
            with raises(PostgresException, match="Recursive"):
                Postgres.acquire_connection()


def test_acquire_before_entering_raises_typed_error() -> None:
    pool = PostgresConnectionPool()

    with raises(PostgresException, match="not initialized"):
        pool.acquire_connection()


@mark.asyncio
async def test_driver_error_arrives_as_postgres_exception_with_sqlstate() -> None:
    # driver failures are translated where the real driver is called, carrying
    # the SQLSTATE so callers can branch on it without importing asyncpg
    class _FailingDriverConnection:
        async def fetch(
            self,
            statement: str,
            /,
            *args: Any,
        ) -> Any:
            raise UniqueViolationError("duplicate key value violates unique constraint")

        async def execute(
            self,
            statement: str,
            /,
            *args: Any,
        ) -> str:
            raise UniqueViolationError("duplicate key value violates unique constraint")

    class _FailingPoolContext:
        async def __aenter__(self) -> _FailingDriverConnection:
            return _FailingDriverConnection()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            return None

    context = _pool_connection_context(_FailingPoolContext())
    async with context as connection:
        with raises(PostgresException) as exc_info:
            await connection.execute("INSERT INTO a (id) VALUES (1)")

        # fetch and execute are separate driver calls, so both translate
        with raises(PostgresException) as fetch_info:
            await connection.fetch("INSERT INTO a (id) VALUES (1) RETURNING id")

    for info in (exc_info, fetch_info):
        assert info.value.sqlstate == "23505"
        assert "sqlstate=23505" in str(info.value)
        assert isinstance(info.value.__cause__, UniqueViolationError)
        # the failing statement must never reach the message
        assert "INSERT" not in str(info.value)


def test_exception_without_sqlstate_omits_it() -> None:
    # failures that never reached the server carry no SQLSTATE to report
    error = PostgresException("Failed to acquire Postgres connection")

    assert error.sqlstate is None
    assert "sqlstate" not in str(error)


def test_exception_without_cause_keeps_context() -> None:
    # constructing PostgresException must not assign __cause__ itself, which
    # would set __suppress_context__ and hide an already propagating exception
    try:
        try:
            raise ValueError("inner")

        except ValueError:
            raise PostgresException("outer")  # noqa: B904

    except PostgresException as exc:
        assert exc.__suppress_context__ is False
        assert isinstance(exc.__context__, ValueError)


class _FailingCleanupTransaction:
    """asyncpg transaction whose commit/rollback always fails."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        raise UniqueViolationError("deferred constraint violated")


@mark.asyncio
async def test_commit_failure_is_raised_when_nothing_else_propagates() -> None:
    # a lost commit is the only failure there is, and the caller must hear it
    transaction = _TransactionContext(_transaction_context=_FailingCleanupTransaction())  # pyright: ignore[reportArgumentType]

    with raises(PostgresException, match="Failed to commit"):
        async with transaction:
            pass


@mark.asyncio
async def test_rollback_failure_does_not_mask_the_body_exception() -> None:
    # the exception that caused the rollback is more useful than the rollback
    # failure, so it is the one that propagates
    transaction = _TransactionContext(_transaction_context=_FailingCleanupTransaction())  # pyright: ignore[reportArgumentType]

    with raises(ValueError, match="body failed"):
        async with transaction:
            raise ValueError("body failed")


class _FailingReleasePoolContext:
    """asyncpg pool acquisition whose release always fails."""

    async def __aenter__(self) -> Any:
        class _DriverConnection:
            async def fetch(
                self,
                statement: str,
                /,
                *args: Any,
            ) -> Any:
                return ()

        return _DriverConnection()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        raise RuntimeError("release boom")


@mark.asyncio
async def test_release_failure_does_not_mask_the_body_exception() -> None:
    # releasing back to the pool is cleanup and must not replace the exception
    # already leaving the block
    with raises(ValueError, match="body failed"):
        async with _pool_connection_context(_FailingReleasePoolContext()):
            raise ValueError("body failed")


@mark.asyncio
async def test_release_failure_is_raised_when_nothing_else_propagates() -> None:
    with raises(PostgresException, match="Failed to release"):
        async with _pool_connection_context(_FailingReleasePoolContext()):
            pass


class _TaggingPoolContext:
    """Acquire context recording which driver call each helper reaches for."""

    def __init__(self) -> None:
        self.fetched: list[str] = []
        self.executed: list[str] = []

        context = self

        class _DriverConnection:
            async def fetch(
                self,
                statement: str,
                /,
                *args: Any,
            ) -> Any:
                context.fetched.append(statement)
                return ()

            async def execute(
                self,
                statement: str,
                /,
                *args: Any,
            ) -> str:
                context.executed.append(statement)
                return "UPDATE 3"

        self.connection: Any = _DriverConnection()

    async def __aenter__(self) -> Any:
        return self.connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


@mark.asyncio
async def test_acquire_executes_nothing_against_the_connection() -> None:
    # the connection is handed over as taken from the pool - no liveness probe,
    # so acquiring costs no round trip
    pool_context = _TaggingPoolContext()

    async with _pool_connection_context(pool_context) as connection:
        assert isinstance(connection, PostgresConnection)

    assert pool_context.executed == []
    assert pool_context.fetched == []


@mark.asyncio
async def test_execute_returns_the_raw_command_tag() -> None:
    pool_context = _TaggingPoolContext()

    async with _pool_connection_context(pool_context) as connection:
        assert await connection.execute("UPDATE a SET b = 1") == "UPDATE 3"

    # execute never decodes a result set, so it must not reach fetch
    assert pool_context.executed == ["UPDATE a SET b = 1"]
    assert pool_context.fetched == []


@mark.asyncio
async def test_fetch_does_not_reach_execute() -> None:
    pool_context = _TaggingPoolContext()

    async with _pool_connection_context(pool_context) as connection:
        assert await connection.fetch("SELECT 1") == ()

    assert pool_context.fetched == ["SELECT 1"]
    assert pool_context.executed == []


@mark.asyncio
async def test_fetch_one_takes_the_first_row_and_drops_the_rest() -> None:
    # fetch is the only way rows are loaded, so fetch_one reads the result set and
    # keeps its first row - a statement returning many rows transfers all of them
    class _ManyRowsPoolContext(_TaggingPoolContext):
        def __init__(self) -> None:
            super().__init__()
            context = self

            class _DriverConnection:
                async def fetch(
                    self,
                    statement: str,
                    /,
                    *args: Any,
                ) -> Any:
                    context.fetched.append(statement)
                    return ({"id": 1}, {"id": 2}, {"id": 3})

            self.connection = _DriverConnection()

    pool_context = _ManyRowsPoolContext()
    async with _pool_connection_context(pool_context) as connection:
        row = await connection.fetch_one("SELECT id FROM a")

    assert row is not None
    assert row["id"] == 1
    assert pool_context.fetched == ["SELECT id FROM a"]
