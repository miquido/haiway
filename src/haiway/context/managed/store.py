from collections.abc import Mapping, Sequence, Set
from contextlib import AbstractAsyncContextManager
from typing import Literal, Protocol, final
from uuid import UUID

from haiway.context.managed.listing import TaskFilter, TaskKeyset, TaskSummary
from haiway.context.managed.stored import (
    AttemptEnd,
    ClaimedTask,
    LeasedCoordination,
    LeaseRenewal,
    NewTask,
    StoredStatus,
    StoredTask,
)
from haiway.context.managed.types import TaskCancellation
from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = (
    "AttemptRecording",
    "CancelRequesting",
    "ChangeWaiting",
    "LeaseRenewing",
    "LocalCoordination",
    "StoreCoordination",
    "TaskClaiming",
    "TaskInserting",
    "TaskQuerying",
    "TaskReading",
    "TaskReleasing",
    "TaskStore",
    "TaskStoreSession",
    "UnsupportedFailing",
)


class ChangeWaiting(Protocol):
    """
    Wait for the change counter of a store, see ``__call__``.

    The counter is a non-negative integer of a store with ``LocalCoordination``. It grows by
    1 at every ``insert``, ``record``, ``release`` and ``cancel``, at every ``claim`` that
    claimed at least one task and every ``fail_unsupported`` that finished at least one, and
    once whenever the due moment of a ``queued`` or ``retry_pending`` task of the store
    passes.
    """

    async def __call__(
        self,
        seen: int,
        /,
    ) -> int:
        """
        Wait until the change counter of a store differs from ``seen``.

        Parameters
        ----------
        seen : int
            The counter last returned, ``-1`` before the first call.

        Returns
        -------
        int
            The change counter of the store, at once when it differs from ``seen``,
            otherwise after the next change or the earliest due moment of a task.
        """
        ...


@final
class LocalCoordination:
    """
    Coordination of a store whose runtimes share its process: leases never expire and
    runtimes wake on every change of the store.

    Only the in-memory store yields it, every other store yields ``LeasedCoordination``.
    ``changed`` follows the change counter rules of ``ChangeWaiting``.

    Parameters
    ----------
    changed : ChangeWaiting
        Waits for the change counter of the store.
    """

    __slots__ = ("_changed",)

    def __init__(
        self,
        changed: ChangeWaiting,
        /,
    ) -> None:
        self._changed: ChangeWaiting
        object.__setattr__(
            self,
            "_changed",
            changed,
        )

    @property
    def coordination(self) -> Literal["local"]:
        """The tag of this coordination."""
        return "local"

    @property
    def changed(self) -> ChangeWaiting:
        """Waits until the change counter of the store differs from the one seen."""
        return self._changed

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


type StoreCoordination = LeasedCoordination | LocalCoordination


class TaskInserting(Protocol):
    """
    Record a new task of a runtime.

    The call stores the task as ``queued``, with no attempts and due at once on the clock of
    the store, and returns once it is stored.

    Raises
    ------
    Exception
        For an identifier the store already holds. Nothing is changed: the task the store
        holds keeps its status, attempts and place in the listing order. The in-memory store
        raises ``ValueError`` naming the identifier, the PostgreSQL store its
        ``PostgresException``.
    """

    async def __call__(
        self,
        task: NewTask,
        /,
    ) -> None: ...


class TaskClaiming(Protocol):
    """
    Take due tasks, and tasks whose lease expired, for a runner.

    Takes the ``queued`` and ``retry_pending`` tasks which are due, the earliest due first
    when a ``limit`` leaves a choice, as ``ClaimedAttempt`` with the attempt number, and the
    ``running`` tasks whose lease expired, the lease of the caller's own included, as
    ``ClaimedInterrupted`` with the number of the interrupted attempt and whether a cancel was
    requested. Only tasks of the listed names are taken. Each task returned becomes
    ``running``, leased to the runner, and no task is returned to two runners while it is
    leased. Expired leases do not count against ``limit``.

    Parameters
    ----------
    runner : UUID
        The identity of the runner taking the tasks.
    names : Set[str]
        The registered names the runtime lists.
    limit : int | None
        The number of due tasks to take: ``None`` takes all of them, ``0`` takes none and
        leaves the claim to the expired leases only.

    Returns
    -------
    Sequence[ClaimedTask]
        The tasks taken.

    Raises
    ------
    ValueError
        When ``limit`` is negative.
    """

    async def __call__(
        self,
        runner: UUID,
        names: Set[str],
        *,
        limit: int | None,
    ) -> Sequence[ClaimedTask]: ...


