import sys
from asyncio import AbstractEventLoop, CancelledError, get_running_loop
from collections.abc import Sequence
from logging import Logger
from types import TracebackType
from typing import Any, final

from haiway.attributes import State
from haiway.context.closing import ContextClosing
from haiway.context.disposables import Disposables
from haiway.context.events import ContextEvents
from haiway.context.identifier import ContextIdentifier
from haiway.context.observability import ContextObservability, Observability, ObservabilityLevel

# Import after other imports to avoid circular dependencies
from haiway.context.presets import ContextPresets, ContextPresetsRegistry
from haiway.context.state import ContextState
from haiway.context.tasks import ContextTaskGroup
from haiway.context.types import ContextMissing
from haiway.utils.exceptions import raise_collected

__all__ = ("ContextScope",)


@final  # consider immutable
class ContextScope:
    __slots__ = (
        "_disposables",
        "_entered",
        "_identifier",
        "_isolated",
        "_name",
        "_observability",
        "_presets",
        "_state",
    )

    def __init__(
        self,
        name: str,
        presets: ContextPresets | None,
        state: Sequence[State],
        disposables: Disposables,
        observability: Observability | Logger | None,
        isolated: bool,
    ) -> None:
        self._identifier: ContextIdentifier | None = None
        self._name: str = name
        self._observability: Observability | Logger | None = observability
        self._presets: ContextPresets | None = presets
        self._state: Sequence[State] = state
        self._disposables: Disposables = disposables
        self._isolated: bool = isolated
        self._entered: list[tuple[bool, Any]] | None = None

    async def __aenter__(self) -> str:
        assert self._identifier is None, "Context reentrance is not allowed"  # nosec: B101
        loop: AbstractEventLoop = get_running_loop()
        # claimed before the first await - the scope has to be rejected for the
        # whole setup, not only after it became fully prepared
        identifier: ContextIdentifier = ContextIdentifier.scope(self._name)
        self._identifier = identifier
        # elements which were entered, paired with whether they exit asynchronously.
        # a plain list instead of an `AsyncExitStack` - the elements are known here,
        # so there is nothing to gain from the stack building a closure per element
        entered: list[tuple[bool, Any]] = []

        try:
            # propagate new scope identifier
            identifier.__enter__()
            entered.append((False, identifier))

            # ensure associated observability and obtain trace identifier
            observability: ContextObservability = ContextObservability.scope(
                identifier,
                observability=self._observability,
            )
            trace_id: str = observability.__enter__()
            entered.append((False, observability))

            # resolve presets
            if self._presets is not None:
                presets: ContextPresets | None = self._presets

            else:
                presets = ContextPresetsRegistry.select(self._name)

            # resolve combined state and ensure it is used
            state: ContextState = await self._resolve_state(presets, entered)
            state.__enter__()
            entered.append((False, state))

            # enter the task group after everything its tasks are given to work
            # with - it is joined on exit before the state and the disposables
            # are released, so a task spawned within the scope can't keep running
            # against a connection pool or a client which was already closed
            task_group: ContextTaskGroup = ContextTaskGroup()
            await task_group.__aenter__()
            entered.append((True, task_group))

            # provide events after the task group so they exit before it - closing
            # the event bus releases all pending subscribers so it can join them
            if self._isolated or identifier.is_root:
                events: ContextEvents = ContextEvents(loop=loop)
                await events.__aenter__()
                entered.append((True, events))

            # provide the closing future last so it completes first - everything
            # waiting for the scope to end is released before its tasks are joined
            closing: ContextClosing = ContextClosing(
                loop=loop,
                identifier=identifier.scope_id,
            )
            closing.__enter__()
            entered.append((False, closing))

            # claim the entered elements only when the scope is fully prepared - a
            # failed enter unwinds them here and leaves nothing behind to exit later
            self._entered = entered

            return trace_id

        except BaseException as exc:
            try:  # ensure unwinding on error
                await _unwind(
                    entered,
                    type(exc),
                    exc,
                    exc.__traceback__,
                )

            finally:
                # released only after the unwinding is complete - a failed enter
                # leaves nothing behind, yet until it finishes cleaning up there
                # is still a partially prepared scope which can't be entered
                self._identifier = None

            raise  # reraise original

    async def _resolve_state(
        self,
        presets: ContextPresets | None,
        entered: list[tuple[bool, Any]],
        /,
    ) -> ContextState:
        """
        Combine the state of every source, lowest priority first.

        The disposables of each source are entered only when there is something
        to prepare - entering an empty set would cost a few event loop round
        trips to prepare nothing. State given to the scope directly never needs
        preparation, so it is applied last without going through them at all.
        """
        presets_state: tuple[State, ...] = ()
        if presets is not None:
            presets_disposables: Disposables = presets.resolve_disposables()
            if presets_disposables:
                presets_state = (
                    *await self._enter_disposables(presets_disposables, entered),
                    # the state a preset carries directly needs no preparation,
                    # it keeps the priority it would have as the last disposable
                    *presets.static_state,
                )

            else:
                presets_state = tuple(presets.static_state)

        disposables_state: tuple[State, ...] = ()
        if self._disposables:
            disposables_state = tuple(await self._enter_disposables(self._disposables, entered))

        return ContextState.updating(
            (
                *presets_state,
                *disposables_state,
                *self._state,
            )
        )

    @staticmethod
    async def _enter_disposables(
        disposables: Disposables,
        entered: list[tuple[bool, Any]],
        /,
    ) -> Any:
        prepared: Any = await disposables.__aenter__()
        entered.append((True, disposables))
        return prepared

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        entered: list[tuple[bool, Any]] | None = self._entered
        if entered is None:
            raise ContextMissing("Context scope requested but not defined!")

        # a claimed identifier always comes with the entered elements
        claimed: ContextIdentifier | None = self._identifier
        assert claimed is not None  # nosec: B101
        # released before unwinding - the scope is spent either way, so a failing
        # exit can't leave it looking like it could be exited again
        self._entered = None

        try:  # unwind entered elements
            await _unwind(
                entered,
                exc_type,
                exc_val,
                exc_tb,
            )

        except CancelledError, GeneratorExit:
            # neither is a failure of the scope - a cancellation is delivered to it,
            # and a `GeneratorExit` is the scope living inside a generator which is
            # being closed, which is exactly how such a scope is meant to end
            raise

        except BaseException as exc:
            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                f"Context scope {claimed.unique_name} exit failed",
                exception=exc,
            )
            raise  # record and reraise other errors

        finally:
            # released last - the scope stays claimed for the whole teardown so
            # that nothing can enter it while it is still unwinding
            self._identifier = None


