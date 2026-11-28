from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from haiway.context.managed.runtime import OPENING, TaskRuntime
from haiway.context.managed.state import ManagedTasks
from haiway.context.observability import Observability
from haiway.context.state import ContextState

__all__: tuple[str, ...] = ()


@asynccontextmanager
async def managed_tasks_block(
    runtime: TaskRuntime,
    /,
    *,
    observability: Observability | None,
) -> AsyncGenerator[ManagedTasks]:
    async with runtime.opening(OPENING, observability) as tasks:
        # the state of the body is merged as `ctx.updating` does, and restored first on exit
        with ContextState.Updating((tasks,)):
            yield tasks