class LeaseRenewing(Protocol):
    """
    Extend the leases a runner holds.

    A runner holds the lease of a task while the task is ``running`` with that runner as the
    lease owner, whether or not the lease expired, until another claim takes it. With
    ``LocalCoordination`` nothing is extended.

    Parameters
    ----------
    runner : UUID
        The identity of the runner.
    identifiers : Set[UUID]
        The identifiers of the tasks it holds.

    Returns
    -------
    LeaseRenewal
        The leases the runner holds among ``identifiers``, each ``"cancel_requested"`` when a
        cancel was requested and ``"held"`` otherwise.
    """

    async def __call__(
        self,
        runner: UUID,
        identifiers: Set[UUID],
    ) -> LeaseRenewal: ...


class AttemptRecording(Protocol):
    """
    Record the end of a counted attempt.

    Applies only when the runner holds that attempt: it holds the lease of the task and the
    attempt number of the task, the number of its counted attempts plus 1, is ``attempt``.
    Otherwise nothing is changed and the result is ``None``. When a cancel was requested it
    wins: the attempt is recorded as cancelled and the task finishes with
    ``StoredCancelled``, whatever ``end`` says. Without it the attempt is recorded as ``end``
    gives it and the task takes the status that variant fixes, ``retry_pending`` due after
    ``AttemptRetried.delay`` for a retry.

    Parameters
    ----------
    runner : UUID
        The identity of the runner.
    identifier : UUID
        The identifier of the task.
    attempt : int
        The number of the attempt which ended.
    end : AttemptEnd
        How the attempt ended.

    Returns
    -------
    StoredStatus | None
        The status the task has afterwards, ``None`` when the runner did not hold the attempt.
    """

    async def __call__(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,
        end: AttemptEnd,
        /,
    ) -> StoredStatus | None: ...


class TaskReleasing(Protocol):
    """
    Give a claimed attempt back without recording its end.

    Fenced as ``AttemptRecording``: it applies only when the runner holds that attempt,
    otherwise nothing is changed and the result is ``None``. The attempt is not counted. With
    a pending cancel request the task finishes with ``StoredCancelled``. Without one, a store
    with ``LeasedCoordination`` requeues the task as ``queued`` and due now, so another
    runtime resumes it, and the in-memory store, where nothing else could, finishes it with
    ``StoredCancelled``.

    Parameters
    ----------
    runner : UUID
        The identity of the runner.
    identifier : UUID
        The identifier of the task.
    attempt : int
        The number of the attempt given back.

    Returns
    -------
    StoredStatus | None
        The status the task has afterwards, ``None`` when the runner did not hold the attempt.
    """

    async def __call__(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,
        /,
    ) -> StoredStatus | None: ...


class CancelRequesting(Protocol):
    """
    Request the cancellation of a task.

    A ``queued`` or ``retry_pending`` task finishes with ``StoredCancelled`` at once. For a
    ``running`` task the request is marked when none is yet, and the runtime which holds the
    task learns of it through ``LeaseRenewing`` and ``AttemptRecording``.

    Parameters
    ----------
    identifier : UUID
        The identifier of the task.

    Returns
    -------
    TaskCancellation | None
        ``"cancelled"`` for a task which was not running, ``"pending"`` for a running one,
        ``"finished"`` for a finished one, and ``None`` when the store holds no such task.
    """

    async def __call__(
        self,
        identifier: UUID,
        /,
    ) -> TaskCancellation | None: ...


class UnsupportedFailing(Protocol):
    """
    Finish the due tasks whose registered name is not listed.

    Finishes every ``queued`` or ``retry_pending`` task which is due and whose name is not
    listed: one attempt is recorded as failed with ``TaskUnsupported`` and the task finishes
    with ``StoredFailed``, whatever retries its policy leaves. A ``running`` or ``finished``
    task is never touched, nor a task which is not due yet.

    Parameters
    ----------
    names : Set[str]
        The registered names a runtime lists.

    Returns
    -------
    Mapping[UUID, str]
        The registered name of each task finished, by its identifier.
    """

    async def __call__(
        self,
        names: Set[str],
        /,
    ) -> Mapping[UUID, str]: ...


class TaskQuerying(Protocol):
    """
    List task summaries in the listing order: oldest first, by creation time and identifier.

    Parameters
    ----------
    matching : TaskFilter
        The filter the tasks have to match.
    after : TaskKeyset | None
        The position to continue from, ``None`` for the start.
    limit : int
        The largest number of summaries to return.

    Returns
    -------
    Sequence[TaskSummary]
        The first ``limit`` tasks which match and come after ``after``.

    Raises
    ------
    ValueError
        When ``limit`` is negative.
    """

    async def __call__(
        self,
        matching: TaskFilter,
        /,
        *,
        after: TaskKeyset | None,
        limit: int,
    ) -> Sequence[TaskSummary]: ...


