from asyncio import CancelledError, Task, sleep
from unittest import TestCase

from pytest import MonkeyPatch, mark, raises

from haiway import retry
from haiway.helpers import retries


class FakeException(Exception):
    pass


@mark.asyncio
async def test_returns_value_without_errors():
    executions: int = 0

    @retry
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        return value

    assert compute("expected") == "expected"
    assert executions == 1


@mark.asyncio
async def test_retries_with_errors():
    executions: int = 0

    @retry
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException()
        else:
            return value

    assert compute("expected") == "expected"
    assert executions == 2


@mark.asyncio
async def test_logs_issue_with_errors():
    executions: int = 0
    test_case = TestCase()

    @retry
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException("fake")
        else:
            return value

    with test_case.assertLogs() as logs:
        compute("expected")
        assert executions == 2
        assert logs.output == [
            f"ERROR:root:Attempting to retry {compute.__name__}"
            f" which failed due to an error: {FakeException('fake')}"
        ]


@mark.asyncio
async def test_fails_with_exceeding_errors():
    executions: int = 0

    @retry(limit=1)
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        compute("expected")
    assert executions == 2


@mark.asyncio
async def test_fails_with_cancellation():
    executions: int = 0

    @retry(limit=1)
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise CancelledError()

    with raises(CancelledError):
        compute("expected")
    assert executions == 1


@mark.asyncio
async def test_retries_with_selected_errors():
    executions: int = 0

    @retry
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException()
        else:
            return value

    assert compute("expected") == "expected"
    assert executions == 2


@mark.asyncio
async def test_fails_with_not_selected_errors():
    executions: int = 0

    def should_retry(exc: Exception) -> bool:
        return isinstance(exc, ValueError)

    @retry(catching=should_retry)
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        compute("expected")

    assert executions == 1


@mark.asyncio
async def test_retries_with_custom_predicate():
    executions: int = 0
    observed: list[Exception] = []

    def should_retry(exc: Exception) -> bool:
        observed.append(exc)
        return isinstance(exc, FakeException)

    @retry(catching=should_retry)
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException("retryable")
        else:
            return value

    assert compute("expected") == "expected"
    assert executions == 2
    assert len(observed) == 1
    assert isinstance(observed[0], FakeException)


@mark.asyncio
async def test_async_returns_value_without_errors():
    executions: int = 0

    @retry
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        return value

    assert await compute("expected") == "expected"
    assert executions == 1


@mark.asyncio
async def test_async_retries_with_errors():
    executions: int = 0

    @retry
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException()
        else:
            return value

    assert await compute("expected") == "expected"
    assert executions == 2


@mark.asyncio
async def test_async_fails_with_exceeding_errors():
    executions: int = 0

    @retry(limit=1)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        await compute("expected")
    assert executions == 2


@mark.asyncio
async def test_async_fails_with_cancellation():
    executions: int = 0

    @retry(limit=1)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise CancelledError()

    with raises(CancelledError):
        await compute("expected")
    assert executions == 1


@mark.asyncio
async def test_async_fails_when_cancelled():
    executions: int = 0

    @retry(limit=1)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        await sleep(1)
        return value

    with raises(CancelledError):
        task = Task(compute("expected"))
        await sleep(0.02)
        task.cancel()
        await task
    assert executions == 1


@mark.asyncio
async def test_async_uses_delay_with_errors(monkeypatch: MonkeyPatch):
    executions: int = 0
    delays: list[float] = []

    async def record_delay(delay: float, /) -> None:
        delays.append(delay)

    # assert the requested delays instead of measuring wall clock time
    monkeypatch.setattr(retries, "sleep", record_delay)

    @retry(limit=2, delay=0.05)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        await compute("expected")
    assert delays == [0.05, 0.05]
    assert executions == 3


@mark.asyncio
async def test_async_uses_computed_delay_with_errors(monkeypatch: MonkeyPatch):
    executions: int = 0
    delays: list[float] = []

    async def record_delay(delay: float, /) -> None:
        delays.append(delay)

    # assert the requested delays instead of measuring wall clock time
    monkeypatch.setattr(retries, "sleep", record_delay)

    @retry(limit=2, delay=lambda attempt, _: attempt * 0.035)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        await compute("expected")
    assert delays == [0.035, 0.07]
    assert executions == 3


