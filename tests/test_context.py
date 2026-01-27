import asyncio
from collections.abc import Callable
from contextlib import asynccontextmanager
from types import TracebackType

from pytest import mark, raises

from haiway import ContextMissing, State, ctx
from haiway.context import tasks as tasks_module
from haiway.context.state import ContextState
from haiway.context.tasks import ContextTaskGroup


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
            yield ExampleState()
        finally:
            raise exc_factory()

    return managed()


@mark.asyncio
async def test_state_is_available_according_to_context():
    # Outside of context, require explicit default
    assert ctx.state(ExampleState, default=ExampleState()).state == "default"

    async with ctx.scope("default"):
        assert ctx.state(ExampleState).state == "default"

        async with ctx.scope("specified", ExampleState(state="specified")):
            assert ctx.state(ExampleState).state == "specified"

            async with ctx.scope("modified", ExampleState(state="modified")):
                assert ctx.state(ExampleState).state == "modified"

            assert ctx.state(ExampleState).state == "specified"

        assert ctx.state(ExampleState).state == "default"

    # Outside of context again, require explicit default
    assert ctx.state(ExampleState, default=ExampleState()).state == "default"


@mark.asyncio
async def test_state_update_updates_local_context():
    # Outside of context, require explicit default
    assert ctx.state(ExampleState, default=ExampleState()).state == "default"

    async with ctx.scope("default"):
        assert ctx.state(ExampleState).state == "default"

        with ctx.updating(ExampleState(state="updated")):
            assert ctx.state(ExampleState).state == "updated"

            with ctx.updating(ExampleState(state="modified")):
                assert ctx.state(ExampleState).state == "modified"

            assert ctx.state(ExampleState).state == "updated"

        assert ctx.state(ExampleState).state == "default"

    # Outside of context again, require explicit default
    assert ctx.state(ExampleState, default=ExampleState()).state == "default"


@mark.asyncio
async def test_exceptions_are_propagated():
    with raises(FakeException):
        async with ctx.scope("outer"):
            async with ctx.scope("inner"):
                raise FakeException()


def test_state_context_outside_scope_with_default_constructor():
    """Test that ContextState.state() requires explicit default outside of any context."""
    with raises(ContextMissing, match="ContextState requested but not defined"):
        ContextState.state(ExampleState)


def test_state_context_outside_scope_with_default_parameter():
    """Test that ContextState.state() uses default parameter outside of any context."""
    default_state = ExampleState(state="custom_default")

    # Should use the provided default instead of instantiating
    state = ContextState.state(ExampleState, default=default_state)
    assert state is default_state
    assert state.state == "custom_default"


def test_state_context_outside_scope_fails_without_default():
    """Test that ContextState.state() raises ContextMissing for non-instantiable states."""
    # StateThatFailsInit requires parameters, so instantiation should fail
    # and ContextMissing should be raised
    with raises(ContextMissing, match="ContextState requested but not defined"):
        ContextState.state(StateThatFailsInit)


def test_ctx_state_outside_scope_fails_without_default():
    """Test that ctx.state() raises ContextMissing for non-instantiable states."""
    # StateThatFailsInit requires parameters, so instantiation should fail
    # and ContextMissing should be raised
    with raises(ContextMissing, match="ContextState requested but not defined"):
        ctx.state(StateThatFailsInit)


def test_state_context_outside_scope_works_with_explicit_default():
    """Test that ContextState.state() uses provided default for non-instantiable states."""
    default_state = StateThatFailsInit(required_param="test_value")

    # Should use the provided default instead of trying to instantiate
    state = ContextState.state(StateThatFailsInit, default=default_state)
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
    """Test that ContextState.contains() returns False outside of any context."""
    # Outside of any context, should return False
    assert not ContextState.contains(ExampleState)
    assert not ContextState.contains(StateThatFailsInit)


def test_current_state_outside_scope():
    """Test that ContextState.snapshot() returns empty tuple outside of any context."""
    # Outside of any context, should return empty tuple
    current = ContextState.snapshot()
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
        parent_group = ContextTaskGroup._context.get()

        async with ctx.scope("child"):
            child_group = ContextTaskGroup._context.get()
            assert child_group is parent_group


