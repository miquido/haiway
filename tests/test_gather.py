from asyncio import (
    Barrier,
    CancelledError,
    Event,
    Task,
    all_tasks,
    current_task,
    sleep,
    timeout,
)
from collections.abc import Coroutine, Sequence
from contextvars import ContextVar
from inspect import CORO_CLOSED, getcoroutinestate

from pytest import MonkeyPatch, mark, raises

from haiway import State, ctx, gather
from haiway.context.tasks import ContextTaskGroup


class FakeException(Exception):
    pass


class Marker(State):
    value: str


@mark.asyncio
@mark.parametrize(
    "delays",
    [
        (0.0,),
        (0.03, 0.0),
        (0.0, 0.03),
        (0.04, 0.0, 0.02),
        (0.0, 0.0, 0.0, 0.0),
    ],
)
async def test_returns_results_in_argument_order(delays: Sequence[float]):
    async def delayed(value: int, delay: float) -> int:
        await sleep(delay)
        return value

    results: Sequence[int] = await gather(
        *(delayed(index, delay) for index, delay in enumerate(delays))
    )

    assert results == tuple(range(len(delays)))


@mark.asyncio
async def test_returns_immutable_tuple():
    async def value() -> int:
        return 1

    assert isinstance(await gather(value()), tuple)


@mark.asyncio
async def test_returns_empty_without_coroutines():
    assert await gather() == ()


@mark.asyncio
async def test_preserves_none_and_falsy_results():
    async def value[Result](result: Result) -> Result:
        return result

    assert await gather(value(None), value(None)) == (None, None)
    assert await gather(value(0), value(0)) == (0, 0)


@mark.asyncio
async def test_runs_coroutines_concurrently():
    first: Event = Event()
    second: Event = Event()

    async def run_first() -> str:
        first.set()
        await second.wait()  # completes only when the other coroutine runs alongside
        return "first"

    async def run_second() -> str:
        second.set()
        await first.wait()
        return "second"

    async with timeout(2):
        assert await gather(run_first(), run_second()) == ("first", "second")


@mark.asyncio
async def test_does_not_limit_concurrent_tasks():
    count: int = 25
    barrier: Barrier = Barrier(count)

    async def meet(value: int) -> int:
        await barrier.wait()  # releases only when all of the tasks are running at once
        return value

    async with timeout(2):
        assert await gather(*(meet(value) for value in range(count))) == tuple(range(count))


@mark.asyncio
async def test_raises_single_failure_as_is():
    error: FakeException = FakeException("failure")

    async def failing() -> int:
        raise error

    async def waiting() -> int:
        await sleep(10)
        return 0

    with raises(FakeException) as failure:
        await gather(waiting(), failing(), waiting())

    assert failure.value is error


@mark.asyncio
async def test_keeps_the_cause_of_a_failure():
    cause: ValueError = ValueError("cause")

    async def failing() -> int:
        raise FakeException("failure") from cause

    with raises(FakeException) as failure:
        await gather(failing())

    assert failure.value.__cause__ is cause


@mark.asyncio
async def test_failure_cancels_and_awaits_the_rest_before_raising():
    started: Event = Event()
    cleaned_up: Event = Event()

    async def failing() -> int:
        await started.wait()
        raise FakeException()

    async def waiting() -> int:
        started.set()
        try:
            await sleep(10)

        finally:
            await sleep(0)  # cleanup taking a moment must be awaited as well
            cleaned_up.set()

        return 0

    with raises(FakeException):
        await gather(waiting(), failing())

    assert cleaned_up.is_set()


@mark.asyncio
async def test_failure_leaves_no_task_running():
    before = all_tasks()

    async def failing() -> int:
        await sleep(0)
        raise FakeException()

    async def waiting() -> int:
        await sleep(10)
        return 0

    with raises(FakeException):
        await gather(waiting(), waiting(), failing())

    assert all_tasks() == before


@mark.asyncio
async def test_failure_does_not_wait_for_slow_coroutines():
    async def failing() -> int:
        raise FakeException()

    async def slow() -> int:
        await sleep(10)
        return 0

    async with timeout(2):
        with raises(FakeException):
            await gather(slow(), failing())


@mark.asyncio
async def test_multiple_failures_are_raised_as_group():
    barrier: Barrier = Barrier(2)

    async def failing(value: int) -> int:
        await barrier.wait()  # both fail before either is able to cancel the other
        raise FakeException(str(value))

    with raises(ExceptionGroup) as failure:
        await gather(failing(1), failing(2))

    assert sorted(str(error) for error in failure.value.exceptions) == ["1", "2"]
    assert all(isinstance(error, FakeException) for error in failure.value.exceptions)


@mark.asyncio
async def test_caller_cancellation_cancels_all_tasks():
    running: Barrier = Barrier(3)
    cancelled: list[int] = []

    async def waiting(value: int) -> int:
        await running.wait()
        try:
            await sleep(10)

        except CancelledError:
            cancelled.append(value)
            raise

        return value

    async def run() -> Sequence[int]:
        return await gather(waiting(1), waiting(2))

    async with ctx.scope("test"):
        task = ctx.spawn(run)
        await running.wait()
        task.cancel()

        with raises(CancelledError):
            await task

    assert sorted(cancelled) == [1, 2]


@mark.asyncio
async def test_tasks_inherit_context_variables():
    variable: ContextVar[str] = ContextVar("variable", default="unset")

    async def read() -> str:
        return variable.get()

    token = variable.set("value")
    try:
        assert await gather(read(), read()) == ("value", "value")

    finally:
        variable.reset(token)


@mark.asyncio
async def test_tasks_do_not_leak_context_changes():
    variable: ContextVar[str] = ContextVar("variable", default="unset")

    async def write(value: str) -> str:
        variable.set(value)
        return variable.get()

    assert await gather(write("a"), write("b")) == ("a", "b")
    assert variable.get() == "unset"


@mark.asyncio
async def test_tasks_access_the_scope_state():
    async def read() -> str:
        return ctx.state(Marker).value

    async with ctx.scope("test", Marker(value="scoped")):
        assert await gather(read(), read()) == ("scoped", "scoped")


@mark.asyncio
async def test_runs_coroutines_in_separate_tasks():
    async def task_of() -> object:
        return current_task()

    tasks = await gather(task_of(), task_of())

    assert tasks[0] is not tasks[1]
    assert current_task() not in tasks


@mark.asyncio
@mark.filterwarnings("error")
async def test_closes_coroutines_left_without_task(monkeypatch: MonkeyPatch):
    original = ContextTaskGroup.run
    calls: list[int] = []

    def failing_run(coro: Coroutine[None, None, int], /) -> Task[int]:
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("no task")

        return original(coro)

    monkeypatch.setattr(ContextTaskGroup, "run", staticmethod(failing_run))

    async def value(result: int) -> int:
        return result

    coroutines: Sequence[Coroutine[None, None, int]] = (value(1), value(2), value(3))

    with raises(RuntimeError, match="no task"):
        await gather(*coroutines)

    assert getcoroutinestate(coroutines[1]) == CORO_CLOSED
    assert getcoroutinestate(coroutines[2]) == CORO_CLOSED
