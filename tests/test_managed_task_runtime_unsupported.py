import asyncio
from typing import Literal
from uuid import UUID, uuid4

from pytest import mark, raises

from haiway import ObservabilityLevel, State, ctx
from haiway.context.managed import (
    AttemptRetried,
    InMemoryTaskStore,
    LeaseTimings,
    ManagedTask,
    ManagedTasks,
    NewTask,
    StoredAttempt,
    StoredCancelled,
    StoredFailed,
    StoredFinished,
    StoredQueued,
    StoredRetryPending,
    StoredRunning,
    StoredTask,
    TaskCompleted,
    TaskFailed,
    TaskFailureRecord,
    TaskRetryPolicy,
    TaskRuntime,
    TaskStoreSession,
    TaskUnsupported,
    managed_task,
)
from tests.managed_helpers import (
    CountingStore,
    LeasedStore,
    Output,
    Recording,
    Request,
    Secret,
    eventually,
    holding,
    wait_for,
)

FAST = LeaseTimings(lease=1.0, renewal=0.3, polling=0.05)
FAILURE = TaskFailureRecord(kind="raised", exception_type="builtins.ValueError")


@managed_task(name="r")
async def listed(request: Request) -> Output:
    return Output(n=request.n)


@managed_task(name="x")
async def unlisted(request: Secret) -> Output:
    return Output(n=1)


def waiting_task(
    name: str,
    *,
    delays: tuple[float, ...] = (),
) -> NewTask:
    return NewTask(
        identifier=uuid4(),
        name=name,
        argument="{}",
        retry=TaskRetryPolicy(delays=delays),
        time_limit=None,
        origin=None,
    )


async def retry_pending(
    session: TaskStoreSession,
    name: str,
    delay: float,
) -> NewTask:
    # one attempt failed and a retry is left, due after the delay
    task: NewTask = waiting_task(name, delays=(delay,))
    runner: UUID = uuid4()
    await session.insert(task)
    claimed = await session.claim(runner, {name}, limit=1)
    assert [claim.task for claim in claimed] == [task]
    await session.record(
        runner,
        task.identifier,
        1,
        AttemptRetried(failure=FAILURE, delay=delay),
    )
    return task


async def stored_of(
    session: TaskStoreSession,
    identifier: UUID,
) -> StoredTask:
    return (await session.read({identifier}))[identifier]


async def finished[Result: State | None](
    task: ManagedTask[Result],
) -> None:
    async def poll() -> None:
        while await task.status() != "finished":
            await asyncio.sleep(0.005)

    await wait_for(poll(), 2.0)


def errors(
    recording: Recording,
) -> list[str]:
    return recording.messages(level=ObservabilityLevel.ERROR)


# --- FR-45, FR-92: the setting ----------------------------------------------------------


@mark.parametrize("value", ["drop", "", "FAIL", None, True])
def test_an_unknown_unsupported_setting_is_rejected(value: object) -> None:
    # FR-45.3
    with raises(ValueError, match="unsupported"):
        TaskRuntime(**{"unsupported": value})


@mark.parametrize("value", ["leave", "fail"])
def test_the_two_unsupported_settings_are_accepted(value: Literal["leave", "fail"]) -> None:
    TaskRuntime(unsupported=value)


def test_the_runtime_documents_what_failing_unsupported_tasks_does() -> None:
    # FR-92.2
    doc: str = TaskRuntime.__doc__ or ""
    assert "unsupported" in doc
    assert "every due" in doc


@mark.asyncio
async def test_by_default_a_due_task_of_an_unlisted_name_is_left_and_the_store_not_asked() -> None:
    # FR-92.1
    store = CountingStore()
    async with holding(store.inner) as holder:
        task = waiting_task("x")
        await holder.insert(task)
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(listed,))):
            await asyncio.sleep(0.3)
            assert (await stored_of(holder, task.identifier)).status == StoredQueued()

    assert store.unsupported_calls == 0


# --- FR-93: failing unsupported tasks ---------------------------------------------------------


