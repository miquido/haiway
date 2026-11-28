"""
Acceptance tests of ``PostgresTaskStore`` against a PostgreSQL server (TDS-002 NFR-3).

They run only when ``HAIWAY_TEST_POSTGRES_DSN`` holds the DSN of a server whose role may
create schemas and tables, for example
``HAIWAY_TEST_POSTGRES_DSN=postgresql://postgres:postgres@localhost:5432/postgres``. Every test
creates a schema of its own, points every connection at it through ``search_path`` and drops
it at the end, so nothing else in the database is touched.
"""

import json
import os
import re
from asyncio import gather, sleep
from collections.abc import AsyncGenerator, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import UUID, uuid4

import pytest
import pytest_asyncio

pytest.importorskip("asyncpg", reason="requires the postgres extra")

import asyncpg

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
    StoredTask,
    TaskFailureRecord,
    TaskFilter,
    TaskInterrupted,
    TaskKeyset,
    TaskRetryPolicy,
    TasksFinished,
    TaskStoreSession,
    TaskSummaryFinished,
    TaskSummaryUnfinished,
    TasksUnfinished,
    TaskTimeBefore,
    TaskTimeFrom,
    TaskTimeLimitExceeded,
    TaskUnsupported,
)
from haiway.postgres import PostgresConnectionPool, PostgresException, PostgresTaskStore
from haiway.postgres import tasks as tasks_module

DSN: str | None = os.environ.get("HAIWAY_TEST_POSTGRES_DSN")

pytestmark = pytest.mark.skipif(
    DSN is None,
    reason="HAIWAY_TEST_POSTGRES_DSN is not set, no PostgreSQL server to test against",
)

RAISED = TaskFailureRecord(kind="raised", exception_type="builtins.ValueError")


class Database:
    """A schema of its own, with an admin connection for direct SQL."""

    def __init__(
        self,
        dsn: str,
        schema: str,
        admin: asyncpg.Connection,
    ) -> None:
        self.schema: str = schema
        self.admin: asyncpg.Connection = admin
        parts = urlsplit(dsn)
        query = [*parse_qsl(parts.query), ("search_path", schema)]
        self.dsn: str = urlunsplit(parts._replace(query=urlencode(query)))

    def store(
        self,
        *,
        connections: int = 3,
        lease: float = 30.0,
        renewal: float = 10.0,
        polling: float = 1.0,
    ) -> PostgresTaskStore:
        return PostgresTaskStore(
            PostgresConnectionPool.of(self.dsn, connection_limit=connections),
            lease=lease,
            lease_renewal=renewal,
            polling=polling,
        )

    async def task(self, identifier: UUID) -> asyncpg.Record:
        row = await self.admin.fetchrow(
            "SELECT * FROM haiway_managed_tasks WHERE id = $1",
            identifier,
        )
        assert row is not None
        return row

    async def attempts(self, identifier: UUID) -> Sequence[asyncpg.Record]:
        return await self.admin.fetch(
            "SELECT * FROM haiway_managed_task_attempts WHERE task_id = $1 ORDER BY number",
            identifier,
        )

    async def expire(self, *identifiers: UUID) -> None:
        await self.admin.execute(
            "UPDATE haiway_managed_tasks"
            " SET lease_expires_at = statement_timestamp() - interval '1 second'"
            " WHERE id = ANY($1::uuid[])",
            list(identifiers),
        )

    async def versions(self) -> list[int]:
        return [
            row["version"]
            for row in await self.admin.fetch(
                "SELECT version FROM haiway_managed_tasks_migrations ORDER BY version"
            )
        ]


@pytest_asyncio.fixture
async def blank() -> AsyncGenerator[Database]:
    assert DSN is not None
    schema = "haiway_test_" + uuid4().hex
    admin = await asyncpg.connect(DSN)
    try:
        await admin.execute(f"CREATE SCHEMA {schema}")
        await admin.execute(f"SET search_path TO {schema}")
        yield Database(DSN, schema, admin)

    finally:
        try:
            await admin.execute(f"DROP SCHEMA IF EXISTS {schema} CASCADE")

        finally:
            await admin.close()


@pytest_asyncio.fixture
async def database(blank: Database) -> Database:
    await blank.store().migrate()
    return blank


def new_task(
    name: str = "r",
    *,
    identifier: UUID | None = None,
    delays: Sequence[float] = (0.0,),
    time_limit: float | None = None,
    argument: str = '{"a": 1}',
) -> NewTask:
    return NewTask(
        identifier=identifier or uuid4(),
        name=name,
        argument=argument,
        retry=TaskRetryPolicy(delays=delays),
        time_limit=time_limit,
        origin=None,
    )


async def inserted(
    session: TaskStoreSession,
    name: str = "r",
    **overrides: Any,
) -> UUID:
    task = new_task(name, **overrides)
    await session.insert(task)
    return task.identifier


