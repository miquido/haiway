import asyncio

from pytest import mark

from haiway import ContextMissing, State, ctx


@mark.asyncio
async def test_shutdown_background_tasks_cancels_running_tasks() -> None:
    finished = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.sleep(10)
        finally:
            finished.set()

    task = ctx.spawn_background(worker)
    await asyncio.sleep(0)  # let it start
    assert not task.done()

    ctx.shutdown_background_tasks()
    await asyncio.sleep(0.05)  # allow cancellation to propagate

    assert task.cancelled() or task.done()
    assert finished.is_set()


@mark.asyncio
async def test_background_task_runs_detached_from_the_scope() -> None:
    # a detached task may outlive the scope it was spawned in, so inheriting that
    # scope would bind it to state and records free to be released while it runs
    missing_state = asyncio.Event()
    missing_events = asyncio.Event()

    class Marker(State):
        value: int = 0

    async def worker() -> None:
        try:
            ctx.state(Marker)

        except ContextMissing:
            missing_state.set()

        try:
            ctx.subscribe(Marker)

        except ContextMissing:
            missing_events.set()

    async with ctx.scope("root", Marker(value=42)):
        task = ctx.spawn_background(worker)
        await task

    assert missing_state.is_set()
    assert missing_events.is_set()


@mark.asyncio
async def test_background_task_can_enter_its_own_scope() -> None:
    # detached from any scope, so a scope entered within it becomes a root one
    class Marker(State):
        value: int = 0

    resolved: list[int] = []

    async def worker() -> None:
        async with ctx.scope("background", Marker(value=7)):
            resolved.append(ctx.state(Marker).value)

    async with ctx.scope("root", Marker(value=42)):
        await ctx.spawn_background(worker)

    assert resolved == [7]
