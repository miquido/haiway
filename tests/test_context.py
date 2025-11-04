import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager

from pytest import mark, raises

from haiway import MissingContext, State, ctx
from haiway.context.disposables import Disposables
from haiway.context.state import StateContext
from haiway.context.tasks import TaskGroupContext


class ExampleState(State):
    state: str = "default"


class StateThatFailsInit(State):
    required_param: str


class FakeException(Exception):
    pass


class DisposableBaseError(BaseException):
    pass


def disposable_that_raises(
    exc_factory: Callable[[], BaseException],
):
    @asynccontextmanager
    async def managed():
        try:
            yield None
        finally:
            raise exc_factory()

    return managed()


@mark.asyncio
async def test_state_is_available_according_to_context():
    # Outside of context, should instantiate with default values
    assert ctx.state(ExampleState).state == "default"

    async with ctx.scope("default"):
        assert ctx.state(ExampleState).state == "default"

        async with ctx.scope("specified", ExampleState(state="specified")):
            assert ctx.state(ExampleState).state == "specified"

            async with ctx.scope("modified", ExampleState(state="modified")):
                assert ctx.state(ExampleState).state == "modified"

            assert ctx.state(ExampleState).state == "specified"

        assert ctx.state(ExampleState).state == "default"

    # Outside of context again, should instantiate with default values
    assert ctx.state(ExampleState).state == "default"


@mark.asyncio
async def test_state_update_updates_local_context():
    # Outside of context, should instantiate with default values
    assert ctx.state(ExampleState).state == "default"

    async with ctx.scope("default"):
        assert ctx.state(ExampleState).state == "default"

        with ctx.updated(ExampleState(state="updated")):
            assert ctx.state(ExampleState).state == "updated"

            with ctx.updated(ExampleState(state="modified")):
                assert ctx.state(ExampleState).state == "modified"

            assert ctx.state(ExampleState).state == "updated"

        assert ctx.state(ExampleState).state == "default"

    # Outside of context again, should instantiate with default values
    assert ctx.state(ExampleState).state == "default"


@mark.asyncio
async def test_exceptions_are_propagated():
    with raises(FakeException):
        async with ctx.scope("outer"):
            async with ctx.scope("inner"):
                raise FakeException()


def test_state_context_outside_scope_with_default_constructor():
    """Test that StateContext.state() can instantiate states outside of any context."""
    # Outside of any context, should successfully instantiate ExampleState()
    state = StateContext.state(ExampleState)
    assert isinstance(state, ExampleState)
    assert state.state == "default"


def test_state_context_outside_scope_with_default_parameter():
    """Test that StateContext.state() uses default parameter outside of any context."""
    default_state = ExampleState(state="custom_default")

    # Should use the provided default instead of instantiating
    state = StateContext.state(ExampleState, default=default_state)
    assert state is default_state
    assert state.state == "custom_default"


def test_state_context_outside_scope_fails_without_default():
    """Test that StateContext.state() raises MissingContext for non-instantiable states."""
    # StateThatFailsInit requires parameters, so instantiation should fail
    # and MissingContext should be raised
    with raises(MissingContext, match="StateContext requested but not defined"):
        StateContext.state(StateThatFailsInit)


def test_ctx_state_outside_scope_fails_without_default():
    """Test that ctx.state() raises MissingContext for non-instantiable states."""
    # StateThatFailsInit requires parameters, so instantiation should fail
    # and MissingContext should be raised
    with raises(MissingContext, match="StateContext requested but not defined"):
        ctx.state(StateThatFailsInit)


def test_state_context_outside_scope_works_with_explicit_default():
    """Test that StateContext.state() uses provided default for non-instantiable states."""
    default_state = StateThatFailsInit(required_param="test_value")

    # Should use the provided default instead of trying to instantiate
    state = StateContext.state(StateThatFailsInit, default=default_state)
    assert state is default_state
    assert state.required_param == "test_value"


@mark.asyncio
async def test_state_that_fails_init_works_within_context():
    """Test that StateThatFailsInit works when provided explicitly in context scope."""
    test_state = StateThatFailsInit(required_param="context_value")

    async with ctx.scope("test", test_state):
        # Should resolve from context
        state = ctx.state(StateThatFailsInit)
        assert state is test_state
        assert state.required_param == "context_value"