async def claimed(
    session: TaskStoreSession,
    runner: UUID,
    *,
    names: set[str] | None = None,
    limit: int | None = None,
) -> Sequence[ClaimedAttempt | ClaimedInterrupted]:
    return await session.claim(runner, names or {"r"}, limit=limit)


async def running(
    database: Database,
    session: TaskStoreSession,
    runner: UUID,
    name: str = "r",
    **overrides: Any,
) -> UUID:
    identifier = await inserted(session, name, **overrides)
    (claim,) = await claimed(session, runner, names={name})
    assert claim.task.identifier == identifier
    return identifier


def assert_cleared(row: asyncpg.Record, *columns: str) -> None:
    for column in columns:
        assert row[column] is None, column


# FR-1, FR-22, FR-4


@pytest.mark.asyncio
async def test_migrate_creates_schema_and_is_idempotent(blank: Database) -> None:
    store = blank.store()
    await store.migrate()
    await store.migrate()
    assert await blank.versions() == [1]
    tables = {
        row["tablename"]
        for row in await blank.admin.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = $1",
            blank.schema,
        )
    }
    assert tables == {
        "haiway_managed_tasks",
        "haiway_managed_task_attempts",
        "haiway_managed_tasks_migrations",
    }
    indexes = {
        row["indexname"]
        for row in await blank.admin.fetch(
            "SELECT indexname FROM pg_indexes WHERE schemaname = $1",
            blank.schema,
        )
    }
    assert {
        "haiway_managed_tasks_open",
        "haiway_managed_tasks_leases",
        "haiway_managed_tasks_created",
        "haiway_managed_tasks_names",
    } <= indexes
    # it never touches the table of execute_migrations
    legacy = await blank.admin.fetchval("SELECT to_regclass($1)", f"{blank.schema}.migrations")
    assert legacy is None


@pytest.mark.asyncio
async def test_concurrent_migrations_record_each_version_once(blank: Database) -> None:
    await gather(blank.store().migrate(), blank.store().migrate())
    assert await blank.versions() == [1]


@pytest.mark.asyncio
async def test_migrate_on_an_open_store_raises_and_changes_nothing(database: Database) -> None:
    store = database.store()
    async with store.open():
        with pytest.raises(PostgresException):
            await store.migrate()

    assert await database.versions() == [1]


@pytest.mark.asyncio
async def test_open_checks_the_schema_version(blank: Database) -> None:
    store = blank.store()
    with pytest.raises(PostgresException):
        async with store.open():
            pass

    await store.migrate()
    async with store.open() as session:
        coordination = session.coordination
        assert isinstance(coordination, LeasedCoordination)
        assert coordination.timings.lease == 30.0

    await blank.admin.execute("INSERT INTO haiway_managed_tasks_migrations (version) VALUES (2)")
    async with store.open():
        pass


@pytest.mark.asyncio
async def test_open_refuses_a_table_below_the_latest_version(blank: Database) -> None:
    await blank.store().migrate()
    await blank.admin.execute("DELETE FROM haiway_managed_tasks_migrations")
    with pytest.raises(PostgresException):
        async with blank.store().open():
            pass


# FR-2: constraints


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "columns",
    [
        "status, due_at, lease_expires_at, started_at, retry_delays, argument, name, id)"
        " VALUES ('running', NULL, now(), now(), '{}', '{}', 'r', gen_random_uuid()",
        "status, outcome, finished_at, retry_delays, argument, name, id)"
        " VALUES ('finished', 'completed', now(), '{}', '{}', 'r', gen_random_uuid()",
        "status, due_at, retry_delays, argument, name, id)"
        " VALUES ('queued', now(), '{NULL}', '{}', 'r', gen_random_uuid()",
    ],
)
async def test_invalid_rows_are_refused(database: Database, columns: str) -> None:
    with pytest.raises(asyncpg.CheckViolationError):
        await database.admin.execute(f"INSERT INTO haiway_managed_tasks ({columns})")


# column writes, one test per row of the table


@pytest.mark.asyncio
async def test_insert_writes_a_queued_due_row(database: Database) -> None:
    async with database.store().open() as session:
        identifier = await inserted(session, time_limit=2.5, argument='{"n": {"m": [1, 2]}}')

    row = await database.task(identifier)
    assert (row["status"], row["name"], row["time_limit"]) == ("queued", "r", 2.5)
    assert row["retry_delays"] == [0.0]
    assert json.loads(row["argument"]) == {"n": {"m": [1, 2]}}
    assert row["due_at"] is not None
    assert row["created_at"] is not None
    assert_cleared(
        row,
        "origin",
        "lease_owner",
        "lease_expires_at",
        "started_at",
        "cancel_requested_at",
        "outcome",
        "result",
        "finished_at",
    )
    assert await database.attempts(identifier) == []


