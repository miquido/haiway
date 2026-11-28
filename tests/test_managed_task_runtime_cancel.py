import asyncio
from collections.abc import AsyncGenerator, Sequence
from contextlib import asynccontextmanager

from pytest import mark, raises

from haiway import ContextPresets, ObservabilityLevel, State, ctx
from haiway.context.managed import (
    InMemoryTaskStore,
    LeaseTimings,
    ManagedTask,
    ManagedTasks,
    TaskCancelled,
    TaskCompleted,
    TaskRetryPolicy,
    TaskRuntime,
    managed_task,
)
from tests.managed_helpers import LeasedStore, Output, Recording, Request, holding, wait_for

# --- FR-25, FR-26: the handle cancels a task ------------------------------------------------


@mark.asyncio
async def test_cancelling_a_running_task_returns_pending_and_the_outcome_follows() -> None:
    # FR-25.1, FR-86.3: no retry follows a cancel the caller asked for
    started = asyncio.Event()

    @managed_task(name="sleeps", retry=TaskRetryPolicy(delays=(0.0,)))
    async def sleeps(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(sleeps,))):
        task = await ManagedTasks.spawn(sleeps(Request()))
        await started.wait()
        await asyncio.sleep(0.01)
        loop = asyncio.get_running_loop()
        cancelled = loop.time()

        assert await task.cancel() == "pending"
        assert await wait_for(task.outcome(), 1.0) == TaskCancelled()
        assert loop.time() - cancelled < 0.1


@mark.asyncio
async def test_cancelling_a_finished_task_changes_nothing() -> None:
    # FR-26.1
    @managed_task(name="quick")
    async def quick(request: Request) -> Output:
        return Output(n=3)

    async with ctx.managed_tasks(TaskRuntime(tasks=(quick,))):
        task = await ManagedTasks.spawn(quick(Request()))
        outcome = await task.outcome()

        assert await task.cancel() == "finished"
        assert await task.outcome() == outcome == TaskCompleted(Output(n=3))


@mark.asyncio
async def test_two_cancels_of_a_running_task_are_both_pending() -> None:
    # FR-26.2
    started = asyncio.Event()

    @managed_task(name="sleeps")
    async def sleeps(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(sleeps,))):
        task = await ManagedTasks.spawn(sleeps(Request()))
        await started.wait()

        assert [await task.cancel(), await task.cancel()] == ["pending", "pending"]
        assert await wait_for(task.outcome(), 1.0) == TaskCancelled()


@mark.asyncio
async def test_cancelling_a_queued_task_finishes_it_at_once_and_logs_it_for_the_caller() -> None:
    # FR-26.3, FR-41.1, FR-41.2
    recording = Recording()
    started = asyncio.Event()
    calls: list[int] = []

    @managed_task(name="busy")
    async def busy(request: Request) -> Output:
        calls.append(request.n)
        started.set()
        await asyncio.sleep(10)
        return Output()

    async with ctx.scope("caller", observability=recording.observability):
        async with ctx.managed_tasks(
            TaskRuntime(
                tasks=(busy,),
                concurrency_limit=1,
                observability=recording.observability,
            )
        ):
            first = await ManagedTasks.spawn(busy(Request(n=1)))
            second = await ManagedTasks.spawn(busy(Request(n=2)))
            await started.wait()

            assert await second.cancel() == "cancelled"
            assert await wait_for(second.outcome(), 1.0) == TaskCancelled()
            assert await first.cancel() == "pending"

    assert calls == [1]
    logged = recording.messages(
        level=ObservabilityLevel.DEBUG,
        scope="caller",
        containing="cancelled before it ran",
    )
    assert len(logged) == 1 and str(second.identifier) in logged[0]


@mark.asyncio
async def test_a_cancel_during_the_cleanup_of_a_time_limit_is_the_outcome() -> None:
    # FR-26.5
    @managed_task(name="slow_cleanup")
    async def slow_cleanup(request: Request) -> Output:
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            await asyncio.sleep(0.2)
            raise

        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(slow_cleanup,))):
        task = await ManagedTasks.spawn(slow_cleanup(Request()), time_limit=0.05)
        await asyncio.sleep(0.1)

        assert await task.cancel() == "pending"
        assert await wait_for(task.outcome(), 2.0) == TaskCancelled()


@mark.asyncio
async def test_a_time_limit_elapsing_during_the_cleanup_of_a_cancel_is_the_cancellation() -> None:
    # FR-40.2
    cancelled = asyncio.Event()

    @managed_task(name="slow_cleanup")
    async def slow_cleanup(request: Request) -> Output:
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            cancelled.set()
            await asyncio.sleep(0.2)
            raise

        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(slow_cleanup,))):
        task = await ManagedTasks.spawn(slow_cleanup(Request()), time_limit=0.1)
        await asyncio.sleep(0.01)

        assert await task.cancel() == "pending"
        await wait_for(cancelled.wait(), 1.0)
        assert await wait_for(task.outcome(), 2.0) == TaskCancelled()


