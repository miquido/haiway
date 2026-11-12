from asyncio import CancelledError, create_task, gather, sleep
from datetime import timedelta
from time import monotonic

import pytest

from haiway import throttle
from haiway.helpers import throttling


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


@pytest.mark.asyncio
async def test_cancelled_waiting_calls_give_their_reservations_back() -> None:
    @throttle(limit=1, period=0.3)
    async def call() -> float:
        return monotonic()

    first: float = await call()
    waiting = [create_task(call()) for _ in range(3)]
    await sleep(0)
    for task in waiting:
        task.cancel()

    await gather(*waiting, return_exceptions=True)
    # one period after the call which ran, not after the ones which never did
    started: float = await call()
    assert 0.25 <= started - first < 0.5


@pytest.mark.asyncio
async def test_cancelled_reservation_does_not_open_extra_slot() -> None:
    @throttle(limit=2, period=0.3)
    async def call() -> float:
        return monotonic()

    first: float = await call()
    await call()
    waiting = create_task(call())
    await sleep(0)
    waiting.cancel()
    await gather(waiting, return_exceptions=True)
    # both slots of the period are still taken by the calls which ran
    assert await call() - first >= 0.25


def test_rejects_sync_functions() -> None:
    def sync_function() -> None:
        return None

    with pytest.raises(AssertionError):
        throttle(sync_function)  # pyright: ignore[reportArgumentType, reportCallIssue]


@pytest.mark.parametrize("limit", [0, -1])
def test_rejects_non_positive_limit(limit: int):
    async def call() -> None:
        pass

    with pytest.raises(AssertionError):
        throttle(limit=limit)(call)


class _FakeClock:
    # time of the throttle moved by the test - a sleep ends once the time is moved
    # past it, and fails instead of hanging when it never is
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now: float = 0.0
        self.delays: list[float] = []
        monkeypatch.setattr(throttling, "monotonic", lambda: self.now)
        monkeypatch.setattr(throttling, "sleep", self.sleep)

    async def sleep(self, delay: float) -> None:
        self.delays.append(delay)
        until: float = self.now + delay
        for _ in range(1000):
            if self.now >= until:
                return

            await sleep(0)

        raise AssertionError(f"Sleep until {until} never ended, time is {self.now}")

    async def settle(self) -> None:
        for _ in range(5):
            await sleep(0)


@pytest.mark.asyncio
async def test_reservations_stay_ordered_after_cancellations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock(monkeypatch)

    @throttle(limit=2, period=10)
    async def call() -> float:
        return clock.now

    assert [await call(), await call()] == [0.0, 0.0]
    cancelled = [create_task(call()), create_task(call())]  # reserved for 10
    later = create_task(call())  # reserved for 20
    await clock.settle()
    for task in cancelled:
        task.cancel()

    await gather(*cancelled, return_exceptions=True)
    clock.now = 10.0
    assert await call() == 10.0  # a slot given back by a cancelled call
    clock.delays.clear()
    # starts at 10, 10 and 20 keep each period within two - no waiting
    assert await call() == 10.0
    assert clock.delays == []
    pending = create_task(call())
    await clock.settle()
    # 10 is taken twice now - the next slot is a period after the first of them
    assert clock.delays == [10.0]
    clock.now = 20.0
    assert await later == 20.0
    assert await pending == 20.0


@pytest.mark.asyncio
async def test_slot_given_back_is_taken_before_later_reservations(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = _FakeClock(monkeypatch)

    @throttle(limit=1, period=10)
    async def call() -> float:
        return clock.now

    assert await call() == 0.0
    given_back = create_task(call())  # reserved for 10
    later = create_task(call())  # reserved for 20
    await clock.settle()
    given_back.cancel()
    await gather(given_back, return_exceptions=True)
    clock.delays.clear()
    taking = create_task(call())
    await clock.settle()
    # 10 is free again and a period apart from both 0 and 20 - not 30
    assert clock.delays == [10.0]
    clock.now = 10.0
    assert await taking == 10.0
    clock.now = 20.0
    assert await later == 20.0
