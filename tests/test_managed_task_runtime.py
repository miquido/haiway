import asyncio
import threading
import time
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager
from uuid import UUID, uuid4

from pytest import mark, raises

from haiway import ContextStateMissing, State, ctx
from haiway.context.managed import (
    InMemoryTaskStore,
    ManagedTask,
    ManagedTasks,
    NewTask,
    StoredCompleted,
    StoredFinished,
    TaskCompleted,
    TaskFailed,
    TaskFailureRecord,
    TaskFilter,
    TaskOutcome,
    TaskRetryPolicy,
    TaskRuntime,
    TaskTimeLimitExceeded,
    managed_task,
)
from haiway.context.managed.store import TaskStoreSession
from tests.managed_helpers import Output, Request, eventually, holding


class Floating(State):
    x: float


# --- FR-2, FR-3, FR-5: availability and a missing ManagedTasks ---------------------------


@managed_task(name="double")
async def double(request: Request) -> Output:
    return Output(n=request.n * 2)


@managed_task(name="fails")
async def fails(request: Request) -> Output:
    raise ValueError("secret")


@mark.asyncio
async def test_missing_managed_tasks_is_never_constructed() -> None:
    # FR-2.1
    async with ctx.scope("s"):
        with raises(ContextStateMissing):
            ctx.state(ManagedTasks)

        assert not ctx.contains_state(ManagedTasks)


@mark.asyncio
async def test_available_is_false_without_a_block() -> None:
    # FR-3.1
    assert await ManagedTasks.available() is False
    async with ctx.scope("s"):
        assert await ManagedTasks.available() is False


@mark.asyncio
async def test_available_in_a_block_only() -> None:
    # FR-3.2
    async with ctx.managed_tasks(TaskRuntime(tasks=(double,))):
        assert await ManagedTasks.available() is True

    assert await ManagedTasks.available() is False


@mark.asyncio
async def test_spawn_without_a_block_raises_and_calls_nothing() -> None:
    # FR-5.1
    called: list[Request] = []

    @managed_task(name="report")
    async def report(request: Request) -> Output:
        called.append(request)
        return Output()

    async with ctx.scope("s"):
        with raises(ContextStateMissing):
            await ManagedTasks.spawn(report(Request()))

    assert called == []


@mark.asyncio
async def test_lookup_outside_any_scope_raises() -> None:
    # FR-5.2
    with raises(ContextStateMissing):
        await ManagedTasks.task(uuid4())


@mark.asyncio
async def test_block_yields_the_state_it_puts_into_the_body() -> None:
    # FR-55.1
    async with ctx.managed_tasks(TaskRuntime()) as tasks:
        assert ctx.state(ManagedTasks) is tasks

    assert not ctx.contains_state(ManagedTasks)


# --- FR-4, FR-18, FR-62, FR-99: spawning --------------------------------------------------


@mark.asyncio
async def test_spawn_returns_before_the_task_ends_and_stores_it() -> None:
    # FR-4.1, FR-4.2
    release = asyncio.Event()

    @managed_task(name="waits")
    async def waits(request: Request) -> Output:
        await release.wait()
        return Output(n=1)

    store = InMemoryTaskStore()
    async with holding(store) as holder:
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(waits,))):
            async with ctx.scope("a"):
                async with ctx.scope("b"):
                    async with ctx.scope("c"):
                        async with ctx.scope("d"):
                            async with ctx.scope("e"):
                                task = await ManagedTasks.spawn(waits(Request()))

            # the nested scopes exited without waiting for the task
            assert task.identifier in await holder.read({task.identifier})
            assert await task.status() in ("queued", "running")
            release.set()
            assert await task.outcome() == TaskCompleted(Output(n=1))


def test_spawn_docstring_mentions_transactions() -> None:
    # FR-4.3
    assert "transaction" in (ManagedTasks.spawn.__doc__ or "")


@mark.asyncio
async def test_identifiers_are_distinct() -> None:
    # FR-18.1
    async with ctx.managed_tasks(TaskRuntime(tasks=(double,))):
        tasks = [await ManagedTasks.spawn(double(Request(n=1))) for _ in range(1000)]
        assert len({task.identifier for task in tasks}) == 1000
        for task in tasks:
            await task.outcome()


def test_identifier_docstring_says_it_is_not_a_credential() -> None:
    # FR-18.2
    assert "not a credential" in (ManagedTask.identifier.__doc__ or "")