@pytest.mark.asyncio
async def test_insert_of_a_held_identifier_raises_and_changes_nothing(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, runner)
        before = await database.task(identifier)
        with pytest.raises(PostgresException) as caught:
            await session.insert(new_task(identifier=identifier))

    assert caught.value.sqlstate == "23505"
    assert dict(await database.task(identifier)) == dict(before)


@pytest.mark.asyncio
async def test_claim_of_a_due_row_writes_the_lease(database: Database) -> None:
    runner = uuid4()
    async with database.store(lease=30.0).open() as session:
        identifier = await inserted(session, time_limit=2.5)
        due_at = (await database.task(identifier))["due_at"]
        claim = await claimed(session, runner)

    assert claim == (
        ClaimedAttempt(task=new_task(identifier=identifier, time_limit=2.5), attempt=1),
    )
    row = await database.task(identifier)
    assert row["status"] == "running"
    assert row["lease_owner"] == runner
    assert row["due_at"] == due_at
    assert row["started_at"] is not None
    assert (
        timedelta(seconds=29) < row["lease_expires_at"] - row["started_at"] <= timedelta(seconds=30)
    )
    assert_cleared(row, "cancel_requested_at", "outcome", "result", "finished_at")


@pytest.mark.asyncio
async def test_claim_of_an_expired_row_keeps_start_and_cancel_request(database: Database) -> None:
    first, second = uuid4(), uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, first)
        assert await session.cancel(identifier) == "pending"
        await database.expire(identifier)
        before = await database.task(identifier)
        claim = await claimed(session, second)

    assert claim == (
        ClaimedInterrupted(
            task=new_task(identifier=identifier),
            attempt=1,
            cancel_requested=True,
        ),
    )
    row = await database.task(identifier)
    assert row["lease_owner"] == second
    assert row["started_at"] == before["started_at"]
    assert row["cancel_requested_at"] == before["cancel_requested_at"]
    assert row["status"] == "running"
    assert row["lease_expires_at"] > before["lease_expires_at"]


@pytest.mark.asyncio
async def test_claim_returns_the_callers_own_expired_row(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, runner)
        await database.expire(identifier)
        (claim,) = await claimed(session, runner)

    assert isinstance(claim, ClaimedInterrupted)
    assert claim.attempt == 1
    assert not claim.cancel_requested


@pytest.mark.asyncio
async def test_claim_returns_listed_names_only_and_unlisted_rows_wait(database: Database) -> None:
    async with database.store().open() as session:
        other = await inserted(session, "unknown")
        for _ in range(3):
            assert await claimed(session, uuid4()) == ()

    row = await database.task(other)
    assert row["status"] == "queued"
    unlisted = await database.admin.fetch(
        "SELECT name, status, created_at FROM haiway_managed_tasks"
        " WHERE status <> 'finished' AND NOT (name = ANY($1::text[]))",
        ["r"],
    )
    assert [row["name"] for row in unlisted] == ["unknown"]


@pytest.mark.asyncio
async def test_claim_limit_counts_attempts_only(database: Database) -> None:
    first, second = uuid4(), uuid4()
    async with database.store().open() as session:
        for _ in range(3):
            await inserted(session)

        interrupted = [claim.task.identifier for claim in await claimed(session, first)]
        await database.expire(*interrupted)
        for _ in range(5):
            await inserted(session)

        result = await claimed(session, second, limit=2)
        assert sorted(type(claim).__name__ for claim in result) == [
            "ClaimedAttempt",
            "ClaimedAttempt",
            "ClaimedInterrupted",
            "ClaimedInterrupted",
            "ClaimedInterrupted",
        ]
        assert (
            await database.admin.fetchval(
                "SELECT count(*) FROM haiway_managed_tasks WHERE lease_owner = $1",
                second,
            )
            == 5
        )

        await database.expire(*interrupted)
        only_expired = await claimed(session, second, limit=0)
        assert sorted(claim.task.identifier for claim in only_expired) == sorted(interrupted)
        assert all(isinstance(claim, ClaimedInterrupted) for claim in only_expired)


@pytest.mark.asyncio
async def test_concurrent_claims_are_disjoint_and_complete(database: Database) -> None:
    async with database.store().open() as first, database.store().open() as second:
        identifiers = {await inserted(first) for _ in range(100)}

        async def drain(session: TaskStoreSession) -> list[UUID]:
            runner, taken = uuid4(), []
            while claim := await claimed(session, runner, limit=7):
                taken.extend(task.task.identifier for task in claim)

            return taken

        one, two = await gather(drain(first), drain(second))

    assert set(one).isdisjoint(two)
    assert set(one) | set(two) == identifiers
    assert len(one) + len(two) == 100


