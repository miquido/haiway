import asyncio
from contextvars import ContextVar
from types import TracebackType
from typing import Any, final

from pytest import MonkeyPatch, mark, raises

from haiway import (
    ContextMissing,
    ContextPresets,
    ContextStateMissing,
    ObservabilityLevel,
    State,
    ctx,
)
from haiway.context import ContextIdentifier
from haiway.context.managed import (
    InMemoryTaskStore,
    ManagedTasks,
    TaskCompleted,
    TaskFailed,
    TaskFailureRecord,
    TaskOutcome,
    TaskRetryPolicy,
    TaskRuntime,
    TaskTimeLimitExceeded,
    managed_task,
)
from haiway.context.managed import runtime as runtime_module
from tests.managed_helpers import (
    Counts,
    Output,
    Recording,
    Request,
    counting_disposable,
    eventually,
)


class Number(State):
    n: int


class Other(State):
    n: int


class Needed(State):
    value: str  # no default, so it can not be constructed on demand


class Secret(State):
    value: str


class Ping(State):
    n: int


# --- FR-9, FR-56: the state of a task ----------------------------------------------------


@managed_task(name="reads")
async def reads(request: Request) -> Output:
    return Output(n=ctx.state(Number).n)


@mark.asyncio
async def test_a_task_reads_the_snapshot_and_the_preset_of_its_name() -> None:
    # FR-9.1
    preset = ContextPresets.of("reads", Number(n=1), Other(n=7))
    async with ctx.scope("app", Number(n=0)):
        with ctx.presets(preset):
            async with ctx.managed_tasks(TaskRuntime(tasks=(reads,))):
                task = await ManagedTasks.spawn(reads(Request()))
                assert await task.outcome() == TaskCompleted(Output(n=1))

        async with ctx.managed_tasks(TaskRuntime(tasks=(reads,))):
            task = await ManagedTasks.spawn(reads(Request()))
            assert await task.outcome() == TaskCompleted(Output(n=0))


@mark.asyncio
async def test_state_of_the_spawning_scope_does_not_reach_the_task() -> None:
    # FR-9.2
    @managed_task(name="needs")
    async def needs(request: Request) -> Output:
        try:
            ctx.state(Needed)

        except ContextStateMissing:
            return Output(n=1)

        return Output(n=0)

    async with ctx.managed_tasks(TaskRuntime(tasks=(needs,))):
        async with ctx.scope("spawning", Needed(value="here")):
            task = await ManagedTasks.spawn(needs(Request()))

        assert await task.outcome() == TaskCompleted(Output(n=1))


@mark.asyncio
async def test_preset_resources_are_entered_for_each_attempt() -> None:
    # FR-9.3
    counts = Counts()

    @managed_task(name="counted", retry=TaskRetryPolicy(delays=(0.0, 0.0)))
    async def counted(request: Request) -> Output:
        raise ValueError

    with ctx.presets(ContextPresets.of("counted", disposables=(counting_disposable(counts),))):
        async with ctx.managed_tasks(TaskRuntime(tasks=(counted,))):
            task = await ManagedTasks.spawn(counted(Request()))
            await task.outcome()

    assert (counts.entered, counts.exited) == (3, 3)


@mark.asyncio
async def test_state_merged_in_the_body_does_not_reach_tasks() -> None:
    # FR-56.1
    @managed_task(name="inner_state")
    async def inner_state(request: Request) -> Output:
        try:
            ctx.state(Needed)

        except ContextStateMissing:
            return Output(n=ctx.state(Number).n)

        return Output(n=-1)

    async with ctx.scope("app", Number(n=5)):
        async with ctx.managed_tasks(TaskRuntime(tasks=(inner_state,))):
            async with ctx.scope("inner", Needed(value="v")):
                task = await ManagedTasks.spawn(inner_state(Request()))
                assert await task.outcome() == TaskCompleted(Output(n=5))


@mark.asyncio
async def test_presets_entered_in_the_body_are_not_resolved_by_tasks() -> None:
    # FR-56.2
    async with ctx.scope("app", Number(n=0)):
        async with ctx.managed_tasks(TaskRuntime(tasks=(reads,))):
            with ctx.presets(ContextPresets.of("reads", Number(n=9))):
                task = await ManagedTasks.spawn(reads(Request()))
                assert await task.outcome() == TaskCompleted(Output(n=0))


