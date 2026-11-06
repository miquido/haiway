import asyncio

import pytest

from haiway import ctx
from haiway.helpers import cache_externally


@pytest.mark.asyncio
async def test_body_explicit_cause_is_kept_when_tasks_fail() -> None:
    async def failing() -> None:
        raise ValueError("task")

    root = KeyError("root")
    with pytest.raises(RuntimeError) as info:
        async with ctx.scope("cause"):
            ctx.spawn(failing)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            raise RuntimeError("body") from root

    assert info.value.__cause__ is root


@pytest.mark.asyncio
async def test_body_without_cause_gets_task_failure_as_cause() -> None:
    async def failing() -> None:
        raise ValueError("task")

    with pytest.raises(RuntimeError) as info:
        async with ctx.scope("cause"):
            ctx.spawn(failing)
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            raise RuntimeError("body")

    assert isinstance(info.value.__cause__, ValueError)


@pytest.mark.asyncio
async def test_ctx_disposables_forbid_spawning_while_preparing() -> None:
    async def noop() -> None:
        pass

    class Spawning:
        async def __aenter__(self) -> tuple[()]:
            ctx.spawn(noop)
            return ()

        async def __aexit__(self, *args: object) -> None:
            pass

    async with ctx.scope("disposables"):
        with pytest.raises(RuntimeError):
            async with ctx.disposables(Spawning()):
                pass


@pytest.mark.asyncio
async def test_task_spawned_while_cancelling_is_cancelled() -> None:
    spawned: list[asyncio.Task[None]] = []

    async def child() -> None:
        await asyncio.sleep(10)

    async def parent() -> None:
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            spawned.append(ctx.spawn(child))
            raise

    with pytest.raises(RuntimeError):
        async with ctx.scope("cancelling"):
            ctx.spawn(parent)
            await asyncio.sleep(0)
            raise RuntimeError("fail")

    assert len(spawned) == 1
    assert spawned[0].cancelled()


@pytest.mark.asyncio
async def test_external_cache_write_failure_does_not_fail_scope() -> None:
    async def read(key: str) -> int | None:
        return None

    async def write(key: str, value: int) -> None:
        raise OSError("write failed")

    @cache_externally(make_key=lambda: "key", read=read, write=write)
    async def compute() -> int:
        return 1

    async with ctx.scope("cache"):
        assert await compute() == 1
