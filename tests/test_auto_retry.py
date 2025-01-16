from asyncio import CancelledError, Task, sleep
from time import time
from unittest import TestCase

from pytest import mark, raises

from haiway import retry


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

    @retry(catching={ValueError})
    def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        compute("expected")

    assert executions == 1


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
async def test_async_uses_delay_with_errors():
    executions: int = 0

    @retry(limit=2, delay=0.05)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    time_start: float = time()
    with raises(FakeException):
        await compute("expected")
    assert (time() - time_start) >= 0.1
    assert executions == 3


@mark.asyncio
async def test_async_uses_computed_delay_with_errors():
    executions: int = 0

    @retry(limit=2, delay=lambda attempt, _: attempt * 0.035)
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    time_start: float = time()
    with raises(FakeException):
        await compute("expected")
    assert (time() - time_start) >= 0.1
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

    @retry(catching={ValueError})
    async def compute(value: str, /) -> str:
        nonlocal executions
        executions += 1
        raise FakeException()

    with raises(FakeException):
        await compute("expected")

    assert executions == 1
