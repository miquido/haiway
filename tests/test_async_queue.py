from asyncio import CancelledError, Task, create_task, sleep, to_thread

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


@mark.asyncio
async def test_limit_none_when_unbounded():
    stream: AsyncQueue[int] = AsyncQueue()

    assert stream.limit is None


@mark.asyncio
async def test_limit_property_returns_configured_value():
    stream: AsyncQueue[int] = AsyncQueue(limit=5)

    assert stream.limit == 5


@mark.asyncio
async def test_drops_oldest_when_limit_exceeded():
    stream: AsyncQueue[int] = AsyncQueue(limit=3)
    for i in range(5):
        stream.enqueue(i)
    stream.finish()

    elements: list[int] = []
    async for element in stream:
        elements.append(element)

    assert elements == [2, 3, 4]


@mark.asyncio
async def test_initial_elements_trimmed_to_limit():
    stream: AsyncQueue[int] = AsyncQueue(1, 2, 3, 4, 5, limit=3)
    stream.finish()

    elements: list[int] = []
    async for element in stream:
        elements.append(element)

    assert elements == [3, 4, 5]


@mark.asyncio
async def test_waiting_consumer_bypasses_limit():
    # Elements delivered directly to a waiting consumer do not count against
    # the limit, so no item should be dropped when the buffer is empty.
    stream: AsyncQueue[int] = AsyncQueue(limit=1)

    consumer = ctx.spawn(stream.next)
    await sleep(0)

    stream.enqueue(42)

    assert await consumer == 42
    # Buffer should still be empty — nothing was dropped.
    with raises(AsyncQueueEmpty):
        stream.pending_next()


@mark.asyncio
async def test_limited_queue_does_not_drop_when_below_limit():
    stream: AsyncQueue[int] = AsyncQueue(limit=3)
    stream.enqueue(10)
    stream.enqueue(20)
    stream.finish()

    elements: list[int] = []
    async for element in stream:
        elements.append(element)

    assert elements == [10, 20]


@mark.asyncio
async def test_aclose_ends_the_iteration():
    queue: AsyncQueue[int] = AsyncQueue(1, 2, 3)

    await queue.aclose()

    # closing a generator ends it - a closed queue hands out nothing more
    elements: list[int] = []
    async for element in queue:
        elements.append(element)

    assert elements == []
    assert queue.is_finished

    with raises(StopAsyncIteration):
        await queue.next()


@mark.asyncio
async def test_finish_keeps_buffered_elements_available():
    queue: AsyncQueue[int] = AsyncQueue(1, 2, 3)

    # unlike `aclose`, finishing is the producer saying it has nothing more to
    # send - what the consumer was already given stays deliverable
    queue.finish()

    elements: list[int] = []
    async for element in queue:
        elements.append(element)

    assert elements == [1, 2, 3]
    assert queue.is_finished


@mark.asyncio
async def test_aclose_rejects_further_elements():
    queue: AsyncQueue[int] = AsyncQueue()

    await queue.aclose()

    with raises(RuntimeError):
        queue.enqueue(1)


@mark.asyncio
async def test_aclose_is_idempotent():
    queue: AsyncQueue[int] = AsyncQueue(1)

    await queue.aclose()
    await queue.aclose()

    with raises(StopAsyncIteration):
        await queue.next()


@mark.asyncio
async def test_athrow_drops_buffered_elements():
    queue: AsyncQueue[int] = AsyncQueue(1, 2, 3)

    with raises(FakeException):
        await queue.athrow(FakeException())

    # an exception ends the iteration where it stands - nothing is left to
    # deliver the buffered elements to
    with raises(FakeException):
        await queue.next()


@mark.asyncio
async def test_asend_resumes_iteration_and_rejects_values():
    queue: AsyncQueue[int] = AsyncQueue(7)

    assert await queue.asend() == 7

    with raises(AssertionError):
        await queue.asend(1)  # pyright: ignore[reportArgumentType]


@mark.asyncio
async def test_aclosing_ends_the_queue():
    queue: AsyncQueue[int] = AsyncQueue(1, 2)

    async with ctx.closing(queue) as elements:
        assert await elements.asend() == 1

    assert queue.is_finished
    # leaving the iteration drops the element which was left buffered - there is
    # no consumer left to deliver it to
    with raises(StopAsyncIteration):
        queue.pending_next()


@mark.asyncio
async def test_cancelled_consumer_does_not_lose_delivered_element():
    queue = AsyncQueue[int]()
    consumer = create_task(queue.next())
    await sleep(0)  # consumer waits

    queue.enqueue(1)  # delivered to the waiting consumer
    consumer.cancel()  # cancelled before it could resume
    with raises(CancelledError):
        await consumer

    queue.enqueue(2)
    assert queue.pending_next() == 1
    assert queue.pending_next() == 2


@mark.asyncio
@mark.parametrize("closing", ["aclose", "athrow"])
async def test_closed_queue_does_not_restore_delivered_element(closing: str):
    queue = AsyncQueue[int]()
    consumer = create_task(queue.next())
    await sleep(0)  # consumer waits

    queue.enqueue(1)  # delivered to the waiting consumer
    if closing == "aclose":
        await queue.aclose()

    else:
        with raises(ValueError):
            await queue.athrow(ValueError("closed"))

    consumer.cancel()  # cancelled before it could resume
    with raises(CancelledError):
        await consumer

    # closed by the consumer side - nothing is handed out anymore
    with raises(StopAsyncIteration if closing == "aclose" else ValueError):
        queue.pending_next()


@mark.asyncio
async def test_finished_queue_restores_delivered_element():
    queue = AsyncQueue[int]()
    consumer = create_task(queue.next())
    await sleep(0)

    queue.enqueue(1)
    queue.finish()  # the producer is done, accepted elements are kept
    consumer.cancel()
    with raises(CancelledError):
        await consumer

    assert queue.pending_next() == 1
    with raises(StopAsyncIteration):
        queue.pending_next()


@mark.asyncio
async def test_finish_from_another_thread_releases_waiting_consumer():
    queue = AsyncQueue[int]()
    consumer = create_task(queue.next())
    await sleep(0)

    await to_thread(queue.finish)

    with raises(StopAsyncIteration):
        await consumer

    assert queue.is_finished


@mark.asyncio
async def test_finish_without_running_loop_is_immediate():
    queue = AsyncQueue[int]()
    queue.finish()  # the loop of the queue is running, here - same thread
    assert queue.is_finished


@mark.asyncio
@mark.parametrize("limit", [0, -1])
async def test_queue_rejects_limit_which_is_not_positive(limit: int) -> None:
    # raised rather than asserted - an optimized build would make a queue dropping everything
    with raises(ValueError):
        AsyncQueue[int](limit=limit)


@mark.asyncio
async def test_queue_rejects_second_consumer() -> None:
    queue: AsyncQueue[int] = AsyncQueue()
    first: Task[int] = create_task(anext(queue))
    await sleep(0)

    with raises(RuntimeError):
        await anext(queue)

    queue.enqueue(1)
    assert await first == 1


def _traceback_depth(error: BaseException) -> int:
    depth: int = 0
    traceback = error.__traceback__
    while traceback is not None:
        depth += 1
        traceback = traceback.tb_next

    return depth


@mark.asyncio
async def test_finished_queue_raises_with_a_stable_traceback() -> None:
    queue: AsyncQueue[int] = AsyncQueue()
    queue.finish(ValueError("finished"))
    depths: list[int] = []
    for _ in range(3):
        with raises(ValueError) as failure:
            await anext(queue)

        depths.append(_traceback_depth(failure.value))

    assert len(set(depths)) == 1
