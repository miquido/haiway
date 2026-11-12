import asyncio
from types import TracebackType

import pytest

from haiway import State, ctx
from haiway.context import Disposables
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


@pytest.mark.asyncio
async def test_ctx_disposables_forbid_spawning_while_disposing() -> None:
    async def noop() -> None:
        pass

    class Spawning:
        async def __aenter__(self) -> tuple[()]:
            return ()

        async def __aexit__(self, *args: object) -> None:
            ctx.spawn(noop)

    async with ctx.scope("disposables"):
        with pytest.raises(RuntimeError):
            async with ctx.disposables(Spawning()):
                pass


@pytest.mark.asyncio
@pytest.mark.parametrize("hops", range(4))
async def test_disposal_errors_are_logged_when_cancellation_lands_on_completion(
    hops: int,
    caplog: pytest.LogCaptureFixture,
) -> None:
    # the cancellation is delayed by a number of loop iterations, so that one of
    # them lands exactly when the disposal has completed but the caller did not resume
    loop = asyncio.get_running_loop()
    caller: asyncio.Task[None] | None = None

    def cancel_later(remaining: int) -> None:
        if remaining > 0:
            loop.call_soon(cancel_later, remaining - 1)

        elif caller is not None:
            caller.cancel()

    class Failing:
        async def __aenter__(self) -> tuple[()]:
            return ()

        async def __aexit__(self, *args: object) -> None:
            cancel_later(hops)
            raise OSError("close failed")

    disposables = Disposables.of(Failing())
    await disposables.__aenter__()

    async def dispose() -> None:
        await disposables.__aexit__(None, None, None)

    caller = asyncio.create_task(dispose())
    with caplog.at_level("ERROR"), pytest.raises(asyncio.CancelledError):
        await caller

    assert any(
        record.exc_info is not None and record.exc_info[0] is OSError for record in caplog.records
    )


@pytest.mark.asyncio
async def test_failed_background_task_is_logged_to_root_after_scope_exit(
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def failing() -> None:
        await asyncio.sleep(0.01)
        raise ValueError("background")

    with caplog.at_level("ERROR"):
        async with ctx.scope("background"):
            task = ctx.spawn_background(failing)

        with pytest.raises(ValueError):
            await task

        await asyncio.sleep(0)

    records = [
        record for record in caplog.records if record.getMessage() == "Background task failed"
    ]
    assert len(records) == 1
    assert records[0].name == "root"


class _Ping(State):
    value: int = 0


class _Resource:
    def __init__(self, log: list[str]) -> None:
        self._log: list[str] = log

    async def __aenter__(self) -> State:
        return _Ping()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._log.append("disposed")


@pytest.mark.asyncio
async def test_late_subscription_within_nested_scope_finishes() -> None:
    async def scope_with_late_subscriber() -> None:
        async with ctx.scope("outer"):
            async with ctx.scope("inner"):

                async def late_subscriber() -> None:
                    await asyncio.sleep(0.01)
                    async for _ in ctx.subscribe(_Ping):
                        pass

                ctx.spawn(late_subscriber)

    await asyncio.wait_for(scope_with_late_subscriber(), timeout=1.0)


@pytest.mark.asyncio
async def test_tasks_spawned_within_disposables_are_joined_before_disposal() -> None:
    log: list[str] = []

    async def task() -> None:
        await asyncio.sleep(0.02)
        log.append("task")

    async with ctx.scope("disposables"):
        async with ctx.disposables(_Resource(log)):
            ctx.spawn(task)

    assert log == ["task", "disposed"]


@pytest.mark.asyncio
async def test_tasks_of_failing_disposables_body_are_cancelled_and_disposed() -> None:
    log: list[str] = []

    async def task() -> None:
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            log.append("cancelled")
            raise

    with pytest.raises(RuntimeError):
        async with ctx.scope("disposables"):
            async with ctx.disposables(_Resource(log)):
                ctx.spawn(task)
                await asyncio.sleep(0)
                raise RuntimeError("body")

    assert log == ["cancelled", "disposed"]


class _Outcome:
    def __init__(self, log: list[BaseException | None]) -> None:
        self._log: list[BaseException | None] = log

    async def __aenter__(self) -> State:
        return _Ping()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self._log.append(exc_val)


@pytest.mark.asyncio
async def test_disposables_are_released_with_the_failure_of_a_task() -> None:
    seen: list[BaseException | None] = []

    async def failing() -> None:
        raise RuntimeError("task")

    with pytest.raises(RuntimeError, match="task"):
        async with ctx.scope("disposables"):
            async with ctx.disposables(_Outcome(seen)):
                ctx.spawn(failing)

    assert len(seen) == 1
    assert isinstance(seen[0], RuntimeError)


@pytest.mark.asyncio
async def test_disposables_are_released_with_the_cancellation_of_the_join() -> None:
    seen: list[BaseException | None] = []
    joining: asyncio.Event = asyncio.Event()

    async def scope() -> None:
        async with ctx.scope("disposables"):
            async with ctx.disposables(_Outcome(seen)):
                ctx.spawn(asyncio.sleep, 10)
                joining.set()

    task: asyncio.Task[None] = asyncio.create_task(scope())
    await joining.wait()
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert len(seen) == 1
    assert isinstance(seen[0], asyncio.CancelledError)
