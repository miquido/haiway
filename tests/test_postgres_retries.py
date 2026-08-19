from collections.abc import Sequence

import pytest
from pytest import mark, raises

pytest.importorskip("asyncpg", reason="requires the postgres extra")

from haiway import ctx
from haiway.postgres import (
    Postgres,
    PostgresConnection,
    PostgresErrorCode,
    PostgresException,
)
from haiway.postgres.types import PostgresRow, PostgresValue


def _connection(
    failures: list[PostgresException | None],
    attempts: list[str],
) -> PostgresConnection:
    def attempt(statement: str) -> None:
        attempts.append(statement)
        failure = failures.pop(0) if failures else None
        if failure is not None:
            raise failure

    # retries wrap both primitives, so both share the scripted failures
    async def fetch(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]:
        attempt(statement)
        return ()

    async def execute(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> str:
        attempt(statement)
        return "FAKE"

    def transaction(**kwargs: object) -> object:  # pragma: no cover - unused here
        raise NotImplementedError

    return PostgresConnection(
        statement_fetching=fetch,
        statement_executing=execute,
        transaction_preparing=transaction,  # pyright: ignore[reportArgumentType]
    )


def _serialization_failure() -> PostgresException:
    return PostgresException(
        "could not serialize access",
        sqlstate=PostgresErrorCode.serialization_failure,
    )


@mark.asyncio
async def test_retriable_failure_is_repeated_until_it_succeeds() -> None:
    attempts: list[str] = []
    connection = _connection([_serialization_failure(), _serialization_failure()], attempts)

    async with ctx.scope("test", connection):
        await PostgresConnection.execute("UPDATE a SET b = 1", retries=3)

    assert len(attempts) == 3


@mark.asyncio
async def test_retries_are_not_attempted_by_default() -> None:
    attempts: list[str] = []
    connection = _connection([_serialization_failure()], attempts)

    async with ctx.scope("test", connection):
        with raises(PostgresException) as error:
            await PostgresConnection.execute("UPDATE a SET b = 1")

    assert error.value.sqlstate == PostgresErrorCode.serialization_failure
    assert len(attempts) == 1


@mark.asyncio
async def test_non_retriable_failure_is_raised_on_the_first_attempt() -> None:
    # running a constraint violation again would fail in exactly the same way
    attempts: list[str] = []
    connection = _connection(
        [
            PostgresException(
                "duplicate key",
                sqlstate=PostgresErrorCode.unique_violation,
            )
        ],
        attempts,
    )

    async with ctx.scope("test", connection):
        with raises(PostgresException, match="duplicate key"):
            await PostgresConnection.execute("INSERT INTO a VALUES (1)", retries=5)

    assert len(attempts) == 1


@mark.asyncio
async def test_exhausted_retries_raise_the_last_failure() -> None:
    attempts: list[str] = []
    connection = _connection([_serialization_failure() for _ in range(3)], attempts)

    async with ctx.scope("test", connection):
        with raises(PostgresException) as error:
            await PostgresConnection.fetch("SELECT 1", retries=2)

    assert error.value.sqlstate == PostgresErrorCode.serialization_failure
    # the initial attempt plus the two allowed retries
    assert len(attempts) == 3


@mark.asyncio
async def test_retry_inside_a_doomed_transaction_reports_the_original_failure() -> None:
    # the abort doomed the whole transaction, so the retry can only be told that
    # the transaction is unusable - which explains nothing about why
    attempts: list[str] = []
    connection = _connection(
        [
            _serialization_failure(),
            PostgresException(
                "current transaction is aborted",
                sqlstate=PostgresErrorCode.in_failed_sql_transaction,
            ),
        ],
        attempts,
    )

    async with ctx.scope("test", connection):
        with raises(PostgresException) as error:
            await PostgresConnection.execute("UPDATE a SET b = 1", retries=5)

    assert error.value.sqlstate == PostgresErrorCode.serialization_failure
    assert isinstance(error.value.__cause__, PostgresException)
    assert error.value.__cause__.sqlstate == PostgresErrorCode.in_failed_sql_transaction
    # giving up immediately rather than burning the remaining retries
    assert len(attempts) == 2


@mark.asyncio
async def test_retries_reach_the_driver_through_the_service_helpers() -> None:
    attempts: list[str] = []
    connection = _connection([_serialization_failure()], attempts)

    def acquire() -> object:  # pragma: no cover - a contextual connection is used
        raise NotImplementedError

    async with ctx.scope("test", Postgres(connection_acquiring=acquire), connection):  # pyright: ignore[reportArgumentType]
        await Postgres.fetch_one("SELECT 1", retries=1)

    assert len(attempts) == 2


def test_error_codes_compare_against_raw_sqlstate() -> None:
    assert PostgresErrorCode.unique_violation == "23505"
    assert PostgresErrorCode.deadlock_detected == "40P01"
    # the class prefix still works for handling a whole class at once
    assert PostgresErrorCode.foreign_key_violation.startswith("23")


def test_retriable_covers_transient_transaction_aborts_only() -> None:
    def exception(sqlstate: str | None) -> PostgresException:
        return PostgresException("failure", sqlstate=sqlstate)

    assert exception(PostgresErrorCode.serialization_failure).retriable
    assert exception(PostgresErrorCode.deadlock_detected).retriable
    assert not exception(PostgresErrorCode.unique_violation).retriable
    assert not exception(PostgresErrorCode.connection_failure).retriable
    assert not exception(None).retriable
