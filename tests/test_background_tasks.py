import asyncio

from pytest import mark

from haiway import ctx


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
