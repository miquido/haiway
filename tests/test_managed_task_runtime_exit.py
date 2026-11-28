import asyncio
import gc
from typing import Any, Literal

from pytest import mark, raises

from haiway import ctx
from haiway.context.managed import ManagedTasks, TaskRuntime, managed_task
from tests.managed_helpers import LeasedStore, Output, Request

type Source = Literal["attempt", "unrelated task with a store call in flight"]


async def exiter(exception: BaseException) -> None:
    await asyncio.sleep(0.05)
    raise exception


async def unrelated(events: list[str]) -> None:
    try:
        await asyncio.sleep(10)

    finally:
        events.append("unrelated task finalized")


@mark.parametrize("exception", [SystemExit(3), KeyboardInterrupt()])
@mark.parametrize("source", ["attempt", "unrelated task with a store call in flight"])
def test_an_interpreter_exit_is_raised_unchanged_and_the_shutdown_of_asyncio_completes(
    exception: BaseException,
    source: Source,
) -> None:
    # FR-40.3: nothing is recorded, and the runtime and everything else is torn down as the
    # interpreter exit leaves the loop
    store = LeasedStore()
    events: list[str] = []
    unhandled: list[dict[str, Any]] = []
    exiters: list[asyncio.Task[None]] = []

    @managed_task(name="exits")
    async def exits(request: Request) -> Output:
        raise exception

    async def main() -> None:
        background = asyncio.create_task(unrelated(events))
        try:
            if source == "unrelated task with a store call in flight":
                # the opening claim never returns, the exit comes while it is in flight
                store.claim_delay = 10.0
                exiters.append(asyncio.create_task(exiter(exception)))

            async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(exits,))):
                try:
                    if source == "attempt":
                        await ManagedTasks.spawn(exits(Request()))

                    await asyncio.sleep(5)

                finally:
                    events.append("block exited")

        finally:
            events.append("main finalized")
            background.cancel()

    def handler(_: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        unhandled.append(context)

    runner = asyncio.Runner()
    try:
        runner.get_loop().set_exception_handler(handler)
        with raises(BaseException) as raised:
            runner.run(main())

        for task in exiters:
            if task.done() and not task.cancelled():
                task.exception()  # the exit of this task is raised by the loop, not lost

    finally:
        runner.close()

    gc.collect()
    assert raised.value is exception
    assert "block exited" in events and "main finalized" in events
    assert "unrelated task finalized" in events
    assert store.closed
    assert store.calls["record"] == 0
    assert store.calls["release"] == 0
    assert unhandled == []
