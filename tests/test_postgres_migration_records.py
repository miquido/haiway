import sys
from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from hashlib import sha256
from pathlib import Path
from types import TracebackType

import pytest

pytest.importorskip("asyncpg", reason="requires the postgres extra")

from haiway import ctx
from haiway.postgres.state import (
    APPLIED_MIGRATIONS_FETCH_STATEMENT,
    MIGRATION_COMPLETION_STATEMENT,
    Postgres,
    PostgresConnection,
    _Migration,  # pyright: ignore[reportPrivateUsage]
    _verify_migration_records,  # pyright: ignore[reportPrivateUsage]
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
        pass

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        return None


def _postgres(
    recorded: list[tuple[PostgresValue, ...]],
) -> Postgres:
    async def fetch(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]:
        return ()

    async def execute(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> str:
        if statement.strip() == MIGRATION_COMPLETION_STATEMENT.strip():
            recorded.append(args)

        return "FAKE"

    @asynccontextmanager
    async def acquire() -> AsyncIterator[PostgresConnection]:
        yield PostgresConnection(
            statement_fetching=fetch,
            statement_executing=execute,
            transaction_preparing=_FakeTransaction,
        )

    return Postgres(connection_acquiring=acquire)


@pytest.mark.asyncio
async def test_provided_migrations_record_identifier_without_checksum() -> None:
    recorded: list[tuple[PostgresValue, ...]] = []
    postgres = _postgres(recorded)

    async def migration(
        connection: PostgresConnection,
    ) -> None:
        await connection.execute("SELECT 1;")

    async with ctx.scope("postgres-migrations-record", postgres):
        await postgres.execute_migrations([migration])

    assert len(recorded) == 1
    identifier, checksum = recorded[0]
    assert isinstance(identifier, str)
    assert identifier == (
        "tests.test_postgres_migration_records"
        ".test_provided_migrations_record_identifier_without_checksum.<locals>.migration"
    )
    # only migrations discovered from files have a source to hash
    assert checksum is None


@pytest.mark.asyncio
async def test_discovered_migrations_record_module_source_checksum(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package: Path = tmp_path / "haiway_test_migrations"
    package.mkdir()
    (package / "__init__.py").write_text("")
    sources: list[Path] = []
    for idx in range(2):
        source: Path = package / f"migration_{idx}.py"
        source.write_text(
            "from haiway.postgres import PostgresConnection\n"
            "\n"
            "\n"
            "async def migration(connection: PostgresConnection) -> None:\n"
            f"    await connection.execute('SELECT {idx};')\n"
        )
        sources.append(source)

    monkeypatch.syspath_prepend(str(tmp_path))
    recorded: list[tuple[PostgresValue, ...]] = []
    postgres = _postgres(recorded)
    try:
        async with ctx.scope("postgres-migrations-record-discovered", postgres):
            await postgres.execute_migrations("haiway_test_migrations")

    finally:
        for name in [name for name in sys.modules if name.startswith("haiway_test_migrations")]:
            del sys.modules[name]

    assert recorded == [
        (
            f"haiway_test_migrations.migration_{idx}",
            sha256(source.read_bytes()).hexdigest(),
        )
        for idx, source in enumerate(sources)
    ]


class _Record:
    """Minimal record exposing only what PostgresRow reads."""

    def __init__(
        self,
        values: dict[str, PostgresValue],
    ) -> None:
        self._values = values

    def get(self, key: str, default: PostgresValue = None) -> PostgresValue:
        return self._values.get(key, default)

    def keys(self) -> object:
        return self._values.keys()

    def __getitem__(self, key: str) -> PostgresValue:
        return self._values[key]

    def __len__(self) -> int:
        return len(self._values)


def _applied(
    identifier: str | None,
    checksum: str | None,
) -> PostgresRow:
    return PostgresRow(
        _Record({"identifier": identifier, "checksum": checksum}),  # pyright: ignore[reportArgumentType]
    )


async def _noop(connection: PostgresConnection) -> None:
    return None


def _available(
    identifier: str,
    checksum: str | None,
) -> _Migration:
    return _Migration(identifier=identifier, checksum=checksum, performing=_noop)


def test_matching_migration_records_are_not_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    _verify_migration_records(
        applied=(_applied("pkg.migration_0", "abc"),),
        available=(_available("pkg.migration_0", "abc"),),
    )

    assert caplog.records == []


def test_changed_migration_source_is_reported_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # drift cannot be repaired from here - the applied count already decided what
    # runs - so it is logged for a human instead of failing the migration run
    with caplog.at_level("WARNING"):
        _verify_migration_records(
            applied=(_applied("pkg.migration_0", "recorded"),),
            available=(_available("pkg.migration_0", "current"),),
        )

    assert any("source changed" in record.message for record in caplog.records)


def test_reordered_migration_is_reported_without_raising(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING"):
        _verify_migration_records(
            applied=(_applied("pkg.migration_0", "abc"),),
            available=(_available("pkg.migration_renamed", "abc"),),
        )

    messages = [record.message for record in caplog.records]
    assert any("renamed or reordered" in message for message in messages)
    # the position holds a different migration, so its checksum says nothing
    assert not any("source changed" in message for message in messages)


def test_records_without_bookkeeping_are_not_reported(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # rows written before the identifier and checksum columns existed, and
    # migrations provided as callables, have nothing to compare
    with caplog.at_level("WARNING"):
        _verify_migration_records(
            applied=(_applied(None, None), _applied("pkg.migration_1", None)),
            available=(
                _available("pkg.migration_0", "abc"),
                _available("pkg.migration_1", "def"),
            ),
        )

    assert caplog.records == []


@pytest.mark.asyncio
async def test_applied_migrations_are_fetched_with_their_bookkeeping() -> None:
    # the version lookup reads the recorded rows so they can be verified, rather
    # than only counting them
    fetched: list[str] = []

    async def fetch(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]:
        fetched.append(statement.strip())
        return ()

    async def execute(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> str:
        return "FAKE"

    @asynccontextmanager
    async def acquire() -> AsyncIterator[PostgresConnection]:
        yield PostgresConnection(
            statement_fetching=fetch,
            statement_executing=execute,
            transaction_preparing=_FakeTransaction,
        )

    postgres = Postgres(connection_acquiring=acquire)
    async with ctx.scope("postgres-migrations-applied", postgres):
        await postgres.execute_migrations([])

    assert APPLIED_MIGRATIONS_FETCH_STATEMENT.strip() in fetched