async def _unwind(
    entered: list[tuple[bool, Any]],
    exc_type: type[BaseException] | None,
    exc_val: BaseException | None,
    exc_tb: TracebackType | None,
    /,
) -> None:
    """
    Exit entered scope elements in reverse order, as nested context managers.

    Mirrors what an ``AsyncExitStack`` does for the same elements - every element
    is exited even when an earlier one failed, and an error raised while exiting
    replaces the one in flight while keeping it as its context. None of the scope
    elements suppress exceptions, so the suppression handling of the stack has no
    counterpart here. Unlike the stack, several failures are all delivered - see
    ``_raise_unwound``.

    An element reraising the exception it was given is not an exit failure - it is
    the error already propagating through the scope body, which is reported where
    it was raised and propagates from the `async with` on its own. Only an error
    which is not the one in flight replaces it and marks the exit as failed.
    """
    frame_exception: BaseException | None = sys.exception()

    def fix_exception_context(
        new_exception: BaseException,
        old_exception: BaseException | None,
    ) -> None:
        # the context of the newly raised error may point anywhere - walk to the
        # end of its chain and link it to the error it is replacing, the same way
        # nested `with` statements would have chained them
        while (context := new_exception.__context__) is not None and context is not old_exception:
            if context is frame_exception:
                new_exception.__context__ = old_exception
                return

            new_exception = context

    collected: list[BaseException] = []
    while entered:
        is_async, element = entered.pop()
        try:
            if is_async:
                await element.__aexit__(exc_type, exc_val, exc_tb)

            else:
                element.__exit__(exc_type, exc_val, exc_tb)

        except BaseException as exc:
            if exc is exc_val:
                continue  # reraised what it was given - nothing of its own failed

            fix_exception_context(exc, exc_val)
            collected.append(exc)
            # the remaining elements are exited against the newest error, exactly
            # as the enclosing `with` statements of a nesting would have been
            exc_type = type(exc)
            exc_val = exc
            exc_tb = exc.__traceback__

    _raise_unwound(collected)


def _raise_unwound(
    collected: list[BaseException],
    /,
) -> None:
    """
    Deliver the errors the elements raised while exiting, if there were any.

    A single failure is raised as it is - it is the error to report, and wrapping it
    would break every caller handling what a scope element can raise. Several are
    delivered together instead of all but the last being left to the context chain,
    where they can only be read and never handled.

    The error which was propagating through the scope body is never one of them - it
    stays the context of what is raised here, exactly as it would with nested `with`
    statements, so a cancellation passing through a scope keeps propagating as one.
    """
    match collected:
        case ():
            return  # nothing of its own failed

        case (exception,):
            # raising replaces the carefully prepared context - keep it to restore
            fixed_context: BaseException | None = exception.__context__
            try:
                raise exception

            except BaseException:
                exception.__context__ = fixed_context
                raise

        case _:
            raise_collected(collected, message="Context scope exit errors")