# --- FR-10: the task context -------------------------------------------------------------

_APPLICATION_VARIABLE: ContextVar[int] = ContextVar("application_variable")


@mark.asyncio
async def test_application_context_variables_are_not_passed() -> None:
    # FR-10.1
    @managed_task(name="variable")
    async def variable(request: Request) -> Output:
        return Output(n=_APPLICATION_VARIABLE.get(-1))

    token = _APPLICATION_VARIABLE.set(7)
    try:
        async with ctx.managed_tasks(TaskRuntime(tasks=(variable,))):
            task = await ManagedTasks.spawn(variable(Request()))
            assert await task.outcome() == TaskCompleted(Output(n=-1))

    finally:
        _APPLICATION_VARIABLE.reset(token)


@mark.asyncio
async def test_the_task_scope_is_the_root_of_its_tree() -> None:
    # FR-10.2, FR-74.1
    recording = Recording()
    seen: list[ContextIdentifier] = []

    @managed_task(name="rooted")
    async def rooted(request: Request) -> Output:
        seen.append(ContextIdentifier.current())
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(rooted,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(rooted(Request()))
        await task.outcome()

    (root,) = (entered for entered in recording.scopes("rooted") if entered.scope.is_root)
    assert seen[0].parent_id == root.scope.scope_id
    assert seen[0].scope_id != root.scope.scope_id


@mark.asyncio
async def test_concurrent_tasks_resolve_a_preset_each() -> None:
    # FR-10.3
    barrier = asyncio.Barrier(2)

    @managed_task(name="shared_preset")
    async def shared_preset(request: Request) -> Output:
        await barrier.wait()
        return Output(n=ctx.state(Number).n)

    with ctx.presets(ContextPresets.of("shared_preset", Number(n=3))):
        async with ctx.managed_tasks(TaskRuntime(tasks=(shared_preset,))):
            first = await ManagedTasks.spawn(shared_preset(Request()))
            second = await ManagedTasks.spawn(shared_preset(Request()))
            assert await first.outcome() == TaskCompleted(Output(n=3))
            assert await second.outcome() == TaskCompleted(Output(n=3))


# --- FR-11, FR-12, FR-13, FR-16: backend, origin, names ----------------------------------


@mark.asyncio
async def test_a_task_records_through_the_backend_of_the_runtime_only() -> None:
    # FR-11.1
    spawning = Recording()
    runtime_backend = Recording()
    seen: list[ContextIdentifier] = []

    @managed_task(name="logs")
    async def logs(request: Request) -> Output:
        await asyncio.sleep(0.05)
        seen.append(ContextIdentifier.current())
        ctx.log_info("x")
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(logs,), observability=runtime_backend.observability)
    ):
        async with ctx.scope("spawning", observability=spawning.observability):
            task = await ManagedTasks.spawn(logs(Request()))

        await task.outcome()

    assert spawning.messages(containing="x") == []
    (record,) = [record for record in runtime_backend.records if record.message == "x"]
    assert record.scope.scope_id == seen[0].scope_id


@mark.asyncio
async def test_a_task_is_linked_to_the_spawning_scope_in_a_new_trace() -> None:
    # FR-12.1, FR-13.2
    recording = Recording()
    spawning = Recording()
    traces: list[str] = []
    origins: list[ContextIdentifier] = []

    @managed_task(name="linked")
    async def linked(request: Request) -> Output:
        traces.append(ctx.trace_id())
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(linked,), observability=recording.observability)
    ):
        async with ctx.scope("spawning", observability=spawning.observability):
            origins.append(ContextIdentifier.current())
            origin_trace = ctx.trace_id()
            task = await ManagedTasks.spawn(linked(Request()))

        await task.outcome()

    task_scope, attempt_scope = recording.scopes("linked")
    assert len(task_scope.links) == 1
    assert task_scope.links[0].scope_id == origins[0].scope_id
    assert task_scope.links[0].trace_id.hex == origin_trace
    assert traces[0] != origin_trace
    assert attempt_scope.links == ()


