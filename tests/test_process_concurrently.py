from asyncio import CancelledError, Event, current_task, sleep, timeout
from collections import deque
from collections.abc import AsyncGenerator, Iterable

from pytest import mark, raises

from haiway import AsyncQueue, ctx
from haiway.helpers.concurrent import process_concurrently


class FakeException(Exception):
    pass


async def elements_source(
    elements: Iterable[int] | None = None,
    exception: Exception | None = None,
) -> AsyncGenerator[int]:
    pending = deque(elements or [])
    while pending:
        yield pending.popleft()

    if exception is not None:
        raise exception


@mark.asyncio
async def test_processes_all_elements():
    processed: list[int] = []

    async def handler(element: int) -> None:
        processed.append(element)

    source = elements_source(range(10))
    await process_concurrently(source, handler)
    assert sorted(processed) == list(range(10))


@mark.asyncio
async def test_processes_elements_concurrently():
    processed: list[int] = []
    completion_order: list[int] = []

    async def handler(element: int) -> None:
        # Simulate varying processing times
        await sleep(0.1 if element % 2 == 0 else 0.05)
        processed.append(element)
        completion_order.append(element)

    source = elements_source(range(10))
    await process_concurrently(source, handler, concurrent_tasks=3)
    assert sorted(processed) == list(range(10))
    # Odd numbers should complete before even numbers due to sleep times
    assert completion_order != sorted(completion_order)


@mark.asyncio
async def test_handles_empty_source():
    processed: list[int] = []

    async def handler(element: int) -> None:
        processed.append(element)

    source = elements_source([])
    await process_concurrently(source, handler)
    assert processed == []


@mark.asyncio
async def test_propagates_handler_exceptions():
    async def handler(element: int) -> None:
        if element == 3:
            raise FakeException("Test exception")

    source = elements_source(range(10))
    with raises(FakeException):
        await process_concurrently(source, handler)


@mark.asyncio
async def test_cancelled_task_does_not_mask_failure():
    async def handler(element: int) -> None:
        if element == 0:
            task = current_task()
            assert task is not None
            task.cancel()
            await sleep(0)
            return

        await sleep(0.05)
        raise FakeException("boom")

    with raises(FakeException):
        await process_concurrently([0, 1], handler, concurrent_tasks=2)


@mark.asyncio
async def test_ignores_handler_exceptions_when_configured():
    processed: list[int] = []

    async def handler(element: int) -> None:
        if element == 3:
            raise FakeException("Test exception")
        processed.append(element)

    source = elements_source([0, 1, 2, 3, 4, 5])
    await process_concurrently(source, handler, ignore_exceptions=True)
    assert sorted(processed) == [0, 1, 2, 4, 5]


@mark.asyncio
async def test_ignore_exceptions_inside_scope_task_group():
    processed: list[int] = []

    async def handler(element: int) -> None:
        if element % 2 == 1:
            raise FakeException("odd")
        processed.append(element)

    source = elements_source(range(10))
    async with ctx.scope("tg_process"):
        await process_concurrently(source, handler, ignore_exceptions=True)

    # Only even elements processed; odd failures ignored without cancelling group
    assert sorted(processed) == [0, 2, 4, 6, 8]


@mark.asyncio
async def test_handles_source_exception():
    processed: list[int] = []

    async def handler(element: int) -> None:
        processed.append(element)

    source = elements_source([1, 2], FakeException("Source exception"))

    with raises(FakeException):
        await process_concurrently(source, handler)
    assert sorted(processed) == [1, 2]


@mark.asyncio
async def test_cancels_running_tasks_on_cancellation():
    processed: list[int] = []
    started: list[int] = []
    first_started = Event()

    async def slow_handler(element: int) -> None:
        started.append(element)
        first_started.set()
        try:
            await sleep(10)  # Long sleep that should be cancelled
            processed.append(element)

        except CancelledError:
            # Just to track cancellation, not needed in real code
            pass

    # Run the process with cancellation
    with raises(CancelledError):
        task = ctx.spawn(
            process_concurrently,
            elements_source(range(10)),
            slow_handler,
        )
        # Wait deterministically until at least one handler actually started
        async with timeout(5):
            await first_started.wait()
        # Cancel the main task
        task.cancel()
        await task
    # Some tasks should have started but none should have completed
    assert len(started) > 0
    assert processed == []