@pytest.mark.asyncio
async def test_renew_extends_held_leases_only(database: Database) -> None:
    runner = uuid4()
    async with database.store(lease=30.0).open() as session:
        held = await running(database, session, runner)
        marked = await running(database, session, runner)
        stolen = await running(database, session, runner)
        await session.cancel(marked)
        await database.admin.execute(
            "UPDATE haiway_managed_tasks SET lease_owner = $2 WHERE id = $1",
            stolen,
            uuid4(),
        )
        await database.expire(held)
        before = await database.task(held)
        renewal = await session.renew(runner, {held, marked, stolen})

    assert renewal.leases == {held: "held", marked: "cancel_requested"}
    row = await database.task(held)
    assert row["lease_expires_at"] > before["lease_expires_at"]
    for column in ("status", "due_at", "lease_owner", "started_at", "cancel_requested_at"):
        assert row[column] == before[column]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("end", "status", "outcome"),
    [
        (
            AttemptCompleted(result='{"v": 1}'),
            StoredFinished(outcome=StoredCompleted(result='{"v": 1}')),
            "completed",
        ),
        (AttemptRetried(failure=RAISED, delay=0.0), StoredRetryPending(), None),
        (AttemptFailed(failure=RAISED), StoredFinished(outcome=StoredFailed()), "failed"),
        (AttemptCancelled(), StoredFinished(outcome=StoredCancelled()), "cancelled"),
    ],
)
async def test_record_writes_each_end(
    database: Database,
    end: Any,
    status: Any,
    outcome: str | None,
) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, runner)
        started_at = (await database.task(identifier))["started_at"]
        recorded = await session.record(runner, identifier, 1, end)

    if isinstance(status, StoredFinished) and isinstance(status.outcome, StoredCompleted):
        assert isinstance(recorded, StoredFinished)
        assert isinstance(recorded.outcome, StoredCompleted)
        assert json.loads(recorded.outcome.result) == {"v": 1}

    else:
        assert recorded == status

    row = await database.task(identifier)
    assert_cleared(row, "lease_owner", "lease_expires_at", "started_at", "cancel_requested_at")
    assert row["outcome"] == outcome
    if outcome is None:
        assert row["status"] == "retry_pending"
        assert row["due_at"] is not None
        assert_cleared(row, "finished_at", "result")

    else:
        assert row["status"] == "finished"
        assert row["finished_at"] is not None
        assert_cleared(row, "due_at")
        assert (row["result"] is not None) == (outcome == "completed")

    (attempt,) = await database.attempts(identifier)
    assert attempt["number"] == 1
    assert attempt["started_at"] == started_at
    assert attempt["ended_at"] >= started_at
    assert attempt["result"] == ("failed" if end.end in ("retried", "failed") else end.end)


@pytest.mark.asyncio
async def test_record_stores_every_failure_kind(database: Database) -> None:
    runner = uuid4()
    failures = (
        (RAISED, ("raised", "builtins.ValueError", None)),
        (TaskTimeLimitExceeded(time_limit=3.0), ("time_limit", None, 3.0)),
        (TaskInterrupted(), ("interrupted", None, None)),
        (
            TaskFailureRecord(kind="preparation", exception_type="x.Error"),
            ("preparation", "x.Error", None),
        ),
    )
    async with database.store().open() as session:
        for failure, expected in failures:
            identifier = await running(database, session, runner)
            await session.record(runner, identifier, 1, AttemptFailed(failure=failure))
            (attempt,) = await database.attempts(identifier)
            assert (
                attempt["failure_kind"],
                attempt["exception_type"],
                attempt["time_limit"],
            ) == expected


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "end",
    [AttemptRetried(failure=RAISED, delay=60.0), AttemptCompleted(result="null")],
)
async def test_record_after_cancel_finishes_the_task_cancelled(
    database: Database,
    end: Any,
) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, runner)
        started_at = (await database.task(identifier))["started_at"]
        assert await session.cancel(identifier) == "pending"
        assert await session.record(runner, identifier, 1, end) == StoredFinished(
            outcome=StoredCancelled()
        )

    row = await database.task(identifier)
    assert (row["status"], row["outcome"]) == ("finished", "cancelled")
    assert_cleared(row, "due_at", "cancel_requested_at", "result", "lease_owner")
    (attempt,) = await database.attempts(identifier)
    assert attempt["result"] == "cancelled"
    assert attempt["started_at"] == started_at
    assert_cleared(attempt, "failure_kind", "exception_type", "time_limit")


@pytest.mark.asyncio
async def test_record_is_refused_to_everyone_but_the_holder_of_the_attempt(
    database: Database,
) -> None:
    runner, other = uuid4(), uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, runner)
        before = dict(await database.task(identifier))
        assert await session.record(other, identifier, 1, AttemptCancelled()) is None
        assert await session.record(runner, identifier, 2, AttemptCancelled()) is None
        assert await session.release(other, identifier, 1) is None
        assert dict(await database.task(identifier)) == before
        assert await database.attempts(identifier) == []

        # the lease expired unclaimed: its holder still records
        await database.expire(identifier)
        assert await session.record(runner, identifier, 1, AttemptCompleted(result="null"))
        assert await session.record(runner, identifier, 1, AttemptCompleted(result="null")) is None
        assert len(await database.attempts(identifier)) == 1


