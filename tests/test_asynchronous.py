import threading
from asyncio import create_task, gather, get_running_loop, sleep
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from time import monotonic
from time import sleep as blocking_sleep

import pytest

from haiway import asynchronous

variable: ContextVar[str] = ContextVar("variable", default="default")


@pytest.mark.asyncio
async def test_runs_sync_function_off_the_event_loop_thread() -> None:
    @asynchronous
    def thread_id() -> int:
        return threading.get_ident()

    assert await thread_id() != threading.get_ident()


@pytest.mark.asyncio
async def test_forwards_arguments_and_returns_result() -> None:
    @asynchronous
    def add(a: int, *, b: int = 0) -> int:
        return a + b

    assert await add(1, b=2) == 3
    assert await add(5) == 5


@pytest.mark.asyncio
async def test_propagates_exceptions() -> None:
    @asynchronous
    def failing() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await failing()


@pytest.mark.asyncio
async def test_does_not_block_event_loop() -> None:
    @asynchronous
    def slow() -> str:
        blocking_sleep(0.3)
        return "done"

    ticks = 0

    async def ticker() -> None:
        nonlocal ticks
        while True:
            await sleep(0.01)
            ticks += 1

    task = create_task(ticker())
    started = monotonic()
    result = await slow()
    task.cancel()

    assert result == "done"
    assert monotonic() - started >= 0.25
    assert ticks >= 2


@pytest.mark.asyncio
async def test_runs_calls_concurrently() -> None:
    @asynchronous
    def slow() -> None:
        blocking_sleep(0.3)

    started = monotonic()
    await gather(slow(), slow(), slow())

    assert monotonic() - started < 0.8


@pytest.mark.asyncio
async def test_propagates_context_variables_to_executor() -> None:
    @asynchronous
    def read() -> str:
        return variable.get()

    token = variable.set("from-caller")
    try:
        assert await read() == "from-caller"

    finally:
        variable.reset(token)


@pytest.mark.asyncio
async def test_context_changes_in_executor_do_not_leak_back() -> None:
    @asynchronous
    def write() -> None:
        variable.set("from-executor")

    await write()

    assert variable.get() == "default"


@pytest.mark.asyncio
async def test_custom_executor_is_used() -> None:
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="custom-pool") as executor:

        @asynchronous(executor=executor)
        def thread_name() -> str:
            return threading.current_thread().name

        assert (await thread_name()).startswith("custom-pool")


@pytest.mark.asyncio
async def test_explicit_loop_is_used() -> None:
    loop = get_running_loop()

    @asynchronous(loop=loop)
    def value() -> int:
        return 1

    assert await value() == 1


@pytest.mark.asyncio
async def test_preserves_function_metadata() -> None:
    @asynchronous
    def documented(a: int, b: str = "x") -> str:
        """Docstring."""
        return b * a

    assert documented.__name__ == "documented"
    assert documented.__doc__ == "Docstring."
    assert documented.__module__ == __name__
    assert documented.__wrapped__ is not None  # pyright: ignore[reportFunctionMemberAccess]
    assert documented.__annotations__["a"] is int


def test_rejects_coroutine_functions() -> None:
    async def coroutine() -> None:
        return None

    with pytest.raises(AssertionError):
        asynchronous(coroutine)  # pyright: ignore[reportArgumentType, reportCallIssue]