@mark.asyncio
async def test_isolated_scope_uses_separate_task_group():
    async with ctx.scope("parent"):
        parent_group = ContextTaskGroup._context.get()
        completion = asyncio.Event()

        async def worker() -> None:
            await asyncio.sleep(0)
            completion.set()

        async with ctx.scope("child", isolated=True):
            isolated_group = ContextTaskGroup._context.get()
            assert isolated_group is not parent_group
            ctx.spawn(worker)

        assert completion.is_set()
        # Parent task group context should be restored after isolated scope exit
        assert ContextTaskGroup._context.get() is parent_group


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
async def test_scope_task_group_exit_propagates_exception_group():
    async def worker() -> None:
        await asyncio.sleep(0)
        raise FakeException()

    with raises(ExceptionGroup):
        async with ctx.scope("task-group-exception-group"):
            ctx.spawn(worker)


@mark.asyncio
async def test_context_task_group_keeps_context_during_exit(monkeypatch):
    instances: list[object] = []
    captured: list[object | None] = []

    class CapturingTaskGroup(asyncio.TaskGroup):
        def __init__(self) -> None:
            super().__init__()
            instances.append(self)

        async def __aenter__(self) -> asyncio.TaskGroup:
            return await super().__aenter__()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> bool | None:
            try:
                captured.append(ContextTaskGroup._context.get())
            except LookupError:
                captured.append(None)
            return await super().__aexit__(exc_type, exc_val, exc_tb)

    monkeypatch.setattr(tasks_module, "TaskGroup", CapturingTaskGroup)

    async with ContextTaskGroup():
        await asyncio.sleep(0)

    assert len(instances) == 1
    assert captured == [instances[0]]


def test_background_task_shutdown_handles_loop_race(monkeypatch):
    class DummyTask:
        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

    class DummyLoop:
        def __init__(self) -> None:
            self.called = False

        def is_closed(self) -> bool:
            return False

        def call_soon_threadsafe(self, callback: object) -> None:
            self.called = True
            raise RuntimeError("loop closed")

    dummy_loop = DummyLoop()
    dummy_task = DummyTask()

    monkeypatch.setattr(
        tasks_module.BackgroundTaskGroup,
        "_loops_tasks",
        {dummy_loop: {dummy_task}},
    )

    tasks_module.BackgroundTaskGroup.shutdown(loop=dummy_loop)

    assert dummy_loop.called is True
    assert dummy_task.cancelled is True


@mark.asyncio
async def test_scope_task_group_exit_propagates_cancelled_error():
    cancellation = asyncio.Event()
    started = asyncio.Event()

    async def worker() -> None:
        started.set()
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
    await started.wait()
    task.cancel()

    with raises(asyncio.CancelledError):
        await task


@mark.asyncio
async def test_spawned_task_error_is_recorded_on_task():
    async def failing_task() -> None:
        await asyncio.sleep(0)
        raise FakeException()

    task: asyncio.Task[None] | None = None
    with raises(ExceptionGroup):
        async with ctx.scope("subtask-error"):
            task = ctx.spawn(failing_task)
            # Yield control so the task can run and fail inside the scope
            await asyncio.sleep(0)

    assert task is not None
    assert task.done()
    assert not task.cancelled()
    exception = task.exception()
    assert isinstance(exception, FakeException)


@mark.asyncio
async def test_scope_disposables_error_is_propagated():
    with raises(RuntimeError, match="dispose failed"):
        async with ctx.scope(
            "disposable-error",
            disposables=(disposable_that_raises(lambda: RuntimeError("dispose failed")),),
        ):
            pass


@mark.asyncio
async def test_scope_disposables_base_exception_group_contains_all_errors():
    with raises(BaseExceptionGroup) as excinfo:
        async with ctx.scope(
            "disposable-errors",
            disposables=(
                disposable_that_raises(lambda: DisposableBaseError("base failure")),
                disposable_that_raises(lambda: RuntimeError("runtime failure")),
            ),
        ):
            pass

    errors = excinfo.value.exceptions
    assert any(isinstance(err, DisposableBaseError) for err in errors)
    assert any(isinstance(err, RuntimeError) for err in errors)
