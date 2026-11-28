import asyncio
import time
from types import TracebackType
from typing import Any, final
from uuid import uuid4

from pytest import mark, raises

from haiway import ObservabilityLevel, State, ctx
from haiway.context.managed import (
    InMemoryTaskStore,
    ManagedTask,
    ManagedTasks,
    NewTask,
    StoredCompleted,
    StoredFinished,
    StoredRetryPending,
    TaskCancelled,
    TaskCompleted,
    TaskFilter,
    TaskRetryPolicy,
    TaskRuntime,
    managed_task,
)
from tests.managed_helpers import (
    CountingStore,
    Output,
    Recording,
    Request,
    Secret,
    eventually,
    holding,
)

# --- FR-29, FR-30: close -------------------------------------------------------------------


@mark.asyncio
async def test_close_cancels_running_tasks_at_once_with_the_default_grace_period() -> None:
    # FR-29.1, FR-30.1
    started = asyncio.Event()
    count = 0

    @managed_task(name="sleeps")
    async def sleeps(request: Request) -> Output:
        nonlocal count
        count += 1
        if count == 3:
            started.set()

        await asyncio.sleep(0.5)
        return Output()

    tasks: list[ManagedTask[Output]] = []
    begun = time.monotonic()
    async with ctx.managed_tasks(TaskRuntime(tasks=(sleeps,))):
        for _ in range(3):
            tasks.append(await ManagedTasks.spawn(sleeps(Request())))

        await started.wait()
        begun = time.monotonic()

    assert time.monotonic() - begun < 0.2
    assert [await task.outcome() for task in tasks] == [TaskCancelled()] * 3


@mark.asyncio
async def test_close_waits_for_the_grace_period() -> None:
    # FR-29.2
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        started = asyncio.Event()
        count = 0

        @managed_task(name="sleeps")
        async def sleeps(request: Request) -> Output:
            nonlocal count
            count += 1
            if count == 2:
                started.set()

            await asyncio.sleep(request.n / 10)
            return Output(n=request.n)

        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(sleeps,), grace_period=0.5)):
            quick = await ManagedTasks.spawn(sleeps(Request(n=1)))
            slow = await ManagedTasks.spawn(sleeps(Request(n=10)))
            await started.wait()
            begun = time.monotonic()

        assert 0.45 <= time.monotonic() - begun <= 0.8
        stored = await holder.read({quick.identifier, slow.identifier})
        quick_status = stored[quick.identifier].status
        assert isinstance(quick_status, StoredFinished)
        assert isinstance(quick_status.outcome, StoredCompleted)
        slow_status = stored[slow.identifier].status
        assert isinstance(slow_status, StoredFinished)
        assert slow_status.outcome.outcome == "cancelled"


@mark.asyncio
async def test_a_task_failing_during_the_grace_period_keeps_its_retry() -> None:
    # FR-29.3
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        started = asyncio.Event()

        @managed_task(name="fails_late", retry=TaskRetryPolicy(delays=(0.0,)))
        async def fails_late(request: Request) -> Output:
            started.set()
            await asyncio.sleep(0.1)
            raise ValueError

        runtime = TaskRuntime(store=store, tasks=(fails_late,), grace_period=0.5)
        async with ctx.managed_tasks(runtime):
            task = await ManagedTasks.spawn(fails_late(Request()))
            await started.wait()

        stored = (await holder.read({task.identifier}))[task.identifier]
        assert isinstance(stored.status, StoredRetryPending)
        assert len(stored.attempts) == 1


