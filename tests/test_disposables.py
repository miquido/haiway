from asyncio import CancelledError, Event, ensure_future, sleep
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any

from pytest import mark, raises

from haiway import State, ctx
from haiway.context.disposables import ContextDisposables, Disposables


class ExampleState(State):
    value: str = "test"


class AnotherExampleState(State):
    data: int = 42


class MockDisposable:
    """Mock disposable for testing that tracks calls and can simulate various behaviors."""

    def __init__(
        self,
        enter_return: Any = (),
        enter_exception: Exception | None = None,
        exit_exception: Exception | None = None,
    ):
        self.enter_return = enter_return
        self.enter_exception = enter_exception
        self.exit_exception = exit_exception
        self.enter_called = False
        self.exit_called = False
        self.exit_args: tuple[Any, ...] = ()

    async def __aenter__(self) -> Any:
        self.enter_called = True
        if self.enter_exception:
            raise self.enter_exception
        return self.enter_return

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        self.exit_called = True
        self.exit_args = (exc_type, exc_val, exc_tb)
        if self.exit_exception:
            raise self.exit_exception


@asynccontextmanager
async def disposable_returning_single_state() -> AsyncIterator[ExampleState]:
    yield ExampleState(value="single")


@asynccontextmanager
async def disposable_returning_multiple_states() -> AsyncIterator[list[State]]:
    yield [ExampleState(value="first"), AnotherExampleState(data=100)]


def test_context_disposables_empty_is_falsy() -> None:
    disposables = ContextDisposables.of()
    assert not disposables


def test_context_disposables_non_empty_is_truthy() -> None:
    disposables = ContextDisposables.of(MockDisposable())
    assert bool(disposables) is True


@mark.asyncio
async def test_context_disposables_collects_state() -> None:
    state = ExampleState(value="test")
    mock = MockDisposable(enter_return=state)
    disposables = ContextDisposables.of(mock)

    async with disposables:
        assert ctx.state(ExampleState) is state
    assert mock.enter_called
    assert mock.exit_called


@mark.asyncio
async def test_context_disposables_collects_multiple_states() -> None:
    state1 = ExampleState(value="first")
    state2 = AnotherExampleState(data=42)
    mock = MockDisposable(enter_return=[state1, state2])
    disposables = ContextDisposables.of(mock)

    async with disposables:
        assert ctx.state(ExampleState) is state1
        assert ctx.state(AnotherExampleState) is state2
    assert mock.enter_called
    assert mock.exit_called


@mark.asyncio
async def test_context_disposables_enter_exception_propagates() -> None:
    exc = RuntimeError("enter failed")
    mock = MockDisposable(enter_exception=exc)
    disposables = ContextDisposables.of(mock)

    with raises(RuntimeError, match="enter failed"):
        async with disposables:
            pass

    assert mock.enter_called
    assert mock.exit_called is False


@mark.asyncio
async def test_context_disposables_exit_with_single_exception_is_risen() -> None:
    exc = RuntimeError("cleanup failed")
    mock = MockDisposable(exit_exception=exc)
    disposables = ContextDisposables.of(mock)

    with raises(RuntimeError, match="cleanup failed"):
        async with disposables:
            pass

    assert mock.enter_called
    assert mock.exit_called


@mark.asyncio
async def test_context_disposables_exit_with_multiple_exceptions_groups() -> None:
    exc1 = RuntimeError("error 1")
    exc2 = ValueError("error 2")
    mock1 = MockDisposable(exit_exception=exc1)
    mock2 = MockDisposable(exit_exception=exc2)
    disposables = ContextDisposables.of(mock1, mock2)

    with raises(ExceptionGroup) as exc_info:
        async with disposables:
            pass

    exception_group = exc_info.value
    assert "Disposables disposal errors" in str(exception_group)
    assert len(exception_group.exceptions) == 2
    assert exc1 in exception_group.exceptions
    assert exc2 in exception_group.exceptions


@mark.asyncio
async def test_disposables_collects_state() -> None:
    async with Disposables.of(disposable_returning_single_state()) as states:
        collected = tuple(states)

    assert len(collected) == 1
    assert collected[0].value == "single"


@mark.asyncio
async def test_disposables_collects_multiple_states() -> None:
    async with Disposables.of(disposable_returning_multiple_states()) as states:
        collected = tuple(states)

    assert len(collected) == 2
    assert {type(state) for state in collected} == {ExampleState, AnotherExampleState}
    assert next(state for state in collected if isinstance(state, ExampleState)).value == "first"
    assert next(state for state in collected if isinstance(state, AnotherExampleState)).data == 100


