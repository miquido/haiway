import asyncio
from contextlib import asynccontextmanager
from types import TracebackType
from typing import Any

from pytest import mark, raises

from haiway import State
from haiway.context import Disposables


class ExampleState(State):
    value: str = "test"


class AnotherExampleState(State):
    data: int = 42


class MockDisposable:
    """Mock disposable for testing that tracks calls and can simulate various behaviors."""

    def __init__(
        self,
        enter_return: Any = None,
        enter_exception: Exception | None = None,
        exit_exception: Exception | None = None,
        exit_return: bool | None = None,
    ):
        self.enter_return = enter_return
        self.enter_exception = enter_exception
        self.exit_exception = exit_exception
        self.exit_return = exit_return
        self.enter_called = False
        self.exit_called = False
        self.exit_args: tuple[Any, ...] = ()

    async def __aenter__(self):
        self.enter_called = True
        if self.enter_exception:
            raise self.enter_exception
        return self.enter_return

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        self.exit_called = True
        self.exit_args = (exc_type, exc_val, exc_tb)
        if self.exit_exception:
            raise self.exit_exception
        return self.exit_return


@asynccontextmanager
async def disposable_returning_none():
    yield None


@asynccontextmanager
async def disposable_returning_single_state():
    yield ExampleState(value="single")


@asynccontextmanager
async def disposable_returning_multiple_states():
    yield [ExampleState(value="first"), AnotherExampleState(data=100)]


def test_empty_initialization():
    disposables = Disposables()
    assert not disposables
    assert disposables._disposables == ()
    assert disposables._loop is None


def test_single_disposable_initialization():
    mock = MockDisposable()
    disposables = Disposables(mock)
    assert bool(disposables)
    assert len(disposables._disposables) == 1
    assert disposables._disposables[0] is mock


def test_multiple_disposables_initialization():
    mock1 = MockDisposable()
    mock2 = MockDisposable()
    mock3 = MockDisposable()
    disposables = Disposables(mock1, mock2, mock3)
    assert bool(disposables)
    assert len(disposables._disposables) == 3
    assert disposables._disposables == (mock1, mock2, mock3)


def test_cannot_set_attributes():
    disposables = Disposables()
    with raises(AttributeError, match="Can't modify immutable"):
        disposables.new_attr = "value"
    with raises(AttributeError, match="Can't modify immutable"):
        disposables._disposables = ()


def test_cannot_delete_attributes():
    disposables = Disposables()
    with raises(AttributeError, match="Can't modify immutable"):
        del disposables._disposables
    with raises(AttributeError, match="Can't modify immutable"):
        del disposables._loop


def test_empty_disposables_is_falsy():
    disposables = Disposables()
    assert not disposables
    assert bool(disposables) is False


def test_non_empty_disposables_is_truthy():
    mock = MockDisposable()
    disposables = Disposables(mock)
    assert disposables
    assert bool(disposables) is True


@mark.asyncio
async def test_enter_with_no_disposables():
    disposables = Disposables()
    result = await disposables.__aenter__()
    assert result == []
    assert disposables._loop is not None


@mark.asyncio
async def test_enter_with_disposable_returning_none():
    mock = MockDisposable(enter_return=None)
    disposables = Disposables(mock)
    result = await disposables.__aenter__()
    assert result == []
    assert mock.enter_called
    assert disposables._loop is not None


@mark.asyncio
async def test_enter_with_disposable_returning_single_state():
    test_state = ExampleState(value="test")
    mock = MockDisposable(enter_return=test_state)
    disposables = Disposables(mock)
    result = tuple(await disposables.__aenter__())
    assert len(result) == 1
    assert result[0] is test_state
    assert mock.enter_called


@mark.asyncio
async def test_enter_with_disposable_returning_multiple_states():
    state1 = ExampleState(value="first")
    state2 = AnotherExampleState(data=42)
    states = [state1, state2]
    mock = MockDisposable(enter_return=states)
    disposables = Disposables(mock)
    result = tuple(await disposables.__aenter__())
    assert len(result) == 2
    assert state1 in result
    assert state2 in result
    assert mock.enter_called


@mark.asyncio
async def test_enter_with_multiple_disposables_mixed_returns():
    state1 = ExampleState(value="single")
    state2 = AnotherExampleState(data=100)
    state3 = ExampleState(value="another")

    mock1 = MockDisposable(enter_return=None)
    mock2 = MockDisposable(enter_return=state1)
    mock3 = MockDisposable(enter_return=[state2, state3])

    disposables = Disposables(mock1, mock2, mock3)
    result = tuple(await disposables.__aenter__())

    assert len(result) == 3
    assert state1 in result
    assert state2 in result
    assert state3 in result
    assert all(mock.enter_called for mock in [mock1, mock2, mock3])


@mark.asyncio
async def test_enter_sets_loop_correctly():
    disposables = Disposables()
    current_loop = asyncio.get_running_loop()
    await disposables.__aenter__()
    assert disposables._loop is current_loop


