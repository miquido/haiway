from asyncio import CancelledError, sleep
from collections import deque
from collections.abc import AsyncIterator, Iterable

from pytest import mark, raises

from haiway import AsyncQueue, ctx
from haiway.helpers.concurrent import process_concurrently


class FakeException(Exception):
    pass


class Source:
    def __init__(
        self,
        elements: Iterable[int] | None = None,
        exception: Exception | None = None,
    ) -> None:
        self.elements = deque(elements or [])
        self.exception = exception

    def __aiter__(self) -> AsyncIterator[int]:
        return self

    async def __anext__(self) -> int:
        if not self.elements:
            if self.exception:
                raise self.exception

            raise StopAsyncIteration

        return self.elements.popleft()


@mark.asyncio
async def test_processes_all_elements():
    processed: list[int] = []

    async def handler(element: int) -> None:
        processed.append(element)

    source = Source(range(10))
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

    source = Source(range(10))
    await process_concurrently(source, handler, concurrent_tasks=3)
    assert sorted(processed) == list(range(10))
    # Odd numbers should complete before even numbers due to sleep times
    assert completion_order != sorted(completion_order)


@mark.asyncio
async def test_handles_empty_source():
    processed: list[int] = []

    async def handler(element: int) -> None:
        processed.append(element)

    source = Source([])
    await process_concurrently(source, handler)
    assert processed == []


@mark.asyncio
async def test_propagates_handler_exceptions():
    async def handler(element: int) -> None:
        if element == 3:
            raise FakeException("Test exception")

    source = Source(range(10))
    with raises(FakeException):
        await process_concurrently(source, handler)


@mark.asyncio
async def test_ignores_handler_exceptions_when_configured():
    processed: list[int] = []

    async def handler(element: int) -> None:
        if element == 3:
            raise FakeException("Test exception")
        processed.append(element)

    source = Source([0, 1, 2, 3, 4, 5])
    await process_concurrently(source, handler, ignore_exceptions=True)
    assert sorted(processed) == [0, 1, 2, 4, 5]


@mark.asyncio
async def test_handles_source_exception():
    processed: list[int] = []

    async def handler(element: int) -> None:
        processed.append(element)

    source = Source([1, 2], FakeException("Source exception"))

    with raises(FakeException):
        await process_concurrently(source, handler)
    assert sorted(processed) == [1, 2]


@mark.asyncio
async def test_cancels_running_tasks_on_cancellation():
    processed: list[int] = []
    started: list[int] = []

    async def slow_handler(element: int) -> None:
        started.append(element)
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
            Source(range(10)),
            slow_handler,
        )
        # Give some time for tasks to start
        await sleep(0.1)
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

    source = Source(range(10))
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
