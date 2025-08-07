from asyncio import CancelledError, sleep

from pytest import mark, raises

from haiway import ctx
from haiway.helpers.concurrent import concurrently


class FakeException(Exception):
    pass


@mark.asyncio
async def test_processes_all_coroutines():
    async def coro1() -> int:
        return 10

    async def coro2() -> int:
        return 20

    async def coro3() -> int:
        return 30

    coroutines = [coro1(), coro2(), coro3()]
    results = await concurrently(coroutines)
    assert list(results) == [10, 20, 30]


@mark.asyncio
async def test_preserves_order():
    async def fast_coro(value: int) -> int:
        await sleep(0.05)
        return value * 2

    async def slow_coro(value: int) -> int:
        await sleep(0.1)
        return value * 3

    # Mix fast and slow coroutines
    coroutines = [
        slow_coro(1),  # Should be 3
        fast_coro(2),  # Should be 4
        slow_coro(3),  # Should be 9
        fast_coro(4),  # Should be 8
    ]

    results = await concurrently(coroutines, concurrent_tasks=2)
    # Results should be in the same order as inputs despite different processing times
    assert list(results) == [3, 4, 9, 8]


@mark.asyncio
async def test_handles_empty_collection():
    results = await concurrently([])
    assert list(results) == []


@mark.asyncio
async def test_propagates_coroutine_exceptions():
    async def good_coro() -> int:
        return 42

    async def bad_coro() -> int:
        raise FakeException("Test exception")

    coroutines = [good_coro(), bad_coro(), good_coro()]
    with raises(FakeException):
        await concurrently(coroutines)


@mark.asyncio
async def test_returns_exceptions_when_configured():
    async def good_coro(value: int) -> int:
        return value * 2

    async def bad_coro() -> int:
        raise FakeException("Test exception")

    coroutines = [
        good_coro(1),  # Should return 2
        bad_coro(),  # Should return FakeException
        good_coro(3),  # Should return 6
        bad_coro(),  # Should return FakeException
    ]

    results = await concurrently(coroutines, return_exceptions=True)

    # Check that we got all results
    assert len(results) == 4

    # Check successful results
    assert results[0] == 2
    assert results[2] == 6

    # Check that failed coroutines returned exceptions
    assert isinstance(results[1], FakeException)
    assert str(results[1]) == "Test exception"
    assert isinstance(results[3], FakeException)
    assert str(results[3]) == "Test exception"


@mark.asyncio
async def test_cancels_running_tasks_on_cancellation():
    started: list[int] = []
    completed: list[int] = []

    async def slow_coro(element: int) -> int:
        started.append(element)
        try:
            await sleep(10)  # Long sleep that should be cancelled
            completed.append(element)
            return element * 2
        except CancelledError:
            raise

    coroutines = [slow_coro(i) for i in range(5)]

    # Run the process with cancellation
    with raises(CancelledError):
        task = ctx.spawn(
            concurrently,
            coroutines,
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

    async def tracking_coro(element: int) -> int:
        nonlocal max_concurrent
        currently_running.add(element)
        max_concurrent = max(max_concurrent, len(currently_running))
        await sleep(0.05)  # Short sleep to allow concurrency
        currently_running.remove(element)
        return element * 2

    coroutines = [tracking_coro(i) for i in range(10)]
    results = await concurrently(coroutines, concurrent_tasks=3)
    assert max_concurrent <= 3
    assert list(results) == [i * 2 for i in range(10)]
    assert currently_running == set()


@mark.asyncio
async def test_works_with_different_return_types():
    async def int_coro() -> int:
        return 42

    async def str_coro() -> str:
        return "hello"

    async def float_coro() -> float:
        return 3.14

    coroutines = [int_coro(), str_coro(), float_coro()]
    results = await concurrently(coroutines)
    assert results[0] == 42
    assert results[1] == "hello"
    assert results[2] == 3.14


@mark.asyncio
async def test_handles_mixed_success_and_failure():
    async def success_coro(value: int) -> str:
        return f"Success: {value}"

    async def failure_coro(value: int) -> str:
        raise FakeException(f"Failed on {value}")

    coroutines = [
        success_coro(1),
        failure_coro(2),
        success_coro(3),
        failure_coro(4),
        success_coro(5),
    ]

    results = await concurrently(coroutines, return_exceptions=True)

    assert results[0] == "Success: 1"
    assert isinstance(results[1], FakeException)
    assert str(results[1]) == "Failed on 2"
    assert results[2] == "Success: 3"
    assert isinstance(results[3], FakeException)
    assert str(results[3]) == "Failed on 4"
    assert results[4] == "Success: 5"


@mark.asyncio
async def test_exception_details_preserved():
    class CustomError(Exception):
        def __init__(self, code: int, message: str):
            self.code = code
            super().__init__(message)

    async def good_coro() -> int:
        return 100

    async def bad_coro() -> int:
        raise CustomError(404, "Not found")

    coroutines = [good_coro(), bad_coro(), good_coro()]
    results = await concurrently(coroutines, return_exceptions=True)

    assert results[0] == 100
    assert isinstance(results[1], CustomError)
    assert results[1].code == 404
    assert str(results[1]) == "Not found"
    assert results[2] == 100


@mark.asyncio
async def test_works_with_async_generators_as_source():
    async def make_coro(value: int):
        return value * 2

    async def async_generator():
        for i in range(5):
            yield make_coro(i)

    results = await concurrently(async_generator())
    assert list(results) == [0, 2, 4, 6, 8]


@mark.asyncio
async def test_single_coroutine():
    async def single_coro() -> str:
        await sleep(0.01)
        return "single result"

    results = await concurrently([single_coro()])
    assert list(results) == ["single result"]


@mark.asyncio
async def test_no_tasks_with_return_exceptions_false():
    """Test the edge case where no tasks are spawned with return_exceptions=False"""
    results = await concurrently([], return_exceptions=False)
    assert list(results) == []


@mark.asyncio
async def test_no_tasks_with_return_exceptions_true():
    """Test the edge case where no tasks are spawned with return_exceptions=True"""
    results = await concurrently([], return_exceptions=True)
    assert list(results) == []


@mark.asyncio
async def test_concurrent_limit_larger_than_coroutines():
    """Test when concurrent_tasks is larger than the number of coroutines"""

    async def simple_coro(value: int) -> int:
        return value

    coroutines = [simple_coro(i) for i in range(3)]
    results = await concurrently(coroutines, concurrent_tasks=10)
    assert list(results) == [0, 1, 2]


@mark.asyncio
async def test_all_coroutines_fail_with_return_exceptions():
    """Test when all coroutines fail but return_exceptions=True"""

    async def failing_coro(value: int) -> int:
        raise FakeException(f"Error {value}")

    coroutines = [failing_coro(i) for i in range(3)]
    results = await concurrently(coroutines, return_exceptions=True)

    assert len(results) == 3
    for i, result in enumerate(results):
        assert isinstance(result, FakeException)
        assert str(result) == f"Error {i}"