@mark.asyncio
async def test_a_runtime_failing_unsupported_tasks_fails_what_another_leaves_unrun() -> None:
    # FR-93.1, FR-93.7, FR-92
    shared = InMemoryTaskStore()
    leaving, failing = CountingStore(shared), CountingStore(shared)
    recording = Recording()
    release = asyncio.Event()

    @managed_task(name="blocker")
    async def blocker(request: Request) -> Output:
        await release.wait()
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(store=leaving, tasks=(unlisted, blocker), concurrency_limit=1)
    ):
        # the only slot is taken, the task of "x" waits in the store
        await ManagedTasks.spawn(blocker(Request()))
        queued = await ManagedTasks.spawn(unlisted(Secret(value="s3")))
        async with ctx.managed_tasks(
            TaskRuntime(
                store=failing,
                tasks=(listed,),
                unsupported="fail",
                observability=recording.observability,
            )
        ):
            await finished(queued)
            assert await queued.outcome() == TaskFailed(failure=TaskUnsupported(), attempts=1)
            # a cancel after the failing finds the task finished
            assert await queued.cancel() == "finished"
            assert await queued.outcome() == TaskFailed(failure=TaskUnsupported(), attempts=1)
            # the task the failing runtime lists is its own
            own = await ManagedTasks.spawn(listed(Request(n=7)))
            assert await wait_for(own.outcome()) == TaskCompleted(Output(n=7))

        release.set()

    reported = errors(recording)
    assert len(reported) == 1
    assert str(queued.identifier) in reported[0]
    assert "x" in reported[0] and "unsupported" in reported[0]
    assert "s3" not in reported[0]
    assert [
        record.scope.name
        for record in recording.records
        if record.level == ObservabilityLevel.ERROR
    ] == ["managed_tasks"]
    assert leaving.unsupported_calls == 0
    assert failing.unsupported_calls >= 1


@mark.asyncio
async def test_the_opening_round_fails_an_unsupported_task_without_a_tick() -> None:
    # FR-93.1, FR-55: the polling tick of the store is far away, the opening round did it
    store = LeasedStore(timings=LeaseTimings(lease=20.0, renewal=10.0, polling=10.0))
    async with holding(store.inner) as holder:
        task = waiting_task("x")
        await holder.insert(task)
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(listed,), unsupported="fail")):
            await wait_for(_until_finished(holder, task), 1.0)

    assert store.calls["fail_unsupported"] == 1


@mark.asyncio
async def test_a_retry_pending_task_which_becomes_due_is_failed_after_its_attempts() -> None:
    # FR-93.2
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        task = await retry_pending(holder, "x", 0.0)
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(listed,), unsupported="fail")):
            await wait_for(_until_finished(holder, task))

        stored = await stored_of(holder, task.identifier)
        assert stored.status == StoredFinished(outcome=StoredFailed())
        assert stored.attempts == (
            StoredAttempt(result=FAILURE),
            StoredAttempt(result=TaskUnsupported()),
        )


async def _until_finished(
    session: TaskStoreSession,
    task: NewTask,
) -> None:
    while not isinstance((await stored_of(session, task.identifier)).status, StoredFinished):
        await asyncio.sleep(0.005)


@mark.asyncio
async def test_a_retry_pending_task_is_failed_when_its_delay_has_passed_and_not_before() -> None:
    # FR-93.3
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        task = await retry_pending(holder, "x", 0.4)
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(listed,), unsupported="fail")):
            await asyncio.sleep(0.1)
            assert (await stored_of(holder, task.identifier)).status == StoredRetryPending()
            await wait_for(_until_finished(holder, task))


@mark.asyncio
async def test_a_due_task_of_a_listed_name_is_run_not_failed() -> None:
    # FR-93.4
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        task = waiting_task("r")
        await holder.insert(task)
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(listed,), unsupported="fail")):
            await wait_for(_until_finished(holder, task))

        stored = await stored_of(holder, task.identifier)
        assert stored.attempts == (StoredAttempt(result="completed"),)


