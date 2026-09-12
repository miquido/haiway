import sys
from asyncio import AbstractEventLoop, CancelledError, get_running_loop, iscoroutine
from collections.abc import Callable, Coroutine, MutableSequence, Sequence
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


@final
class ContextScope:
    __slots__ = (
        "_disposables",
        "_identifier",
        "_isolated",
        "_name",
        "_observability",
        "_presets",
        "_state",
        "_unwinds",
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
        self._unwinds: MutableSequence[_Unwind] | None = None

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
        unwinds: MutableSequence[_Unwind] = []

        try:
            # propagate new scope identifier
            identifier.__enter__()
            unwinds.append(identifier.__exit__)

            # ensure associated observability and obtain trace identifier
            observability: ContextObservability = ContextObservability.scope(
                identifier,
                observability=self._observability,
            )
            trace_id: str = observability.__enter__()
            unwinds.append(observability.__exit__)

            # resolve presets
            if self._presets is not None:
                presets: ContextPresets | None = self._presets

            else:
                presets = ContextPresetsRegistry.select(self._name)

            # resolve combined state and ensure it is used
            state: ContextState = await self._resolve_state(presets, unwinds)
            state.__enter__()
            unwinds.append(state.__exit__)

            # enter the task group after everything its tasks are given to work
            # with - it is joined on exit before the state and the disposables
            # are released, so a task spawned within the scope can't keep running
            # against a connection pool or a client which was already closed
            task_group: ContextTaskGroup = ContextTaskGroup()
            await task_group.__aenter__()
            unwinds.append(task_group.__aexit__)

            # provide events after the task group so they exit before it - closing
            # the event bus releases all pending subscribers so it can join them
            if self._isolated or identifier.is_root:
                events: ContextEvents = ContextEvents(loop=loop)
                await events.__aenter__()
                unwinds.append(events.__aexit__)

            # provide the closing future last so it completes first - everything
            # waiting for the scope to end is released before its tasks are joined
            closing: ContextClosing = ContextClosing(
                loop=loop,
                identifier=identifier.scope_id,
            )
            closing.__enter__()
            unwinds.append(closing.__exit__)

            # claim the entered elements only when the scope is fully prepared - a
            # failed enter unwinds them here and leaves nothing behind to exit later
            self._unwinds = unwinds

            return trace_id

        except BaseException as exc:
            try:  # ensure unwinding on error
                await _unwind(
                    unwinds,
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
        unwinds: MutableSequence[_Unwind],
        /,
    ) -> ContextState:
        presets_state: tuple[State, ...] = ()
        if presets is not None:
            presets_disposables: Disposables = presets.resolve_disposables()
            if presets_disposables:
                presets_state = (
                    *await self._enter_disposables(presets_disposables, unwinds),
                    # the state a preset carries directly needs no preparation,
                    # it keeps the priority it would have as the last disposable
                    *presets.static_state,
                )

            else:
                presets_state = tuple(presets.static_state)

        disposables_state: tuple[State, ...] = ()
        if self._disposables:
            disposables_state = tuple(await self._enter_disposables(self._disposables, unwinds))

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
        unwinds: MutableSequence[_Unwind],
        /,
    ) -> Any:
        prepared: Any = await disposables.__aenter__()
        unwinds.append(disposables.__aexit__)
        return prepared

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        unwinds: MutableSequence[_Unwind] | None = self._unwinds
        if unwinds is None:
            raise ContextMissing("Context scope requested but not defined!")

        # a claimed identifier always comes with the entered elements
        claimed: ContextIdentifier | None = self._identifier
        assert claimed is not None  # nosec: B101
        # released before unwinding - the scope is spent either way, so a failing
        # exit can't leave it looking like it could be exited again
        self._unwinds = None

        try:  # unwind entered elements
            await _unwind(
                unwinds,
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


_Unwind = Callable[
    [
        type[BaseException] | None,
        BaseException | None,
        TracebackType | None,
    ],
    Coroutine[None, None, None] | None,
]


async def _unwind(
    unwinds: MutableSequence[_Unwind],
    exc_type: type[BaseException] | None,
    exc_val: BaseException | None,
    exc_tb: TracebackType | None,
    /,
) -> None:
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

    collected: MutableSequence[BaseException] = []
    while unwinds:
        element = unwinds.pop()
        try:
            result: Any = element(exc_type, exc_val, exc_tb)
            if iscoroutine(result):
                await result

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

    raise_collected(
        collected,
        message="Scope exit errors",
    )
