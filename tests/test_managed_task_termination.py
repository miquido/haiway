import asyncio

from pytest import mark, raises

from haiway.context.managed import TaskRuntime
from haiway.context.managed.attempt import AttemptTermination


async def never_ends() -> str:
    await asyncio.sleep(10)
    return "done"


async def ends() -> str:
    return "done"


@mark.asyncio
async def test_a_request_before_the_task_is_bound_decides_and_is_delivered_at_the_binding() -> None:
    termination = AttemptTermination()

    assert termination.request("time_limit") is True
    assert termination.request("cancel") is False  # the first request decides the end
    assert termination.reason == "time_limit"
    task = asyncio.create_task(never_ends())
    termination.bind(task)

    with raises(asyncio.CancelledError):
        await task


@mark.asyncio
async def test_a_delivery_cancels_the_task_and_decides_nothing() -> None:
    termination = AttemptTermination()
    task = asyncio.create_task(never_ends())
    termination.bind(task)

    termination.deliver()

    assert termination.reason is None
    with raises(asyncio.CancelledError):
        await task


@mark.asyncio
async def test_a_delivery_before_the_task_is_bound_cancels_it_at_the_binding() -> None:
    termination = AttemptTermination()

    termination.deliver()

    assert termination.reason is None
    task = asyncio.create_task(never_ends())
    termination.bind(task)
    with raises(asyncio.CancelledError):
        await task


@mark.asyncio
async def test_a_request_which_comes_after_the_task_ended_is_not_one() -> None:
    termination = AttemptTermination()
    task = asyncio.create_task(ends())
    termination.bind(task)
    await task

    assert termination.request("close") is False
    termination.deliver()
    assert termination.reason is None
    assert await task == "done"


@mark.asyncio
async def test_a_request_which_follows_a_decided_one_is_delivered_again() -> None:
    cancellations: list[int] = []

    async def stubborn() -> str:
        for _ in range(2):
            try:
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                cancellations.append(1)

        return "done"

    termination = AttemptTermination()
    task = asyncio.create_task(stubborn())
    termination.bind(task)
    await asyncio.sleep(0)

    assert termination.request("time_limit") is True
    await asyncio.sleep(0)
    assert termination.request("close") is False

    assert await task == "done"
    assert cancellations == [1, 1]
    assert termination.reason == "time_limit"


@mark.asyncio
async def test_a_runtime_is_entered_by_its_block_only() -> None:
    with raises(TypeError):
        async with TaskRuntime().opening(object(), None):
            pass
