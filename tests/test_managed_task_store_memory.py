import asyncio
from collections.abc import AsyncGenerator, Coroutine
from datetime import UTC, datetime, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4

import pytest_asyncio
from pytest import MonkeyPatch, fixture, mark, raises

from haiway.context.managed import (
    AttemptCancelled,
    AttemptCompleted,
    AttemptEnd,
    AttemptFailed,
    AttemptRetried,
    ClaimedAttempt,
    LeasedCoordination,
    NewTask,
    StoredAttempt,
    StoredCancelled,
    StoredCompleted,
    StoredFailed,
    StoredFinished,
    StoredQueued,
    StoredRetryPending,
    StoredRunning,
    StoredTask,
    TaskFailureRecord,
    TaskFilter,
    TaskKeyset,
    TaskRetryPolicy,
    TasksFinished,
    TaskSummaryFinished,
    TaskSummaryUnfinished,
    TasksUnfinished,
    TaskTimeBefore,
    TaskTimeBetween,
    TaskTimeFrom,
    TaskUnsupported,
)
from haiway.context.managed.memory import InMemoryTaskStore
from haiway.context.managed.store import LocalCoordination, TaskStoreSession

_FAILURE = TaskFailureRecord(kind="raised", exception_type="builtins.ValueError")
_WALL_START = datetime(2026, 1, 1, tzinfo=UTC)
_EVERYTHING = TaskFilter()


class Clock:
    """Controls the monotonic and the wall clock of the in-memory store."""

    def __init__(self) -> None:
        self.monotonic: float = 1000.0
        self.wall: datetime = _WALL_START
        self.wall_frozen: bool = False

    def advance(
        self,
        seconds: float,
    ) -> None:
        self.monotonic += seconds

    def read_wall(self) -> datetime:
        wall: datetime = self.wall
        if not self.wall_frozen:
            self.wall += timedelta(microseconds=10)

        return wall


@fixture
def clock(monkeypatch: MonkeyPatch) -> Clock:
    # asyncio's own timers read time.monotonic, only the store's module name is replaced
    controlled = Clock()
    monkeypatch.setattr("haiway.context.managed.memory.monotonic", lambda: controlled.monotonic)
    monkeypatch.setattr("haiway.context.managed.memory._wall_now", controlled.read_wall)
    return controlled


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[TaskStoreSession]:
    async with InMemoryTaskStore().open() as opened:
        yield opened


def _task(
    name: str = "r",
    *,
    identifier: UUID | None = None,
) -> NewTask:
    return NewTask(
        identifier=identifier if identifier is not None else uuid4(),
        name=name,
        argument="{}",
        retry=TaskRetryPolicy(delays=(0.0,)),
        time_limit=None,
        origin=None,
    )


async def _read(
    session: TaskStoreSession,
    task: NewTask,
) -> StoredTask:
    return (await session.read({task.identifier}))[task.identifier]


async def _running(
    session: TaskStoreSession,
    runner: UUID,
    name: str = "r",
) -> NewTask:
    task: NewTask = _task(name)
    await session.insert(task)
    claimed = await session.claim(runner, {name}, limit=1)
    assert [claim.task for claim in claimed] == [task]
    return task


async def _listed(
    session: TaskStoreSession,
    matching: TaskFilter = _EVERYTHING,
    *,
    after: TaskKeyset | None = None,
    limit: int = 1000,
) -> list[UUID]:
    return [
        summary.identifier for summary in await session.query(matching, after=after, limit=limit)
    ]


# --- coordination ------------------------------------------------------------------------


@mark.asyncio
async def test_session_yields_local_coordination() -> None:
    async with InMemoryTaskStore().open() as session:
        assert isinstance(session.coordination, LocalCoordination)
        assert not isinstance(session.coordination, LeasedCoordination)
        assert session.coordination.coordination == "local"


@mark.parametrize("retention", [float("nan"), float("inf"), float("-inf"), -1.0, -0.1])
def test_retention_out_of_range_is_rejected(retention: float) -> None:
    with raises(ValueError, match="retention"):
        InMemoryTaskStore(retention=retention)


def test_retention_accepts_zero() -> None:
    InMemoryTaskStore(retention=0.0)


# --- insert and read (FR-78) -----------------------------------------------------------------


@mark.asyncio
async def test_insert_records_queued_task_without_attempts(session: TaskStoreSession) -> None:
    task: NewTask = _task()
    await session.insert(task)

    stored: StoredTask = await _read(session, task)
    assert stored.task == task
    assert stored.status == StoredQueued()
    assert stored.attempts == ()


@mark.asyncio
async def test_read_returns_only_known_identifiers(session: TaskStoreSession) -> None:
    first: NewTask = _task()
    second: NewTask = _task()
    unknown: UUID = uuid4()
    await session.insert(first)
    await session.insert(second)

    result = await session.read({first.identifier, second.identifier, unknown})

    assert set(result) == {first.identifier, second.identifier}
    assert await session.read(set()) == {}


