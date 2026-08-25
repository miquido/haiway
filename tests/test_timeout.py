from asyncio import CancelledError, Task, sleep
from asyncio import timeout as async_timeout

from pytest import mark, raises

from haiway import timeout


class FakeException(Exception):
    pass


@mark.asyncio
async def test_returns_result_when_returning_value():
    @timeout(3)
    async def long_running() -> int:
        return 42

    assert await long_running() == 42


@mark.asyncio
async def test_raises_with_error():
    @timeout(3)
    async def long_running() -> int:
        raise FakeException()

    with raises(FakeException):
        await long_running()


@mark.asyncio
async def test_raises_with_cancel():
    @timeout(3)
    async def long_running() -> int:
        await sleep(1)
        raise RuntimeError("Invalid state")

    task = Task(long_running())
    with raises(CancelledError):
        await sleep(0.01)
        task.cancel()
        await task


@mark.asyncio
async def test_raises_with_timeout():
    @timeout(0.01)
    async def long_running() -> int:
        await sleep(0.03)
        raise RuntimeError("Invalid state")

    with raises(TimeoutError):
        await long_running()


@mark.asyncio
async def test_awaits_unwinding_before_raising_timeout():
    # the value of a timeout is knowing that the resources are released once it is
    # observed, which only holds when the cancellation is awaited before raising
    unwound: bool = False

    @timeout(0.01)
    async def long_running() -> int:
        nonlocal unwound
        try:
            await sleep(1)

        finally:
            await sleep(0.01)  # cleanup taking its own time to complete
            unwound = True

        raise RuntimeError("Invalid state")

    with raises(TimeoutError):
        await long_running()

    assert unwound


@mark.asyncio
async def test_raises_with_cancel_from_within():
    @timeout(1)
    async def long_running() -> int:
        raise CancelledError()

    with raises(CancelledError):
        async with async_timeout(1):  # guard against awaiting forever
            await long_running()