@mark.asyncio
async def test_a_task_spawned_outside_any_scope_has_no_link() -> None:
    # FR-12.2
    recording = Recording()
    async with ctx.managed_tasks(
        TaskRuntime(tasks=(reads,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(reads(Request()))
        await task.outcome()

    assert recording.scopes("reads")[0].links == ()


@mark.asyncio
async def test_task_scopes_are_named_by_the_registered_name() -> None:
    # FR-16.1
    recording = Recording()

    @managed_task(name="reports.build")
    async def build(request: Request) -> Output:
        ctx.log_info("working")
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(build,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(build(Request()))
        assert task.name == "reports.build"
        await task.outcome()

    assert {
        entered.scope.name for entered in recording.entered if entered.scope.name != "managed_tasks"
    } == {"reports.build"}
    assert all(entered.scope.kind == "internal" for entered in recording.entered)
    (record,) = [r for r in recording.records if r.message == "working"]
    assert record.scope.unique_name.startswith("[reports.build]")


@mark.asyncio
async def test_a_retried_task_keeps_one_task_scope_and_one_scope_per_attempt() -> None:
    # FR-74.1, FR-13.2
    recording = Recording()
    counts = Counts()

    @managed_task(name="twice", retry=TaskRetryPolicy(delays=(0.0,)))
    async def twice(request: Request) -> Output:
        raise ValueError

    with ctx.presets(ContextPresets.of("twice", disposables=(counting_disposable(counts),))):
        async with ctx.managed_tasks(
            TaskRuntime(tasks=(twice,), observability=recording.observability)
        ):
            task = await ManagedTasks.spawn(twice(Request()))
            outcome = await task.outcome()

    assert isinstance(outcome, TaskFailed)
    scopes = recording.scopes("twice")
    assert [entered.scope.is_root for entered in scopes] == [True, False, False]
    assert all(entered.links == () for entered in scopes[1:])
    # FR-74.2: entered by each attempt scope, never by the task scope
    assert (counts.entered, counts.exited) == (2, 2)


# --- FR-17, FR-37, FR-54: work inside a task, separate trees -----------------------------


@mark.asyncio
async def test_scoped_work_inside_a_task() -> None:
    # FR-17.1, FR-17.2, FR-17.3
    @managed_task(name="inner")
    async def inner(request: Request) -> Output:
        return Output(n=request.n + 1)

    @managed_task(name="working")
    async def working(request: Request) -> Output:
        async def helper() -> int:
            return 41

        assert await ctx.spawn(helper) == 41
        subscription = ctx.subscribe(Ping)
        ctx.send(Ping(n=2))
        event = await anext(subscription)
        child = await ManagedTasks.spawn(inner(Request(n=event.n)))
        outcome = await child.outcome()
        assert isinstance(outcome, TaskCompleted)
        return Output(n=outcome.value.n)

    async with ctx.managed_tasks(TaskRuntime(tasks=(working, inner))):
        task = await ManagedTasks.spawn(working(Request()))
        assert await task.outcome() == TaskCompleted(Output(n=3))


@mark.asyncio
async def test_spawning_is_allowed_while_a_disposable_is_prepared() -> None:
    # FR-37.1
    recording = Recording()
    entered_scope: list[ContextIdentifier] = []

    @managed_task(name="from_disposable")
    async def from_disposable(request: Request) -> Output:
        return Output()

    @final
    class Spawning:
        def __init__(self) -> None:
            self.task: Any = None

        async def __aenter__(self) -> list[State]:
            entered_scope.append(ContextIdentifier.current())
            self.task = await ManagedTasks.spawn(from_disposable(Request()))
            return []

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            pass

    spawning = Spawning()
    async with ctx.managed_tasks(
        TaskRuntime(tasks=(from_disposable,), observability=recording.observability)
    ):
        async with ctx.scope("preparing", disposables=(spawning,)):
            pass

        await spawning.task.outcome()

    assert recording.scopes("from_disposable")[0].links[0].scope_id == entered_scope[0].scope_id


@mark.asyncio
async def test_a_time_limit_cancels_the_subtree_of_the_attempt() -> None:
    # FR-54.1
    cancelled = asyncio.Event()

    @managed_task(name="subtree")
    async def subtree(request: Request) -> Output:
        async def long() -> None:
            try:
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                cancelled.set()
                raise

        await ctx.spawn(long)
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(subtree,))):
        task = await ManagedTasks.spawn(subtree(Request()), time_limit=0.05)
        outcome = await task.outcome()

    assert outcome == TaskFailed(failure=TaskTimeLimitExceeded(time_limit=0.05), attempts=1)
    assert cancelled.is_set()


@mark.asyncio
async def test_cancelling_the_spawning_scope_leaves_the_task_running() -> None:
    # FR-54.2
    release = asyncio.Event()
    spawned = asyncio.Event()
    handles: list[Any] = []

    @managed_task(name="detached")
    async def detached(request: Request) -> Output:
        await release.wait()
        return Output(n=1)

    async def spawning() -> None:
        async with ctx.scope("spawning"):
            handles.append(await ManagedTasks.spawn(detached(Request())))
            spawned.set()
            await asyncio.sleep(10)

    async with ctx.managed_tasks(TaskRuntime(tasks=(detached,))):
        spawner = asyncio.create_task(spawning())
        await spawned.wait()
        spawner.cancel()
        with raises(asyncio.CancelledError):
            await spawner

        assert await handles[0].status() in ("queued", "running")
        release.set()
        assert await handles[0].outcome() == TaskCompleted(Output(n=1))


@mark.asyncio
async def test_a_task_spawned_by_a_task_outlives_it() -> None:
    # FR-54.3
    release = asyncio.Event()
    children: list[Any] = []

    @managed_task(name="child")
    async def child(request: Request) -> Output:
        await release.wait()
        return Output(n=1)

    @managed_task(name="parent")
    async def parent(request: Request) -> Output:
        children.append(await ManagedTasks.spawn(child(Request())))
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(parent, child))):
        task = await ManagedTasks.spawn(parent(Request()), time_limit=0.05)
        await task.outcome()
        release.set()
        assert await children[0].outcome() == TaskCompleted(Output(n=1))

    blocked = asyncio.Event()

    @managed_task(name="blocked_child")
    async def blocked_child(request: Request) -> Output:
        await blocked.wait()
        return Output()

    @managed_task(name="blocked_parent")
    async def blocked_parent(request: Request) -> Output:
        children.append(await ManagedTasks.spawn(blocked_child(Request())))
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(blocked_parent, blocked_child))):
        task = await ManagedTasks.spawn(blocked_parent(Request()))
        await task.outcome()
        await eventually(lambda: len(children) == 2)

    # the close cuts the child off
    assert (await children[1].outcome()).__class__.__name__ == "TaskCancelled"


