import asyncio
import time
from itertools import pairwise
from uuid import UUID, uuid4

from pytest import LogCaptureFixture, mark, raises

from haiway import ObservabilityLevel, ctx
from haiway.context.managed import (
    InMemoryTaskStore,
    LeaseTimings,
    ManagedTask,
    ManagedTasks,
    NewTask,
    TaskCompleted,
    TaskRetryPolicy,
    TaskRuntime,
    managed_task,
)
from tests.managed_helpers import (
    CountingStore,
    LeasedStore,
    Output,
    Recording,
    Request,
    eventually,
    holding,
)

# --- FR-21: waking awaiters -----------------------------------------------------------------


@mark.asyncio
async def test_outcomes_of_another_runtime_are_read_once_for_each_polling_interval() -> None:
    # FR-21.3
    inner = InMemoryTaskStore()
    running = LeasedStore(inner)
    awaiting = LeasedStore(inner)
    blocked = asyncio.Event()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        await asyncio.sleep(0.4)
        return Output(n=request.n)

    @managed_task(name="work")
    async def twin(request: Request) -> Output:
        return Output()

    @managed_task(name="blocker")
    async def blocker(request: Request) -> Output:
        blocked.set()
        await asyncio.Event().wait()
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=running, tasks=(work,))):
        spawned = [await ManagedTasks.spawn(work(Request(n=n))) for n in range(50)]
        async with ctx.managed_tasks(
            TaskRuntime(store=awaiting, tasks=(twin, blocker), concurrency_limit=1)
        ):
            await ManagedTasks.spawn(blocker(Request()))
            await blocked.wait()
            handles = [await ManagedTasks.task(task.identifier) for task in spawned]
            assert all(handle is not None for handle in handles)
            awaiting.reads.clear()
            outcomes = await asyncio.gather(
                *(handle.outcome() for handle in handles if handle is not None)
            )

    assert sorted(
        outcome.value.n for outcome in outcomes if isinstance(outcome, TaskCompleted)
    ) == list(range(50))
    times = [moment for moment, _ in awaiting.reads]
    assert len(times) >= 2
    assert all(len(identifiers) <= 50 for _, identifiers in awaiting.reads)
    # one read at most for each polling interval
    assert all(later - earlier >= 0.099 for earlier, later in pairwise(times))


@mark.asyncio
async def test_an_awaiter_is_woken_when_a_committed_record_raised() -> None:
    # FR-21.4, FR-82.3
    store = LeasedStore(timings=LeaseTimings(lease=1.0, renewal=0.3, polling=0.1))
    store.record_error_after_commit = 1

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output(n=1)

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        begun = time.monotonic()
        assert await asyncio.wait_for(task.outcome(), 1.0) == TaskCompleted(Output(n=1))
        assert time.monotonic() - begun < 0.3  # two polling intervals and the margin
        # the retry at the renewal tick finds the task recorded already
        await eventually(lambda: store.calls["record"] >= 2, timeout=1.0)

    assert len(store.recorded) == 1


# --- FR-82: failures of the store ------------------------------------------------------------


