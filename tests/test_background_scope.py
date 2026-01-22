from asyncio import CancelledError, sleep

import pytest

from haiway import ctx


@pytest.mark.asyncio
async def test_shutdown_background_tasks_cancels_fallback_spawn():
    events: list[str] = []

    async def worker() -> None:
        try:
            await sleep(0.1)
            events.append("done")
        except CancelledError:
            events.append("cancelled")
            raise

    task = ctx.spawn(worker)
    await sleep(0)  # allow task scheduling

    ctx.shutdown_background_tasks()

    with pytest.raises(CancelledError):
        await task
    assert "cancelled" in events