# --- FR-58: nested blocks ----------------------------------------------------------------


@mark.asyncio
async def test_a_nested_block_is_a_separate_block() -> None:
    # FR-58.1
    release = asyncio.Event()
    owners: list[ManagedTasks] = []

    @managed_task(name="owned")
    async def owned(request: Request) -> Output:
        owners.append(ctx.state(ManagedTasks))
        await release.wait()
        return Output(n=request.n)

    async with ctx.managed_tasks(TaskRuntime(tasks=(owned,))) as outer:
        outer_task = await ManagedTasks.spawn(owned(Request(n=1)))
        async with ctx.managed_tasks(TaskRuntime(tasks=(owned,))) as inner:
            inner_task = await ManagedTasks.spawn(owned(Request(n=2)))
            await eventually(lambda: len(owners) == 2)

        # closing the inner block cut its task off and left the outer one running
        assert owners[0] is outer
        assert owners[1] is inner
        assert await outer_task.status() == "running"
        release.set()
        assert await outer_task.outcome() == TaskCompleted(Output(n=1))

    assert inner_task.identifier != outer_task.identifier


# --- FR-20, FR-40, FR-47, FR-48: failures and releases ------------------------------------


@mark.asyncio
async def test_an_error_of_the_attempt_scope_exit_fails_the_attempt() -> None:
    # FR-20.4
    counts = Counts()

    @managed_task(name="exits_badly")
    async def exits_badly(request: Request) -> Output:
        return Output(n=1)

    with ctx.presets(
        ContextPresets.of("exits_badly", disposables=(counting_disposable(counts, fail_exit=True),))
    ):
        async with ctx.managed_tasks(TaskRuntime(tasks=(exits_badly,))):
            task = await ManagedTasks.spawn(exits_badly(Request()))
            outcome = await task.outcome()

    assert outcome == TaskFailed(
        failure=TaskFailureRecord(kind="raised", exception_type="builtins.OSError"),
        attempts=1,
    )


