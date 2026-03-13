from asyncio import CancelledError, sleep

from pytest import mark, raises

from haiway import AsyncQueue, ctx
from haiway.utils.queue import AsyncQueueEmpty


class FakeException(Exception):
    pass


@mark.asyncio
async def test_fails_when_stream_fails():
    stream: AsyncQueue[int] = AsyncQueue()
    stream.enqueue(0)
    stream.finish(exception=FakeException())
    elements: int = 0
    with raises(FakeException):
        async for _ in stream:
            elements += 1

    assert elements == 1


@mark.asyncio
async def test_cancels_when_iteration_cancels():
    stream: AsyncQueue[int] = AsyncQueue()
    elements: int = 0
    with raises(CancelledError):
        ctx.cancel()
        async for _ in stream:
            elements += 1

    assert elements == 0


@mark.asyncio
async def test_ends_when_stream_ends():
    stream: AsyncQueue[int] = AsyncQueue()
    stream.finish()
    elements: int = 0
    async for _ in stream:
        elements += 1

    assert elements == 0


@mark.asyncio
async def test_buffers_values_when_not_reading():
    stream: AsyncQueue[int] = AsyncQueue()
    stream.enqueue(0)
    stream.enqueue(1)
    stream.enqueue(2)
    stream.enqueue(3)
    stream.finish()
    elements: int = 0

    async for _ in stream:
        elements += 1

    assert elements == 4


@mark.asyncio
async def test_delivers_buffer_when_streaming_fails():
    stream: AsyncQueue[int] = AsyncQueue()
    stream.enqueue(0)
    stream.enqueue(1)
    stream.enqueue(2)
    stream.enqueue(3)
    stream.finish(exception=FakeException())
    elements: int = 0

    with raises(FakeException):
        async for _ in stream:
            elements += 1

    assert elements == 4


@mark.asyncio
async def test_delivers_updates_when_sending():
    stream: AsyncQueue[int] = AsyncQueue()
    stream.enqueue(0)

    elements: list[int] = []

    async for element in stream:
        elements.append(element)
        if len(elements) < 10:
            stream.enqueue(element + 1)
        else:
            stream.finish()

    assert elements == list(range(0, 10))


@mark.asyncio
async def test_fails_when_sending_to_finished():
    stream: AsyncQueue[int] = AsyncQueue()
    stream.finish()

    with raises(RuntimeError):
        stream.enqueue(42)


@mark.asyncio
async def test_ignores_when_finishing_when_finished():
    stream: AsyncQueue[int] = AsyncQueue()
    stream.finish()
    stream.finish()  # should not raise


@mark.asyncio
async def test_pending_next_returns_buffered_element():
    stream: AsyncQueue[int] = AsyncQueue(1, 2)

    assert stream.pending_next() == 1
    assert stream.pending_next() == 2


@mark.asyncio
async def test_pending_next_raises_when_queue_is_empty():
    stream: AsyncQueue[int] = AsyncQueue()

    with raises(AsyncQueueEmpty):
        stream.pending_next()


@mark.asyncio
async def test_pending_next_re_raises_finish_reason():
    stream: AsyncQueue[int] = AsyncQueue()
    stream.finish(exception=FakeException())

    with raises(FakeException):
        stream.pending_next()


@mark.asyncio
async def test_next_returns_buffered_element():
    stream: AsyncQueue[int] = AsyncQueue(42)

    assert await stream.next() == 42


@mark.asyncio
async def test_cancel_finishes_queue_with_cancelled_error():
    stream: AsyncQueue[int] = AsyncQueue()

    stream.cancel()

    assert stream.is_finished is True
    with raises(CancelledError):
        await stream.next()


@mark.asyncio
async def test_clear_removes_buffered_elements():
    stream: AsyncQueue[int] = AsyncQueue(1, 2, 3)

    stream.clear()

    with raises(AsyncQueueEmpty):
        stream.pending_next()


@mark.asyncio
async def test_clear_does_not_drop_waiting_consumer_result():
    stream: AsyncQueue[int] = AsyncQueue()

    consumer = ctx.spawn(stream.next)
    await sleep(0)

    stream.clear()
    stream.enqueue(7)

    assert await consumer == 7


@mark.asyncio
async def test_queue_is_immutable():
    stream: AsyncQueue[int] = AsyncQueue()

    with raises(AttributeError):
        stream._queue = []  # pyright: ignore[reportAttributeAccessIssue]

    with raises(AttributeError):
        del stream._queue  # pyright: ignore[reportAttributeAccessIssue]