@mark.asyncio
async def test_partial_preparation_failure_disposes_prepared() -> None:
    prepared = MockDisposable(enter_return=ExampleState(value="prepared"))
    failing = MockDisposable(enter_exception=RuntimeError("cannot prepare"))

    with raises(RuntimeError):
        async with ctx.disposables(prepared, failing):
            pass

    # the disposable which was prepared has to be disposed instead of leaking
    assert prepared.enter_called is True
    assert prepared.exit_called is True
    # the one which failed to prepare was never prepared, so it is not disposed
    assert failing.exit_called is False


@mark.asyncio
async def test_preparation_failure_within_scope_is_not_disposed() -> None:
    failing = MockDisposable(enter_exception=RuntimeError("cannot prepare"))

    with raises(RuntimeError):
        async with ctx.scope("test", disposables=(failing,)):
            pass

    assert failing.enter_called is True
    assert failing.exit_called is False


@mark.asyncio
async def test_preparation_failure_reports_original_error() -> None:
    @asynccontextmanager
    async def failing_disposable() -> AsyncIterator[ExampleState]:
        raise RuntimeError("original failure")
        yield ExampleState()  # pragma: no cover

    with raises(RuntimeError, match="original failure"):
        async with ctx.scope("test", disposables=(failing_disposable(),)):
            pass


@mark.asyncio
async def test_cancelled_preparation_disposes_prepared() -> None:
    entered: Event = Event()

    class FastDisposable:
        def __init__(self) -> None:
            self.exit_called: bool = False

        async def __aenter__(self) -> Any:
            entered.set()
            return ExampleState(value="prepared")

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            self.exit_called = True

    class SlowDisposable:
        def __init__(self) -> None:
            self.exit_called: bool = False

        async def __aenter__(self) -> Any:
            await sleep(0.1)
            return ExampleState(value="slow")

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            self.exit_called = True

    prepared = FastDisposable()
    slow = SlowDisposable()

    async def entering() -> None:
        async with ctx.scope("test", disposables=(prepared, slow)):
            pass  # pragma: no cover

    task = ensure_future(entering())
    await entered.wait()  # one is prepared, the other is still preparing
    task.cancel()
    with raises(CancelledError):
        await task

    # preparation is atomic - cancelling it midway completes the preparation
    # and disposes everything prepared instead of leaking it
    assert prepared.exit_called is True
    assert slow.exit_called is True


@mark.asyncio
async def test_cancelled_before_preparation_skips_preparation() -> None:
    class NeverPreparedDisposable:
        def __init__(self) -> None:
            self.enter_called: bool = False

        async def __aenter__(self) -> Any:
            self.enter_called = True  # pragma: no cover
            return ExampleState(value="never")  # pragma: no cover

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            pass  # pragma: no cover

    disposable = NeverPreparedDisposable()

    async def entering() -> None:
        ctx.cancel()  # request cancellation before preparing
        async with ctx.scope("test", disposables=(disposable,)):
            pass  # pragma: no cover

    with raises(CancelledError):
        await ensure_future(entering())

    # preparation is not started when cancellation is already pending
    assert disposable.enter_called is False


@mark.asyncio
async def test_self_cancelled_preparation_disposes_prepared() -> None:
    class CancellingDisposable:
        async def __aenter__(self) -> Any:
            raise CancelledError

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            pass  # pragma: no cover

    prepared = MockDisposable(enter_return=ExampleState(value="prepared"))

    with raises(CancelledError):
        async with ctx.disposables(prepared, CancellingDisposable()):
            pass  # pragma: no cover

    # cancellation of a single preparation cancels the whole preparation
    assert prepared.exit_called is True


@mark.asyncio
async def test_spawned_tasks_are_joined_before_disposables_are_released() -> None:
    task_completed: bool = False
    completed_before_release: bool | None = None

    class TrackedDisposable:
        async def __aenter__(self) -> ExampleState:
            return ExampleState(value="tracked")

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            nonlocal completed_before_release
            completed_before_release = task_completed

    async def work() -> None:
        nonlocal task_completed
        for _ in range(4):  # outlive the scope body and any single teardown step
            await sleep(0)

        # the state provided by the disposable is still available to the task
        assert ctx.state(ExampleState).value == "tracked"
        task_completed = True

    async with ctx.scope("ordering", disposables=(TrackedDisposable(),)):
        ctx.spawn(work)

    assert task_completed is True
    assert completed_before_release is True
