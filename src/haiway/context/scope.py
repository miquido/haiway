from asyncio import CancelledError, get_running_loop
from contextlib import AsyncExitStack
from logging import Logger
from types import TracebackType
from typing import final

from haiway.context.disposables import Disposables
from haiway.context.events import ContextEvents
from haiway.context.identifier import ContextIdentifier
from haiway.context.observability import ContextObservability, Observability, ObservabilityLevel

# Import after other imports to avoid circular dependencies
from haiway.context.presets import ContextPresets, ContextPresetsRegistry
from haiway.context.state import ContextState
from haiway.context.tasks import ContextTaskGroup

__all__ = ("ContextScope",)


@final  # consider immutable
class ContextScope:
    __slots__ = (
        "_disposables",
        "_exit_stack",
        "_identifier",
        "_isolated",
        "_observability",
        "_presets",
        "_state",
    )

    def __init__(
        self,
        name: str,
        presets: ContextPresets | None,
        disposables: Disposables,
        observability: Observability | Logger | None,
        isolated: bool,
    ) -> None:
        # prepare new context identifier, will become nested if able otherwise becomes root
        self._identifier: ContextIdentifier = ContextIdentifier.scope(name)
        # initialize observability scope with new context identifier
        self._observability: ContextObservability = ContextObservability.scope(
            self._identifier,
            observability=observability,
        )
        # remember requested presets
        self._presets: ContextPresets | None = presets
        # store provided disposables extended with provided state
        self._disposables: Disposables = disposables
        # prepare for context state management
        self._state: ContextState | None = None
        # prepare for isolation
        self._isolated: bool = isolated or self._identifier.is_root
        self._exit_stack: AsyncExitStack = AsyncExitStack()

    async def __aenter__(self) -> str:
        # start scope exit stack
        await self._exit_stack.__aenter__()
        try:
            # propagate new scope identifier
            self._exit_stack.enter_context(self._identifier)
            # ensure associated observability and obtain trace identifier
            trace_id: str = self._exit_stack.enter_context(self._observability)

            # resolve presets
            if self._presets is not None:
                presets: ContextPresets | None = self._presets

            else:
                presets = ContextPresetsRegistry.select(self._identifier.name)

            # resolve combined state
            if presets is None:
                self._state = ContextState.updating(
                    await self._exit_stack.enter_async_context(self._disposables)
                )

            else:
                self._state = ContextState.updating(
                    (
                        *await self._exit_stack.enter_async_context(presets.resolve()),
                        *await self._exit_stack.enter_async_context(self._disposables),
                    )
                )

            # and ensure state is used
            self._exit_stack.enter_context(self._state)

            # enter the task group after everything its tasks are given to work
            # with - it is joined on exit before the state and the disposables
            # are released, so a task spawned within the scope can't keep running
            # against a connection pool or a client which was already closed
            await self._exit_stack.enter_async_context(ContextTaskGroup())

            # provide events last so they exit first - closing the event bus
            # releases all pending subscribers so the task group can join them
            if self._isolated:
                await self._exit_stack.enter_async_context(ContextEvents(loop=get_running_loop()))

            return trace_id

        except BaseException as exc:
            # ensure stack exiting on error
            await self._exit_stack.__aexit__(type(exc), exc, exc.__traceback__)
            raise  # reraise original

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        try:  # exit stack
            await self._exit_stack.__aexit__(
                exc_type,
                exc_val,
                exc_tb,
            )

        except CancelledError:
            raise  # reraise cancellation

        except BaseException as exc:
            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                f"Context scope `{self._identifier.unique_name}` exit failed",
                exception=exc,
            )
            raise  # record and reraise other errors