@mark.asyncio
async def test_spawn_of_an_unlisted_task_raises_and_stores_nothing() -> None:
    # FR-62.3
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(double,))):
            with raises(ValueError, match="fails"):
                await ManagedTasks.spawn(fails(Request()))

        assert await holder.query(TaskFilter(), after=None, limit=10) == []


@mark.asyncio
async def test_spawn_of_another_task_under_a_listed_name_raises() -> None:
    # FR-99.1, FR-62.3
    @managed_task(name="double")
    async def impostor(request: Request) -> Output:
        return Output(n=-1)

    store = InMemoryTaskStore()
    async with holding(store) as holder:
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(double,))):
            with raises(ValueError, match="double"):
                await ManagedTasks.spawn(impostor(Request()))

            # FR-99.2
            task = await ManagedTasks.spawn(double(Request(n=2)))
            assert await task.outcome() == TaskCompleted(Output(n=4))

        assert len(await holder.query(TaskFilter(), after=None, limit=10)) == 1


@mark.asyncio
@mark.parametrize("limit", [0, -1, float("nan"), float("inf")])
async def test_invalid_time_limit_raises(limit: float) -> None:
    # FR-27.4
    async with ctx.managed_tasks(TaskRuntime(tasks=(double,))):
        with raises(ValueError):
            await ManagedTasks.spawn(double(Request()), time_limit=limit)


# --- FR-19, FR-20, FR-21, FR-76: status and outcomes -------------------------------------


@mark.asyncio
async def test_completed_outcome_holds_the_decoded_value() -> None:
    # FR-20.1, FR-70.2
    async with ctx.managed_tasks(TaskRuntime(tasks=(double,))):
        task = await ManagedTasks.spawn(double(Request(n=5)))
        assert await task.outcome() == TaskCompleted(Output(n=10))
        assert await task.status() == "finished"
        found = await ManagedTasks.task(task.identifier)
        assert found is not None
        assert await found.status() == "finished"
        assert await found.outcome() == TaskCompleted(Output(n=10))


@mark.asyncio
async def test_a_task_returning_none_completes_with_none() -> None:
    # FR-76
    @managed_task(name="nothing")
    async def nothing(request: Request) -> None:
        return None

    async with ctx.managed_tasks(TaskRuntime(tasks=(nothing,))):
        task = await ManagedTasks.spawn(nothing(Request()))
        assert await task.outcome() == TaskCompleted(None)


@mark.asyncio
async def test_failed_outcome_holds_the_failure_and_the_attempts() -> None:
    # FR-20.2
    async with ctx.managed_tasks(TaskRuntime(tasks=(fails,))):
        task = await ManagedTasks.spawn(fails(Request()))
        outcome = await task.outcome()

    assert outcome == TaskFailed(
        failure=TaskFailureRecord(kind="raised", exception_type="builtins.ValueError"),
        attempts=1,
    )


@mark.asyncio
async def test_a_failing_subtask_gives_its_own_error_type() -> None:
    # FR-20.3
    @managed_task(name="spawning")
    async def spawning(request: Request) -> Output:
        async def failing() -> None:
            raise KeyError("x")

        await ctx.spawn(failing)
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(spawning,))):
        task = await ManagedTasks.spawn(spawning(Request()))
        outcome = await task.outcome()

    assert outcome == TaskFailed(
        failure=TaskFailureRecord(kind="raised", exception_type="builtins.KeyError"),
        attempts=1,
    )


@mark.asyncio
async def test_a_value_encoding_failure_is_a_failure_of_the_attempt() -> None:
    # FR-76.2
    @managed_task(name="nan")
    async def nan(request: Request) -> Floating:
        return Floating(x=float("nan"))

    async with ctx.managed_tasks(TaskRuntime(tasks=(nan,))):
        task = await ManagedTasks.spawn(nan(Request()))
        outcome = await task.outcome()

    assert outcome == TaskFailed(
        failure=TaskFailureRecord(kind="raised", exception_type="builtins.ValueError"),
        attempts=1,
    )


@mark.asyncio
async def test_two_awaiters_receive_equal_outcomes() -> None:
    # FR-21.1
    async with ctx.managed_tasks(TaskRuntime(tasks=(fails,))):
        task = await ManagedTasks.spawn(fails(Request()))
        first, second = await asyncio.gather(task.outcome(), task.outcome())

    assert first == second
    assert isinstance(first, TaskFailed)


