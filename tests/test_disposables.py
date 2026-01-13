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