@mark.asyncio
async def test_respects_concurrency_limit():
    # Test that only the specified number of tasks run concurrently
    currently_running: set[int] = set()
    max_concurrent: int = 0
    processed: list[int] = []

    async def tracking_handler(element: int) -> None:
        nonlocal max_concurrent
        currently_running.add(element)
        max_concurrent = max(max_concurrent, len(currently_running))
        await sleep(0.05)  # Short sleep to allow concurrency
        currently_running.remove(element)
        processed.append(element)

    source = elements_source(range(10))
    await process_concurrently(source, tracking_handler, concurrent_tasks=3)
    assert max_concurrent <= 3
    assert sorted(processed) == list(range(10))
    assert currently_running == set()


@mark.asyncio
async def test_processes_elements_from_queue():
    queue = AsyncQueue[int]()
    processed: list[int] = []

    async def handler(element: int) -> None:
        processed.append(element + 1)

    # Start processing in the background
    task = ctx.spawn(process_concurrently, queue, handler)
    # Add elements to the queue
    for i in range(5):
        queue.enqueue(i)
        await sleep(0.01)  # Small delay to ensure processing happens

    queue.finish()
    await task
    assert sorted(processed) == list(range(1, 6))


@mark.asyncio
async def test_closes_the_source_when_exhausted():
    closed: list[str] = []

    async def tracked_source() -> AsyncGenerator[int]:
        try:
            for element in range(3):
                yield element

        finally:
            closed.append("source")

    async def handler(element: int) -> None:
        pass

    await process_concurrently(tracked_source(), handler)
    assert closed == ["source"]


@mark.asyncio
async def test_closes_the_source_when_a_handler_fails():
    closed: list[str] = []

    async def tracked_source() -> AsyncGenerator[int]:
        try:
            for element in range(100):
                yield element

        finally:
            closed.append("source")

    async def handler(element: int) -> None:
        raise FakeException("Test exception")

    with raises(FakeException):
        await process_concurrently(tracked_source(), handler)

    # the source is released where the processing stopped, not left to the
    # collector - it is what makes an `AsyncGenerator` source a requirement
    assert closed == ["source"]


@mark.asyncio
async def test_closes_the_source_when_cancelled():
    closed: list[str] = []
    started = Event()

    async def tracked_source() -> AsyncGenerator[int]:
        try:
            for element in range(100):
                yield element

        finally:
            closed.append("source")

    async def slow_handler(element: int) -> None:
        started.set()
        await sleep(10)

    async with ctx.scope("cancelled_source"):
        task = ctx.spawn(process_concurrently, tracked_source(), slow_handler)
        await started.wait()
        task.cancel()
        with raises(CancelledError):
            await task

    assert closed == ["source"]


@mark.asyncio
async def test_rejects_source_which_can_not_be_released():
    class AsyncIterableSource:
        """An async source without `aclose`, which could not be released."""

        def __aiter__(self) -> AsyncIterableSource:
            return self

        async def __anext__(self) -> int:
            return 1

    async def handler(element: int) -> None:
        pass

    with raises(TypeError):
        await process_concurrently(
            AsyncIterableSource(),  # pyright: ignore[reportArgumentType]
            handler,
        )


@mark.asyncio
async def test_raises_handler_error_while_awaiting_a_slow_source():
    async def slow_source() -> AsyncGenerator[int]:
        for element in range(10):
            await sleep(0.01)  # suspends between the elements
            yield element

    async def handler(element: int) -> None:
        if element == 0:
            raise FakeException("handler failed")

        await sleep(10)  # keep the remaining slots busy

    # the task group aborts on the failure while the source is awaited, and the
    # handler error has to surface instead of that cancellation or an exception group
    with raises(FakeException, match="handler failed"):
        await process_concurrently(slow_source(), handler, concurrent_tasks=4)
