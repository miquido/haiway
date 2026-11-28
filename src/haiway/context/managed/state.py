from math import isfinite
from typing import Final, Protocol, final
from uuid import UUID

from haiway.attributes import State
from haiway.context.managed.handle import ManagedTask
from haiway.context.managed.listing import TaskFilter, TaskSummary
from haiway.context.managed.records import record_debug
from haiway.context.managed.registration import RegisteredTaskCall
from haiway.context.observability import ObservabilityLink
from haiway.context.state import ContextState
from haiway.context.types import ContextMissing, ContextStateMissing
from haiway.utils.pagination import Paginated, Pagination

__all__ = (
    "ManagedTasks",
    "RegisteredTaskSpawning",
    "TaskAvailability",
    "TaskListing",
    "TaskLookup",
)


class TaskAvailability(Protocol):
    """
    Tells whether the runtime accepts spawns.

    Returns
    -------
    bool
        ``True`` exactly while the runtime is open.
    """

    async def __call__(self) -> bool: ...


class RegisteredTaskSpawning(Protocol):
    """
    Stores a registered task call and hands it to the runtime.

    Parameters
    ----------
    call : RegisteredTaskCall[Result]
        The registered task bound to its argument.
    time_limit : float | None
        Seconds each attempt may run, unlimited when absent.
    origin : ObservabilityLink | None
        The scope which spawned the task, when there is one.

    Returns
    -------
    ManagedTask[Result]
        The handle of the stored task.

    Raises
    ------
    ValueError
        When the name or the registration of the call is not the one listed in the runtime.
    RuntimeError
        When the runtime is closing or closed, or on another event loop.
    """

    async def __call__[Result: State | None](
        self,
        call: RegisteredTaskCall[Result],
        /,
        *,
        time_limit: float | None,
        origin: ObservabilityLink | None,
    ) -> ManagedTask[Result]: ...


class TaskLookup(Protocol):
    """
    Looks a task up by its identifier.

    Returns
    -------
    ManagedTask[State | None] | None
        The handle while the store holds the task, ``None`` otherwise.

    Raises
    ------
    RuntimeError
        When the runtime is closed, or on another event loop.
    """

    async def __call__(
        self,
        identifier: UUID,
        /,
    ) -> ManagedTask[State | None] | None: ...


class TaskListing(Protocol):
    """
    Lists the stored tasks a page at a time, see ``ManagedTasks.tasks``.

    Parameters
    ----------
    pagination : Pagination
        The page requested.
    matching : TaskFilter
        The conditions a listed task meets.

    Returns
    -------
    Paginated[TaskSummary]
        The page, with the token of the next one for a full page.

    Raises
    ------
    ValueError
        For a limit outside 1 to 1,000, a token no listing returned, or arguments.
    RuntimeError
        When the runtime is closed, or on another event loop.
    """

    async def __call__(
        self,
        pagination: Pagination,
        /,
        *,
        matching: TaskFilter,
    ) -> Paginated[TaskSummary]: ...


# every task, the default of a listing
_ANY_TASK: Final[TaskFilter] = TaskFilter()


def _current() -> ManagedTasks:
    # checked instead of read, a read would construct a default which fails, and outside
    # any scope it would raise another error
    if not ContextState.contains(ManagedTasks):
        raise ContextStateMissing("ManagedTasks is not defined in current scope")

    return ContextState.state(ManagedTasks)


