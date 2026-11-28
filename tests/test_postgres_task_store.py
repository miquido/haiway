from collections.abc import Callable, Generator, Mapping, Sequence
from datetime import UTC, datetime
from types import TracebackType
from typing import Any
from uuid import UUID

import pytest
from pytest import MonkeyPatch, raises

pytest.importorskip("asyncpg", reason="requires the postgres extra")

from asyncpg.exceptions import UndefinedTableError, UniqueViolationError

from haiway.context.managed import (
    AttemptCancelled,
    AttemptCompleted,
    AttemptFailed,
    AttemptRetried,
    ClaimedAttempt,
    ClaimedInterrupted,
    LeasedCoordination,
    NewTask,
    StoredCancelled,
    StoredCompleted,
    StoredFailed,
    StoredFinished,
    StoredQueued,
    StoredRetryPending,
    StoredRunning,
    TaskFailureRecord,
    TaskFilter,
    TaskInterrupted,
    TaskKeyset,
    TaskRetryPolicy,
    TasksFinished,
    TaskSummaryFinished,
    TaskSummaryUnfinished,
    TasksUnfinished,
    TaskTimeBefore,
    TaskTimeBetween,
    TaskTimeFrom,
    TaskTimeLimitExceeded,
    TaskUnsupported,
)
from haiway.postgres import PostgresConnectionPool, PostgresException, PostgresTaskStore
from haiway.postgres import client as client_module
from haiway.postgres import tasks as tasks_module

# every POSTGRES_* variable resolves when a pool is constructed
POSTGRES_VARIABLES = (
    "POSTGRES_ACQUIRE_TIMEOUT",
    "POSTGRES_CLOSE_TIMEOUT",
    "POSTGRES_COMMAND_TIMEOUT",
    "POSTGRES_CONNECTIONS",
    "POSTGRES_DATABASE",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_SSLMODE",
    "POSTGRES_STATEMENT_CACHE",
    "POSTGRES_USER",
)

RUNNER = UUID(int=1)
IDENTIFIER = UUID(int=2)
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def clear_postgres_environment(monkeypatch: MonkeyPatch) -> None:
    for name in POSTGRES_VARIABLES:
        monkeypatch.delenv(name, raising=False)


class _Record:
    # stands in for asyncpg.Record, PostgresRow only calls `get` on it
    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values: Mapping[str, Any] = values

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]


class _Transaction:
    def __init__(self, connection: _Connection) -> None:
        self.connection: _Connection = connection

    async def __aenter__(self) -> None:
        self.connection.calls.append(("BEGIN", ()))

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.connection.calls.append(("ROLLBACK" if exc_val else "COMMIT", ()))


