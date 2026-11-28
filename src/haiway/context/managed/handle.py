from collections.abc import Callable, Coroutine
from typing import final
from uuid import UUID

from haiway.attributes import State
from haiway.context.managed.types import TaskCancellation, TaskOutcome, TaskStatus
from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = ("ManagedTask",)


@final
class _Construction:
    __slots__ = ()


_CONSTRUCTION: _Construction = _Construction()


@final
class ManagedTask[Result: State | None]:
    """
    Handle of a managed task, returned by a spawn or a lookup.

    The handle owns nothing: its status and outcome are read from the task store of the
    runtime it was created by, so every handle of one task, in any runtime, sees the same.
    Awaiting or abandoning a handle never affects the task. Once the runtime was closed,
    its operations raise ``RuntimeError``, except for a task which the close finished as
    cancelled.
    """

    __slots__ = (
        "_cancel",
        "_identifier",
        "_name",
        "_outcome",
        "_status",
    )

    def __init__(
        self,
        construction: _Construction,
        /,
        *,
        identifier: UUID,
        name: str,
        status: Callable[[], Coroutine[None, None, TaskStatus]],
        outcome: Callable[[], Coroutine[None, None, TaskOutcome[Result]]],
        cancel: Callable[[], Coroutine[None, None, TaskCancellation]],
    ) -> None:
        if construction is not _CONSTRUCTION:
            raise TypeError("ManagedTask is created by spawning or looking up a task")

        self._identifier: UUID
        object.__setattr__(self, "_identifier", identifier)
        self._name: str
        object.__setattr__(self, "_name", name)
        self._status: Callable[[], Coroutine[None, None, TaskStatus]]
        object.__setattr__(self, "_status", status)
        self._outcome: Callable[[], Coroutine[None, None, TaskOutcome[Result]]]
        object.__setattr__(self, "_outcome", outcome)
        self._cancel: Callable[[], Coroutine[None, None, TaskCancellation]]
        object.__setattr__(self, "_cancel", cancel)

    @property
    def identifier(self) -> UUID:
        """
        The identifier of the task, a random ``UUID``.

        It is an identifier, not a credential: it is drawn from a generator which is not
        cryptographic. Anyone able to look tasks up in a runtime, or to read its store, can
        use it. Code exposing tasks to remote callers has to map them to identifiers of its
        own.
        """
        return self._identifier

    @property
    def name(self) -> str:
        """The registered name of the task, which names its scopes."""
        return self._name

    async def status(self) -> TaskStatus:
        """
        Read the status of the task from the store.

        Returns
        -------
        TaskStatus
            ``"queued"`` while an attempt waits for a slot, ``"running"`` while one runs,
            ``"retry_pending"`` during a retry delay and ``"finished"`` once the outcome is
            fixed.

        Raises
        ------
        RuntimeError
            When called on another event loop than the one the block was entered on, or
            after the runtime closed, except for a task the close finished as cancelled.
        LookupError
            When the store no longer holds the task, naming its identifier.
        """
        return await self._status()

    async def outcome(self) -> TaskOutcome[Result]:
        """
        Wait for the outcome of the task.

        Cancelling the awaiting coroutine does not affect the task, and every awaiter of
        one task receives an equal outcome. The task's own exception is never raised: it
        is reported as ``TaskFailed``.

        Returns
        -------
        TaskOutcome[Result]
            ``TaskCompleted`` with the value decoded from the store, ``TaskFailed`` with
            the failure of the last attempt, or ``TaskCancelled``.

        Raises
        ------
        RuntimeError
            When called on another event loop than the one the block was entered on, or
            when the runtime closed before the outcome was fixed, except for a task the
            close finished as cancelled.
        LookupError
            When the store no longer holds the task, naming its identifier.
        ValueError
            When the registered name of the task is not listed in the runtime, so that its
            result class is unknown.
        """
        return await self._outcome()

    async def cancel(self) -> TaskCancellation:
        """
        Cancel the task, or request its cancellation.

        Every call goes to the store, whatever the runtime requested for the task itself, and
        returns what the store answered. A cancel which the store accepted wins: the end of
        the attempt, whatever it was, is replaced by the cancellation. A task cancelled
        before it ran finishes at once with its function not called, which is recorded at
        DEBUG in the scope of the caller.

        Returns
        -------
        TaskCancellation
            ``"cancelled"`` when the task was queued or waiting for a retry, it finishes as
            ``TaskCancelled`` at once. ``"pending"`` when it was running: the request is
            kept, delivered to the attempt, and the task finishes as ``TaskCancelled`` when
            the attempt ends, whatever the attempt returned. ``"finished"`` when the task
            had its outcome already, nothing is changed.

        Raises
        ------
        RuntimeError
            When called on another event loop than the one the block was entered on, or
            after the runtime closed, except for a task the close finished as cancelled.
        LookupError
            When the store no longer holds the task, naming its identifier.
        """
        return await self._cancel()

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


def new_handle[Result: State | None](
    *,
    identifier: UUID,
    name: str,
    status: Callable[[], Coroutine[None, None, TaskStatus]],
    outcome: Callable[[], Coroutine[None, None, TaskOutcome[Result]]],
    cancel: Callable[[], Coroutine[None, None, TaskCancellation]],
) -> ManagedTask[Result]:
    return ManagedTask(
        _CONSTRUCTION,
        identifier=identifier,
        name=name,
        status=status,
        outcome=outcome,
        cancel=cancel,
    )