@mark.asyncio
async def test_cancelling_an_awaiter_leaves_the_task_running() -> None:
    # FR-21.2
    release = asyncio.Event()

    @managed_task(name="waits")
    async def waits(request: Request) -> Output:
        await release.wait()
        return Output(n=1)

    async with ctx.managed_tasks(TaskRuntime(tasks=(waits,))):
        task = await ManagedTasks.spawn(waits(Request()))
        awaiter = asyncio.create_task(task.outcome())
        other = asyncio.create_task(task.outcome())
        await asyncio.sleep(0.02)
        awaiter.cancel()
        with raises(asyncio.CancelledError):
            await awaiter

        await eventually(lambda: True)
        assert await task.status() == "running"
        release.set()
        assert await other == TaskCompleted(Output(n=1))
        assert await task.outcome() == TaskCompleted(Output(n=1))


@mark.asyncio
async def test_status_follows_the_queue_under_a_concurrency_limit() -> None:
    # FR-19.1, FR-33.1
    release = asyncio.Event()

    @managed_task(name="waits")
    async def waits(request: Request) -> Output:
        await release.wait()
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(waits,), concurrency_limit=1)):
        first = await ManagedTasks.spawn(waits(Request()))
        second = await ManagedTasks.spawn(waits(Request()))
        await eventually(lambda: True)
        await asyncio.sleep(0.02)
        assert await first.status() == "running"
        assert await second.status() == "queued"
        release.set()
        await first.outcome()
        await second.outcome()
        assert await second.status() == "finished"


@mark.asyncio
async def test_a_second_task_runs_once_the_first_finished() -> None:
    # FR-19.1
    release = asyncio.Event()
    started = asyncio.Event()

    @managed_task(name="waits")
    async def waits(request: Request) -> Output:
        if request.n == 1:
            await release.wait()

        else:
            started.set()

        return Output(n=request.n)

    async with ctx.managed_tasks(TaskRuntime(tasks=(waits,), concurrency_limit=1)):
        first = await ManagedTasks.spawn(waits(Request(n=1)))
        second = await ManagedTasks.spawn(waits(Request(n=2)))
        await asyncio.sleep(0.02)
        assert not started.is_set()
        release.set()
        assert await second.outcome() == TaskCompleted(Output(n=2))
        assert await first.outcome() == TaskCompleted(Output(n=1))


@mark.asyncio
async def test_retry_pending_status_during_the_delay() -> None:
    # FR-19.2
    @managed_task(name="fails_once")
    async def fails_once(request: Request) -> Output:
        raise ValueError

    runtime = TaskRuntime(tasks=(fails_once,), retry=TaskRetryPolicy(delays=(0.2,)))
    async with ctx.managed_tasks(runtime):
        task = await ManagedTasks.spawn(fails_once(Request()))
        await eventually(lambda: True)
        await asyncio.sleep(0.1)
        assert await task.status() == "retry_pending"
        outcome = await task.outcome()
        assert isinstance(outcome, TaskFailed)
        assert outcome.attempts == 2


# --- FR-23: failure isolation ------------------------------------------------------------


@mark.asyncio
async def test_a_failing_task_does_not_affect_another() -> None:
    # FR-23.1
    async with ctx.managed_tasks(TaskRuntime(tasks=(fails, double))):
        failing = await ManagedTasks.spawn(fails(Request()))
        returning = await ManagedTasks.spawn(double(Request(n=1)))
        assert isinstance(await failing.outcome(), TaskFailed)
        assert await returning.outcome() == TaskCompleted(Output(n=2))


@mark.asyncio
async def test_a_task_in_a_retry_delay_does_not_delay_another() -> None:
    # FR-23.2
    @managed_task(name="flaky")
    async def flaky(request: Request) -> Output:
        raise ValueError

    runtime = TaskRuntime(
        tasks=(flaky, double),
        concurrency_limit=1,
        retry=TaskRetryPolicy(delays=(0.5,)),
    )
    async with ctx.managed_tasks(runtime):
        retrying = await ManagedTasks.spawn(flaky(Request()))
        await eventually(lambda: True)
        await asyncio.sleep(0.05)
        started = time.monotonic()
        other = await ManagedTasks.spawn(double(Request(n=1)))
        assert await other.outcome() == TaskCompleted(Output(n=2))
        assert time.monotonic() - started < 0.2
        assert await retrying.status() in ("retry_pending", "queued", "running")