@pytest.mark.asyncio
async def test_a_stale_attempt_cannot_record_or_release(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, runner)
        assert (
            await session.record(runner, identifier, 1, AttemptRetried(failure=RAISED, delay=0.0))
            == StoredRetryPending()
        )
        (second,) = await claimed(session, runner)
        assert second.attempt == 2
        before = dict(await database.task(identifier))
        assert await session.record(runner, identifier, 1, AttemptCancelled()) is None
        assert await session.release(runner, identifier, 1) is None
        assert dict(await database.task(identifier)) == before
        assert len(await database.attempts(identifier)) == 1


@pytest.mark.asyncio
async def test_a_stale_record_and_release_waiting_on_the_row_lock_write_nothing(
    database: Database,
) -> None:
    # a transaction records attempt 1 and claims attempt 2 while a stale record and a stale
    # release of attempt 1 wait for the row lock; both have to find the attempt gone
    runner = uuid4()
    holder = await asyncpg.connect(database.dsn)
    try:
        async with database.store().open() as session:
            identifier = await running(database, session, runner)
            transaction = holder.transaction()
            await transaction.start()
            recorded = await holder.fetch(
                tasks_module.TASK_RECORD_STATEMENT,
                runner,
                identifier,
                1,
                "retried",
                None,
                0.0,
                "raised",
                "builtins.ValueError",
                None,
            )
            assert len(recorded) == 1
            stale = gather(
                session.record(runner, identifier, 1, AttemptCompleted(result='{"n": 9}')),
                session.release(runner, identifier, 1),
            )
            for _ in range(200):
                waiting = await database.admin.fetchval(
                    "SELECT count(*) FROM pg_stat_activity"
                    " WHERE datname = current_database() AND wait_event_type = 'Lock'"
                )
                if waiting >= 2:
                    break

                await sleep(0.05)

            assert waiting >= 2
            claim = await holder.fetch(tasks_module.TASK_CLAIM_STATEMENT, ["r"], None, runner, 30.0)
            assert [row["attempt"] for row in claim] == [2]
            await transaction.commit()
            assert await stale == [None, None]

        row = await database.task(identifier)
        assert (row["status"], row["lease_owner"], row["attempts"]) == ("running", runner, 1)
        assert [attempt["number"] for attempt in await database.attempts(identifier)] == [1]

    finally:
        await holder.close()


@pytest.mark.asyncio
async def test_retry_then_completion_keeps_both_attempts(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, runner)
        await session.record(runner, identifier, 1, AttemptRetried(failure=RAISED, delay=0.0))
        await claimed(session, runner)
        await session.record(runner, identifier, 2, AttemptCompleted(result="null"))
        stored = (await session.read({identifier}))[identifier]

    assert [attempt.result for attempt in stored.attempts] == [RAISED, "completed"]
    assert stored.status == StoredFinished(outcome=StoredCompleted(result="null"))


@pytest.mark.asyncio
async def test_interrupted_attempt_is_recorded_by_the_claimer(database: Database) -> None:
    first, second = uuid4(), uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, first)
        started_at = (await database.task(identifier))["started_at"]
        await database.expire(identifier)
        (claim,) = await claimed(session, second)
        assert isinstance(claim, ClaimedInterrupted)
        assert claim.attempt == 1
        await session.record(second, identifier, 1, AttemptFailed(failure=TaskInterrupted()))
        stored = (await session.read({identifier}))[identifier]

    assert stored.attempts[0].result == TaskInterrupted()
    assert (await database.attempts(identifier))[0]["started_at"] == started_at