@mark.asyncio
async def test_a_task_sees_the_close_and_cannot_spawn_during_it() -> None:
    # FR-3.3, FR-31.1
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        seen: list[Any] = []

        @managed_task(name="other")
        async def other(request: Request) -> Output:
            return Output()

        @managed_task(name="watches")
        async def watches(request: Request) -> Output:
            while await ManagedTasks.available():
                await asyncio.sleep(0.01)

            seen.append(False)
            try:
                await ManagedTasks.spawn(other(Request()))

            except RuntimeError as exc:
                seen.append(exc)

            return Output()

        started = asyncio.Event()

        @managed_task(name="starts")
        async def starts(request: Request) -> Output:
            started.set()
            return await watches.bind(request.to_json())()  # runs the same function

        async with ctx.managed_tasks(
            TaskRuntime(store=store, tasks=(starts, other), grace_period=1.0)
        ):
            await ManagedTasks.spawn(starts(Request()))
            await started.wait()

        assert seen[0] is False
        assert isinstance(seen[1], RuntimeError)
        assert len(await holder.query(TaskFilter(), after=None, limit=10)) == 1


@mark.asyncio
async def test_cancelling_the_exit_does_not_abandon_the_close() -> None:
    # FR-29 (the exit itself is cancelled)
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        started = asyncio.Event()
        cleaned = asyncio.Event()
        handles: list[ManagedTask[Output]] = []

        @managed_task(name="slow_cleanup")
        async def slow_cleanup(request: Request) -> Output:
            started.set()
            try:
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                await asyncio.sleep(0.2)
                cleaned.set()
                raise

            return Output()

        async def block() -> None:
            async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(slow_cleanup,))):
                handles.append(await ManagedTasks.spawn(slow_cleanup(Request())))
                await started.wait()

        outer = asyncio.create_task(block())
        await started.wait()
        await asyncio.sleep(0.05)  # the exit is waiting for the cleanup
        outer.cancel()
        with raises(asyncio.CancelledError):
            await outer

        assert cleaned.is_set()
        stored = (await holder.read({handles[0].identifier}))[handles[0].identifier]
        assert isinstance(stored.status, StoredFinished)
        assert stored.status.outcome.outcome == "cancelled"


# --- FR-57: the lifetime of the snapshot ---------------------------------------------------


@mark.asyncio
async def test_tasks_are_cut_off_before_the_resources_around_the_block_exit() -> None:
    # FR-57.1
    order: list[str] = []
    started = asyncio.Event()

    @final
    class Pool:
        async def __aenter__(self) -> list[State]:
            return []

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            order.append("pool exit")

    @managed_task(name="holds_pool")
    async def holds_pool(request: Request) -> Output:
        started.set()
        try:
            await asyncio.sleep(10)

        finally:
            order.append("task cut off")

        return Output()

    async with ctx.scope("app", disposables=(Pool(),)):
        async with ctx.managed_tasks(TaskRuntime(tasks=(holds_pool,))):
            await ManagedTasks.spawn(holds_pool(Request()))
            await started.wait()

    assert order == ["task cut off", "pool exit"]


# --- FR-61: the close log ------------------------------------------------------------------


@mark.asyncio
async def test_close_logs_each_cut_off_task_without_its_argument() -> None:
    # FR-61.1
    recording = Recording()
    started: list[Any] = []

    @managed_task(name="secret_sleeper")
    async def secret_sleeper(request: Secret) -> Output:
        started.append(1)
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(secret_sleeper,), observability=recording.observability)
    ):
        first = await ManagedTasks.spawn(secret_sleeper(Secret(value="s3")))
        second = await ManagedTasks.spawn(secret_sleeper(Secret(value="s3")))
        await eventually(lambda: len(started) == 2)

    errors = recording.messages(level=ObservabilityLevel.ERROR)
    assert len(errors) == 2
    assert all("s3" not in error for error in errors)
    assert {str(first.identifier), str(second.identifier)} == {
        identifier
        for error in errors
        for identifier in (str(first.identifier), str(second.identifier))
        if identifier in error
    }
    assert all(
        record.scope.name == "managed_tasks"
        for record in recording.records
        if record.level == ObservabilityLevel.ERROR
    )


