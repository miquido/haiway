from asyncio import CancelledError, create_task, gather, sleep
from datetime import timedelta
from time import monotonic

import pytest

from haiway import throttle


@pytest.mark.asyncio
async def test_calls_within_limit_run_immediately() -> None:
    @throttle(limit=3, period=5)
    async def call(value: int) -> int:
        return value

    started = monotonic()
    results = [await call(index) for index in range(3)]

    assert results == [0, 1, 2]
    assert monotonic() - started < 1


@pytest.mark.asyncio
async def test_call_over_limit_waits_for_period() -> None:
    @throttle(limit=2, period=0.5)
    async def call() -> float:
        return monotonic()

    started = monotonic()
    times = [await call() for _ in range(3)]

    assert times[1] - started < 0.25
    assert times[2] - started >= 0.4


@pytest.mark.asyncio
async def test_timedelta_period_is_supported() -> None:
    @throttle(limit=1, period=timedelta(milliseconds=300))
    async def call() -> float:
        return monotonic()

    first = await call()
    second = await call()

    assert second - first >= 0.25


@pytest.mark.asyncio
async def test_bare_decorator_defaults_to_one_call_per_second() -> None:
    @throttle
    async def call() -> float:
        return monotonic()

    first = await call()
    second = await call()

    assert second - first >= 0.9


@pytest.mark.asyncio
async def test_concurrent_calls_respect_limit() -> None:
    times: list[float] = []

    @throttle(limit=2, period=0.3)
    async def call() -> None:
        times.append(monotonic())

    await gather(*(call() for _ in range(4)))

    assert len(times) == 4
    times.sort()
    # no window of `period` may contain more than `limit` calls
    assert times[2] - times[0] >= 0.25
    assert times[3] - times[1] >= 0.25


@pytest.mark.asyncio
async def test_window_expires_and_allows_new_calls() -> None:
    @throttle(limit=1, period=0.1)
    async def call() -> float:
        return monotonic()

    await call()
    await sleep(0.15)

    started = monotonic()
    await call()

    assert monotonic() - started < 0.1


@pytest.mark.asyncio
async def test_preserves_arguments_result_and_metadata() -> None:
    @throttle(limit=10, period=1)
    async def documented(a: int, *, b: int) -> int:
        """Docstring."""
        return a + b

    assert await documented(1, b=2) == 3
    assert documented.__name__ == "documented"
    assert documented.__doc__ == "Docstring."


@pytest.mark.asyncio
async def test_exceptions_propagate_and_still_count() -> None:
    @throttle(limit=1, period=0.3)
    async def failing() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await failing()

    started = monotonic()
    with pytest.raises(RuntimeError, match="boom"):
        await failing()

    assert monotonic() - started >= 0.2


@pytest.mark.asyncio
async def test_cancellation_while_waiting_propagates() -> None:
    calls: list[int] = []

    @throttle(limit=1, period=5)
    async def call(value: int) -> None:
        calls.append(value)

    await call(1)
    task = create_task(call(2))
    await sleep(0.05)
    task.cancel()

    with pytest.raises(CancelledError):
        await task

    assert calls == [1]


def test_rejects_sync_functions() -> None:
    def sync_function() -> None:
        return None

    with pytest.raises(AssertionError):
        throttle(sync_function)  # pyright: ignore[reportArgumentType, reportCallIssue]