@mark.asyncio
async def test_stored_task_keeps_status_through_json(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    await session.record(runner, task.identifier, 1, AttemptRetried(failure=_FAILURE, delay=5.0))

    stored: StoredTask = await _read(session, task)

    assert stored.status == StoredRetryPending()
    assert StoredTask.from_json(stored.to_json()) == stored


# --- claim (FR-78) -------------------------------------------------------------------------


@mark.asyncio
async def test_claim_marks_running_with_attempt_number(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    task: NewTask = _task()
    await session.insert(task)

    claimed = await session.claim(runner, {"r"}, limit=None)

    assert claimed == [ClaimedAttempt(task=task, attempt=1)]
    assert (await _read(session, task)).status == StoredRunning(cancel_requested=False)
    assert await session.claim(uuid4(), {"r"}, limit=None) == []


@mark.asyncio
async def test_claim_takes_only_listed_names(session: TaskStoreSession) -> None:
    listed: NewTask = _task("a")
    other: NewTask = _task("b")
    await session.insert(listed)
    await session.insert(other)

    claimed = await session.claim(uuid4(), {"a"}, limit=None)

    assert [claim.task for claim in claimed] == [listed]
    assert (await _read(session, other)).status == StoredQueued()
    assert await session.claim(uuid4(), set(), limit=None) == []


@mark.parametrize(
    ("limit", "expected"),
    [(None, 3), (0, 0), (1, 1), (2, 2), (10, 3)],
)
@mark.asyncio
async def test_claim_respects_limit(
    session: TaskStoreSession,
    limit: int | None,
    expected: int,
) -> None:
    for _ in range(3):
        await session.insert(_task())

    assert len(await session.claim(uuid4(), {"r"}, limit=limit)) == expected


@mark.asyncio
async def test_claim_returns_tasks_in_due_order(session: TaskStoreSession, clock: Clock) -> None:
    runner: UUID = uuid4()
    first: NewTask = await _running(session, runner)  # due at 1000
    await session.record(runner, first.identifier, 1, AttemptRetried(failure=_FAILURE, delay=1.0))
    clock.advance(0.5)
    second: NewTask = _task()
    third: NewTask = _task()
    await session.insert(second)  # due at 1000.5
    await session.insert(third)  # due at 1000.5, inserted after the second
    clock.advance(1.0)  # the retry of the first is due at 1001

    claimed = await session.claim(runner, {"r"}, limit=None)

    assert [claim.task for claim in claimed] == [second, third, first]
    assert [claim.attempt for claim in claimed] == [1, 1, 2]


@mark.asyncio
async def test_claim_takes_retry_pending_only_when_due(
    session: TaskStoreSession,
    clock: Clock,
) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    await session.record(runner, task.identifier, 1, AttemptRetried(failure=_FAILURE, delay=2.0))

    assert await session.claim(runner, {"r"}, limit=None) == []
    clock.advance(1.9)
    assert await session.claim(runner, {"r"}, limit=None) == []
    clock.advance(0.1)

    assert await session.claim(runner, {"r"}, limit=None) == [ClaimedAttempt(task=task, attempt=2)]


@mark.asyncio
async def test_concurrent_claims_return_task_once(session: TaskStoreSession) -> None:
    task: NewTask = _task()
    await session.insert(task)

    results = await asyncio.gather(
        session.claim(uuid4(), {"r"}, limit=None),
        session.claim(uuid4(), {"r"}, limit=None),
    )

    assert sorted(len(result) for result in results) == [0, 1]


@mark.asyncio
async def test_lease_never_expires(session: TaskStoreSession, clock: Clock) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)

    clock.advance(10.0)

    assert await session.claim(uuid4(), {"r"}, limit=None) == []
    assert (await session.renew(runner, {task.identifier})).leases == {task.identifier: "held"}
    assert await session.record(runner, task.identifier, 1, AttemptCompleted(result="null")) == (
        StoredFinished(outcome=StoredCompleted(result="null"))
    )


# --- renew ---------------------------------------------------------------------------------


@mark.asyncio
async def test_renew_maps_held_and_cancel_requested(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    held: NewTask = await _running(session, runner)
    cancelled: NewTask = await _running(session, runner)
    queued: NewTask = _task()
    await session.insert(queued)
    await session.cancel(cancelled.identifier)

    renewal = await session.renew(
        runner,
        {held.identifier, cancelled.identifier, queued.identifier, uuid4()},
    )

    assert renewal.leases == {
        held.identifier: "held",
        cancelled.identifier: "cancel_requested",
    }


@mark.asyncio
async def test_renew_ignores_leases_of_other_runners(session: TaskStoreSession) -> None:
    task: NewTask = await _running(session, uuid4())

    assert (await session.renew(uuid4(), {task.identifier})).leases == {}


# --- record (FR-78, FR-65 store side) ------------------------------------------------------


@mark.parametrize(
    ("end", "expected"),
    [
        (
            AttemptCompleted(result='{"a":1}'),
            StoredFinished(outcome=StoredCompleted(result='{"a":1}')),
        ),
        (AttemptRetried(failure=_FAILURE, delay=1.0), StoredRetryPending()),
        (AttemptFailed(failure=_FAILURE), StoredFinished(outcome=StoredFailed())),
        (AttemptCancelled(), StoredFinished(outcome=StoredCancelled())),
    ],
)
@mark.asyncio
async def test_record_fixes_status_of_variant(
    session: TaskStoreSession,
    end: AttemptEnd,
    expected: object,
) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)

    assert await session.record(runner, task.identifier, 1, end) == expected

    stored: StoredTask = await _read(session, task)
    assert stored.status == expected
    assert len(stored.attempts) == 1


@mark.asyncio
async def test_record_keeps_failure_without_message(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    await session.record(runner, task.identifier, 1, AttemptRetried(failure=_FAILURE, delay=0.0))
    await session.claim(runner, {"r"}, limit=None)
    await session.record(runner, task.identifier, 2, AttemptFailed(failure=_FAILURE))

    stored: StoredTask = await _read(session, task)

    assert stored.attempts == (StoredAttempt(result=_FAILURE), StoredAttempt(result=_FAILURE))
    assert stored.status == StoredFinished(outcome=StoredFailed())


@mark.asyncio
async def test_record_retried_makes_task_due_after_delay(
    session: TaskStoreSession,
    clock: Clock,
) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    await session.record(runner, task.identifier, 1, AttemptRetried(failure=_FAILURE, delay=3.0))

    assert await session.claim(runner, {"r"}, limit=None) == []
    clock.advance(3.0)
    assert [claim.attempt for claim in await session.claim(runner, {"r"}, limit=None)] == [2]


@mark.asyncio
async def test_record_without_lease_returns_none(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    running: NewTask = await _running(session, runner)
    queued: NewTask = _task()
    await session.insert(queued)
    end = AttemptCompleted(result="null")

    assert await session.record(runner, queued.identifier, 1, end) is None  # not claimed
    assert await session.record(uuid4(), running.identifier, 1, end) is None  # another runner
    assert await session.record(runner, uuid4(), 1, end) is None  # unknown
    assert await session.record(runner, running.identifier, 1, end) is not None
    assert await session.record(runner, running.identifier, 1, end) is None  # already recorded
    assert len((await _read(session, running)).attempts) == 1
    assert (await _read(session, queued)).attempts == ()


@mark.parametrize(
    "end",
    [
        AttemptRetried(failure=_FAILURE, delay=1.0),
        AttemptCompleted(result="null"),
        AttemptFailed(failure=_FAILURE),
        AttemptCancelled(),
    ],
)
@mark.asyncio
async def test_record_after_accepted_cancel_finishes_cancelled(
    session: TaskStoreSession,
    end: AttemptEnd,
) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    assert await session.cancel(task.identifier) == "pending"

    assert await session.record(runner, task.identifier, 1, end) == StoredFinished(
        outcome=StoredCancelled()
    )

    stored: StoredTask = await _read(session, task)
    assert stored.attempts == (StoredAttempt(result="cancelled"),)
    assert stored.status == StoredFinished(outcome=StoredCancelled())


# --- release -------------------------------------------------------------------------------


@mark.asyncio
async def test_release_finishes_cancelled_without_attempt(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)

    assert await session.release(runner, task.identifier, 1) == StoredFinished(
        outcome=StoredCancelled()
    )

    stored: StoredTask = await _read(session, task)
    assert stored.attempts == ()
    assert stored.status == StoredFinished(outcome=StoredCancelled())


@mark.asyncio
async def test_release_after_cancel_request_finishes_cancelled(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    await session.cancel(task.identifier)

    assert await session.release(runner, task.identifier, 1) == StoredFinished(
        outcome=StoredCancelled()
    )
    assert await session.cancel(task.identifier) == "finished"


@mark.asyncio
async def test_release_without_lease_returns_none(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    running: NewTask = await _running(session, runner)
    queued: NewTask = _task()
    await session.insert(queued)

    assert await session.release(runner, queued.identifier, 1) is None
    assert await session.release(uuid4(), running.identifier, 1) is None
    assert await session.release(runner, uuid4(), 1) is None
    assert (await _read(session, queued)).status == StoredQueued()
    assert (await _read(session, running)).status == StoredRunning(cancel_requested=False)


# --- cancel --------------------------------------------------------------------------------


@mark.asyncio
async def test_cancel_result_per_status(session: TaskStoreSession, clock: Clock) -> None:
    runner: UUID = uuid4()
    running: NewTask = await _running(session, runner)
    retrying: NewTask = await _running(session, runner)
    queued: NewTask = _task()
    await session.insert(queued)
    retried = AttemptRetried(failure=_FAILURE, delay=9.0)
    await session.record(runner, retrying.identifier, 1, retried)

    assert await session.cancel(queued.identifier) == "cancelled"
    assert (await _read(session, queued)).status == StoredFinished(outcome=StoredCancelled())
    assert await session.cancel(retrying.identifier) == "cancelled"
    retrying_stored: StoredTask = await _read(session, retrying)
    assert retrying_stored.status == StoredFinished(outcome=StoredCancelled())
    assert len(retrying_stored.attempts) == 1
    assert await session.cancel(running.identifier) == "pending"
    assert await session.cancel(running.identifier) == "pending"
    assert (await _read(session, running)).status == StoredRunning(cancel_requested=True)
    await session.record(runner, running.identifier, 1, AttemptCompleted(result="null"))
    assert await session.cancel(running.identifier) == "finished"
    assert await session.cancel(uuid4()) is None


@mark.asyncio
async def test_cancelled_task_is_not_claimed(session: TaskStoreSession) -> None:
    task: NewTask = _task()
    await session.insert(task)
    await session.cancel(task.identifier)

    assert await session.claim(uuid4(), {"r"}, limit=None) == []


# --- fail_unsupported (FR-93 store side, D-116) ----------------------------------------------


@mark.asyncio
async def test_fail_unsupported_finishes_due_queued_task(session: TaskStoreSession) -> None:
    unlisted: NewTask = _task("x")
    listed: NewTask = _task("r")
    await session.insert(unlisted)
    await session.insert(listed)

    failed = await session.fail_unsupported({"r"})

    assert failed == {unlisted.identifier: "x"}
    stored: StoredTask = await _read(session, unlisted)
    assert stored.status == StoredFinished(outcome=StoredFailed())
    assert stored.attempts == (StoredAttempt(result=TaskUnsupported()),)
    assert (await _read(session, listed)).status == StoredQueued()
    assert await session.claim(uuid4(), {"x"}, limit=None) == []


@mark.asyncio
async def test_fail_unsupported_finishes_due_retry_pending_task(
    session: TaskStoreSession,
    clock: Clock,
) -> None:
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner, "x")
    await session.record(runner, task.identifier, 1, AttemptRetried(failure=_FAILURE, delay=0.3))

    assert await session.fail_unsupported({"r"}) == {}
    assert (await _read(session, task)).status == StoredRetryPending()
    clock.advance(0.2)
    assert await session.fail_unsupported({"r"}) == {}
    clock.advance(0.1)

    assert await session.fail_unsupported({"r"}) == {task.identifier: "x"}
    stored: StoredTask = await _read(session, task)
    assert stored.status == StoredFinished(outcome=StoredFailed())
    assert stored.attempts == (
        StoredAttempt(result=_FAILURE),
        StoredAttempt(result=TaskUnsupported()),
    )
    assert await session.claim(runner, {"x"}, limit=None) == []


@mark.asyncio
async def test_fail_unsupported_leaves_listed_running_and_finished_tasks(
    session: TaskStoreSession,
) -> None:
    runner: UUID = uuid4()
    running: NewTask = await _running(session, runner, "x")
    finished: NewTask = await _running(session, runner, "x")
    await session.record(runner, finished.identifier, 1, AttemptCompleted(result="null"))
    cancelled: NewTask = _task("x")
    await session.insert(cancelled)
    await session.cancel(cancelled.identifier)
    listed: NewTask = _task("x")
    await session.insert(listed)

    assert await session.fail_unsupported({"x"}) == {}
    assert await session.fail_unsupported({"r"}) == {listed.identifier: "x"}

    assert (await _read(session, running)).status == StoredRunning(cancel_requested=False)
    assert (await _read(session, finished)).status == StoredFinished(
        outcome=StoredCompleted(result="null")
    )
    assert (await _read(session, cancelled)).status == StoredFinished(outcome=StoredCancelled())


@mark.asyncio
async def test_cancel_after_fail_unsupported_returns_finished(session: TaskStoreSession) -> None:
    task: NewTask = _task("x")
    await session.insert(task)
    await session.fail_unsupported(set())

    assert await session.cancel(task.identifier) == "finished"
    assert (await _read(session, task)).status == StoredFinished(outcome=StoredFailed())


@mark.asyncio
async def test_fail_unsupported_after_cancel_keeps_cancelled(session: TaskStoreSession) -> None:
    task: NewTask = _task("x")
    await session.insert(task)
    await session.cancel(task.identifier)

    assert await session.fail_unsupported(set()) == {}
    assert (await _read(session, task)).status == StoredFinished(outcome=StoredCancelled())


# --- query (FR-95, FR-96, FR-97, FR-98 store side) ---------------------------------------------


@mark.asyncio
async def test_query_filters_by_name_status_and_outcome(session: TaskStoreSession) -> None:
    runner: UUID = uuid4()
    queued: NewTask = _task("a")
    await session.insert(queued)
    completed: NewTask = await _running(session, runner, "b")
    await session.record(runner, completed.identifier, 1, AttemptCompleted(result="null"))
    failed: NewTask = await _running(session, runner, "b")
    await session.record(runner, failed.identifier, 1, AttemptFailed(failure=_FAILURE))
    running: NewTask = await _running(session, runner, "b")

    assert await _listed(session) == [
        queued.identifier,
        completed.identifier,
        failed.identifier,
        running.identifier,
    ]
    assert await _listed(session, TaskFilter(names={"a"})) == [queued.identifier]
    assert await _listed(session, TaskFilter(names=set())) == []
    assert await _listed(session, TaskFilter(names={"a", "b", "c"})) == await _listed(session)
    assert await _listed(session, TaskFilter(status=TasksFinished(outcomes={"failed"}))) == [
        failed.identifier
    ]
    assert await _listed(
        session, TaskFilter(status=TasksFinished(outcomes={"failed", "completed"}))
    ) == [completed.identifier, failed.identifier]
    assert await _listed(session, TaskFilter(status=TasksFinished(outcomes=set()))) == []
    assert await _listed(
        session, TaskFilter(status=TasksUnfinished(statuses={"queued", "running"}))
    ) == [queued.identifier, running.identifier]
    retry_pending = TaskFilter(status=TasksUnfinished(statuses={"retry_pending"}))
    assert await _listed(session, retry_pending) == []
    assert await _listed(session, TaskFilter(status=TasksUnfinished(statuses=set()))) == []
    assert await _listed(
        session,
        TaskFilter(names={"b"}, status=TasksUnfinished(statuses={"queued", "running"})),
    ) == [running.identifier]


@mark.asyncio
async def test_query_summarizes_tasks_without_task_data(
    session: TaskStoreSession,
    clock: Clock,
) -> None:
    runner: UUID = uuid4()
    task: NewTask = NewTask(
        identifier=uuid4(),
        name="r",
        argument='{"secret":"s3"}',
        retry=TaskRetryPolicy(delays=(0.0,)),
        time_limit=None,
        origin=None,
    )
    await session.insert(task)
    await session.claim(runner, {"r"}, limit=None)
    await session.record(runner, task.identifier, 1, AttemptRetried(failure=_FAILURE, delay=0.0))

    (retrying,) = await session.query(_EVERYTHING, after=None, limit=10)

    assert retrying.status == TaskSummaryUnfinished(status="retry_pending")
    assert retrying.attempts == 1
    assert retrying.name == "r"
    assert retrying.created == _WALL_START

    await session.claim(runner, {"r"}, limit=None)
    await session.record(runner, task.identifier, 2, AttemptCompleted(result="null"))
    (completed,) = await session.query(_EVERYTHING, after=None, limit=10)

    assert completed.attempts == 2
    assert completed.status == TaskSummaryFinished(
        outcome="completed",
        finished=completed.created + timedelta(microseconds=10),
    )
    assert "s3" not in completed.to_json()


@mark.asyncio
async def test_query_lists_cancelled_without_attempts(session: TaskStoreSession) -> None:
    task: NewTask = _task()
    await session.insert(task)
    await session.cancel(task.identifier)

    (summary,) = await session.query(_EVERYTHING, after=None, limit=10)

    assert summary.attempts == 0
    assert isinstance(summary.status, TaskSummaryFinished)
    assert summary.status.outcome == "cancelled"


@mark.asyncio
async def test_query_pages_by_keyset_oldest_first(session: TaskStoreSession) -> None:
    tasks: list[NewTask] = [_task() for _ in range(5)]
    for task in tasks:
        await session.insert(task)
    expected: list[UUID] = [task.identifier for task in tasks]

    first = await session.query(_EVERYTHING, after=None, limit=2)
    second = await session.query(
        _EVERYTHING,
        after=TaskKeyset(created=first[-1].created, identifier=first[-1].identifier),
        limit=2,
    )
    third = await session.query(
        _EVERYTHING,
        after=TaskKeyset(created=second[-1].created, identifier=second[-1].identifier),
        limit=2,
    )
    fourth = await session.query(
        _EVERYTHING,
        after=TaskKeyset(created=third[-1].created, identifier=third[-1].identifier),
        limit=2,
    )

    assert [len(page) for page in (first, second, third, fourth)] == [2, 2, 1, 0]
    assert [summary.identifier for page in (first, second, third) for summary in page] == expected


@mark.asyncio
async def test_query_orders_equal_creation_times_by_identifier(
    session: TaskStoreSession,
    clock: Clock,
) -> None:
    clock.wall_frozen = True
    for identifier in (UUID(int=3), UUID(int=1), UUID(int=2)):
        await session.insert(_task(identifier=identifier))

    pages: list[UUID] = []
    keyset: TaskKeyset | None = None
    for _ in range(4):
        page = await session.query(_EVERYTHING, after=keyset, limit=1)
        if not page:
            break

        pages.append(page[0].identifier)
        keyset = TaskKeyset(created=page[0].created, identifier=page[0].identifier)

    assert pages == [UUID(int=1), UUID(int=2), UUID(int=3)]


@mark.asyncio
async def test_query_keyset_continues_with_another_filter(session: TaskStoreSession) -> None:
    tasks: list[NewTask] = [_task("a" if index % 2 == 0 else "b") for index in range(6)]
    for task in tasks:
        await session.insert(task)
    (position,) = await session.query(_EVERYTHING, after=None, limit=1)

    rest = await _listed(
        session,
        TaskFilter(names={"b"}),
        after=TaskKeyset(created=position.created, identifier=position.identifier),
    )

    assert rest == [task.identifier for task in tasks if task.name == "b"]


@mark.asyncio
async def test_query_filters_by_creation_time(session: TaskStoreSession, clock: Clock) -> None:
    clock.wall_frozen = True
    created: datetime = _WALL_START
    task: NewTask = _task()
    await session.insert(task)
    one: timedelta = timedelta(microseconds=1)

    def matching(created_range: TaskTimeFrom | TaskTimeBefore | TaskTimeBetween) -> TaskFilter:
        return TaskFilter(created=created_range)

    assert await _listed(session, matching(TaskTimeFrom(start=created))) == [task.identifier]
    assert await _listed(session, matching(TaskTimeFrom(start=created + one))) == []
    assert await _listed(session, matching(TaskTimeBefore(end=created + one))) == [task.identifier]
    assert await _listed(session, matching(TaskTimeBefore(end=created))) == []
    assert await _listed(session, matching(TaskTimeBetween(start=created, end=created + one))) == [
        task.identifier
    ]
    assert await _listed(session, matching(TaskTimeBetween(start=created - one, end=created))) == []
    # compared as instants, not as wall clock text
    assert await _listed(
        session,
        matching(TaskTimeFrom(start=created.astimezone(timezone(timedelta(hours=5))))),
    ) == [task.identifier]


@mark.asyncio
async def test_query_filters_by_finish_time(session: TaskStoreSession, clock: Clock) -> None:
    runner: UUID = uuid4()
    clock.wall_frozen = True
    early: NewTask = await _running(session, runner)
    await session.record(runner, early.identifier, 1, AttemptCompleted(result="null"))
    clock.wall = _WALL_START + timedelta(hours=1)
    late: NewTask = await _running(session, runner)
    await session.record(runner, late.identifier, 1, AttemptCompleted(result="null"))
    unfinished: NewTask = _task()
    await session.insert(unfinished)

    def finished_in(finished: TaskTimeFrom | TaskTimeBefore) -> TaskFilter:
        return TaskFilter(status=TasksFinished(outcomes={"completed"}, finished=finished))

    boundary: datetime = _WALL_START + timedelta(hours=1)
    assert await _listed(session, finished_in(TaskTimeFrom(start=boundary))) == [late.identifier]
    assert await _listed(session, finished_in(TaskTimeBefore(end=boundary))) == [early.identifier]


@mark.asyncio
async def test_query_limit_bounds_the_page(session: TaskStoreSession) -> None:
    for _ in range(4):
        await session.insert(_task())

    assert len(await _listed(session, limit=1)) == 1
    assert len(await _listed(session, limit=3)) == 3
    assert len(await _listed(session, limit=1000)) == 4


@mark.asyncio
async def test_task_created_between_wall_readings_is_listed() -> None:
    async with InMemoryTaskStore().open() as session:
        before: datetime = datetime.now(UTC)
        task: NewTask = _task()
        await session.insert(task)
        after: datetime = datetime.now(UTC)

        listed: list[UUID] = await _listed(
            session,
            TaskFilter(
                created=TaskTimeBetween(start=before, end=after + timedelta(microseconds=1))
            ),
        )
        (summary,) = await session.query(_EVERYTHING, after=None, limit=1)

        assert listed == [task.identifier]
        assert summary.created.tzinfo is not None
        assert summary.created.utcoffset() == timedelta(0)


# --- retention (FR-32, FR-96) --------------------------------------------------------------


@mark.asyncio
async def test_finished_task_is_dropped_after_retention(clock: Clock) -> None:
    async with InMemoryTaskStore(retention=10.0).open() as retaining:
        runner: UUID = uuid4()
        task: NewTask = await _running(retaining, runner)
        await retaining.record(runner, task.identifier, 1, AttemptCompleted(result="null"))

        assert task.identifier in await retaining.read({task.identifier})
        clock.advance(9.9)
        assert task.identifier in await retaining.read({task.identifier})
        assert await _listed(retaining) == [task.identifier]
        clock.advance(0.1)

        assert await retaining.read({task.identifier}) == {}
        assert await _listed(retaining) == []
        assert await retaining.cancel(task.identifier) is None


@mark.asyncio
async def test_retention_counts_from_finish_not_from_creation(clock: Clock) -> None:
    async with InMemoryTaskStore(retention=10.0).open() as session:
        runner: UUID = uuid4()
        task: NewTask = await _running(session, runner)
        clock.advance(100.0)
        await session.record(runner, task.identifier, 1, AttemptCompleted(result="null"))
        clock.advance(5.0)

        assert task.identifier in await session.read({task.identifier})


@mark.asyncio
async def test_unfinished_tasks_are_never_dropped(clock: Clock) -> None:
    async with InMemoryTaskStore(retention=1.0).open() as session:
        running: NewTask = await _running(session, uuid4())
        queued: NewTask = _task()
        await session.insert(queued)
        clock.advance(100.0)

        assert set(await session.read({queued.identifier, running.identifier})) == {
            queued.identifier,
            running.identifier,
        }


@mark.asyncio
async def test_retention_is_measured_on_monotonic_clock(clock: Clock) -> None:
    async with InMemoryTaskStore(retention=10.0).open() as session:
        runner: UUID = uuid4()
        task: NewTask = await _running(session, runner)
        await session.record(runner, task.identifier, 1, AttemptCompleted(result="null"))
        clock.wall -= timedelta(hours=1)  # the wall clock moves back

        assert task.identifier in await session.read({task.identifier})
        clock.advance(10.0)
        assert await session.read({task.identifier}) == {}


@mark.asyncio
async def test_task_finished_by_cancel_is_dropped_after_retention(clock: Clock) -> None:
    async with InMemoryTaskStore(retention=1.0).open() as session:
        task: NewTask = _task()
        await session.insert(task)
        await session.cancel(task.identifier)
        clock.advance(1.0)

        assert await session.read({task.identifier}) == {}


@mark.asyncio
async def test_retention_with_real_clock() -> None:
    async with InMemoryTaskStore(retention=0.05).open() as session:
        runner: UUID = uuid4()
        task: NewTask = await _running(session, runner)
        await session.record(runner, task.identifier, 1, AttemptCompleted(result="null"))

        assert task.identifier in await session.read({task.identifier})
        await asyncio.sleep(0.2)
        assert await session.read({task.identifier}) == {}


# --- change counter (FR-78, D-103) -----------------------------------------------------------


@mark.asyncio
async def test_changed_returns_counter_at_once_when_it_differs(
    session: TaskStoreSession,
) -> None:
    assert isinstance(session.coordination, LocalCoordination)
    changed = session.coordination.changed

    first: int = await asyncio.wait_for(changed(-1), 1.0)
    await session.insert(_task())
    second: int = await asyncio.wait_for(changed(first), 1.0)

    assert second == first + 1
    # a change landing between two waits is not missed
    await session.insert(_task())
    assert await asyncio.wait_for(changed(first), 1.0) == second + 1


@mark.asyncio
async def test_changed_waits_until_next_change(session: TaskStoreSession) -> None:
    assert isinstance(session.coordination, LocalCoordination)
    changed = session.coordination.changed
    seen: int = await changed(-1)
    waiting = asyncio.create_task(changed(seen))

    await asyncio.sleep(0.02)
    assert not waiting.done()
    await session.insert(_task())

    assert await asyncio.wait_for(waiting, 1.0) == seen + 1


@mark.parametrize(
    "operation",
    [
        "insert",
        "record",
        "record_without_lease",
        "release",
        "release_without_lease",
        "cancel_running",
        "cancel_unknown",
        "cancel_finished",
    ],
)
@mark.asyncio
async def test_changed_counts_every_operation_once(
    session: TaskStoreSession,
    operation: str,
) -> None:
    assert isinstance(session.coordination, LocalCoordination)
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    finished: NewTask = await _running(session, runner)
    await session.record(runner, finished.identifier, 1, AttemptCompleted(result="null"))
    seen: int = await session.coordination.changed(-1)

    match operation:
        case "insert":
            await session.insert(_task())

        case "record":
            await session.record(runner, task.identifier, 1, AttemptCompleted(result="null"))

        case "record_without_lease":
            assert await session.record(uuid4(), task.identifier, 1, AttemptCancelled()) is None

        case "release":
            await session.release(runner, task.identifier, 1)

        case "release_without_lease":
            assert await session.release(uuid4(), task.identifier, 1) is None

        case "cancel_running":
            await session.cancel(task.identifier)

        case "cancel_unknown":
            assert await session.cancel(uuid4()) is None

        case _:
            assert await session.cancel(finished.identifier) == "finished"

    assert await session.coordination.changed(seen) == seen + 1


@mark.asyncio
async def test_fail_unsupported_counts_only_when_it_finished_a_task(
    session: TaskStoreSession,
) -> None:
    assert isinstance(session.coordination, LocalCoordination)
    changed = session.coordination.changed
    seen: int = await changed(-1)

    assert await session.fail_unsupported({"r"}) == {}
    waiting = asyncio.create_task(changed(seen))
    await asyncio.sleep(0.02)
    assert not waiting.done()

    await session.insert(_task("x"))
    assert await asyncio.wait_for(waiting, 1.0) == seen + 1
    await session.fail_unsupported({"r"})

    assert await asyncio.wait_for(changed(seen + 1), 1.0) == seen + 2


@mark.asyncio
async def test_changed_returns_when_due_moment_of_retry_passes(
    session: TaskStoreSession,
) -> None:
    assert isinstance(session.coordination, LocalCoordination)
    changed = session.coordination.changed
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    await session.record(runner, task.identifier, 1, AttemptRetried(failure=_FAILURE, delay=0.2))
    seen: int = await changed(-1)
    loop = asyncio.get_running_loop()

    started: float = loop.time()
    returned: int = await asyncio.wait_for(changed(seen), 2.0)
    elapsed: float = loop.time() - started

    assert returned == seen + 1
    assert 0.15 <= elapsed < 1.5
    assert [claim.attempt for claim in await session.claim(runner, {"r"}, limit=None)] == [2]


@mark.asyncio
async def test_cancelled_wait_leaves_store_usable(session: TaskStoreSession) -> None:
    assert isinstance(session.coordination, LocalCoordination)
    changed = session.coordination.changed
    seen: int = await changed(-1)
    waiting = asyncio.create_task(changed(seen))
    await asyncio.sleep(0.01)

    waiting.cancel()
    with raises(asyncio.CancelledError):
        await waiting

    await session.insert(_task())
    assert await asyncio.wait_for(changed(seen), 1.0) == seen + 1


# --- sharing (FR-46, FR-79 store side) -------------------------------------------------------


@mark.asyncio
async def test_sessions_of_one_store_share_tasks_as_one_queue() -> None:
    store = InMemoryTaskStore()
    async with store.open() as first, store.open() as second:
        task: NewTask = _task()
        await first.insert(task)

        claimed = await second.claim(uuid4(), {"r"}, limit=None)

        assert [claim.task for claim in claimed] == [task]
        assert await first.claim(uuid4(), {"r"}, limit=None) == []
        assert (await first.read({task.identifier}))[task.identifier].status == StoredRunning(
            cancel_requested=False
        )


@mark.asyncio
async def test_sessions_of_one_store_share_changes() -> None:
    store = InMemoryTaskStore()
    async with store.open() as first, store.open() as second:
        assert isinstance(first.coordination, LocalCoordination)
        assert isinstance(second.coordination, LocalCoordination)
        seen: int = await first.coordination.changed(-1)
        waiting = asyncio.create_task(first.coordination.changed(seen))
        await asyncio.sleep(0.01)

        await second.insert(_task())

        assert await asyncio.wait_for(waiting, 1.0) == seen + 1


@mark.asyncio
async def test_release_cancels_also_on_shared_store() -> None:
    store = InMemoryTaskStore()
    async with store.open() as first, store.open() as second:
        runner: UUID = uuid4()
        running: NewTask = await _running(first, runner)
        queued: NewTask = _task()
        await first.insert(queued)

        assert await first.release(runner, running.identifier, 1) == StoredFinished(
            outcome=StoredCancelled()
        )

        assert (await second.read({running.identifier}))[running.identifier].status == (
            StoredFinished(outcome=StoredCancelled())
        )
        # the queued task stays for the other runtimes
        assert [claim.task for claim in await second.claim(uuid4(), {"r"}, limit=None)] == [queued]


@mark.asyncio
async def test_tasks_stay_after_session_closes() -> None:
    store = InMemoryTaskStore()
    task: NewTask = _task()
    async with store.open() as first:
        await first.insert(task)

    async with store.open() as second:
        assert task.identifier in await second.read({task.identifier})


# --- event loop (FR-34 store side) -----------------------------------------------------------


def _run_on_other_loop(coroutine: Coroutine[Any, Any, object]) -> Coroutine[Any, Any, object]:
    return asyncio.to_thread(asyncio.run, coroutine)


@mark.asyncio
async def test_open_on_another_event_loop_raises() -> None:
    store = InMemoryTaskStore()
    async with store.open():
        pass

    async def open_store() -> None:
        async with store.open():
            pass

    with raises(RuntimeError, match="event loop"):
        await _run_on_other_loop(open_store())


@mark.asyncio
async def test_operations_on_another_event_loop_raise(session: TaskStoreSession) -> None:
    assert isinstance(session.coordination, LocalCoordination)
    task: NewTask = _task()
    await session.insert(task)
    runner: UUID = uuid4()
    changed = session.coordination.changed

    calls = (
        lambda: session.insert(_task()),
        lambda: session.claim(runner, {"r"}, limit=None),
        lambda: session.renew(runner, {task.identifier}),
        lambda: session.record(runner, task.identifier, 1, AttemptCancelled()),
        lambda: session.release(runner, task.identifier, 1),
        lambda: session.cancel(task.identifier),
        lambda: session.read({task.identifier}),
        lambda: session.fail_unsupported(set()),
        lambda: session.query(_EVERYTHING, after=None, limit=1),
        lambda: changed(-1),
    )
    for call in calls:

        async def run(call: Any = call) -> None:
            await call()

        with raises(RuntimeError, match="event loop"):
            await _run_on_other_loop(run())

    # nothing was changed by the refused calls
    assert (await _read(session, task)).status == StoredQueued()
    assert len(await _listed(session)) == 1


# --- duplicate identifiers, limits, order, traversal, scale ------------------------------------


@mark.parametrize("state", ["queued", "running", "finished"])
@mark.asyncio
async def test_insert_of_held_identifier_raises_and_changes_nothing(
    session: TaskStoreSession,
    state: str,
) -> None:
    assert isinstance(session.coordination, LocalCoordination)
    runner: UUID = uuid4()
    task: NewTask = _task()
    await session.insert(task)
    if state != "queued":
        await session.claim(runner, {"r"}, limit=None)

    if state == "finished":
        await session.record(runner, task.identifier, 1, AttemptCompleted(result="null"))

    before: StoredTask = await _read(session, task)
    listed_before = await session.query(_EVERYTHING, after=None, limit=10)
    seen: int = await session.coordination.changed(-1)

    with raises(ValueError, match=str(task.identifier)):
        await session.insert(_task(identifier=task.identifier))

    assert await _read(session, task) == before
    assert await session.query(_EVERYTHING, after=None, limit=10) == listed_before
    with raises(TimeoutError):
        await asyncio.wait_for(session.coordination.changed(seen), 0.05)


@mark.asyncio
async def test_insert_of_dropped_identifier_is_accepted(clock: Clock) -> None:
    async with InMemoryTaskStore(retention=1.0).open() as session:
        task: NewTask = _task()
        await session.insert(task)
        await session.cancel(task.identifier)
        clock.advance(1.0)

        await session.insert(task)

        assert (await _read(session, task)).status == StoredQueued()


@mark.asyncio
async def test_negative_limits_raise(session: TaskStoreSession) -> None:
    await session.insert(_task())

    with raises(ValueError, match="limit"):
        await session.claim(uuid4(), {"r"}, limit=-1)

    with raises(ValueError, match="limit"):
        await session.query(_EVERYTHING, after=None, limit=-1)

    assert await session.query(_EVERYTHING, after=None, limit=0) == []
    assert await session.claim(uuid4(), {"r"}, limit=0) == []


@mark.asyncio
async def test_equal_creation_times_follow_identifier_byte_order(
    session: TaskStoreSession,
    clock: Clock,
) -> None:
    clock.wall_frozen = True
    identifiers: list[UUID] = [
        UUID(int=(1 << 127) | 1),
        UUID(int=2),
        UUID(int=1 << 127),
        UUID(int=(1 << 64) | 5),
        UUID(int=1 << 64),
    ]
    for identifier in identifiers:
        await session.insert(_task(identifier=identifier))

    listed: list[UUID] = await _listed(session)

    assert listed == sorted(identifiers, key=lambda identifier: identifier.bytes)
    assert listed == sorted(identifiers)


@mark.asyncio
async def test_traversal_returns_each_task_present_throughout_once(clock: Clock) -> None:
    async with InMemoryTaskStore(retention=5.0).open() as session:
        runner: UUID = uuid4()
        stable: list[NewTask] = [_task() for _ in range(50)]
        for task in stable:
            await session.insert(task)

        returned: list[UUID] = []
        keyset: TaskKeyset | None = None
        pages: int = 0
        while True:
            page = await session.query(_EVERYTHING, after=keyset, limit=7)
            returned.extend(summary.identifier for summary in page)
            if len(page) < 7:
                break

            keyset = TaskKeyset(created=page[-1].created, identifier=page[-1].identifier)
            pages += 1
            # between pages: insert new tasks, finish others, drop finished ones
            transient: NewTask = _task("t")
            await session.insert(transient)
            await session.claim(runner, {"t"}, limit=None)
            await session.record(runner, transient.identifier, 1, AttemptCompleted(result="null"))
            await session.cancel(stable[pages].identifier)  # finishes a listed task
            clock.advance(3.0)  # retention drops the finished transients of earlier rounds

        assert len(returned) == len(set(returned))
        # the cancelled tasks are dropped only after 5 s from their finish, and the
        # listing still holds them when the traversal passes
        assert {task.identifier for task in stable} <= set(returned)


@mark.asyncio
async def test_traversal_survives_deleted_position(clock: Clock) -> None:
    async with InMemoryTaskStore(retention=1.0).open() as session:
        tasks: list[NewTask] = [_task() for _ in range(5)]
        for task in tasks:
            await session.insert(task)
        first = await session.query(_EVERYTHING, after=None, limit=2)
        await session.cancel(first[-1].identifier)
        clock.advance(1.0)  # the task at the position of the token is dropped

        rest = await session.query(
            _EVERYTHING,
            after=TaskKeyset(created=first[-1].created, identifier=first[-1].identifier),
            limit=10,
        )

        assert [summary.identifier for summary in rest] == [task.identifier for task in tasks[2:]]


@mark.asyncio
async def test_many_retained_finished_tasks_keep_operations_cheap() -> None:
    count: int = 100_000
    async with InMemoryTaskStore().open() as session:
        assert isinstance(session.coordination, LocalCoordination)
        runner: UUID = uuid4()
        for _ in range(count):
            task: NewTask = _task()
            await session.insert(task)
            await session.cancel(task.identifier)

        loop = asyncio.get_running_loop()
        started: float = loop.time()
        operations: int = 200
        seen: int = await session.coordination.changed(-1)
        for _ in range(operations):
            task = _task()
            await session.insert(task)
            seen = await session.coordination.changed(seen)
            assert len(await session.claim(runner, {"r"}, limit=1)) == 1
            await session.record(runner, task.identifier, 1, AttemptCompleted(result="null"))
        elapsed: float = loop.time() - started

        assert len(await session.query(_EVERYTHING, after=None, limit=10)) == 10
        # a scan of the retained tasks per operation would take 200 * 4 scans of 100k
        assert elapsed < 2.0, f"{operations} insert/changed/claim/record rounds took {elapsed}s"


@mark.asyncio
async def test_record_and_release_of_another_attempt_change_nothing(
    session: TaskStoreSession,
) -> None:
    # D1: fenced by the attempt number
    runner: UUID = uuid4()
    task: NewTask = await _running(session, runner)
    await session.record(runner, task.identifier, 1, AttemptRetried(failure=_FAILURE, delay=0.0))
    await session.claim(runner, {"r"}, limit=None)  # attempt 2, the same runner

    assert await session.record(runner, task.identifier, 1, AttemptCompleted(result="null")) is None
    assert await session.release(runner, task.identifier, 1) is None
    assert await session.record(runner, task.identifier, 3, AttemptCompleted(result="null")) is None
    stored: StoredTask = await _read(session, task)
    assert stored.status == StoredRunning(cancel_requested=False)
    assert len(stored.attempts) == 1
    completed = AttemptCompleted(result="null")
    assert await session.record(runner, task.identifier, 2, completed) is not None


@mark.asyncio
async def test_claim_bumps_the_change_counter_when_it_claimed(session: TaskStoreSession) -> None:
    # D3
    coordination = session.coordination
    assert isinstance(coordination, LocalCoordination)
    await session.insert(_task())
    seen: int = await coordination.changed(-1)
    assert await session.claim(uuid4(), {"r"}, limit=None) != []
    assert await coordination.changed(seen) == seen + 1
    seen += 1
    assert await session.claim(uuid4(), {"r"}, limit=None) == []
    assert await coordination.changed(seen - 1) == seen  # nothing was bumped


@mark.parametrize("time_limit", [0.0, -1.0, float("nan"), float("inf")])
def test_new_task_rejects_a_bad_time_limit(time_limit: float) -> None:
    # D4
    with raises(ValueError, match="time_limit"):
        NewTask(
            identifier=uuid4(),
            name="r",
            argument="{}",
            retry=TaskRetryPolicy(),
            time_limit=time_limit,
            origin=None,
        )

    valid = _task()
    with raises(ValueError, match="time_limit"):
        valid.updating(time_limit=time_limit)