@final
class ManagedTasks(State):
    """
    The state through which code spawns, looks up and checks managed tasks.

    Its functions are bound to one open runtime, which puts the instance into the context
    of its block and of every task it runs. No field has a default, so a context without
    a block holds none: ``ctx.state(ManagedTasks)`` raises ``ContextStateMissing``.
    The class methods use the instance of the current context.

    Attributes
    ----------
    availability : TaskAvailability
        Tells whether the runtime accepts spawns.
    spawning : RegisteredTaskSpawning
        Stores a task call and hands it to the runtime.
    lookup : TaskLookup
        Looks a task up by its identifier.
    listing : TaskListing
        Lists the stored tasks a page at a time.
    """

    availability: TaskAvailability
    spawning: RegisteredTaskSpawning
    lookup: TaskLookup
    listing: TaskListing

    @classmethod
    async def available(cls) -> bool:
        """
        Check whether managed tasks can be spawned.

        Returns
        -------
        bool
            ``True`` exactly when the current context holds a ``ManagedTasks`` whose
            runtime is open, ``False`` otherwise, also outside any scope.
        """
        if not ContextState.contains(ManagedTasks):
            return False

        return await ContextState.state(ManagedTasks).availability()

    @classmethod
    async def spawn[Result: State | None](
        cls,
        call: RegisteredTaskCall[Result],
        /,
        *,
        time_limit: float | None = None,
    ) -> ManagedTask[Result]:
        """
        Spawn a registered task out of the current scope.

        The task is stored before this returns, and it is tied to no scope: the exit of the
        spawning scope neither waits for it, cancels it, nor fails it. Every attempt runs
        in a context of its own, built from the root snapshot of the runtime, so nothing of
        the spawning scope reaches the task but its argument. The task is linked to the
        scope which spawned it, when there is one.

        The store records the task through its own connections: a task spawned inside an
        application transaction is stored at once, can start before that transaction
        commits and survives its rollback. Applications spawn after commit.

        Spawning is allowed while the disposables of a scope are prepared or disposed.

        Parameters
        ----------
        call : RegisteredTaskCall[Result]
            The registered task bound to its argument.
        time_limit : float | None, default=None
            Seconds each attempt may run, counted from the claim of the attempt. Exceeding
            it fails the attempt, which the retry policy may retry. Unlimited when absent.

        Returns
        -------
        ManagedTask[Result]
            The handle of the stored task.

        Raises
        ------
        ContextStateMissing
            When the context holds no ``ManagedTasks``, also outside any scope.
        ValueError
            When ``time_limit`` is not a finite number above 0, when the name of the call is
            not listed in the runtime, or when its registration differs from that of the
            task listed under its name. Nothing is stored.
        RuntimeError
            When the runtime is closing or closed, or on another event loop than the one
            the block was entered on.
        """
        tasks: ManagedTasks = _current()
        if time_limit is not None and (
            isinstance(time_limit, bool) or not isfinite(time_limit) or time_limit <= 0
        ):
            raise ValueError(f"time_limit has to be a finite number above 0: {time_limit}")

        origin: ObservabilityLink | None
        try:
            origin = ObservabilityLink.current()

        except ContextMissing:
            origin = None  # spawned outside of any scope

        task: ManagedTask[Result] = await tasks.spawning(
            call,
            time_limit=time_limit,
            origin=origin,
        )
        if origin is not None:
            record_debug("Managed task %s spawned: %s", task.name, task.identifier)

        return task

    @classmethod
    async def task(
        cls,
        identifier: UUID,
        /,
    ) -> ManagedTask[State | None] | None:
        """
        Look a task up by its identifier.

        The handle is returned whether or not the runtime lists the registered name of the
        task. For a task of an unlisted name, its status works, and its outcome raises
        ``ValueError``.

        Parameters
        ----------
        identifier : UUID
            The identifier of the task.

        Returns
        -------
        ManagedTask[State | None] | None
            The handle while the store holds the task, ``None`` otherwise.

        Raises
        ------
        ContextStateMissing
            When the context holds no ``ManagedTasks``, also outside any scope.
        RuntimeError
            When the runtime is closed, or on another event loop than the one the block
            was entered on.
        """
        return await _current().lookup(identifier)

    @classmethod
    async def tasks(
        cls,
        pagination: Pagination,
        /,
        *,
        matching: TaskFilter = _ANY_TASK,
    ) -> Paginated[TaskSummary]:
        """
        List the stored tasks, oldest first, a page at a time.

        Tasks are listed by creation time and then by identifier, whether or not the
        runtime lists their registered names. Each is described by a ``TaskSummary``: its
        identifier, registered name, status, number of counted attempts and creation time,
        and the finish time when finished, never its argument, result or failure. The
        creation and finish times are the clock of the store, which with a database store is
        the clock of the database server.

        A page holds the first ``pagination.limit`` matching tasks after the position of its
        token, or from the start without one. The token of the returned pagination is given
        only for a full page, so a partial page ends a traversal and its ``has_next_page`` is
        ``False``, while a full last page leads to one empty page. Tokens are opaque: they
        are produced by this method and only passed back to it, also with another filter.
        A traversal returns every task which matches and is held from its start to its end
        once, a task created during it only when its creation time is after the position.

        Parameters
        ----------
        pagination : Pagination
            The page requested: ``limit`` from 1 to 1,000, ``token`` ``None`` for the first
            page or a token a listing returned, and no ``arguments``.
        matching : TaskFilter, default=TaskFilter()
            The conditions a listed task meets. Every task by default.

        Returns
        -------
        Paginated[TaskSummary]
            The page, with the ``pagination`` given and its token replaced.

        Raises
        ------
        ContextStateMissing
            When the context holds no ``ManagedTasks``, also outside any scope.
        ValueError
            When ``pagination.limit`` is not an integer from 1 to 1,000, ``pagination.token``
            is not a token of a listing, or ``pagination.arguments`` is not empty. No store
            operation is called.
        RuntimeError
            When the runtime is closed, or on another event loop than the one the block was
            entered on.
        """
        return await _current().listing(pagination, matching=matching)
