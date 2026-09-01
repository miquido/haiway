from asyncio import AbstractEventLoop, CancelledError, get_running_loop
from contextlib import AsyncExitStack
from logging import Logger
from types import TracebackType
from typing import final

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

__all__ = ("ContextScope",)


@final  # consider immutable
class ContextScope:
    __slots__ = (
        "_claimed_name",
        "_disposables",
        "_exit_stack",
        "_isolated",
        "_loop",
        "_name",
        "_observability",
        "_presets",
    )

    def __init__(
        self,
        name: str,
        presets: ContextPresets | None,
        disposables: Disposables,
        observability: Observability | Logger | None,
        isolated: bool,
    ) -> None:
        self._name: str = name
        self._observability: Observability | Logger | None = observability
        self._presets: ContextPresets | None = presets
        self._disposables: Disposables = disposables
        self._isolated: bool = isolated
        # the stack is prepared on entering - a scope can be created ahead of
        # its use, like `ctx.stream` does, and entered on a different loop
        self._exit_stack: AsyncExitStack | None = None
        self._loop: AbstractEventLoop | None = None
        # the escaped, scope id qualified name - available only from entering,
        # when the identifier of this very scope is created
        self._claimed_name: str | None = None

    async def __aenter__(self) -> str:
        assert self._claimed_name is None, "Context reentrance is not allowed"  # nosec: B101
        loop: AbstractEventLoop = get_running_loop()
        # claimed before the first await - the scope has to be rejected for the
        # whole setup, not only after it became fully prepared
        identifier: ContextIdentifier = ContextIdentifier.scope(self._name)
        self._claimed_name = identifier.unique_name
        # start scope exit stack
        exit_stack = AsyncExitStack()
        await exit_stack.__aenter__()

        try:
            # propagate new scope identifier
            exit_stack.enter_context(identifier)
            # ensure associated observability and obtain trace identifier
            trace_id: str = exit_stack.enter_context(
                ContextObservability.scope(
                    identifier,
                    observability=self._observability,
                )
            )

            # resolve presets
            if self._presets is not None:
                presets: ContextPresets | None = self._presets

            else:
                presets = ContextPresetsRegistry.select(self._name)

            # resolve combined state
            state: ContextState
            if presets is None:
                state = ContextState.updating(
                    await exit_stack.enter_async_context(self._disposables)
                )

            else:
                state = ContextState.updating(
                    (
                        *await exit_stack.enter_async_context(presets.resolve()),
                        *await exit_stack.enter_async_context(self._disposables),
                    )
                )

            # and ensure state is used
            exit_stack.enter_context(state)

            # enter the task group after everything its tasks are given to work
            # with - it is joined on exit before the state and the disposables
            # are released, so a task spawned within the scope can't keep running
            # against a connection pool or a client which was already closed
            await exit_stack.enter_async_context(ContextTaskGroup())

            # provide events after the task group so they exit before it - closing
            # the event bus releases all pending subscribers so it can join them
            if self._isolated or identifier.is_root:
                await exit_stack.enter_async_context(ContextEvents(loop=loop))

            # provide the closing future last so it completes first - everything
            # waiting for the scope to end is released before its tasks are joined
            exit_stack.enter_context(ContextClosing(loop))

            # claim the stack only when the scope is fully prepared - a failed
            # enter unwinds it here and leaves nothing behind to exit later
            self._exit_stack = exit_stack
            self._loop = loop

            return trace_id

        except BaseException as exc:
            try:  # ensure stack exiting on error
                await exit_stack.__aexit__(
                    type(exc),
                    exc,
                    exc.__traceback__,
                )

            finally:
                # released only after the unwinding is complete - a failed enter
                # leaves nothing behind, yet until it finishes cleaning up there
                # is still a partially prepared scope which can't be entered
                self._claimed_name = None

            raise  # reraise original

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        exit_stack: AsyncExitStack | None = self._exit_stack
        if exit_stack is None:
            raise ContextMissing("Context scope requested but not defined!")

        assert self._loop is get_running_loop()  # nosec: B101
        # released before unwinding - the scope is spent either way, so a failing
        # exit can't leave it looking like it could be exited again
        self._exit_stack = None
        self._loop = None

        try:  # exit stack
            await exit_stack.__aexit__(
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
                f"Context scope {self._claimed_name} exit failed",
                exception=exc,
            )
            raise  # record and reraise other errors

        finally:
            # released last - the scope stays claimed for the whole teardown so
            # that nothing can enter it while it is still unwinding
            self._claimed_name = None
