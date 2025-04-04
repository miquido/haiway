from asyncio import CancelledError, Task

import pytest
from pytest import raises

from haiway import AsyncStream, ctx


class FakeException(Exception):
    pass


@pytest.mark.asyncio
async def test_fails_when_stream_fails():
    stream: AsyncStream[int] = AsyncStream()
    Task(stream.send(0))
    elements: int = 0
    with raises(FakeException):
        async for _ in stream:
            elements += 1
            stream.finish(exception=FakeException())

    assert elements == 1


@pytest.mark.asyncio
async def test_cancels_when_iteration_cancels():
    stream: AsyncStream[int] = AsyncStream()
    elements: int = 0
    with raises(CancelledError):
        ctx.cancel()
        async for _ in stream:
            elements += 1

    assert elements == 0


@pytest.mark.asyncio
async def test_ends_when_stream_ends():
    stream: AsyncStream[int] = AsyncStream()
    stream.finish()
    elements: int = 0
    async for _ in stream:
        elements += 1

    assert elements == 0


@pytest.mark.asyncio
async def test_finishes_without_buffer():
    stream: AsyncStream[int] = AsyncStream()
    Task(stream.send(0))
    Task(stream.send(1))
    Task(stream.send(2))
    Task(stream.send(3))
    stream.finish()
    elements: int = 0

    async for _ in stream:
        elements += 1

    assert elements == 0


@pytest.mark.asyncio
async def test_fails_without_buffer():
    stream: AsyncStream[int] = AsyncStream()
    Task(stream.send(0))
    Task(stream.send(1))
    Task(stream.send(2))
    Task(stream.send(3))
    stream.finish(exception=FakeException())
    elements: int = 0

    with raises(FakeException):
        async for _ in stream:
            elements += 1

    assert elements == 0


@pytest.mark.asyncio
async def test_delivers_updates_when_sending():
    stream: AsyncStream[int] = AsyncStream()
    Task(stream.send(0))

    elements: list[int] = []

    async for element in stream:
        elements.append(element)
        if len(elements) < 10:
            Task(stream.send(element + 1))
        else:
            stream.finish()

    assert elements == list(range(0, 10))


@pytest.mark.asyncio
async def test_ignores_when_sending_to_finished():
    stream: AsyncStream[int] = AsyncStream()
    stream.finish()

    await stream.send(42)


@pytest.mark.asyncio
async def test_ignores_when_sending_to_failed():
    stream: AsyncStream[int] = AsyncStream()
    stream.finish(exception=FakeException())

    await stream.send(42)


@pytest.mark.asyncio
async def test_ignores_when_finishing_when_finished():
    stream: AsyncStream[int] = AsyncStream()
    stream.finish()
    stream.finish()  # should not raise


@pytest.mark.asyncio
async def test_delivers_all_when_sending_async():
    stream: AsyncStream[int] = AsyncStream()

    async def sender() -> None:
        await stream.send(0)
        await stream.send(1)
        await stream.send(2)
        stream.finish()

    ctx.spawn(sender)
    elements: list[int] = []

    async for element in stream:
        elements.append(element)

    assert elements == [0, 1, 2]
