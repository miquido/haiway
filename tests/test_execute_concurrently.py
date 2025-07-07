from asyncio import CancelledError, sleep
from collections.abc import Sequence
from typing import Any

from pytest import mark, raises

from haiway import ctx
from haiway.helpers.concurrent import execute_concurrently


class FakeException(Exception):
    pass


@mark.asyncio
async def test_processes_all_elements():
    results: Sequence[int] = []

    async def handler(element: int) -> int:
        return element * 2

    elements = list(range(10))
    results = await execute_concurrently(
        handler,
        elements,
    )
    assert list(results) == [i * 2 for i in range(10)]


@mark.asyncio
async def test_preserves_order():
    async def handler(element: int) -> int:
        # Simulate varying processing times
        await sleep(0.1 if element % 2 == 0 else 0.05)
        return element * 2

    elements = list(range(10))
    results = await execute_concurrently(handler, elements, concurrent_tasks=3)
    # Results should be in the same order as inputs despite different processing times
    assert list(results) == [i * 2 for i in range(10)]


@mark.asyncio
async def test_handles_empty_collection():
    async def handler(element: int) -> int:
        return element * 2

    results = await execute_concurrently(
        handler,
        [],
    )
    assert list(results) == []


@mark.asyncio
async def test_propagates_handler_exceptions():
    async def handler(element: int) -> int:
        if element == 3:
            raise FakeException("Test exception")
        return element * 2

    elements = list(range(10))
    with raises(FakeException):
        await execute_concurrently(
            handler,
            elements,
        )


@mark.asyncio
async def test_returns_exceptions_when_configured():
    async def handler(element: int) -> int:
        if element == 3:
            raise FakeException("Test exception")
        return element * 2

    elements = list(range(6))
    results = await execute_concurrently(handler, elements, return_exceptions=True)

    # Check that we got all results
    assert len(results) == 6

    # Check that element 3 returned an exception
    assert isinstance(results[3], FakeException)
    assert str(results[3]) == "Test exception"

    # Check that other elements returned correct values
    for i in [0, 1, 2, 4, 5]:
        assert results[i] == i * 2


@mark.asyncio
async def test_cancels_running_tasks_on_cancellation():
    started: list[int] = []
    completed: list[int] = []

    async def slow_handler(element: int) -> int:
        started.append(element)
        try:
            await sleep(10)  # Long sleep that should be cancelled
            completed.append(element)
            return element * 2
        except CancelledError:
            raise

    # Run the process with cancellation
    with raises(CancelledError):
        task = ctx.spawn(
            execute_concurrently,
            slow_handler,
            list(range(10)),
        )
        # Give some time for tasks to start
        await sleep(0.1)
        # Cancel the main task
        task.cancel()
        await task

    # Some tasks should have started but none should have completed
    assert len(started) > 0
    assert completed == []


@mark.asyncio
async def test_respects_concurrency_limit():
    currently_running: set[int] = set()
    max_concurrent: int = 0

    async def tracking_handler(element: int) -> int:
        nonlocal max_concurrent
        currently_running.add(element)
        max_concurrent = max(max_concurrent, len(currently_running))
        await sleep(0.05)  # Short sleep to allow concurrency
        currently_running.remove(element)
        return element * 2

    elements = list(range(10))
    results = await execute_concurrently(tracking_handler, elements, concurrent_tasks=3)
    assert max_concurrent <= 3
    assert list(results) == [i * 2 for i in range(10)]
    assert currently_running == set()


@mark.asyncio
async def test_works_with_different_types():
    async def handler(element: str) -> int:
        return len(element)

    words = ["hello", "world", "test", "async"]
    results = await execute_concurrently(
        handler,
        words,
    )
    assert list(results) == [5, 5, 4, 5]


@mark.asyncio
async def test_handles_mixed_success_and_failure():
    async def handler(element: int) -> str:
        if element % 3 == 0:
            raise FakeException(f"Failed on {element}")
        return f"Success: {element}"

    elements = list(range(10))
    results = await execute_concurrently(handler, elements, return_exceptions=True)

    for i, result in enumerate(results):
        if i % 3 == 0:
            assert isinstance(result, FakeException)
            assert str(result) == f"Failed on {i}"
        else:
            assert result == f"Success: {i}"


@mark.asyncio
async def test_works_with_sets_and_tuples():
    async def handler(element: int) -> int:
        return element**2

    # Test with set (unordered collection)
    elements_set = {1, 2, 3, 4, 5}
    results_set = await execute_concurrently(
        handler,
        elements_set,
    )
    # Convert to set for comparison since order isn't guaranteed with input sets
    assert set(results_set) == {1, 4, 9, 16, 25}

    # Test with tuple (ordered collection)
    elements_tuple = (1, 2, 3, 4, 5)
    results_tuple = await execute_concurrently(
        handler,
        elements_tuple,
    )
    assert list(results_tuple) == [1, 4, 9, 16, 25]


@mark.asyncio
async def test_exception_details_preserved():
    class CustomError(Exception):
        def __init__(self, code: int, message: str):
            self.code = code
            super().__init__(message)

    async def handler(element: int) -> Any:
        if element == 2:
            raise CustomError(404, "Not found")
        return element

    results = await execute_concurrently(handler, [1, 2, 3], return_exceptions=True)

    assert results[0] == 1
    assert isinstance(results[1], CustomError)
    assert results[1].code == 404
    assert str(results[1]) == "Not found"
    assert results[2] == 3