@mark.asyncio
async def test_exit_with_no_disposables():
    """Test exiting empty Disposables."""
    disposables = Disposables()
    await disposables.__aenter__()
    await disposables.__aexit__(None, None, None)
    assert disposables._loop is None


@mark.asyncio
async def test_exit_with_successful_cleanup():
    """Test exiting with successful cleanup of all disposables."""
    mock1 = MockDisposable()
    mock2 = MockDisposable()
    disposables = Disposables(mock1, mock2)

    await disposables.__aenter__()
    await disposables.__aexit__(None, None, None)

    assert mock1.exit_called
    assert mock2.exit_called
    assert mock1.exit_args == (None, None, None)
    assert mock2.exit_args == (None, None, None)
    assert disposables._loop is None


@mark.asyncio
async def test_exit_with_exception_context():
    mock = MockDisposable()
    disposables = Disposables(mock)

    await disposables.__aenter__()

    exc = ValueError("test exception")
    await disposables.__aexit__(type(exc), exc, exc.__traceback__)

    assert mock.exit_called
    assert mock.exit_args[0] is type(exc)
    assert mock.exit_args[1] is exc
    assert mock.exit_args[2] is exc.__traceback__


@mark.asyncio
async def test_exit_with_multiple_exceptions_creates_group():
    exc1 = RuntimeError("error 1")
    exc2 = ValueError("error 2")
    mock1 = MockDisposable(exit_exception=exc1)
    mock2 = MockDisposable(exit_exception=exc2)
    disposables = Disposables(mock1, mock2)

    await disposables.__aenter__()

    with raises(BaseExceptionGroup) as exc_info:
        await disposables.__aexit__(None, None, None)

    exception_group = exc_info.value
    assert "Disposables cleanup errors" in str(exception_group)
    assert len(exception_group.exceptions) == 2
    assert exc1 in exception_group.exceptions
    assert exc2 in exception_group.exceptions
    assert disposables._loop is None


@mark.asyncio
async def test_exit_with_single_exception_is_swallowed():
    exc = RuntimeError("cleanup failed")
    mock = MockDisposable(exit_exception=exc)
    disposables = Disposables(mock)

    await disposables.__aenter__()

    # Single exception does not get re-raised due to current implementation
    await disposables.__aexit__(None, None, None)
    assert disposables._loop is None


@mark.asyncio
async def test_exit_resets_loop_even_on_exception():
    exc = RuntimeError("cleanup failed")
    mock = MockDisposable(exit_exception=exc)
    disposables = Disposables(mock)

    await disposables.__aenter__()
    assert disposables._loop is not None

    # Single exception case - it gets handled but not re-raised
    await disposables.__aexit__(None, None, None)
    assert disposables._loop is None


@mark.asyncio
async def test_same_loop_cleanup():
    mock = MockDisposable()
    disposables = Disposables(mock)

    await disposables.__aenter__()
    initial_loop = disposables._loop
    current_loop = asyncio.get_running_loop()
    assert initial_loop is current_loop

    await disposables.__aexit__(None, None, None)
    assert mock.exit_called


@mark.asyncio
async def test_with_real_async_context_managers():
    disposables = Disposables(
        disposable_returning_none(),
        disposable_returning_single_state(),
        disposable_returning_multiple_states(),
    )

    async with disposables as states:
        state_values = tuple(
            state.value
            if isinstance(state, ExampleState)
            else state.data
            if isinstance(state, AnotherExampleState)
            else state
            for state in states
        )
        assert len(state_values) == 3

        # Check the states returned
        assert "single" in state_values
        assert "first" in state_values
        assert 100 in state_values


@mark.asyncio
async def test_nested_disposables_usage():
    """Test using Disposables in nested contexts."""
    outer_mock = MockDisposable(enter_return=ExampleState(value="outer"))
    inner_mock = MockDisposable(enter_return=ExampleState(value="inner"))

    outer_disposables = Disposables(outer_mock)
    inner_disposables = Disposables(inner_mock)

    async with outer_disposables as outer:
        outer_states = tuple(outer)
        assert len(outer_states) == 1
        assert isinstance(outer_states[0], ExampleState) and outer_states[0].value == "outer"

        async with inner_disposables as inner:
            inner_states = tuple(inner)
            assert len(inner_states) == 1
            assert isinstance(inner_states[0], ExampleState) and inner_states[0].value == "inner"

    assert outer_mock.exit_called
    assert inner_mock.exit_called


@mark.asyncio
async def test_exception_during_enter_phase():
    exc = RuntimeError("enter failed")
    mock = MockDisposable(enter_exception=exc)
    disposables = Disposables(mock)

    with raises(RuntimeError, match="enter failed"):
        await disposables.__aenter__()


@mark.asyncio
async def test_assertion_on_double_enter():
    disposables = Disposables()
    await disposables.__aenter__()

    # Second enter should assert because loop is already set
    with raises(AssertionError):
        await disposables.__aenter__()


@mark.asyncio
async def test_assertion_on_exit_without_enter():
    """Test that exiting without entering raises AssertionError."""
    disposables = Disposables()

    # Exit without enter should assert because loop is None
    with raises(AssertionError):
        await disposables.__aexit__(None, None, None)