@pytest.mark.asyncio
async def test_release_writes_queued_or_cancelled(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        plain = await running(database, session, runner)
        marked = await running(database, session, runner)
        await session.cancel(marked)
        assert await session.release(runner, plain, 1) == StoredQueued()
        assert await session.release(runner, marked, 1) == StoredFinished(outcome=StoredCancelled())
        # a second runtime takes the released task
        (again,) = await claimed(session, uuid4())

    assert again.task.identifier == plain
    row = await database.task(marked)
    assert (row["status"], row["outcome"]) == ("finished", "cancelled")
    assert row["finished_at"] is not None
    assert_cleared(row, "due_at", "lease_owner", "lease_expires_at", "started_at")
    assert await database.attempts(plain) == []
    assert await database.attempts(marked) == []


@pytest.mark.asyncio
async def test_cancel_writes_by_status(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        queued = await inserted(session)
        assert await session.cancel(queued) == "cancelled"
        row = await database.task(queued)
        assert (row["status"], row["outcome"]) == ("finished", "cancelled")
        assert row["finished_at"] is not None
        assert_cleared(row, "due_at")
        assert await session.cancel(queued) == "finished"
        assert await session.cancel(uuid4()) is None

        retrying = await running(database, session, runner)
        await session.record(runner, retrying, 1, AttemptRetried(failure=RAISED, delay=3600.0))
        assert await session.cancel(retrying) == "cancelled"
        assert (await database.task(retrying))["outcome"] == "cancelled"

        live = await running(database, session, runner)
        assert await session.cancel(live) == "pending"
        first = (await database.task(live))["cancel_requested_at"]
        assert first is not None
        assert await session.cancel(live) == "pending"
        row = await database.task(live)
        assert row["cancel_requested_at"] == first
        assert row["status"] == "running"
        assert row["lease_owner"] == runner


@pytest.mark.asyncio
async def test_cancel_racing_a_claim_never_violates_a_constraint(database: Database) -> None:
    async with database.store().open() as first, database.store().open() as second:
        for _ in range(20):
            identifier = await inserted(first)
            claim, cancellation = await gather(
                claimed(first, uuid4()),
                second.cancel(identifier),
            )
            row = await database.task(identifier)
            if claim:
                assert cancellation == "pending"
                assert row["status"] == "running"
                assert row["cancel_requested_at"] is not None

            else:
                assert cancellation == "cancelled"
                assert row["outcome"] == "cancelled"


# FR-23


@pytest.mark.asyncio
async def test_fail_unsupported_finishes_due_rows_of_unlisted_names(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        # the setup rows are claimed under another name and renamed, so no claim takes a
        # row set up for another case
        # each under a name of its own: a retry with no delay is due at once and would be
        # claimed again by a later claim of the same name
        retried = await running(database, session, runner, "y1")
        await session.record(runner, retried, 1, AttemptRetried(failure=RAISED, delay=0.0))
        later = await running(database, session, runner, "y2")
        await session.record(runner, later, 1, AttemptRetried(failure=RAISED, delay=3600.0))
        expired = await running(database, session, runner, "y3")
        await database.expire(expired)
        await database.admin.execute(
            "UPDATE haiway_managed_tasks SET name = 'x' WHERE id = ANY($1::uuid[])",
            [retried, later, expired],
        )
        due = [await inserted(session, "x") for _ in range(2)]
        listed = await inserted(session, "r")
        kept = {
            identifier: await database.task(identifier) for identifier in (listed, later, expired)
        }

        failed = await session.fail_unsupported({"r"})
        assert failed == dict.fromkeys((*due, retried), "x")

        for identifier in failed:
            row = await database.task(identifier)
            assert (row["status"], row["outcome"]) == ("finished", "failed")
            assert row["finished_at"] is not None
            assert_cleared(row, "due_at", "lease_owner", "started_at", "result")

        attempts = await database.attempts(retried)
        assert [attempt["number"] for attempt in attempts] == [1, 2]
        assert (attempts[1]["result"], attempts[1]["failure_kind"]) == ("failed", "unsupported")
        assert attempts[1]["started_at"] == attempts[1]["ended_at"]
        for identifier, before in kept.items():
            assert dict(await database.task(identifier)) == dict(before)

        stored = (await session.read({retried}))[retried]
        assert stored.status == StoredFinished(outcome=StoredFailed())
        assert stored.attempts[-1].result == TaskUnsupported()


@pytest.mark.asyncio
async def test_fail_unsupported_of_nothing_listed_finishes_every_due_row(
    database: Database,
) -> None:
    async with database.store().open() as session:
        identifiers = {await inserted(session, f"n{index}") for index in range(4)}
        assert set(await session.fail_unsupported(set())) == identifiers


@pytest.mark.asyncio
async def test_concurrent_fail_unsupported_is_disjoint_and_complete(database: Database) -> None:
    async with database.store().open() as first, database.store().open() as second:
        identifiers = {await inserted(first, "x") for _ in range(100)}
        one, two = await gather(first.fail_unsupported({"r"}), second.fail_unsupported({"r"}))

    assert set(one).isdisjoint(two)
    assert set(one) | set(two) == identifiers
    for identifier in identifiers:
        assert [a["failure_kind"] for a in await database.attempts(identifier)] == ["unsupported"]


@pytest.mark.asyncio
async def test_fail_unsupported_racing_a_claim_never_finishes_a_claimed_row(
    database: Database,
) -> None:
    runner = uuid4()
    async with database.store().open() as first, database.store().open() as second:
        for _ in range(5):
            identifiers = {await inserted(first, "x") for _ in range(50)}
            failed, taken = await gather(
                first.fail_unsupported({"r"}),
                claim_all(second, runner, {"x"}),
            )
            assert set(failed).isdisjoint(taken)
            assert set(failed) | set(taken) == identifiers
            for identifier in taken:
                assert (await database.task(identifier))["status"] == "running"

            for identifier in failed:
                assert (await database.task(identifier))["status"] == "finished"

            await database.admin.execute("DELETE FROM haiway_managed_tasks")


async def claim_all(
    session: TaskStoreSession,
    runner: UUID,
    names: set[str],
) -> list[UUID]:
    taken: list[UUID] = []
    for _ in range(100):
        taken.extend(
            claim.task.identifier for claim in await session.claim(runner, names, limit=10)
        )

    return taken


# FR-11, FR-21, FR-15


@pytest.mark.asyncio
async def test_read_returns_what_another_runtime_recorded(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as writer, database.store().open() as reader:
        identifier = await running(
            database, writer, runner, argument='{"a": {"b": [1, {"c": null}]}}'
        )
        await writer.record(
            runner,
            identifier,
            1,
            AttemptRetried(failure=TaskTimeLimitExceeded(time_limit=1.5), delay=0.0),
        )
        await claimed(writer, runner)
        await writer.record(runner, identifier, 2, AttemptCompleted(result='{"v": [1]}'))
        stored = (await reader.read({identifier}))[identifier]

    assert json.loads(stored.task.argument) == {"a": {"b": [1, {"c": None}]}}
    assert stored.attempts[0].result == TaskTimeLimitExceeded(time_limit=1.5)
    assert stored.attempts[1].result == "completed"
    assert isinstance(stored.status, StoredFinished)
    assert isinstance(stored.status.outcome, StoredCompleted)
    assert json.loads(stored.status.outcome.result) == {"v": [1]}
    assert StoredTask.from_json(stored.to_json()) == stored


@pytest.mark.asyncio
async def test_read_skips_unknown_identifiers(database: Database) -> None:
    async with database.store().open() as session:
        known = {await inserted(session) for _ in range(48)}
        stored = await session.read({*known, uuid4(), uuid4()})

    assert set(stored) == known


@pytest.mark.asyncio
async def test_read_keeps_the_origin(database: Database) -> None:
    async with database.store().open() as session:
        identifier = await inserted(session)
        await database.admin.execute(
            "UPDATE haiway_managed_tasks SET origin = $2::jsonb WHERE id = $1",
            identifier,
            json.dumps(
                {
                    "trace_id": str(UUID(int=1)),
                    "scope_id": str(UUID(int=2)),
                    "trace_context": {},
                }
            ),
        )
        stored = (await session.read({identifier}))[identifier]

    assert stored.task.origin is not None
    assert stored.task.origin.scope_id == UUID(int=2)


@pytest.mark.asyncio
async def test_deleted_finished_tasks_leave_no_attempts(database: Database) -> None:
    runner = uuid4()
    async with database.store().open() as session:
        identifier = await running(database, session, runner)
        await session.record(runner, identifier, 1, AttemptFailed(failure=RAISED))
        await database.admin.execute(
            "DELETE FROM haiway_managed_tasks WHERE status = 'finished' AND finished_at < $1",
            datetime.now(UTC) + timedelta(days=1),
        )
        assert await session.read({identifier}) == {}

    assert await database.attempts(identifier) == []


# FR-24


@pytest.mark.asyncio
async def test_query_pages_in_creation_order(database: Database) -> None:
    async with database.store().open() as session:
        identifiers = [await inserted(session, "r" if index % 2 else "s") for index in range(5)]
        first = await session.query(TaskFilter(), after=None, limit=2)
        assert [summary.identifier for summary in first] == identifiers[:2]
        keyset = TaskKeyset(created=first[1].created, identifier=first[1].identifier)
        second = await session.query(TaskFilter(), after=keyset, limit=2)
        assert [summary.identifier for summary in second] == identifiers[2:4]
        assert await session.query(TaskFilter(), after=None, limit=0) == ()
        with pytest.raises(ValueError):
            await session.query(TaskFilter(), after=None, limit=-1)

        odd = await session.query(TaskFilter(names={"r"}), after=None, limit=10)
        assert [summary.identifier for summary in odd] == [identifiers[1], identifiers[3]]
        both = await session.query(TaskFilter(names={"r", "s"}), after=None, limit=10)
        assert [summary.identifier for summary in both] == identifiers
        assert await session.query(TaskFilter(names=set()), after=None, limit=10) == ()


@pytest.mark.asyncio
async def test_query_filters_and_summaries(database: Database) -> None:
    runner = uuid4()
    start = datetime.now(UTC) - timedelta(seconds=1)
    async with database.store().open() as session:
        failed = await running(database, session, runner, "r")
        await session.record(runner, failed, 1, AttemptRetried(failure=RAISED, delay=0.0))
        await claimed(session, runner)
        await session.record(runner, failed, 2, AttemptFailed(failure=RAISED))
        completed = await running(database, session, runner, "r")
        await session.record(runner, completed, 1, AttemptCompleted(result="null"))
        other = await running(database, session, runner, "s")
        await session.record(runner, other, 1, AttemptFailed(failure=RAISED))
        queued = await inserted(session, "r")

        matching = await session.query(
            TaskFilter(
                names={"r"},
                status=TasksFinished(outcomes={"failed"}, finished=TaskTimeFrom(start=start)),
            ),
            after=None,
            limit=10,
        )
        assert [summary.identifier for summary in matching] == [failed]
        summary = matching[0]
        assert summary.attempts == 2
        assert isinstance(summary.status, TaskSummaryFinished)
        assert summary.status.outcome == "failed"
        assert summary.status.finished >= start
        assert summary.created.tzinfo is not None

        assert (
            await session.query(
                TaskFilter(
                    status=TasksFinished(
                        outcomes={"failed", "completed"},
                        finished=TaskTimeBefore(end=start),
                    )
                ),
                after=None,
                limit=10,
            )
            == ()
        )
        unfinished = await session.query(
            TaskFilter(status=TasksUnfinished(statuses={"queued"})), after=None, limit=10
        )
        assert [(s.identifier, s.status) for s in unfinished] == [
            (queued, TaskSummaryUnfinished(status="queued"))
        ]
        assert (
            await session.query(
                TaskFilter(status=TasksUnfinished(statuses=set())), after=None, limit=10
            )
            == ()
        )
        recent = await session.query(
            TaskFilter(created=TaskTimeFrom(start=start)), after=None, limit=10
        )
        assert len(recent) == 4


# FR-5 acceptance 4, FR-23 acceptance 6, FR-24 acceptance 5 and 6


async def plan(
    database: Database,
    types: str,
    statement: str,
    arguments: str,
    mode: str,
) -> str:
    admin = database.admin
    # a name of its own, dropped alone: asyncpg caches prepared statements of the connection
    name = "checked_" + uuid4().hex
    await admin.execute(f"SET plan_cache_mode = {mode}")
    await admin.execute(f"PREPARE {name} ({types}) AS {statement}")
    try:
        rows = await admin.fetch(f"EXPLAIN EXECUTE {name}({arguments})")

    finally:
        await admin.execute(f"DEALLOCATE {name}")

    return "\n".join(row[0] for row in rows)


@pytest.mark.asyncio
async def test_statements_are_served_by_their_indexes(database: Database) -> None:
    admin = database.admin
    await admin.execute(
        """
        INSERT INTO haiway_managed_tasks
            (id, name, argument, retry_delays, status, outcome, finished_at, created_at)
        SELECT gen_random_uuid(), 'n' || (g % 200), '{}', '{}', 'finished', 'cancelled',
            statement_timestamp(), statement_timestamp() - g * interval '1 second'
        FROM generate_series(1, 200000) AS g
        """
    )
    await admin.execute(
        """
        INSERT INTO haiway_managed_tasks
            (id, name, argument, retry_delays, status, due_at)
        SELECT gen_random_uuid(), 'n1', '{}', '{}', 'queued',
            statement_timestamp() - interval '1 minute'
        FROM generate_series(1, 100)
        """
    )
    await admin.execute("ANALYZE haiway_managed_tasks")

    for mode in ("force_custom_plan", "force_generic_plan"):
        for limit in ("10", "NULL", "0"):
            claim = await plan(
                database,
                "text[], bigint, uuid, double precision",
                tasks_module.TASK_CLAIM_STATEMENT,
                f"ARRAY['n1'], {limit}, gen_random_uuid(), 30.0",
                mode,
            )
            assert "haiway_managed_tasks_open" in claim, claim
            assert re.search(r"Index Cond:.*due_at <= statement_timestamp\(\)", claim), claim
            assert "haiway_managed_tasks_leases" in claim, claim
            assert re.search(r"Index Cond:.*lease_expires_at < statement_timestamp\(\)", claim), (
                claim
            )

        unsupported = await plan(
            database,
            "text[]",
            tasks_module.TASK_FAIL_UNSUPPORTED_STATEMENT,
            "ARRAY['r']",
            mode,
        )
        assert "haiway_managed_tasks_open" in unsupported, unsupported
        assert re.search(r"Index Cond:.*due_at <= statement_timestamp\(\)", unsupported), (
            unsupported
        )

        statement, _ = tasks_module._query_statement(
            TaskFilter(),
            TaskKeyset(created=datetime.now(UTC), identifier=uuid4()),
            50,
        )
        listing = await plan(
            database,
            "timestamptz, uuid, bigint",
            statement,
            "statement_timestamp() - interval '1 day', gen_random_uuid(), 50",
            mode,
        )
        assert "haiway_managed_tasks_created" in listing, listing
        assert re.search(r"created_at, id\) > ", listing), listing

        statement, _ = tasks_module._query_statement(
            TaskFilter(names={"n7"}),
            TaskKeyset(created=datetime.now(UTC), identifier=uuid4()),
            50,
        )
        named = await plan(
            database,
            "text, timestamptz, uuid, bigint",
            statement,
            "'n7', statement_timestamp() - interval '1 day', gen_random_uuid(), 50",
            mode,
        )
        assert "haiway_managed_tasks_names" in named, named
        assert re.search(r"Index Cond:.*name = ", named), named
        assert "Sort" not in named, named
