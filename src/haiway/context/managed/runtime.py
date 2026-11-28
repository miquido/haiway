from collections.abc import AsyncGenerator, Iterable, Mapping
from contextlib import asynccontextmanager
from logging import Logger
from math import isfinite
from typing import Literal, Never, final

from haiway.attributes import State
from haiway.context.managed.memory import InMemoryTaskStore
from haiway.context.managed.registration import RegisteredTask, registered_tasks_by_name
from haiway.context.managed.root import TaskRoot, take_root_snapshot
from haiway.context.managed.session import RuntimeSession
from haiway.context.managed.state import ManagedTasks
from haiway.context.managed.store import TaskStore
from haiway.context.managed.types import TaskRetryPolicy
from haiway.context.observability import LoggerObservability, Observability
from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = ("TaskRuntime",)


@final
class _Opening:
    __slots__ = ()


# the token of the block which enters a runtime, left out of ``__all__`` and imported by
# ``block.py`` only
OPENING: _Opening = _Opening()


@final
class TaskRuntime:
    """
    The runtime of managed tasks, the settings of one ``ctx.managed_tasks`` block.

    The runtime owns scheduling, the queue, concurrency, retries, time limits, attempt
    scopes, telemetry links, close and the handle API. The only part which can be swapped
    is the task store, which holds the tasks: the in-memory store by default, or any
    other implementing ``TaskStore``. Every runtime opening one store is a worker of one
    shared queue, claiming any due task whose registered name it lists.

    Without ``concurrency_limit``, every attempt starts as soon as it is due: the first
    runtime to claim from a shared store takes every due task, so a limit is recommended
    for runtimes sharing a store, and for any application which spawns without bound.

    A runtime object is entered once. Everything is served on the event loop its block was
    entered on.

    Parameters
    ----------
    store : TaskStore | None, default=None
        Where the tasks are kept. A new ``InMemoryTaskStore`` when absent.
    tasks : Iterable[RegisteredTask[Never, State | None]], default=()
        The registered tasks this runtime spawns and runs. A stored task of any other
        registered name is never claimed by it. No two may share a registered name.
    retry : TaskRetryPolicy, default=TaskRetryPolicy()
        The retry policy of a task whose registered task gives none. The default allows
        no retry.
    grace_period : float, default=0.0
        Seconds for which a close lets running attempts finish before cancelling the
        rest, finite and at least 0. The default cancels them at once. A ``record`` of
        the store failing when a close begins is retried only until the grace period has
        elapsed from the close, so with the default grace period it is abandoned at once,
        and the task, still running in the store, is recovered as interrupted once its
        lease expires.
    concurrency_limit : int | None, default=None
        The largest number of attempts this runtime runs at the same time, at least 1.
        Due tasks beyond it wait in the store as queued, in the order they became due.
        Unlimited when absent.
    observability : Observability | Logger | None, default=None
        The backend recording every task and the runtime itself. A ``Logger`` is wrapped
        in a ``LoggerObservability``. When absent, the one given to ``ctx.managed_tasks``,
        and else a ``LoggerObservability`` created when the block is entered.
    unsupported : Literal["leave", "fail"], default="leave"
        What the runtime does with a due stored task whose registered name it does not
        list. With ``"leave"`` it never claims it and the task stays as it is. With
        ``"fail"`` every claim round of the runtime, while it is open, also finishes every
        due ``queued`` or ``retry_pending`` task of an unlisted name as failed with
        ``TaskUnsupported``, never retried, and logs one ERROR record for each in the
        runtime scope. A cancel requested before still wins, and no ``running`` task is
        taken, expired lease or not.

    Raises
    ------
    ValueError
        When ``grace_period`` is not finite or is below 0, ``concurrency_limit`` is below
        1, ``tasks`` holds two tasks of one registered name, or ``unsupported`` is neither
        ``"leave"`` nor ``"fail"``.

    Warnings
    --------
    With ``unsupported="fail"``, a runtime fails every due stored task of a name it does not
    list, also one which another runtime on the same store lists and would run. Runtimes of
    different applications or worker kinds sharing one database fail each other's tasks;
    during a rolling deploy, older replicas fail tasks of names only newer ones list; a
    runtime given ``tasks=()`` fails every due task; and tasks of a renamed function are
    failed, as the registered name is the name of the function unless given.
    """

    __slots__ = (
        "_concurrency_limit",
        "_entered",
        "_grace_period",
        "_observability",
        "_retry",
        "_store",
        "_tasks",
        "_unsupported",
    )

    def __init__(
        self,
        *,
        store: TaskStore | None = None,
        tasks: Iterable[RegisteredTask[Never, State | None]] = (),
        retry: TaskRetryPolicy = TaskRetryPolicy(),  # noqa: B008
        grace_period: float = 0.0,
        concurrency_limit: int | None = None,
        observability: Observability | Logger | None = None,
        unsupported: Literal["leave", "fail"] = "leave",
    ) -> None:
        if isinstance(grace_period, bool) or not isfinite(grace_period) or grace_period < 0:
            raise ValueError(
                f"TaskRuntime grace_period has to be finite and at least 0: {grace_period}"
            )

        if concurrency_limit is not None and (
            isinstance(concurrency_limit, bool) or concurrency_limit < 1
        ):
            raise ValueError(
                f"TaskRuntime concurrency_limit has to be an integer of at least 1: "
                f"{concurrency_limit}"
            )

        if unsupported not in ("leave", "fail"):
            raise ValueError(
                f"TaskRuntime unsupported has to be 'leave' or 'fail': {unsupported!r}"
            )

        self._tasks: Mapping[str, RegisteredTask[Never, State | None]]
        object.__setattr__(self, "_tasks", registered_tasks_by_name(tasks))
        self._store: TaskStore
        object.__setattr__(self, "_store", store if store is not None else InMemoryTaskStore())
        self._retry: TaskRetryPolicy
        object.__setattr__(self, "_retry", retry)
        self._grace_period: float
        object.__setattr__(self, "_grace_period", float(grace_period))
        self._concurrency_limit: int | None
        object.__setattr__(self, "_concurrency_limit", concurrency_limit)
        self._observability: Observability | Logger | None
        object.__setattr__(self, "_observability", observability)
        self._unsupported: Literal["leave", "fail"]
        object.__setattr__(self, "_unsupported", unsupported)
        self._entered: bool
        object.__setattr__(self, "_entered", False)

    @asynccontextmanager
    async def opening(
        self,
        opening: _Opening,
        observability: Observability | None,
        /,
    ) -> AsyncGenerator[ManagedTasks]:
        """
        Open the runtime, for ``ctx.managed_tasks`` only.

        Public in name since pyright forbids private access across modules, guarded by a
        token only ``ctx.managed_tasks`` holds: anything else gets ``TypeError``.

        Parameters
        ----------
        opening : _Opening
            The token of ``ctx.managed_tasks``.
        observability : Observability | None
            The backend recording the runtime, when it is given one.

        Returns
        -------
        AsyncGenerator[ManagedTasks]
            Takes the root snapshot, once, before the runtime opens, opens the store session
            and starts the first claim round, so the due tasks the runtime lists start running
            at once. Yields the ``ManagedTasks`` of the runtime and closes it on exit.

        Raises
        ------
        TypeError
            When the token is not the one of ``ctx.managed_tasks``.
        RuntimeError
            When the runtime object was entered before.
        """
        if opening is not OPENING:
            raise TypeError("TaskRuntime is entered by ctx.managed_tasks")

        root: TaskRoot = take_root_snapshot()
        if self._entered:
            raise RuntimeError("TaskRuntime was entered before, a runtime is entered once")

        object.__setattr__(self, "_entered", True)
        backend: Observability
        match self._observability:
            case None:
                backend = (
                    observability
                    if observability is not None
                    else LoggerObservability(debug_context=False)
                )

            case Logger() as logger:
                backend = LoggerObservability(logger, debug_context=False)

            case configured:
                backend = configured

        session: RuntimeSession = RuntimeSession(
            store=self._store,
            tasks=self._tasks,
            retry=self._retry,
            grace_period=self._grace_period,
            concurrency_limit=self._concurrency_limit,
            backend=backend,
            root=root,
            unsupported=self._unsupported,
        )
        async with session.running() as tasks:
            yield tasks

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