@mark.asyncio
async def test_async_logs_issue_with_errors():
    executions: int = 0
    test_case = TestCase()

    @retry
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException("fake")
        else:
            return value

    with test_case.assertLogs() as logs:
        await compute("expected")
        assert executions == 2
        assert logs.output[0].startswith(
            f"ERROR:root:Attempting to retry {compute.__name__} which failed due to an error"
        )


@mark.asyncio
async def test_async_retries_with_selected_errors():
    executions: int = 0

    @retry
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException()
        else:
            return value

    assert await compute("expected") == "expected"
    assert executions == 2


@mark.asyncio
async def test_async_fails_with_not_selected_errors():
    executions: int = 0

    def should_retry(exc: Exception) -> bool:
        return isinstance(exc, ValueError)

    @retry(catching=should_retry)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        await compute("expected")

    assert executions == 1


@mark.asyncio
async def test_async_retries_with_custom_predicate():
    executions: int = 0
    observed: list[Exception] = []

    def should_retry(exc: Exception) -> bool:
        observed.append(exc)
        return isinstance(exc, FakeException)

    @retry(catching=should_retry)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException("retryable")
        else:
            return value

    assert await compute("expected") == "expected"
    assert executions == 2
    assert len(observed) == 1
    assert isinstance(observed[0], FakeException)


def test_sync_uses_delay_with_errors(monkeypatch: MonkeyPatch):
    executions: int = 0
    delays: list[float] = []

    # assert the requested delays instead of measuring wall clock time
    monkeypatch.setattr(retries, "sleep_sync", delays.append)

    @retry(limit=2, delay=0.05)
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        compute("expected")

    assert delays == [0.05, 0.05]
    assert executions == 3


def test_sync_uses_integer_delay_as_seconds(monkeypatch: MonkeyPatch):
    delays: list[float] = []
    monkeypatch.setattr(retries, "sleep_sync", delays.append)

    @retry(limit=1, delay=2)
    def compute() -> None:
        raise FakeException()

    with raises(FakeException):
        compute()

    assert delays == [2.0]
    assert isinstance(delays[0], float)


def test_sync_uses_computed_delay_with_errors(monkeypatch: MonkeyPatch):
    delays: list[float] = []
    seen: list[tuple[int, type[Exception]]] = []
    monkeypatch.setattr(retries, "sleep_sync", delays.append)

    def make_delay(attempt: int, exc: Exception) -> float:
        seen.append((attempt, type(exc)))
        return attempt * 0.035

    @retry(limit=2, delay=make_delay)
    def compute() -> None:
        raise FakeException()

    with raises(FakeException):
        compute()

    assert delays == [0.035, 0.07]
    assert seen == [(1, FakeException), (2, FakeException)]


def test_sync_without_delay_does_not_sleep(monkeypatch: MonkeyPatch):
    delays: list[float] = []
    monkeypatch.setattr(retries, "sleep_sync", delays.append)
    executions: int = 0

    @retry(limit=3)
    def compute() -> str:
        nonlocal executions
        executions += 1
        if executions < 3:
            raise FakeException()

        return "done"

    assert compute() == "done"
    assert delays == []
    assert executions == 3


def test_sync_does_not_sleep_for_errors_which_are_not_retried(monkeypatch: MonkeyPatch):
    delays: list[float] = []
    monkeypatch.setattr(retries, "sleep_sync", delays.append)

    @retry(limit=3, delay=1, catching=FakeException)
    def compute() -> None:
        raise ValueError("not retried")

    with raises(ValueError):
        compute()

    assert delays == []


def test_sync_stops_sleeping_after_success(monkeypatch: MonkeyPatch):
    delays: list[float] = []
    monkeypatch.setattr(retries, "sleep_sync", delays.append)
    executions: int = 0

    @retry(limit=5, delay=0.5)
    def compute() -> int:
        nonlocal executions
        executions += 1
        if executions == 1:
            raise FakeException()

        return executions

    assert compute() == 2
    assert delays == [0.5]