def test_check_state_outside_scope():
    """Test that StateContext.check_state() returns False outside of any context."""
    # Outside of any context, should return False
    assert not StateContext.check_state(ExampleState)
    assert not StateContext.check_state(StateThatFailsInit)


def test_current_state_outside_scope():
    """Test that StateContext.current_state() returns empty tuple outside of any context."""
    # Outside of any context, should return empty tuple
    current = StateContext.current_state()
    assert current == ()
    assert isinstance(current, tuple)


@mark.asyncio
async def test_scope_task_group_waits_for_spawned_tasks():
    completed = asyncio.Event()

    async def worker() -> None:
        await asyncio.sleep(0)
        completed.set()

    async with ctx.scope("task-group-root"):
        ctx.spawn(worker)

    assert completed.is_set()


@mark.asyncio
async def test_nested_scope_reuses_parent_task_group():
    async with ctx.scope("parent"):
        parent_group = TaskGroupContext._context.get()

        async with ctx.scope("child"):
            child_group = TaskGroupContext._context.get()
            assert child_group is parent_group


@mark.asyncio
async def test_isolated_scope_uses_separate_task_group():
    async with ctx.scope("parent"):
        parent_group = TaskGroupContext._context.get()
        completion = asyncio.Event()

        async def worker() -> None:
            await asyncio.sleep(0)
            completion.set()

        async with ctx.scope("child", isolated=True):
            isolated_group = TaskGroupContext._context.get()
            assert isolated_group is not parent_group
            ctx.spawn(worker)

        assert completion.is_set()
        # Parent task group context should be restored after isolated scope exit
        assert TaskGroupContext._context.get() is parent_group


@mark.asyncio
async def test_scope_task_group_cancels_subtasks_on_parent_failure():
    started = 0
    started_event = asyncio.Event()
    lock = asyncio.Lock()
    cancellations: set[str] = set()
    blocker = asyncio.Event()

    async def worker(name: str) -> None:
        nonlocal started
        async with lock:
            started += 1
            if started == 2:
                started_event.set()

        try:
            await blocker.wait()
        except asyncio.CancelledError:
            cancellations.add(name)
            raise

    with raises(FakeException):
        async with ctx.scope("cancel-subtasks"):
            ctx.spawn(worker, "alpha")
            ctx.spawn(worker, "beta")
            await started_event.wait()
            raise FakeException()

    assert cancellations == {"alpha", "beta"}


@mark.asyncio
async def test_scope_task_group_exit_propagates_cancelled_error():
    cancellation = asyncio.Event()

    async def worker() -> None:
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancellation.set()
            raise

    async def run_scope() -> None:
        async with ctx.scope("cancel-during-exit"):
            ctx.spawn(worker)
            await asyncio.sleep(10)

    task = asyncio.create_task(run_scope())
    await asyncio.sleep(0)
    task.cancel()

    with raises(asyncio.CancelledError):
        await task

    assert cancellation.is_set()


@mark.asyncio
async def test_spawned_task_error_is_recorded_on_task():
    async def failing_task() -> None:
        await asyncio.sleep(0)
        raise FakeException()

    async with ctx.scope("subtask-error"):
        task = ctx.spawn(failing_task)
        # Yield control so the task can run and fail inside the scope
        await asyncio.sleep(0)

    assert task.done()
    assert not task.cancelled()
    exception = task.exception()
    assert isinstance(exception, FakeException)


@mark.asyncio
async def test_scope_disposables_error_is_propagated():
    disposables = Disposables(
        (
            disposable_that_raises(lambda: RuntimeError("dispose failed")),
        )
    )

    with raises(RuntimeError, match="dispose failed"):
        async with ctx.scope("disposable-error", disposables=disposables):
            pass


@mark.asyncio
async def test_scope_disposables_base_exception_group_contains_all_errors():
    disposables = Disposables(
        (
            disposable_that_raises(lambda: DisposableBaseError("base failure")),
            disposable_that_raises(lambda: RuntimeError("runtime failure")),
        )
    )

    with raises(BaseExceptionGroup) as excinfo:
        async with ctx.scope("disposable-errors", disposables=disposables):
            pass

    errors = excinfo.value.exceptions
    assert any(isinstance(err, DisposableBaseError) for err in errors)
    assert any(isinstance(err, RuntimeError) for err in errors)