# --- FR-27, FR-66, NFR-3: time limit and retries -----------------------------------------


@mark.asyncio
async def test_time_limit_fails_the_attempt() -> None:
    # FR-27.1, NFR-3
    @managed_task(name="sleeps")
    async def sleeps(request: Request) -> Output:
        await asyncio.sleep(1)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(sleeps,))):
        for _ in range(20):
            started = time.monotonic()
            task = await ManagedTasks.spawn(sleeps(Request()), time_limit=0.05)
            outcome = await task.outcome()
            elapsed = time.monotonic() - started
            assert outcome == TaskFailed(
                failure=TaskTimeLimitExceeded(time_limit=0.05),
                attempts=1,
            )
            assert 0.05 <= elapsed <= 0.5


@mark.asyncio
async def test_time_limit_is_counted_per_attempt() -> None:
    # FR-27.2
    @managed_task(name="slow_first")
    async def slow_first(request: Request) -> Output:
        if request.n == 0:
            await asyncio.sleep(1)

        return Output()

    attempts: list[int] = []

    @managed_task(name="slow_then_quick", retry=TaskRetryPolicy(delays=(0.0,)))
    async def slow_then_quick(request: Request) -> Output:
        attempts.append(1)
        if len(attempts) == 1:
            await asyncio.sleep(1)

        return Output(n=len(attempts))

    async with ctx.managed_tasks(TaskRuntime(tasks=(slow_then_quick,))):
        task = await ManagedTasks.spawn(slow_then_quick(Request()), time_limit=0.05)
        assert await task.outcome() == TaskCompleted(Output(n=2))


@mark.asyncio
async def test_time_limit_does_not_count_the_queue() -> None:
    # FR-27.3
    release = asyncio.Event()

    @managed_task(name="waits")
    async def waits(request: Request) -> Output:
        if request.n == 0:
            await release.wait()

        else:
            await asyncio.sleep(0.01)

        return Output(n=request.n)

    async with ctx.managed_tasks(TaskRuntime(tasks=(waits,), concurrency_limit=1)):
        await ManagedTasks.spawn(waits(Request(n=0)))
        limited = await ManagedTasks.spawn(waits(Request(n=1)), time_limit=0.05)
        await asyncio.sleep(0.2)
        release.set()
        assert await limited.outcome() == TaskCompleted(Output(n=1))


@mark.asyncio
async def test_retries_follow_the_policy() -> None:
    # FR-66.1
    async with ctx.managed_tasks(
        TaskRuntime(tasks=(fails,), retry=TaskRetryPolicy(delays=(0.05, 0.1)))
    ):
        task = await ManagedTasks.spawn(fails(Request()))
        outcome = await task.outcome()

    assert isinstance(outcome, TaskFailed)
    assert outcome.attempts == 3


@mark.asyncio
async def test_a_task_override_wins_over_the_runtime_default() -> None:
    # FR-66.2
    runs: list[int] = []

    @managed_task(name="override", retry=TaskRetryPolicy(delays=(0.0,)))
    async def override(request: Request) -> Output:
        runs.append(1)
        raise ValueError

    async with ctx.managed_tasks(TaskRuntime(tasks=(override,))):
        task = await ManagedTasks.spawn(override(Request()))
        outcome = await task.outcome()

    assert len(runs) == 2
    assert isinstance(outcome, TaskFailed)
    assert outcome.attempts == 2


@mark.asyncio
async def test_attempt_records_hold_no_messages() -> None:
    # FR-65.1
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        async with ctx.managed_tasks(
            TaskRuntime(store=store, tasks=(fails,), retry=TaskRetryPolicy(delays=(0.0,)))
        ):
            task = await ManagedTasks.spawn(fails(Request()))
            await task.outcome()
            stored = (await holder.read({task.identifier}))[task.identifier]

        assert len(stored.attempts) == 2
        assert "builtins.ValueError" in stored.to_json()
        assert "secret" not in stored.to_json()


# --- FR-33, FR-34, FR-45, FR-81: concurrency, loops, settings ----------------------------