@mark.asyncio
async def test_a_task_finishing_within_the_grace_period_is_not_logged() -> None:
    # FR-61.2
    recording = Recording()
    started = asyncio.Event()

    @managed_task(name="quick")
    async def quick(request: Request) -> Output:
        started.set()
        await asyncio.sleep(0.1)
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(quick,), grace_period=1.0, observability=recording.observability)
    ):
        await ManagedTasks.spawn(quick(Request()))
        await started.wait()

    assert recording.messages(level=ObservabilityLevel.ERROR) == []


# --- FR-70, FR-77: handles after the close -------------------------------------------------


@mark.asyncio
async def test_handles_after_the_close_keep_the_cancellation_it_fixed() -> None:
    # FR-77.2, FR-70.5
    started = asyncio.Event()

    @managed_task(name="waits")
    async def waits(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(waits,))) as tasks:
        task = await ManagedTasks.spawn(waits(Request()))
        awaiting = asyncio.create_task(task.outcome())
        await started.wait()
        await asyncio.sleep(0.02)

    assert await awaiting == TaskCancelled()
    assert await task.outcome() == TaskCancelled()
    assert await task.status() == "finished"
    with raises(RuntimeError):
        await tasks.lookup(task.identifier)


@mark.asyncio
async def test_status_after_the_close_raises_for_a_task_finished_before() -> None:
    # FR-77.4
    @managed_task(name="quick")
    async def quick(request: Request) -> Output:
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(quick,))):
        task = await ManagedTasks.spawn(quick(Request()))
        await task.outcome()

    with raises(RuntimeError):
        await task.status()

    with raises(RuntimeError):
        await task.outcome()


# --- FR-46, FR-79, FR-85: runtimes sharing a store -----------------------------------------


@mark.asyncio
async def test_a_runtime_object_is_entered_once() -> None:
    # FR-46.1
    runtime = TaskRuntime()
    async with ctx.managed_tasks(runtime):
        with raises(RuntimeError):
            async with ctx.managed_tasks(runtime):
                raise AssertionError("not entered")

        assert await ManagedTasks.available() is True

    with raises(RuntimeError):
        async with ctx.managed_tasks(runtime):
            raise AssertionError("not entered")


@mark.asyncio
async def test_two_runtimes_on_one_store_both_run_tasks() -> None:
    # FR-46.2, FR-85.2
    store = InMemoryTaskStore()
    release = asyncio.Event()
    runners: list[str] = []

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        runners.append(request.n.__str__())
        await release.wait()
        return Output(n=request.n)

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,), concurrency_limit=1)):
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,), concurrency_limit=1)):
            # the innermost runtime is the one of the current context, and has one slot
            first = await ManagedTasks.spawn(work(Request(n=1)))
            second = await ManagedTasks.spawn(work(Request(n=2)))
            await asyncio.wait_for(eventually_both(runners), 0.1)
            release.set()
            assert await first.outcome() == TaskCompleted(Output(n=1))
            assert await second.outcome() == TaskCompleted(Output(n=2))


async def eventually_both(runners: list[str]) -> None:
    while len(runners) < 2:
        await asyncio.sleep(0.005)


@mark.asyncio
async def test_an_awaiter_in_another_runtime_receives_the_outcome() -> None:
    # FR-21.5
    store = InMemoryTaskStore()
    release = asyncio.Event()
    blocked = asyncio.Event()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        await release.wait()
        return Output(n=1)

    @managed_task(name="work")
    async def twin(request: Request) -> Output:
        blocked.set()
        await asyncio.Event().wait()
        return Output()

    @managed_task(name="blocker")
    async def blocker(request: Request) -> Output:
        blocked.set()
        await asyncio.Event().wait()
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        await asyncio.sleep(0.02)
        # the other runtime has no free slot, so it never runs the task itself
        async with ctx.managed_tasks(
            TaskRuntime(store=store, tasks=(twin, blocker), concurrency_limit=1)
        ):
            await ManagedTasks.spawn(blocker(Request()))
            await blocked.wait()
            found = await ManagedTasks.task(task.identifier)
            assert found is not None
            awaiting = asyncio.create_task(found.outcome())
            await asyncio.sleep(0.02)
            release.set()
            begun = time.monotonic()
            # FR-20.1: equal to the outcome in the runtime which ran the task
            assert await asyncio.wait_for(awaiting, 1.0) == await task.outcome()
            assert await task.outcome() == TaskCompleted(Output(n=1))
            assert time.monotonic() - begun < 0.1


