from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from contextvars import Context
from typing import final

from haiway.attributes import State
from haiway.context.disposables import Disposables
from haiway.context.managed.state import ManagedTasks
from haiway.context.observability import Observability, ObservabilityLink
from haiway.context.presets import ContextPresets, ContextPresetsRegistry
from haiway.context.scope import ContextScope
from haiway.context.state import ContextState
from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = ("TaskRoot",)


@final
class _Construction:
    __slots__ = ()


_CONSTRUCTION: _Construction = _Construction()


@final
class TaskRoot:
    """
    The root snapshot of one ManagedTasks block, from which every task builds its context.

    The snapshot holds the ``State`` instances and the presets registry current when the
    block was entered. It does not change afterwards, so state merged or presets entered
    in the body of the block never reach a task.

    Created only by a ``ctx.managed_tasks`` block when it is entered.
    """

    __slots__ = (
        "_presets",
        "_state",
    )

    def __init__(
        self,
        construction: _Construction,
        /,
        *,
        state: Sequence[State],
        presets: ContextPresetsRegistry | None,
    ) -> None:
        if construction is not _CONSTRUCTION:
            raise TypeError("TaskRoot is created by entering ctx.managed_tasks")

        self._state: Sequence[State]
        object.__setattr__(self, "_state", tuple(state))
        self._presets: ContextPresetsRegistry | None
        object.__setattr__(self, "_presets", presets)

    @property
    def state(self) -> Sequence[State]:
        """The ``State`` instances of the snapshot, in their order."""
        return self._state

    def context(
        self,
        owner: ManagedTasks,
        /,
    ) -> Context:
        """
        Build the context a task runs in.

        Parameters
        ----------
        owner : ManagedTasks
            The ``ManagedTasks`` of the runtime running the task, placed above the state of
            the snapshot.

        Returns
        -------
        contextvars.Context
            A new context holding exactly the state of the snapshot with the owner, and the
            presets registry of the snapshot when it had one. No other context variable,
            of haiway or of the application, is set.
        """
        context: Context = Context()
        # a fresh instance for each context - the state of a scope is mutated when it
        # constructs a default on demand
        ContextState.installing(context, (*self._state, owner))
        if self._presets is not None:
            self._presets.installing(context)

        return context

    def task_scope(
        self,
        name: str,
        /,
        *,
        link: ObservabilityLink | None,
        observability: Observability,
    ) -> AbstractAsyncContextManager[str]:
        """
        Prepare the root scope of a task.

        The scope is entered with an explicit empty preset, so it resolves no registry
        entry: the presets of a task are entered by its attempt scopes.

        Parameters
        ----------
        name : str
            The registered name of the task.
        link : ObservabilityLink | None
            The origin of the task, the only link of the scope, none when absent.
        observability : Observability
            The backend recording the task.

        Returns
        -------
        AbstractAsyncContextManager[str]
            The scope to be entered in the context built by ``context``. Yields its trace
            identifier.
        """
        return ContextScope(
            name=name,
            presets=ContextPresets.of(name),
            state=(),
            disposables=Disposables.of(),
            observability=observability,
            kind="internal",
            isolated=False,
            links=() if link is None else (link,),
        )

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


def take_root_snapshot() -> TaskRoot:
    # the State instances and the innermost presets registry current now
    return TaskRoot(
        _CONSTRUCTION,
        state=tuple(ContextState.snapshot()),
        presets=ContextPresetsRegistry.current(),
    )