@mark.asyncio
async def test_queue_keeps_the_order_and_the_limit() -> None:
    # FR-33.1
    running = 0
    peak = 0
    order: list[int] = []

    @managed_task(name="ordered")
    async def ordered(request: Request) -> Output:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        order.append(request.n)
        await asyncio.sleep(0.05)
        running -= 1
        return Output(n=request.n)

    async with ctx.managed_tasks(TaskRuntime(tasks=(ordered,), concurrency_limit=2)):
        tasks = [await ManagedTasks.spawn(ordered(Request(n=n))) for n in range(5)]
        for task in tasks:
            await task.outcome()

    assert order == [0, 1, 2, 3, 4]
    assert peak == 2


@mark.parametrize("limit", [0, -1])
def test_concurrency_limit_below_one_raises(limit: int) -> None:
    # FR-33.2, FR-45.1
    with raises(ValueError, match="concurrency_limit"):
        TaskRuntime(concurrency_limit=limit)


def test_runtime_docstring_recommends_a_limit() -> None:
    # FR-33.3
    doc = TaskRuntime.__doc__ or ""
    assert "concurrency_limit" in doc
    assert "shared store" in doc


def test_settings_out_of_range_raise() -> None:
    # FR-30.2, FR-45.1
    with raises(ValueError, match="grace_period"):
        TaskRuntime(grace_period=-0.1)

    with raises(ValueError, match="grace_period"):
        TaskRuntime(grace_period=float("nan"))


def test_two_tasks_of_one_name_raise() -> None:
    # FR-45.1, FR-45.2
    @managed_task(name="r")
    async def first(request: Request) -> Output:
        return Output()

    @managed_task(name="r")
    async def second(request: Request) -> Output:
        return Output()

    with raises(ValueError, match="tasks"):
        TaskRuntime(tasks=(first, second))


def test_a_lease_setting_is_not_accepted() -> None:
    # FR-81.1
    with raises(TypeError):
        TaskRuntime(**{"lease": 1.0})


@mark.asyncio
async def test_default_store_is_in_memory() -> None:
    # FR-81.1
    async with ctx.managed_tasks(TaskRuntime(tasks=(double,))):
        task = await ManagedTasks.spawn(double(Request(n=1)))
        assert await task.outcome() == TaskCompleted(Output(n=2))


@mark.asyncio
async def test_spawn_from_another_event_loop_raises() -> None:
    # FR-34.2
    errors: list[BaseException] = []

    async with ctx.managed_tasks(TaskRuntime(tasks=(double,))) as tasks:
        call = double(Request())

        def other_loop() -> None:
            async def spawn() -> None:
                await tasks.spawning(call, time_limit=None, origin=None)

            try:
                asyncio.run(spawn())

            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=other_loop)
        thread.start()
        await asyncio.to_thread(thread.join)

    assert len(errors) == 1
    assert isinstance(errors[0], RuntimeError)


@mark.asyncio
async def test_concurrent_spawns_each_yield_one_outcome() -> None:
    # FR-34.1, NFR-1
    async def spawn_ten(offset: int) -> list[ManagedTask[Output]]:
        async with ctx.scope(f"spawner-{offset}"):
            return [
                await ManagedTasks.spawn(double(Request(n=offset * 10 + index)))
                for index in range(10)
            ]

    async with ctx.managed_tasks(TaskRuntime(tasks=(double,))):
        spawned = await asyncio.gather(*(spawn_ten(offset) for offset in range(100)))
        tasks = [task for group in spawned for task in group]
        outcomes: Sequence[TaskOutcome[Output]] = await asyncio.gather(
            *(task.outcome() for task in tasks)
        )

    assert len({task.identifier for task in tasks}) == 1000
    assert len(outcomes) == 1000
    assert all(isinstance(outcome, TaskCompleted) for outcome in outcomes)
    assert sorted(
        outcome.value.n for outcome in outcomes if isinstance(outcome, TaskCompleted)
    ) == sorted(n * 2 for n in range(1000))


@mark.asyncio
async def test_spawning_scope_exit_does_not_wait() -> None:
    # NFR-2
    @managed_task(name="sleeps")
    async def sleeps(request: Request) -> Output:
        await asyncio.sleep(1)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(sleeps,))):
        for _ in range(20):
            scope = ctx.scope("spawning")
            await scope.__aenter__()
            await ManagedTasks.spawn(sleeps(Request()))
            started = time.monotonic()
            await scope.__aexit__(None, None, None)
            assert time.monotonic() - started < 0.010


# --- FR-32, FR-70: lookup and missing tasks ----------------------------------------------