@mark.asyncio
async def test_a_running_task_with_an_expired_lease_is_not_taken() -> None:
    # FR-93.5, D-116
    shared = InMemoryTaskStore()
    owner, failing = LeasedStore(shared, timings=FAST), LeasedStore(shared, timings=FAST)
    release = asyncio.Event()
    started = asyncio.Event()

    @managed_task(name="x")
    async def held(request: Request) -> Output:
        started.set()
        await release.wait()
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=owner, tasks=(held,))):
        task = await ManagedTasks.spawn(held(Request()))
        await wait_for(started.wait())
        async with holding(shared) as holder:
            assert (await stored_of(holder, task.identifier)).status == StoredRunning(
                cancel_requested=False
            )
            failing.expire(task.identifier)
            async with ctx.managed_tasks(
                TaskRuntime(store=failing, tasks=(listed,), unsupported="fail")
            ):
                await eventually(lambda: failing.calls["claim"] >= 3)
                await eventually(lambda: failing.calls["fail_unsupported"] >= 3)
                assert (await stored_of(holder, task.identifier)).status == StoredRunning(
                    cancel_requested=False
                )

            assert failing.interruptions == []

        release.set()


@mark.asyncio
async def test_a_task_cancelled_before_the_runtime_opens_stays_cancelled() -> None:
    # FR-93.6
    store = LeasedStore(timings=FAST)
    async with holding(store.inner) as holder:
        task = waiting_task("x")
        await holder.insert(task)
        assert await holder.cancel(task.identifier) == "cancelled"
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(listed,), unsupported="fail")):
            await eventually(lambda: store.calls["fail_unsupported"] >= 2)

        stored = await stored_of(holder, task.identifier)
        assert stored.status == StoredFinished(outcome=StoredCancelled())
        assert stored.attempts == ()


@mark.asyncio
async def test_a_raising_call_is_logged_in_the_runtime_scope_and_the_next_round_retries() -> None:
    # FR-93.8, FR-82.2
    store = LeasedStore(timings=FAST)
    store.fail_unsupported_failures = 1
    recording = Recording()
    async with holding(store.inner) as holder:
        task = waiting_task("x")
        await holder.insert(task)
        async with ctx.managed_tasks(
            TaskRuntime(
                store=store,
                tasks=(listed,),
                unsupported="fail",
                observability=recording.observability,
            )
        ):
            await wait_for(_until_finished(holder, task))

    reported = [
        record
        for record in recording.records
        if record.level == ObservabilityLevel.ERROR and "claim round failed" in record.message
    ]
    assert len(reported) == 1
    assert reported[0].scope.name == "managed_tasks"
    assert len(errors(recording)) == 2  # the failed round, then the task failed


@mark.asyncio
async def test_a_full_round_still_fails_unsupported_tasks() -> None:
    # FR-93: rounds with limit=0 included
    store = CountingStore()
    release = asyncio.Event()

    @managed_task(name="r")
    async def holds(request: Request) -> Output:
        await release.wait()
        return Output()

    async with holding(store.inner) as holder:
        async with ctx.managed_tasks(
            TaskRuntime(store=store, tasks=(holds,), concurrency_limit=1, unsupported="fail")
        ):
            await ManagedTasks.spawn(holds(Request()))
            task = waiting_task("x")
            await holder.insert(task)
            await wait_for(_until_finished(holder, task))

        release.set()


@mark.asyncio
async def test_a_runtime_listing_no_task_fails_every_due_task() -> None:
    # FR-92: tasks=()
    store = InMemoryTaskStore()
    async with holding(store) as holder:
        task = waiting_task("x")
        await holder.insert(task)
        async with ctx.managed_tasks(TaskRuntime(store=store, unsupported="fail")):
            await wait_for(_until_finished(holder, task))


@mark.asyncio
async def test_no_call_is_made_once_the_block_is_exited() -> None:
    # FR-93: never while closing or closed
    store = CountingStore()
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(listed,), unsupported="fail")):
        await eventually(lambda: store.unsupported_calls >= 1)

    calls: int = store.unsupported_calls
    async with holding(store.inner) as holder:
        await holder.insert(waiting_task("x"))
        await asyncio.sleep(0.1)

    assert store.unsupported_calls == calls
