from asyncio import AbstractEventLoop, Event, get_running_loop, timeout
from bisect import bisect_left, bisect_right, insort
from collections import deque
from collections.abc import AsyncGenerator, Mapping, Sequence, Set
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from datetime import UTC, datetime
from heapq import heappop, heappush
from math import isfinite
from time import monotonic
from typing import final
from uuid import UUID

from haiway.context.managed.listing import (
    TaskFilter,
    TaskKeyset,
    TasksFinished,
    TaskStatusFilter,
    TaskSummary,
    TaskSummaryFinished,
    TaskSummaryUnfinished,
    TasksUnfinished,
    TaskTimeBefore,
    TaskTimeBetween,
    TaskTimeFrom,
    TaskTimeRange,
)
from haiway.context.managed.store import LocalCoordination, TaskStoreSession
from haiway.context.managed.stored import (
    AttemptCancelled,
    AttemptCompleted,
    AttemptEnd,
    AttemptFailed,
    AttemptRetried,
    ClaimedAttempt,
    ClaimedTask,
    LeaseRenewal,
    LeaseState,
    NewTask,
    StoredAttempt,
    StoredCancelled,
    StoredCompleted,
    StoredFailed,
    StoredFinished,
    StoredQueued,
    StoredRetryPending,
    StoredRunning,
    StoredStatus,
    StoredTask,
)
from haiway.context.managed.types import TaskCancellation, TaskUnsupported

__all__ = ("InMemoryTaskStore",)


def _wall_now() -> datetime:
    return datetime.now(UTC)


# an item of the due heaps: due moment, order of pushing, identifier
type _Due = tuple[float, int, UUID]


@final
class _Waiting:
    # queued or retry pending, due on the monotonic clock
    __slots__ = (
        "due",
        "order",
        "status",
    )

    def __init__(
        self,
        *,
        status: StoredQueued | StoredRetryPending,
        due: float,
        order: int,
    ) -> None:
        self.status: StoredQueued | StoredRetryPending = status
        self.due: float = due
        self.order: int = order


@final
class _Running:
    __slots__ = (
        "cancel_requested",
        "owner",
    )

    def __init__(
        self,
        *,
        owner: UUID,
        cancel_requested: bool,
    ) -> None:
        self.owner: UUID = owner
        self.cancel_requested: bool = cancel_requested


@final
class _Finished:
    __slots__ = (
        "monotonic",
        "outcome",
        "wall",
    )

    def __init__(
        self,
        *,
        outcome: StoredCompleted | StoredFailed | StoredCancelled,
        wall: datetime,
        monotonic: float,
    ) -> None:
        self.outcome: StoredCompleted | StoredFailed | StoredCancelled = outcome
        self.wall: datetime = wall
        self.monotonic: float = monotonic


type _State = _Waiting | _Running | _Finished


@final
class _Entry:
    __slots__ = (
        "attempts",
        "created",
        "state",
        "task",
    )

    def __init__(
        self,
        *,
        task: NewTask,
        created: datetime,
        state: _State,
    ) -> None:
        self.task: NewTask = task
        self.created: datetime = created
        self.state: _State = state
        self.attempts: tuple[StoredAttempt, ...] = ()

    @property
    def status(self) -> StoredStatus:
        match self.state:
            case _Waiting(status=status):
                return status

            case _Running(cancel_requested=cancel_requested):
                return StoredRunning(cancel_requested=cancel_requested)

            case _Finished(outcome=outcome):
                return StoredFinished(outcome=outcome)


def _in_range(
    time: datetime,
    bounds: TaskTimeRange,
) -> bool:
    match bounds:
        case TaskTimeFrom(start=start):
            return start <= time

        case TaskTimeBefore(end=end):
            return time < end

        case TaskTimeBetween(start=start, end=end):
            return start <= time < end


def _status_matches(
    entry: _Entry,
    matching: TaskStatusFilter,
) -> bool:
    match matching:
        case TasksUnfinished(statuses=statuses):
            match entry.state:
                case _Waiting(status=status):
                    return status.status in statuses

                case _Running():
                    return "running" in statuses

                case _Finished():
                    return False

        case TasksFinished(outcomes=outcomes, finished=finished):
            match entry.state:
                case _Finished(outcome=outcome, wall=wall):
                    if outcome.outcome not in outcomes:
                        return False

                    return finished is None or _in_range(wall, finished)

                case _Waiting() | _Running():
                    return False


def _matches(
    entry: _Entry,
    matching: TaskFilter,
) -> bool:
    if matching.names is not None and entry.task.name not in matching.names:
        return False

    if matching.status is not None and not _status_matches(entry, matching.status):
        return False

    return matching.created is None or _in_range(entry.created, matching.created)