class _Connection:
    """Records every call, answering from scripted rows or raising a scripted error."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.rows: list[Mapping[str, Any]] = []
        self.schema_rows: list[Mapping[str, Any]] = []
        self.errors: dict[str, BaseException] = {}

    def _answer(self, statement: str, args: tuple[Any, ...]) -> None:
        self.calls.append((statement, args))
        for fragment, error in self.errors.items():
            if fragment in statement:
                raise error

    async def fetch(self, statement: str, *args: Any) -> list[_Record]:
        self._answer(statement, args)
        rows = self.schema_rows if "max(version)" in statement else self.rows
        return [_Record(row) for row in rows]

    async def execute(self, statement: str, *args: Any) -> str:
        self._answer(statement, args)
        return "OK"

    def transaction(self, **_: Any) -> _Transaction:
        return _Transaction(self)


class _Acquire:
    def __init__(self, pool: _Pool) -> None:
        self.pool: _Pool = pool

    async def __aenter__(self) -> _Connection:
        self.pool.acquired += 1
        return self.pool.connection

    async def __aexit__(self, *args: Any) -> None:
        self.pool.released += 1


class _Pool:
    def __init__(self) -> None:
        self.connection: _Connection = _Connection()
        self.created: int = 0
        self.closed: int = 0
        self.acquired: int = 0
        self.released: int = 0

    def __await__(self) -> Generator[Any, None, _Pool]:
        async def ready() -> _Pool:
            self.created += 1
            return self

        return ready().__await__()

    async def close(self) -> None:
        self.closed += 1

    def terminate(self) -> None:
        pass

    def acquire(self, *, timeout: float | None = None) -> _Acquire:
        return _Acquire(self)

    def get_idle_size(self) -> int:
        return 1

    def get_size(self) -> int:
        return 2


def _store(
    monkeypatch: MonkeyPatch,
    *,
    version: int | None = tasks_module.TASKS_LATEST_VERSION,
) -> tuple[PostgresTaskStore, _Pool]:
    pool = _Pool()
    monkeypatch.setattr(client_module, "create_pool", lambda *_, **__: pool)
    if version is not None:
        pool.connection.schema_rows = [{"version": version}]

    return (
        PostgresTaskStore(
            PostgresConnectionPool(connection_limit=2),
            lease=30.0,
            lease_renewal=10.0,
            polling=1.0,
        ),
        pool,
    )


def _statements(pool: _Pool) -> list[tuple[str, tuple[Any, ...]]]:
    # the schema check is the first statement of every opening
    return pool.connection.calls[1:]


def _task_columns(**overrides: Any) -> dict[str, Any]:
    columns: dict[str, Any] = {
        "id": IDENTIFIER,
        "name": "task",
        "argument": '{"a": 1}',
        "time_limit": None,
        "retry_delays": [0.5, 1.0],
        "origin": None,
        "status": "queued",
        "outcome": None,
        "cancel_requested": False,
        "result": None,
        "attempt_number": None,
    }
    columns.update(overrides)
    return columns


def _new_task() -> NewTask:
    return NewTask(
        identifier=IDENTIFIER,
        name="task",
        argument='{"a": 1}',
        retry=TaskRetryPolicy(delays=(0.5, 1.0)),
        time_limit=2.5,
        origin=None,
    )


# FR-4


def test_settings_are_validated_at_construction() -> None:
    with raises(ValueError, match="renewal"):
        PostgresTaskStore(
            PostgresConnectionPool(connection_limit=2),
            lease=1.0,
            lease_renewal=1.0,
            polling=0.1,
        )


def test_settings_are_required() -> None:
    with raises(TypeError):
        PostgresTaskStore(PostgresConnectionPool(connection_limit=2))


def test_docstring_states_the_pool_requirement() -> None:
    assert "2 connections" in (PostgresTaskStore.__doc__ or "")


@pytest.mark.asyncio
async def test_session_coordination_is_leased_with_the_given_timings(
    monkeypatch: MonkeyPatch,
) -> None:
    store, _ = _store(monkeypatch)
    async with store.open() as session:
        coordination = session.coordination
        assert isinstance(coordination, LeasedCoordination)
        assert coordination.timings.lease == 30.0
        assert coordination.timings.renewal == 10.0
        assert coordination.timings.polling == 1.0


# pool ownership, FR-18, FR-22


@pytest.mark.asyncio
async def test_open_enters_pool_and_exit_closes_it_and_reopens(
    monkeypatch: MonkeyPatch,
) -> None:
    store, pool = _store(monkeypatch)
    async with store.open():
        assert pool.created == 1
        assert pool.closed == 0

    assert pool.closed == 1
    async with store.open():
        pass

    assert pool.created == 2
    assert pool.closed == 2


@pytest.mark.asyncio
async def test_open_fails_when_pool_is_entered_elsewhere(monkeypatch: MonkeyPatch) -> None:
    pool = PostgresConnectionPool(connection_limit=2)
    fake = _Pool()
    fake.connection.schema_rows = [{"version": 1}]
    monkeypatch.setattr(client_module, "create_pool", lambda *_, **__: fake)
    store = PostgresTaskStore(pool, lease=30.0, lease_renewal=10.0, polling=1.0)
    async with pool:
        with raises(PostgresException):
            async with store.open():
                pass

    async with store.open():
        pass


@pytest.mark.asyncio
async def test_migrate_fails_while_store_is_open(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    async with store.open():
        before = len(pool.connection.calls)
        with raises(PostgresException):
            await store.migrate()

        assert len(pool.connection.calls) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("version", [None, 0])
async def test_open_refuses_missing_or_old_schema_and_exits_pool(
    monkeypatch: MonkeyPatch,
    version: int | None,
) -> None:
    store, pool = _store(monkeypatch, version=version)
    with raises(PostgresException, match="needs"):
        async with store.open():
            pass

    assert pool.closed == 1


@pytest.mark.asyncio
async def test_open_maps_absent_table_to_schema_error(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    pool.connection.errors = {"haiway_managed_tasks_migrations": UndefinedTableError("x")}
    with raises(PostgresException, match="missing"):
        async with store.open():
            pass

    assert pool.closed == 1


@pytest.mark.asyncio
async def test_open_accepts_a_newer_schema(monkeypatch: MonkeyPatch) -> None:
    store, _ = _store(monkeypatch, version=tasks_module.TASKS_LATEST_VERSION + 1)
    async with store.open():
        pass


# FR-1


@pytest.mark.asyncio
async def test_migrate_runs_unrecorded_versions_under_a_lock(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch, version=None)
    await store.migrate(timeout=7)
    calls = pool.connection.calls
    statements = [statement.strip() for statement, _ in calls]
    assert calls[0][1] == ("7s",)
    assert calls[1][1] == (tasks_module.TASKS_MIGRATIONS_ADVISORY_LOCK_KEY,)
    assert tasks_module.TASKS_MIGRATIONS_TABLE_CREATE_STATEMENT.strip() in statements
    assert tasks_module.TASKS_SCHEMA_VERSION_1.strip() in statements
    assert (tasks_module.TASKS_MIGRATION_COMPLETION_STATEMENT.strip(), (1,)) in [
        (statement.strip(), args) for statement, args in calls
    ]
    assert "BEGIN" in statements
    assert statements[-3] == "COMMIT"
    assert calls[-2][1] == (tasks_module.TASKS_MIGRATIONS_ADVISORY_LOCK_KEY,)
    assert pool.closed == 1


@pytest.mark.asyncio
async def test_migrate_skips_recorded_versions(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    pool.connection.rows = [{"version": 1}]
    await store.migrate()
    statements = [statement.strip() for statement, _ in pool.connection.calls]
    assert tasks_module.TASKS_SCHEMA_VERSION_1.strip() not in statements


@pytest.mark.asyncio
async def test_migrate_releases_lock_and_pool_when_a_migration_fails(
    monkeypatch: MonkeyPatch,
) -> None:
    store, pool = _store(monkeypatch, version=None)
    pool.connection.errors = {"CREATE TABLE haiway_managed_tasks (": UniqueViolationError("x")}
    with raises(PostgresException):
        await store.migrate()

    statements = [statement.strip() for statement, _ in pool.connection.calls]
    assert "ROLLBACK" in statements
    assert tasks_module.MIGRATIONS_ADVISORY_UNLOCK_STATEMENT.strip() in statements
    assert pool.closed == 1


def test_schema_keeps_version_table_out_of_the_first_migration() -> None:
    assert "haiway_managed_tasks_migrations" not in tasks_module.TASKS_SCHEMA_VERSION_1
    for index in (
        "haiway_managed_tasks_open",
        "haiway_managed_tasks_leases",
        "haiway_managed_tasks_created",
        "haiway_managed_tasks_names",
    ):
        assert index in tasks_module.TASKS_SCHEMA_VERSION_1


# FR-20, NFR-1


def test_no_session_statement_reads_the_volatile_clock() -> None:
    statements = [
        value
        for name, value in vars(tasks_module).items()
        if name.startswith("TASK_")
        and (name.endswith("_STATEMENT") or name.startswith("TASK_QUERY_"))
        and isinstance(value, str)
    ]
    assert len(statements) == 10
    assert all("clock_timestamp" not in statement for statement in statements)


@pytest.mark.asyncio
async def test_every_operation_is_one_statement_without_a_transaction(
    monkeypatch: MonkeyPatch,
) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        operations: Sequence[Callable[[], Any]] = (
            lambda: session.insert(_new_task()),
            lambda: session.claim(RUNNER, {"a"}, limit=None),
            lambda: session.renew(RUNNER, {IDENTIFIER}),
            lambda: session.record(RUNNER, IDENTIFIER, 1, AttemptCancelled()),
            lambda: session.release(RUNNER, IDENTIFIER, 1),
            lambda: session.cancel(IDENTIFIER),
            lambda: session.read({IDENTIFIER}),
            lambda: session.fail_unsupported(set()),
            lambda: session.query(TaskFilter(), after=None, limit=5),
        )
        for operation in operations:
            pool.connection.rows = []
            before = len(pool.connection.calls)
            acquired = pool.acquired
            await operation()
            assert len(pool.connection.calls) == before + 1
            assert pool.acquired == acquired + 1

    assert all(statement not in ("BEGIN", "COMMIT") for statement, _ in pool.connection.calls)
    assert pool.released == pool.acquired


# parameter mapping


@pytest.mark.asyncio
async def test_insert_maps_the_task_to_parameters(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        await session.insert(_new_task())

    assert _statements(pool) == [
        (
            tasks_module.TASK_INSERT_STATEMENT,
            (IDENTIFIER, "task", '{"a": 1}', 2.5, [0.5, 1.0], None),
        )
    ]


@pytest.mark.asyncio
async def test_insert_of_a_duplicate_raises_postgres_exception(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        pool.connection.errors = {"INSERT INTO haiway_managed_tasks": UniqueViolationError("dup")}
        with raises(PostgresException) as caught:
            await session.insert(_new_task())

    assert caught.value.sqlstate == "23505"
    assert f"insert of {IDENTIFIER}" in str(caught.value)
    assert isinstance(caught.value.__cause__, PostgresException)
    assert "dup" not in str(caught.value)


@pytest.mark.asyncio
async def test_undecodable_values_are_reported_as_postgres_exception(
    monkeypatch: MonkeyPatch,
) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        pool.connection.rows = [_task_columns(claim="attempt", attempt=0)]
        with raises(PostgresException, match="claim"):
            await session.claim(RUNNER, {"task"}, limit=None)

        pool.connection.rows = [_task_columns(origin="not json")]
        with raises(PostgresException, match="read"):
            await session.read({IDENTIFIER})


def test_record_clamps_the_attempt_end_to_its_start() -> None:
    assert "greatest(statement_timestamp(), held.started_at)" in tasks_module.TASK_RECORD_STATEMENT


@pytest.mark.asyncio
async def test_claim_passes_sorted_names_limit_runner_and_lease(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        await session.claim(RUNNER, {"b", "a"}, limit=0)
        await session.claim(RUNNER, set(), limit=None)

    assert [args for _, args in _statements(pool)] == [
        (["a", "b"], 0, RUNNER, 30.0),
        ([], None, RUNNER, 30.0),
    ]


@pytest.mark.asyncio
async def test_negative_limits_raise_before_any_statement(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        with raises(ValueError):
            await session.claim(RUNNER, {"a"}, limit=-1)

        with raises(ValueError):
            await session.query(TaskFilter(), after=None, limit=-1)

    assert _statements(pool) == []


@pytest.mark.asyncio
async def test_claim_decodes_attempts_and_interruptions(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    other = UUID(int=3)
    async with store.open() as session:
        pool.connection.rows = [
            _task_columns(claim="attempt", attempt=2, time_limit=2.5),
            _task_columns(id=other, claim="interrupted", attempt=1, cancel_requested=True),
        ]
        claimed = await session.claim(RUNNER, {"task"}, limit=None)

    assert claimed == (
        ClaimedAttempt(task=_new_task(), attempt=2),
        ClaimedInterrupted(
            task=_new_task().updating(identifier=other, time_limit=None),
            attempt=1,
            cancel_requested=True,
        ),
    )


@pytest.mark.asyncio
async def test_renew_maps_leases_with_the_stores_lease(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    other = UUID(int=3)
    async with store.open() as session:
        pool.connection.rows = [
            {"id": IDENTIFIER, "cancel_requested": False},
            {"id": other, "cancel_requested": True},
        ]
        renewal = await session.renew(RUNNER, {IDENTIFIER, other})

    assert renewal.leases == {IDENTIFIER: "held", other: "cancel_requested"}
    assert _statements(pool)[0][1] == (RUNNER, sorted({IDENTIFIER, other}), 30.0)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("end", "arguments"),
    [
        (AttemptCompleted(result='{"x": 1}'), ("completed", '{"x": 1}', None, None, None, None)),
        (
            AttemptRetried(
                failure=TaskFailureRecord(kind="raised", exception_type="builtins.ValueError"),
                delay=0.5,
            ),
            ("retried", None, 0.5, "raised", "builtins.ValueError", None),
        ),
        (
            AttemptFailed(failure=TaskTimeLimitExceeded(time_limit=3.0)),
            ("failed", None, None, "time_limit", None, 3.0),
        ),
        (
            AttemptFailed(failure=TaskInterrupted()),
            ("failed", None, None, "interrupted", None, None),
        ),
        (AttemptCancelled(), ("cancelled", None, None, None, None, None)),
    ],
)
async def test_record_maps_every_end_to_parameters(
    monkeypatch: MonkeyPatch,
    end: Any,
    arguments: tuple[Any, ...],
) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        assert await session.record(RUNNER, IDENTIFIER, 3, end) is None

    assert _statements(pool) == [
        (tasks_module.TASK_RECORD_STATEMENT, (RUNNER, IDENTIFIER, 3, *arguments))
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "status"),
    [
        ({"status": "retry_pending", "outcome": None, "result": None}, StoredRetryPending()),
        (
            {"status": "finished", "outcome": "completed", "result": "null"},
            StoredFinished(outcome=StoredCompleted(result="null")),
        ),
        (
            {"status": "finished", "outcome": "failed", "result": None},
            StoredFinished(outcome=StoredFailed()),
        ),
        (
            {"status": "finished", "outcome": "cancelled", "result": None},
            StoredFinished(outcome=StoredCancelled()),
        ),
    ],
)
async def test_record_decodes_the_returned_status(
    monkeypatch: MonkeyPatch,
    row: Mapping[str, Any],
    status: Any,
) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        pool.connection.rows = [row]
        assert await session.record(RUNNER, IDENTIFIER, 1, AttemptCancelled()) == status


@pytest.mark.asyncio
async def test_release_decodes_status_and_none(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        assert await session.release(RUNNER, IDENTIFIER, 2) is None
        pool.connection.rows = [{"status": "queued", "outcome": None}]
        assert await session.release(RUNNER, IDENTIFIER, 2) == StoredQueued()
        pool.connection.rows = [{"status": "finished", "outcome": "cancelled"}]
        assert await session.release(RUNNER, IDENTIFIER, 2) == StoredFinished(
            outcome=StoredCancelled()
        )

    assert _statements(pool)[0] == (tasks_module.TASK_RELEASE_STATEMENT, (RUNNER, IDENTIFIER, 2))


@pytest.mark.asyncio
@pytest.mark.parametrize("cancellation", ["cancelled", "pending", "finished"])
async def test_cancel_decodes(monkeypatch: MonkeyPatch, cancellation: str) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        assert await session.cancel(IDENTIFIER) is None
        pool.connection.rows = [{"cancellation": cancellation}]
        assert await session.cancel(IDENTIFIER) == cancellation


@pytest.mark.asyncio
async def test_fail_unsupported_passes_an_array_never_null(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        pool.connection.rows = [{"id": IDENTIFIER, "name": "x"}]
        assert await session.fail_unsupported(set()) == {IDENTIFIER: "x"}
        await session.fail_unsupported({"b", "a"})

    assert [args for _, args in _statements(pool)] == [([],), (["a", "b"],)]


# read decoding


@pytest.mark.asyncio
async def test_read_groups_attempts_and_decodes_every_variant(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    failed, running, queued, done = (UUID(int=index) for index in (10, 11, 12, 13))
    origin = (
        '{"trace_id": "00000000-0000-0000-0000-000000000001",'
        ' "scope_id": "00000000-0000-0000-0000-000000000002", "trace_context": {}}'
    )
    async with store.open() as session:
        pool.connection.rows = [
            _task_columns(
                id=failed,
                status="finished",
                outcome="failed",
                origin=origin,
                attempt_number=1,
                attempt_result="failed",
                failure_kind="time_limit",
                attempt_time_limit=3.0,
            ),
            _task_columns(
                id=failed,
                status="finished",
                outcome="failed",
                origin=origin,
                attempt_number=2,
                attempt_result="failed",
                failure_kind="unsupported",
            ),
            _task_columns(
                id=running,
                status="running",
                cancel_requested=True,
                attempt_number=1,
                attempt_result="failed",
                failure_kind="raised",
                exception_type="builtins.ValueError",
            ),
            _task_columns(id=queued),
            _task_columns(
                id=done,
                status="finished",
                outcome="completed",
                result='{"v": 1}',
                attempt_number=1,
                attempt_result="completed",
            ),
        ]
        stored = await session.read({failed, running, queued, done, UUID(int=99)})

    assert set(stored) == {failed, running, queued, done}
    assert stored[failed].attempts[0].result == TaskTimeLimitExceeded(time_limit=3.0)
    assert stored[failed].attempts[1].result == TaskUnsupported()
    assert stored[failed].status == StoredFinished(outcome=StoredFailed())
    assert stored[failed].task.origin is not None
    assert stored[running].status == StoredRunning(cancel_requested=True)
    assert stored[running].attempts[0].result == TaskFailureRecord(
        kind="raised", exception_type="builtins.ValueError"
    )
    assert stored[queued].status == StoredQueued()
    assert stored[queued].attempts == ()
    assert stored[queued].task.retry == TaskRetryPolicy(delays=(0.5, 1.0))
    assert stored[done].status == StoredFinished(outcome=StoredCompleted(result='{"v": 1}'))
    assert stored[done].attempts[0].result == "completed"
    assert _statements(pool)[0][1] == (sorted({failed, running, queued, done, UUID(int=99)}),)


@pytest.mark.asyncio
async def test_read_of_unexpected_column_value_raises_postgres_exception(
    monkeypatch: MonkeyPatch,
) -> None:
    store, pool = _store(monkeypatch)
    async with store.open() as session:
        pool.connection.rows = [_task_columns(status="bogus")]
        with raises(PostgresException):
            await session.read({IDENTIFIER})


# query, FR-24


def _query(matching: TaskFilter, after: TaskKeyset | None = None, limit: int = 10) -> Any:
    return tasks_module._query_statement(matching, after, limit)


def test_query_without_parts_has_no_where() -> None:
    statement, arguments = _query(TaskFilter())
    assert "\nWHERE" not in statement
    assert statement.endswith("ORDER BY t.created_at, t.id\nLIMIT $1::BIGINT;")
    assert arguments == [10]


def test_query_with_keyset_only() -> None:
    keyset = TaskKeyset(created=NOW, identifier=IDENTIFIER)
    statement, arguments = _query(TaskFilter(), keyset)
    assert "WHERE (t.created_at, t.id) > ($1::TIMESTAMPTZ, $2::UUID)" in statement
    assert statement.endswith("LIMIT $3::BIGINT;")
    assert arguments == [NOW, IDENTIFIER, 10]


def test_query_one_name_is_an_equality_and_several_use_any() -> None:
    statement, arguments = _query(TaskFilter(names={"r"}))
    assert "t.name = $1::TEXT" in statement
    assert "ANY" not in statement
    assert arguments == ["r", 10]
    statement, arguments = _query(TaskFilter(names={"b", "a"}))
    assert "t.name = ANY($1::TEXT[])" in statement
    assert arguments == [["a", "b"], 10]


def test_query_builds_conditions_of_the_given_parts_only() -> None:
    end = datetime(2026, 12, 1, tzinfo=UTC)
    keyset = TaskKeyset(created=NOW, identifier=IDENTIFIER)
    statement, arguments = _query(
        TaskFilter(
            names={"r"},
            status=TasksFinished(outcomes={"failed"}, finished=TaskTimeFrom(start=NOW)),
            created=TaskTimeBetween(start=NOW, end=end),
        ),
        keyset,
    )
    assert statement.count("\nWHERE") == 1
    assert "t.name = $1::TEXT" in statement
    assert "t.status = 'finished'" in statement
    assert "t.outcome = ANY($2::TEXT[])" in statement
    assert "t.finished_at >= $3::TIMESTAMPTZ" in statement
    assert "t.created_at >= $4::TIMESTAMPTZ" in statement
    assert "t.created_at < $5::TIMESTAMPTZ" in statement
    assert "(t.created_at, t.id) > ($6::TIMESTAMPTZ, $7::UUID)" in statement
    assert statement.endswith("LIMIT $8::BIGINT;")
    assert arguments == ["r", ["failed"], NOW, NOW, end, NOW, IDENTIFIER, 10]


@pytest.mark.parametrize(
    "matching",
    [
        TaskFilter(),
        TaskFilter(names={"a", "b"}, status=TasksUnfinished(statuses={"queued", "running"})),
        TaskFilter(created=TaskTimeBefore(end=NOW)),
        TaskFilter(names=set(), status=TasksFinished(outcomes=set())),
    ],
)
def test_query_never_uses_the_null_or_form(matching: TaskFilter) -> None:
    statement, arguments = _query(matching, TaskKeyset(created=NOW, identifier=IDENTIFIER))
    assert "IS NULL" not in statement
    assert "clock_timestamp" not in statement
    assert statement.count("$") == len(arguments)


@pytest.mark.asyncio
async def test_query_decodes_summaries(monkeypatch: MonkeyPatch) -> None:
    store, pool = _store(monkeypatch)
    finished = UUID(int=5)
    async with store.open() as session:
        pool.connection.rows = [
            {
                "id": IDENTIFIER,
                "name": "task",
                "status": "retry_pending",
                "outcome": None,
                "created_at": NOW,
                "finished_at": None,
                "attempts": 1,
            },
            {
                "id": finished,
                "name": "task",
                "status": "finished",
                "outcome": "failed",
                "created_at": NOW,
                "finished_at": NOW,
                "attempts": 2,
            },
        ]
        summaries = await session.query(
            TaskFilter(status=TasksUnfinished(statuses={"queued"})), after=None, limit=2
        )

    assert summaries[0].status == TaskSummaryUnfinished(status="retry_pending")
    assert summaries[0].attempts == 1
    assert summaries[1].status == TaskSummaryFinished(outcome="failed", finished=NOW)
    assert summaries[1].attempts == 2
    assert summaries[1].created == NOW


def test_attempt_number_is_read_from_the_task_row() -> None:
    for statement in (tasks_module.TASK_RECORD_STATEMENT, tasks_module.TASK_RELEASE_STATEMENT):
        assert "attempts + 1 = $3::BIGINT" in statement

    assert "t.attempts + 1 AS attempt" in tasks_module.TASK_CLAIM_STATEMENT
    assert "attempts = held.attempts + 1" in tasks_module.TASK_RECORD_STATEMENT
    assert "attempts = t.attempts + 1" in tasks_module.TASK_FAIL_UNSUPPORTED_STATEMENT
    assert "count(*)" not in tasks_module.TASKS_SCHEMA_VERSION_1
    assert "WHERE status IN ('queued', 'retry_pending')" in tasks_module.TASKS_SCHEMA_VERSION_1