@mark.asyncio
async def test_closing_a_runtime_cancels_its_task_and_leaves_its_queue_to_the_other() -> None:
    # FR-79.3
    store = InMemoryTaskStore()
    first_started = asyncio.Event()
    second_started = asyncio.Event()
    blocker_release = asyncio.Event()
    handles: list[ManagedTask[Output]] = []

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        if request.n == 1:
            first_started.set()
            await asyncio.sleep(10)

        else:
            second_started.set()

        return Output(n=request.n)

    @managed_task(name="blocker")
    async def blocker(request: Request) -> Output:
        await blocker_release.wait()
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(work, blocker), concurrency_limit=1)
    ):
        await ManagedTasks.spawn(blocker(Request()))
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,), concurrency_limit=1)):
            handles.append(await ManagedTasks.spawn(work(Request(n=1))))
            handles.append(await ManagedTasks.spawn(work(Request(n=2))))
            await first_started.wait()

        # the closed runtime cancelled its task, and left the queued one for the other
        assert await handles[0].outcome() == TaskCancelled()
        blocker_release.set()
        await asyncio.wait_for(second_started.wait(), 0.5)
        found = await ManagedTasks.task(handles[1].identifier)
        assert found is not None
        assert await found.outcome() == TaskCompleted(Output(n=2))


@mark.asyncio
async def test_a_retry_of_a_closed_runtime_starts_in_the_other_one_when_due() -> None:
    # FR-85.5
    store = InMemoryTaskStore()
    failed_at: list[float] = []
    started_at: list[float] = []
    second_run = asyncio.Event()
    blocker_release = asyncio.Event()

    @managed_task(name="retried", retry=TaskRetryPolicy(delays=(0.2,)))
    async def retried(request: Request) -> Output:
        if not failed_at:
            failed_at.append(time.monotonic())
            raise ValueError

        started_at.append(time.monotonic())
        second_run.set()
        return Output(n=1)

    @managed_task(name="blocker")
    async def blocker(request: Request) -> Output:
        await blocker_release.wait()
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(retried, blocker), concurrency_limit=1)
    ):
        await ManagedTasks.spawn(blocker(Request()))
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(retried,))):
            await ManagedTasks.spawn(retried(Request()))
            await eventually(lambda: bool(failed_at))
            await asyncio.sleep(0.02)  # the failure is recorded

        # the first runtime closed, the other one has a free slot from now on
        blocker_release.set()
        await asyncio.wait_for(second_run.wait(), 1.0)

    assert 0.2 <= started_at[0] - failed_at[0] <= 0.4


@mark.asyncio
async def test_a_runtime_with_no_free_slot_does_not_spin_on_an_overdue_task() -> None:
    # FR-85.6
    store = CountingStore()
    async with holding(store.inner) as holder:

        @managed_task(name="blocker")
        async def blocker(request: Request) -> Output:
            await asyncio.Event().wait()
            return Output()

        @managed_task(name="overdue")
        async def overdue(request: Request) -> Output:
            return Output()

        async with ctx.managed_tasks(
            TaskRuntime(store=store, tasks=(blocker, overdue), concurrency_limit=1)
        ):
            await ManagedTasks.spawn(blocker(Request()))
            await asyncio.sleep(0.05)
            waits = store.waits
            await holder.insert(
                NewTask(
                    identifier=uuid4(),
                    name="overdue",
                    argument="{}",
                    retry=TaskRetryPolicy(),
                    time_limit=None,
                    origin=None,
                )
            )
            await asyncio.sleep(0.5)
            assert store.waits - waits <= 2