@mark.asyncio
async def test_a_cancel_while_the_attempt_scope_is_released_after_the_function_returned() -> None:
    # FR-26.4: the value which the function returned is lost, the outcome is the cancellation
    recording = Recording()
    releasing = asyncio.Event()
    finish = asyncio.Event()

    @asynccontextmanager
    async def slow_release() -> AsyncGenerator[Sequence[State]]:
        try:
            yield ()

        finally:
            releasing.set()
            await finish.wait()

    @managed_task(name="returns")
    async def returns(request: Request) -> Output:
        return Output(n=4242)

    with ctx.presets(ContextPresets.of("returns", disposables=(slow_release,))):
        async with ctx.managed_tasks(
            TaskRuntime(tasks=(returns,), observability=recording.observability)
        ):
            task = await ManagedTasks.spawn(returns(Request()))
            await releasing.wait()

            assert await task.cancel() == "pending"
            finish.set()
            assert await wait_for(task.outcome(), 2.0) == TaskCancelled()

    warnings = recording.messages(level=ObservabilityLevel.WARNING)
    assert len(warnings) == 1 and str(task.identifier) in warnings[0]
    assert all("4242" not in message for message in recording.messages())


@mark.asyncio
async def test_a_cancel_of_a_task_the_store_does_not_hold_raises_lookup_error() -> None:
    # FR-26
    store = InMemoryTaskStore(retention=0.05)

    @managed_task(name="quick")
    async def quick(request: Request) -> Output:
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(quick,))):
        task = await ManagedTasks.spawn(quick(Request()))
        await task.outcome()
        await asyncio.sleep(0.2)

        with raises(LookupError, match=str(task.identifier)):
            await task.cancel()


@mark.asyncio
async def test_a_cancel_after_the_close_is_finished_only_for_what_the_close_cancelled() -> None:
    # FR-77
    started = asyncio.Event()

    @managed_task(name="sleeps")
    async def sleeps(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    @managed_task(name="quick")
    async def quick(request: Request) -> Output:
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(sleeps, quick))):
        cut = await ManagedTasks.spawn(sleeps(Request()))
        await started.wait()
        done = await ManagedTasks.spawn(quick(Request()))
        await done.outcome()

    assert await cut.cancel() == "finished"
    with raises(RuntimeError):
        await done.cancel()


# --- FR-86: a cancel reaches the runtime which runs the task ---------------------------------


@mark.asyncio
async def test_a_cancel_from_another_runtime_of_one_in_memory_store_reaches_the_attempt() -> None:
    # FR-86.2, FR-26.6
    store = InMemoryTaskStore()
    started = asyncio.Event()

    @managed_task(name="sleeps")
    async def sleeps(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    @managed_task(name="other")
    async def other(request: Request) -> Output:
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(sleeps,))):
        task = await ManagedTasks.spawn(sleeps(Request()))
        await started.wait()
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(other,))):
            remote: ManagedTask[State | None] | None = await ManagedTasks.task(task.identifier)
            assert remote is not None
            loop = asyncio.get_running_loop()
            cancelled = loop.time()

            assert await remote.cancel() == "pending"

        assert await wait_for(task.outcome(), 1.0) == TaskCancelled()
        assert loop.time() - cancelled < 0.1


@mark.asyncio
async def test_a_cancel_from_another_runtime_of_a_leased_store_arrives_at_a_renewal() -> None:
    # FR-86.1
    timings = LeaseTimings(lease=1.0, renewal=0.2, polling=0.05)
    inner = InMemoryTaskStore()
    running, cancelling = LeasedStore(inner, timings=timings), LeasedStore(inner, timings=timings)
    started = asyncio.Event()

    @managed_task(name="sleeps")
    async def sleeps(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    @managed_task(name="other")
    async def other(request: Request) -> Output:
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=running, tasks=(sleeps,))):
        task = await ManagedTasks.spawn(sleeps(Request()))
        await started.wait()
        async with ctx.managed_tasks(TaskRuntime(store=cancelling, tasks=(other,))):
            remote = await ManagedTasks.task(task.identifier)
            assert remote is not None
            loop = asyncio.get_running_loop()
            cancelled = loop.time()

            assert await remote.cancel() == "pending"

        assert await wait_for(task.outcome(), 2.0) == TaskCancelled()
        assert loop.time() - cancelled < 0.8


@mark.asyncio
async def test_a_cancel_carried_by_a_claim_of_the_own_lease_reaches_the_attempt() -> None:
    # FR-84: a cancel request is delivered to the run which still holds the attempt, before the
    # next renewal would deliver it
    store = LeasedStore(timings=LeaseTimings(lease=1.0, renewal=0.5, polling=0.05))
    started = asyncio.Event()

    @managed_task(name="sleeps")
    async def sleeps(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(sleeps,))):
        task = await ManagedTasks.spawn(sleeps(Request()))
        await started.wait()
        async with holding(store.inner) as holder:
            assert await holder.cancel(task.identifier) == "pending"

        loop = asyncio.get_running_loop()
        asked = loop.time()
        store.expire(task.identifier)

        assert await wait_for(task.outcome(), 2.0) == TaskCancelled()
        assert loop.time() - asked < 0.4