class TaskReading(Protocol):
    """
    Read tasks by identifier.

    Parameters
    ----------
    identifiers : Set[UUID]
        The identifiers of the tasks.

    Returns
    -------
    Mapping[UUID, StoredTask]
        The ``StoredTask`` of each identifier the store holds, nothing for an unknown one.
    """

    async def __call__(
        self,
        identifiers: Set[UUID],
        /,
    ) -> Mapping[UUID, StoredTask]: ...


@final
class TaskStoreSession:
    """
    The operations a store offers a runtime which opened it.

    Parameters
    ----------
    insert : TaskInserting
        Records a new task, queued and due now.
    claim : TaskClaiming
        Takes due tasks, and tasks whose lease expired, for a runner.
    renew : LeaseRenewing
        Extends the leases a runner holds.
    record : AttemptRecording
        Records the end of an attempt of a task the runner holds.
    release : TaskReleasing
        Releases a task the runner holds.
    cancel : CancelRequesting
        Cancels a task, or requests its cancellation when it runs.
    read : TaskReading
        Reads stored tasks by identifier.
    fail_unsupported : UnsupportedFailing
        Fails the due tasks whose registered name is not listed.
    query : TaskQuerying
        Lists task summaries in the listing order.
    coordination : StoreCoordination
        How the store tells runtimes when to act.
    """

    __slots__ = (
        "_cancel",
        "_claim",
        "_coordination",
        "_fail_unsupported",
        "_insert",
        "_query",
        "_read",
        "_record",
        "_release",
        "_renew",
    )

    def __init__(
        self,
        *,
        insert: TaskInserting,
        claim: TaskClaiming,
        renew: LeaseRenewing,
        record: AttemptRecording,
        release: TaskReleasing,
        cancel: CancelRequesting,
        read: TaskReading,
        fail_unsupported: UnsupportedFailing,
        query: TaskQuerying,
        coordination: StoreCoordination,
    ) -> None:
        self._insert: TaskInserting
        object.__setattr__(self, "_insert", insert)
        self._claim: TaskClaiming
        object.__setattr__(self, "_claim", claim)
        self._renew: LeaseRenewing
        object.__setattr__(self, "_renew", renew)
        self._record: AttemptRecording
        object.__setattr__(self, "_record", record)
        self._release: TaskReleasing
        object.__setattr__(self, "_release", release)
        self._cancel: CancelRequesting
        object.__setattr__(self, "_cancel", cancel)
        self._read: TaskReading
        object.__setattr__(self, "_read", read)
        self._fail_unsupported: UnsupportedFailing
        object.__setattr__(self, "_fail_unsupported", fail_unsupported)
        self._query: TaskQuerying
        object.__setattr__(self, "_query", query)
        self._coordination: StoreCoordination
        object.__setattr__(self, "_coordination", coordination)

    @property
    def insert(self) -> TaskInserting:
        """Records a new task."""
        return self._insert

    @property
    def claim(self) -> TaskClaiming:
        """Takes due tasks for a runner."""
        return self._claim

    @property
    def renew(self) -> LeaseRenewing:
        """Extends leases."""
        return self._renew

    @property
    def record(self) -> AttemptRecording:
        """Records the end of an attempt."""
        return self._record

    @property
    def release(self) -> TaskReleasing:
        """Releases a task."""
        return self._release

    @property
    def cancel(self) -> CancelRequesting:
        """Cancels a task."""
        return self._cancel

    @property
    def read(self) -> TaskReading:
        """Reads stored tasks."""
        return self._read

    @property
    def fail_unsupported(self) -> UnsupportedFailing:
        """Fails due tasks of unlisted names."""
        return self._fail_unsupported

    @property
    def query(self) -> TaskQuerying:
        """Lists task summaries."""
        return self._query

    @property
    def coordination(self) -> StoreCoordination:
        """How the store tells runtimes when to act."""
        return self._coordination

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


class TaskStore(Protocol):
    """
    A place where tasks are kept, opened by a runtime for the length of its block.

    Every runtime opening one store is a worker of one shared queue. The session a store
    yields carries exactly one coordination: only the in-memory store may yield
    ``LocalCoordination``, every other store must yield ``LeasedCoordination`` with the lease
    timings it was constructed with, since the recovery of a failed ``record`` needs leases
    that expire.
    """

    def open(self) -> AbstractAsyncContextManager[TaskStoreSession]:
        """
        Open the store for a runtime.

        Returns
        -------
        AbstractAsyncContextManager[TaskStoreSession]
            Yields the session of the runtime, and closes it on exit.
        """
        ...