@mark.asyncio
async def test_a_record_failing_repeatedly_keeps_the_lease_and_the_outcome(
    caplog: LogCaptureFixture,
) -> None:
    # FR-82.2
    store = LeasedStore(timings=LeaseTimings(lease=0.5, renewal=0.1, polling=0.05))
    store.record_failures = 3
    recording = Recording()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output(n=1)

    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(work,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(work(Request()))
        assert await asyncio.wait_for(task.outcome(), 2.0) == TaskCompleted(Output(n=1))

    renewing = [identifiers for identifiers in store.renewals if task.identifier in identifiers]
    assert len(renewing) >= 2
    assert store.calls["record"] == 4
    assert len(recording.messages(level=ObservabilityLevel.ERROR)) == 3


@mark.asyncio
async def test_a_failing_claim_round_is_logged_and_the_next_one_claims(
    caplog: LogCaptureFixture,
) -> None:
    # FR-82.1, FR-88.1
    store = LeasedStore(timings=LeaseTimings(lease=1.0, renewal=0.3, polling=0.05))
    store.claim_failures = 1
    recording = Recording()
    async with holding(store.inner) as holder:
        identifier: UUID = uuid4()

        @managed_task(name="work")
        async def work(request: Request) -> Output:
            return Output(n=1)

        async with ctx.managed_tasks(
            TaskRuntime(store=store, tasks=(work,), observability=recording.observability)
        ):
            await holder.insert(
                NewTask(
                    identifier=identifier,
                    name="work",
                    argument="{}",
                    retry=TaskRetryPolicy(),
                    time_limit=None,
                    origin=None,
                )
            )
            task = await ManagedTasks.task(identifier)
            assert task is not None
            assert await asyncio.wait_for(task.outcome(), 1.0) == TaskCompleted(Output(n=1))

        errors = [
            record for record in recording.records if record.level == ObservabilityLevel.ERROR
        ]
        assert len(errors) == 1
        assert errors[0].scope.name == "managed_tasks"
        assert caplog.records == []


# --- FR-85: claim rounds and ticks -----------------------------------------------------------


@mark.asyncio
async def test_a_task_of_another_runtime_is_claimed_at_the_polling_tick() -> None:
    # FR-85.1
    store = LeasedStore(timings=LeaseTimings(lease=1.0, renewal=0.3, polling=0.1))
    async with holding(store.inner) as holder:
        started = asyncio.Event()

        @managed_task(name="work")
        async def work(request: Request) -> Output:
            started.set()
            return Output()

        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
            await asyncio.sleep(0.02)
            await holder.insert(
                NewTask(
                    identifier=uuid4(),
                    name="work",
                    argument="{}",
                    retry=TaskRetryPolicy(),
                    time_limit=None,
                    origin=None,
                )
            )
            await asyncio.wait_for(started.wait(), 0.3)


@mark.asyncio
async def test_a_spawn_with_a_free_slot_starts_without_waiting_for_a_tick() -> None:
    # FR-85.3
    store = LeasedStore(timings=LeaseTimings(lease=20.0, renewal=10.0, polling=10.0))
    started = asyncio.Event()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        started.set()
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        await ManagedTasks.spawn(work(Request()))
        await asyncio.wait_for(started.wait(), 0.1)


# --- FR-29, FR-77: close with a store with leases ---------------------------------------------


@mark.asyncio
@mark.parametrize(
    ("grace_period", "lower", "upper"),
    [
        (0.2, 0.2, 0.5),
        (0.0, 0.0, 0.1),
    ],
)
async def test_a_failing_record_bounds_the_close_by_the_grace_period(
    grace_period: float,
    lower: float,
    upper: float,
) -> None:
    # FR-29.5, FR-82.3 (D-99)
    store = LeasedStore(timings=LeaseTimings(lease=1.0, renewal=0.3, polling=0.1))
    store.record_failures = 10_000
    finished = asyncio.Event()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        finished.set()
        return Output()

    begun = 0.0
    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(work,), grace_period=grace_period)
    ):
        await ManagedTasks.spawn(work(Request()))
        await finished.wait()
        await asyncio.sleep(0.05)
        begun = time.monotonic()

    elapsed = time.monotonic() - begun
    assert lower <= elapsed <= upper


@mark.asyncio
async def test_a_status_call_in_progress_returns_before_the_session_closes() -> None:
    # FR-29.6
    store = LeasedStore()
    closed_when_returned: list[bool] = []

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output()

    async def read_status(task: ManagedTask[Output]) -> None:
        await task.status()
        closed_when_returned.append(store.closed)

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        await task.outcome()
        store.read_delay = 0.1
        reading = asyncio.create_task(read_status(task))
        await asyncio.sleep(0.02)

    await reading
    assert closed_when_returned == [False]
    assert store.closed


@mark.asyncio
async def test_an_awaiter_of_a_task_left_resumable_raises_at_the_close() -> None:
    # FR-77.1
    store = LeasedStore()
    store.release_leaves_queued = True
    started = asyncio.Event()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        awaiting = asyncio.create_task(task.outcome())
        await started.wait()
        await asyncio.sleep(0.02)

    with raises(RuntimeError):
        await awaiting

    with raises(RuntimeError):
        await task.status()


@mark.asyncio
async def test_a_retry_claimed_before_a_lost_record_reply_keeps_slots_and_records() -> None:
    # FR-82.3, FR-33: the record of attempt 1 committed, the retry was claimed again
    store = LeasedStore(timings=LeaseTimings(lease=1.0, renewal=0.3, polling=0.05))
    store.record_error_after_commit = 1
    attempts: list[int] = []

    @managed_task(name="flaky", retry=TaskRetryPolicy(delays=(0.0,)))
    async def flaky(request: Request) -> Output:
        attempts.append(1)
        if len(attempts) == 1:
            raise ValueError

        return Output(n=len(attempts))

    @managed_task(name="after")
    async def after(request: Request) -> Output:
        return Output(n=9)

    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(flaky, after), concurrency_limit=1)
    ):
        task = await ManagedTasks.spawn(flaky(Request()))
        assert await asyncio.wait_for(task.outcome(), 2.0) == TaskCompleted(Output(n=2))
        later = await ManagedTasks.spawn(after(Request()))
        assert await asyncio.wait_for(later.outcome(), 1.0) == TaskCompleted(Output(n=9))

    assert len(store.recorded) == 3


@mark.asyncio
async def test_a_failed_claim_round_of_a_local_store_is_retried_without_a_change() -> None:
    # FR-82.2, FR-85: no change of the store is coming to wake the runtime
    store = CountingStore()
    store.claim_failures = 1

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output(n=1)

    async with holding(store.inner) as holder:
        identifier: UUID = uuid4()
        await holder.insert(
            NewTask(
                identifier=identifier,
                name="work",
                argument="{}",
                retry=TaskRetryPolicy(),
                time_limit=None,
                origin=None,
            )
        )
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
            task = await ManagedTasks.task(identifier)
            assert task is not None
            assert await asyncio.wait_for(task.outcome(), 2.0) == TaskCompleted(Output(n=1))