@mark.asyncio
async def test_unknown_identifier_gives_none() -> None:
    # FR-70.1
    async with ctx.managed_tasks(TaskRuntime(tasks=(double,))):
        assert await ManagedTasks.task(uuid4()) is None


@mark.asyncio
async def test_unlisted_name_gives_a_status_only_handle() -> None:
    # FR-70.3
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        identifier: UUID = uuid4()
        await holder.insert(
            NewTask(
                identifier=identifier,
                name="r",
                argument="{}",
                retry=TaskRetryPolicy(),
                time_limit=None,
                origin=None,
            )
        )
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(double,))):
            task = await ManagedTasks.task(identifier)
            assert task is not None
            assert await task.status() == "queued"
            with raises(ValueError, match="r"):
                await task.outcome()


@mark.asyncio
async def test_retention_drops_the_task_from_lookup_and_handles() -> None:
    # FR-32.1, FR-70.4
    store = InMemoryTaskStore(retention=0.05)
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(double,))):
        task = await ManagedTasks.spawn(double(Request(n=1)))
        await task.outcome()
        assert await ManagedTasks.task(task.identifier) is not None
        await asyncio.sleep(0.2)
        assert await ManagedTasks.task(task.identifier) is None
        with raises(LookupError, match=str(task.identifier)):
            await task.outcome()

        with raises(LookupError, match=str(task.identifier)):
            await task.status()


# --- FR-63: argument decoding at the attempt ---------------------------------------------


@mark.asyncio
async def test_a_stored_argument_with_an_unknown_key_runs() -> None:
    # FR-63.3
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        identifier: UUID = uuid4()
        await holder.insert(
            NewTask(
                identifier=identifier,
                name="double",
                argument='{"n": 4, "added": 1}',
                retry=TaskRetryPolicy(),
                time_limit=None,
                origin=None,
            )
        )
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(double,))):
            task = await ManagedTasks.task(identifier)
            assert task is not None
            assert await task.outcome() == TaskCompleted(Output(n=8))


@mark.asyncio
async def test_a_stored_argument_violating_its_schema_is_a_preparation_failure() -> None:
    # FR-47 (decoding), FR-63.2
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        identifier: UUID = uuid4()
        await holder.insert(
            NewTask(
                identifier=identifier,
                name="double",
                argument='{"n": "text"}',
                retry=TaskRetryPolicy(),
                time_limit=None,
                origin=None,
            )
        )
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(double,))):
            task = await ManagedTasks.task(identifier)
            assert task is not None
            outcome = await task.outcome()

        assert outcome == TaskFailed(
            failure=TaskFailureRecord(kind="preparation", exception_type="builtins.ValueError"),
            attempts=1,
        )


@mark.asyncio
async def test_a_task_left_in_a_store_runs_when_the_block_opens() -> None:
    # FR-55.3, FR-85.4
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        done = asyncio.Event()

        @managed_task(name="left")
        async def left(request: Request) -> Output:
            done.set()
            return Output()

        await holder.insert(
            NewTask(
                identifier=uuid4(),
                name="left",
                argument="{}",
                retry=TaskRetryPolicy(),
                time_limit=None,
                origin=None,
            )
        )
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(left,))):
            await asyncio.wait_for(done.wait(), 0.1)


@mark.asyncio
async def test_a_store_failing_to_open_raises_from_the_block() -> None:
    # FR-55.2
    class Failing:
        def open(self) -> AbstractAsyncContextManager[TaskStoreSession]:
            raise OSError("open failed")

    with raises(OSError, match="open failed"):
        async with ctx.managed_tasks(TaskRuntime(store=Failing())):
            raise AssertionError("not entered")

    assert not ctx.contains_state(ManagedTasks)


def test_block_docstring_states_what_entering_does() -> None:
    # FR-55.4, FR-57.2
    doc = ctx.managed_tasks.__doc__ or ""
    assert "starts executing" in doc
    assert "connection" in doc


@mark.asyncio
async def test_stored_result_text_is_the_completed_outcome_of_the_store() -> None:
    # FR-4.2, FR-20.1: what the store holds is what handles see
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(double,))):
            task = await ManagedTasks.spawn(double(Request(n=3)))
            await task.outcome()
            stored = (await holder.read({task.identifier}))[task.identifier]

        assert isinstance(stored.status, StoredFinished)
        assert isinstance(stored.status.outcome, StoredCompleted)
        assert Output.from_json(stored.status.outcome.result) == Output(n=6)