@mark.asyncio
async def test_a_failed_preparation_runs_nothing_and_releases_what_was_entered() -> None:
    # FR-47.1
    entered = Counts()
    failing = Counts()
    called: list[int] = []

    @managed_task(name="prepared")
    async def prepared(request: Request) -> Output:
        called.append(1)
        return Output()

    preset = ContextPresets.of(
        "prepared",
        disposables=(
            counting_disposable(entered),
            counting_disposable(failing, fail_enter=1),
        ),
    )
    with ctx.presets(preset):
        async with ctx.managed_tasks(TaskRuntime(tasks=(prepared,))):
            task = await ManagedTasks.spawn(prepared(Request()))
            outcome = await task.outcome()

    assert outcome == TaskFailed(
        failure=TaskFailureRecord(kind="preparation", exception_type="builtins.OSError"),
        attempts=1,
    )
    assert called == []
    assert (entered.entered, entered.exited) == (1, 1)


@mark.asyncio
async def test_a_preparation_failure_is_retried() -> None:
    # FR-47.2
    counts = Counts()

    @managed_task(name="prepared_once", retry=TaskRetryPolicy(delays=(0.0,)))
    async def prepared_once(request: Request) -> Output:
        return Output(n=1)

    preset = ContextPresets.of(
        "prepared_once",
        disposables=(counting_disposable(counts, fail_enter=1),),
    )
    with ctx.presets(preset):
        async with ctx.managed_tasks(TaskRuntime(tasks=(prepared_once,))):
            task = await ManagedTasks.spawn(prepared_once(Request()))
            assert await task.outcome() == TaskCompleted(Output(n=1))


@mark.asyncio
async def test_presets_are_released_before_the_outcome_for_every_ending() -> None:
    # FR-48.1 (a task cancelled through its handle is covered with the handle, phase 4b)
    counts = {name: Counts() for name in ("returning", "raising", "limited")}

    @managed_task(name="returning")
    async def returning(request: Request) -> Output:
        return Output()

    @managed_task(name="raising")
    async def raising(request: Request) -> Output:
        raise ValueError

    @managed_task(name="limited")
    async def limited(request: Request) -> Output:
        await asyncio.sleep(10)
        return Output()

    presets = [
        ContextPresets.of(name, disposables=(counting_disposable(counts[name]),)) for name in counts
    ]
    with ctx.presets(*presets):
        async with ctx.managed_tasks(TaskRuntime(tasks=(returning, raising, limited))):
            outcomes: list[TaskOutcome[Output]] = []
            for call, time_limit in (
                (returning(Request()), None),
                (raising(Request()), None),
                (limited(Request()), 0.05),
            ):
                task = await ManagedTasks.spawn(call, time_limit=time_limit)
                outcomes.append(await task.outcome())
                assert (counts[task.name].entered, counts[task.name].exited) == (1, 1)

    assert isinstance(outcomes[0], TaskCompleted)