def _summary(entry: _Entry) -> TaskSummary:
    status: TaskSummaryUnfinished | TaskSummaryFinished
    match entry.state:
        case _Waiting(status=waiting):
            status = TaskSummaryUnfinished(status=waiting.status)

        case _Running():
            status = TaskSummaryUnfinished(status="running")

        case _Finished(outcome=outcome, wall=wall):
            status = TaskSummaryFinished(
                outcome=outcome.outcome,
                finished=wall,
            )

    return TaskSummary(
        identifier=entry.task.identifier,
        name=entry.task.name,
        status=status,
        attempts=len(entry.attempts),
        created=entry.created,
    )


@final
class InMemoryTaskStore:
    """
    Task store keeping tasks in process memory, the default store of a runtime.

    Every runtime which opens the same store object shares its tasks, as one work queue.
    The store binds to the event loop of its first opening for good: every operation,
    and a later opening, on another loop raises ``RuntimeError``, so a store object cannot
    be reused across separate ``asyncio.run`` calls. Leases never expire: its coordination
    is ``LocalCoordination``, which wakes runtimes on every change. Releasing a task
    finishes it with ``StoredCancelled``, also when the store is shared. Tasks and their
    state are lost with the process.

    Due moments, leases and retention are measured on ``time.monotonic()``, the creation
    and finish times listed for tasks on the wall clock in UTC.

    Finished tasks are dropped ``retention`` seconds after they finished, no other bound
    is applied: memory grows with the number of tasks finished within one retention period.

    Parameters
    ----------
    retention : float, default=3600.0
        Seconds a finished task is kept, finite and at least 0.

    Raises
    ------
    ValueError
        When ``retention`` is not finite or is below 0. From ``insert`` when the store
        holds a task of the identifier, and from ``claim`` and ``query`` for a negative
        limit.
    RuntimeError
        From every operation called on an event loop other than the one the store was
        first opened on.
    """

    __slots__ = (
        "_counter",
        "_event",
        "_expiry",
        "_listing",
        "_loop",
        "_order",
        "_retention",
        "_tasks",
        "_unannounced",
        "_waiting",
    )

    def __init__(
        self,
        *,
        retention: float = 3600.0,
    ) -> None:
        if not isfinite(retention) or retention < 0:
            raise ValueError(
                f"InMemoryTaskStore retention has to be finite and at least 0: {retention}"
            )

        self._retention: float = retention
        self._tasks: dict[UUID, _Entry] = {}
        # keys of all tasks in the listing order, bisected by queries
        self._listing: list[tuple[datetime, UUID]] = []
        # waiting tasks by name, heaps by due moment with lazy deletion
        self._waiting: dict[str, list[_Due]] = {}
        # waiting tasks whose due moment is yet to pass, heap with lazy deletion
        self._unannounced: list[_Due] = []
        # finished tasks in the order of their finish, which is the order of their expiry
        self._expiry: deque[tuple[float, UUID]] = deque()
        self._order: int = 0
        self._counter: int = 0
        self._event: Event = Event()
        self._loop: AbstractEventLoop | None = None

    def open(self) -> AbstractAsyncContextManager[TaskStoreSession]:
        """
        Open the store for a runtime. Any number of runtimes can open one store object.

        Returns
        -------
        AbstractAsyncContextManager[TaskStoreSession]
            Yields the session of the runtime.

        Raises
        ------
        RuntimeError
            On entry, when called on another event loop than the one the store was first
            opened on.
        """
        return self._session()

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[TaskStoreSession]:
        if self._loop is None:
            self._loop = get_running_loop()

        else:
            self._require_loop()

        yield TaskStoreSession(
            insert=self._insert,
            claim=self._claim,
            renew=self._renew,
            record=self._record,
            release=self._release,
            cancel=self._cancel,
            read=self._read,
            fail_unsupported=self._fail_unsupported,
            query=self._query,
            coordination=LocalCoordination(self._changed),
        )

    def _require_loop(self) -> None:
        if get_running_loop() is not self._loop:
            raise RuntimeError("InMemoryTaskStore is used on another event loop than it was opened")

    def _bump(self) -> None:
        self._counter += 1
        # wake every waiter of the previous counter
        self._event.set()
        self._event = Event()

    def _is_live(
        self,
        item: _Due,
    ) -> bool:
        entry: _Entry | None = self._tasks.get(item[2])
        if entry is None:
            return False

        match entry.state:
            case _Waiting(order=order):
                return order == item[1]

            case _Running() | _Finished():
                return False

    def _top(
        self,
        heap: list[_Due],
    ) -> _Due | None:
        # drops the stale items from the top, those of tasks which left the waiting state
        while heap and not self._is_live(heap[0]):
            heappop(heap)

        return heap[0] if heap else None

    def _enqueue(
        self,
        task: NewTask,
        status: StoredQueued | StoredRetryPending,
        *,
        due: float,
        now: float,
    ) -> _Waiting:
        self._order += 1
        item: _Due = (due, self._order, task.identifier)
        heappush(self._waiting.setdefault(task.name, []), item)
        if due > now:
            heappush(self._unannounced, item)

        return _Waiting(
            status=status,
            due=due,
            order=self._order,
        )

    def _finish(
        self,
        entry: _Entry,
        *,
        outcome: StoredCompleted | StoredFailed | StoredCancelled,
        now: float,
    ) -> StoredStatus:
        entry.state = _Finished(
            outcome=outcome,
            wall=_wall_now(),
            monotonic=now,
        )
        self._expiry.append((now, entry.task.identifier))
        return StoredFinished(outcome=outcome)

    def _refresh(self) -> float:
        # drops what retention expired and counts the due moments which passed, which
        # costs what expired or became due since the previous call
        now: float = monotonic()
        while self._expiry and now - self._expiry[0][0] >= self._retention:
            _, identifier = self._expiry.popleft()
            entry: _Entry = self._tasks.pop(identifier)
            del self._listing[bisect_left(self._listing, (entry.created, identifier))]

        while (top := self._top(self._unannounced)) is not None and top[0] <= now:
            heappop(self._unannounced)
            self._bump()

        return now

    def _next_due(
        self,
        now: float,
    ) -> float | None:
        top: _Due | None = self._top(self._unannounced)
        if top is None:
            return None

        return max(top[0] - now, 0.0)

    async def _changed(
        self,
        seen: int,
        /,
    ) -> int:
        self._require_loop()
        while True:
            now: float = self._refresh()
            if self._counter != seen:
                return self._counter

            event: Event = self._event
            try:
                async with timeout(self._next_due(now)):
                    await event.wait()

            except TimeoutError:
                continue  # a due moment is due, counted by the next refresh

    async def _insert(
        self,
        task: NewTask,
        /,
    ) -> None:
        self._require_loop()
        now: float = self._refresh()
        if task.identifier in self._tasks:
            raise ValueError(f"InMemoryTaskStore already holds the task {task.identifier}")

        entry = _Entry(
            task=task,
            created=_wall_now(),
            # due now: the insert is the change which announces it
            state=self._enqueue(task, StoredQueued(), due=now, now=now),
        )
        self._tasks[task.identifier] = entry
        insort(self._listing, (entry.created, task.identifier))
        self._bump()

    async def _claim(
        self,
        runner: UUID,
        names: Set[str],
        *,
        limit: int | None,
    ) -> Sequence[ClaimedTask]:
        self._require_loop()
        if limit is not None and limit < 0:
            raise ValueError(f"InMemoryTaskStore claim limit cannot be negative: {limit}")

        now: float = self._refresh()
        claimed: list[ClaimedTask] = []
        while limit is None or len(claimed) < limit:
            best: _Due | None = None
            best_name: str = ""
            for name in names:
                heap: list[_Due] | None = self._waiting.get(name)
                if heap is None:
                    continue

                top: _Due | None = self._top(heap)
                if top is None:
                    del self._waiting[name]

                elif top[0] <= now and (best is None or top < best):
                    best = top
                    best_name = name

            if best is None:
                break

            heappop(self._waiting[best_name])
            entry: _Entry = self._tasks[best[2]]
            entry.state = _Running(owner=runner, cancel_requested=False)
            claimed.append(
                ClaimedAttempt(
                    task=entry.task,
                    attempt=len(entry.attempts) + 1,
                )
            )

        if claimed:
            self._bump()

        return claimed

    async def _renew(
        self,
        runner: UUID,
        identifiers: Set[UUID],
    ) -> LeaseRenewal:
        self._require_loop()
        self._refresh()
        leases: dict[UUID, LeaseState] = {}
        for identifier in identifiers:
            entry: _Entry | None = self._tasks.get(identifier)
            if entry is None:
                continue

            match entry.state:
                case _Running(owner=owner, cancel_requested=cancel_requested) if owner == runner:
                    leases[identifier] = "cancel_requested" if cancel_requested else "held"

                case _Running() | _Waiting() | _Finished():
                    pass

        return LeaseRenewal(leases=leases)

    async def _record(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,
        end: AttemptEnd,
        /,
    ) -> StoredStatus | None:
        self._require_loop()
        now: float = self._refresh()
        entry: _Entry | None = self._tasks.get(identifier)
        state: _State | None = None if entry is None else entry.state
        if (
            entry is None
            or not isinstance(state, _Running)
            or state.owner != runner
            or len(entry.attempts) + 1 != attempt
        ):
            self._bump()
            return None

        status: StoredStatus
        if state.cancel_requested:
            entry.attempts = (*entry.attempts, StoredAttempt(result="cancelled"))
            status = self._finish(entry, outcome=StoredCancelled(), now=now)

        else:
            match end:
                case AttemptCompleted(result=result):
                    entry.attempts = (*entry.attempts, StoredAttempt(result="completed"))
                    status = self._finish(
                        entry,
                        outcome=StoredCompleted(result=result),
                        now=now,
                    )

                case AttemptRetried(failure=failure, delay=delay):
                    entry.attempts = (*entry.attempts, StoredAttempt(result=failure))
                    status = StoredRetryPending()
                    entry.state = self._enqueue(entry.task, status, due=now + delay, now=now)

                case AttemptFailed(failure=failure):
                    entry.attempts = (*entry.attempts, StoredAttempt(result=failure))
                    status = self._finish(entry, outcome=StoredFailed(), now=now)

                case AttemptCancelled():
                    entry.attempts = (*entry.attempts, StoredAttempt(result="cancelled"))
                    status = self._finish(entry, outcome=StoredCancelled(), now=now)

        self._bump()
        return status

    async def _release(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,
        /,
    ) -> StoredStatus | None:
        self._require_loop()
        now: float = self._refresh()
        entry: _Entry | None = self._tasks.get(identifier)
        state: _State | None = None if entry is None else entry.state
        if (
            entry is None
            or not isinstance(state, _Running)
            or state.owner != runner
            or len(entry.attempts) + 1 != attempt
        ):
            self._bump()
            return None

        # with local coordination nothing else can resume the task: a release
        # finishes it as cancelled, whether or not a cancel was requested
        status: StoredStatus = self._finish(entry, outcome=StoredCancelled(), now=now)
        self._bump()
        return status

    async def _cancel(
        self,
        identifier: UUID,
        /,
    ) -> TaskCancellation | None:
        self._require_loop()
        now: float = self._refresh()
        entry: _Entry | None = self._tasks.get(identifier)
        result: TaskCancellation | None
        if entry is None:
            result = None

        else:
            match entry.state:
                case _Waiting():
                    self._finish(entry, outcome=StoredCancelled(), now=now)
                    result = "cancelled"

                case _Running() as running:
                    running.cancel_requested = True
                    result = "pending"

                case _Finished():
                    result = "finished"

        self._bump()
        return result

    async def _fail_unsupported(
        self,
        names: Set[str],
        /,
    ) -> Mapping[UUID, str]:
        self._require_loop()
        now: float = self._refresh()
        failed: dict[UUID, str] = {}
        for name in tuple(self._waiting):
            if name in names:
                continue

            heap: list[_Due] = self._waiting[name]
            while (top := self._top(heap)) is not None and top[0] <= now:
                heappop(heap)
                entry: _Entry = self._tasks[top[2]]
                entry.attempts = (*entry.attempts, StoredAttempt(result=TaskUnsupported()))
                self._finish(entry, outcome=StoredFailed(), now=now)
                failed[top[2]] = name

            if not heap:
                del self._waiting[name]

        if failed:
            self._bump()

        return failed

    async def _query(
        self,
        matching: TaskFilter,
        /,
        *,
        after: TaskKeyset | None,
        limit: int,
    ) -> Sequence[TaskSummary]:
        self._require_loop()
        if limit < 0:
            raise ValueError(f"InMemoryTaskStore query limit cannot be negative: {limit}")

        self._refresh()
        position: int = (
            0 if after is None else bisect_right(self._listing, (after.created, after.identifier))
        )
        listed: list[TaskSummary] = []
        for _, identifier in self._listing[position:]:
            if len(listed) >= limit:
                break

            entry: _Entry = self._tasks[identifier]
            if _matches(entry, matching):
                listed.append(_summary(entry))

        return listed

    async def _read(
        self,
        identifiers: Set[UUID],
        /,
    ) -> Mapping[UUID, StoredTask]:
        self._require_loop()
        self._refresh()
        return {
            identifier: StoredTask(
                task=entry.task,
                status=entry.status,
                attempts=entry.attempts,
            )
            for identifier in identifiers
            if (entry := self._tasks.get(identifier)) is not None
        }