@mark.asyncio
async def test_a_function_swallowing_the_time_limit_still_fails_by_it() -> None:
    # FR-40.1
    recording = Recording()

    @managed_task(name="swallows")
    async def swallows(request: Request) -> Output:
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            pass

        return Output(n=1)

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(swallows,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(swallows(Request()), time_limit=0.05)
        outcome = await task.outcome()

    assert outcome == TaskFailed(failure=TaskTimeLimitExceeded(time_limit=0.05), attempts=1)
    assert len(recording.messages(level=ObservabilityLevel.WARNING)) == 1


# --- FR-24, FR-42, NFR-4: records ----------------------------------------------------------


@mark.asyncio
async def test_failures_are_recorded_without_their_content() -> None:
    # FR-24.1, NFR-4
    recording = Recording()

    @managed_task(name="leaks", retry=TaskRetryPolicy(delays=(0.01,)))
    async def leaks(request: Request) -> Output:
        raise ValueError("secret")

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(leaks,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(leaks(Request()))
        await task.outcome()

    (warning,) = recording.messages(level=ObservabilityLevel.WARNING)
    (error,) = recording.messages(level=ObservabilityLevel.ERROR)
    for message in (warning, error):
        assert "builtins.ValueError" in message
        assert str(task.identifier) in message
        assert "secret" not in message


@mark.asyncio
async def test_records_hold_no_argument_and_no_value() -> None:
    # FR-42.1, NFR-4
    recording = Recording()
    spawning = Recording()

    @managed_task(name="secretive")
    async def secretive(request: Secret) -> Secret:
        return Secret(value="s4")

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(secretive,), observability=recording.observability)
    ):
        async with ctx.scope("spawning", observability=spawning.observability):
            task = await ManagedTasks.spawn(secretive(Secret(value="s3")))

        await task.outcome()

    messages = [record.message for record in (*recording.records, *spawning.records)]
    assert any(str(task.identifier) in message for message in messages)
    assert all("s3" not in message and "s4" not in message for message in messages)
    assert spawning.messages(level=ObservabilityLevel.DEBUG, containing=str(task.identifier))


# --- FR-52: the backend --------------------------------------------------------------------


@mark.asyncio
async def test_default_backend_is_one_logger_backend_without_context(
    monkeypatch: MonkeyPatch,
) -> None:
    # FR-52.1
    created: list[bool] = []
    original = runtime_module.LoggerObservability

    def spy(*args: Any, debug_context: bool = True) -> Any:
        created.append(debug_context)
        return original(*args, debug_context=debug_context)

    monkeypatch.setattr(runtime_module, "LoggerObservability", spy)
    async with ctx.managed_tasks(TaskRuntime(tasks=(reads,))):
        first = await ManagedTasks.spawn(reads(Request()))
        second = await ManagedTasks.spawn(reads(Request()))
        await first.outcome()
        await second.outcome()

    assert created == [False]


@mark.asyncio
async def test_the_backend_of_the_runtime_wins_over_the_one_of_the_block() -> None:
    # FR-52.2
    own = Recording()
    block = Recording()

    @managed_task(name="logs")
    async def logs(request: Request) -> Output:
        ctx.log_info("from-the-body")
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(logs,), observability=own.observability),
        observability=block.observability,
    ):
        await (await ManagedTasks.spawn(logs(Request()))).outcome()

    assert own.messages(containing="from-the-body") == ["from-the-body"]
    assert block.records == []

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(logs,)),
        observability=block.observability,
    ):
        await (await ManagedTasks.spawn(logs(Request()))).outcome()

    assert block.messages(containing="from-the-body") == ["from-the-body"]
    assert own.messages(containing="from-the-body") == ["from-the-body"]  # nothing was added


# --- FR-89: the message of a rejected spawn ------------------------------------------------


@mark.asyncio
async def test_ctx_spawn_while_disposables_are_prepared_names_managed_spawn() -> None:
    # FR-89.1
    errors: list[RuntimeError] = []

    async def work() -> None:
        pass

    @final
    class Spawning:
        async def __aenter__(self) -> list[State]:
            try:
                await ctx.spawn(work)

            except RuntimeError as exc:
                errors.append(exc)

            return []

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            pass

    async with ctx.scope("s", disposables=(Spawning(),)):
        pass

    assert len(errors) == 1
    assert "ManagedTasks.spawn" in str(errors[0])
    assert "background" not in str(errors[0])


def test_context_missing_is_not_a_missing_managed_tasks() -> None:
    # FR-5: both are context errors, the spawn raises the latter
    assert not issubclass(ContextMissing, ContextStateMissing)
    _ = InMemoryTaskStore
