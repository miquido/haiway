import asyncio
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

from pytest import LogCaptureFixture, MonkeyPatch, mark, raises

from haiway import ObservabilityLevel, ctx
from haiway.context.managed import (
    AttemptCompleted,
    AttemptRetried,
    InMemoryTaskStore,
    LeaseTimings,
    ManagedTask,
    ManagedTasks,
    NewTask,
    TaskCancelled,
    TaskCompleted,
    TaskFailureRecord,
    TaskRetryPolicy,
    TaskRuntime,
    managed_task,
)
from haiway.context.managed.coordinator import Coordinator
from haiway.context.managed.store import TaskStoreSession
from tests.managed_helpers import (
    CountingStore,
    LeasedStore,
    Output,
    Recording,
    Request,
    holding,
    wait_for,
)

# --- attempt fencing: a repeated record never lands on the next attempt (B1) ---------------


@mark.asyncio
async def test_a_lost_record_reply_does_not_run_or_record_an_attempt_twice() -> None:
    # B1, FR-82.3, D1
    calls: list[int] = []
    store = LeasedStore(timings=LeaseTimings(lease=5.0, renewal=0.25, polling=0.1))
    store.record_delay = 0.3
    store.record_error_after_commit = 1

    @managed_task(name="flaky", retry=TaskRetryPolicy(delays=(0.25, 0.25)))
    async def flaky(request: Request) -> Output:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("x")

        await asyncio.sleep(0.05)
        return Output(n=len(calls))

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(flaky,))):
        task = await ManagedTasks.spawn(flaky(Request()))
        assert await asyncio.wait_for(task.outcome(), 4.0) == TaskCompleted(Output(n=2))
        async with holding(store.inner) as holder:
            stored = (await holder.read({task.identifier}))[task.identifier]

    assert len(calls) == 2
    assert [attempt.result for attempt in stored.attempts] == [
        TaskFailureRecord(kind="raised", exception_type="builtins.ValueError"),
        "completed",
    ]
    assert [type(end) for end in store.recorded] == [AttemptRetried, AttemptCompleted]


# --- cancelled entry leaves nothing behind (B2) ----------------------------------------------


@mark.asyncio
@mark.parametrize("leased", [False, True])
async def test_a_cancelled_entry_leaves_no_task_behind(leased: bool) -> None:
    # B2
    @managed_task(name="work")
    async def work(request: Request) -> Output:
        await asyncio.sleep(5)
        return Output()

    leased_store = LeasedStore(timings=LeaseTimings(lease=1.0, renewal=0.3, polling=0.1))
    counting = CountingStore()
    store = leased_store if leased else counting
    store.claim_delay = 0.2
    before = asyncio.all_tasks()
    with raises(TimeoutError):
        async with asyncio.timeout(0.05):
            async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
                pass

    await asyncio.sleep(0.4)
    left = asyncio.all_tasks() - before - {asyncio.current_task()}
    assert left == set()
    if leased:
        claims = leased_store.calls["claim"]
        await asyncio.sleep(0.3)
        assert leased_store.calls["claim"] == claims


@mark.asyncio
async def test_a_cancelled_entry_ends_the_attempts_it_started() -> None:
    # B2: the entry is cancelled as soon as the first round claimed a task
    events: list[str] = []
    outer: list[asyncio.Task[None]] = []

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        events.append("started")
        try:
            await asyncio.sleep(1.0)
            events.append("finished")

        except asyncio.CancelledError:
            events.append("cancelled")
            raise

        return Output()

    store = CountingStore()

    def cancel_entry() -> None:
        outer[0].cancel()

    store.on_claimed = cancel_entry
    async with holding(store.inner) as holder:
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

        async def block() -> None:
            async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
                await asyncio.sleep(5)

        before = asyncio.all_tasks()
        outer.append(asyncio.create_task(block()))
        with raises(asyncio.CancelledError):
            await outer[0]

        await asyncio.sleep(1.2)
        assert "finished" not in events
        assert asyncio.all_tasks() - before - {asyncio.current_task()} == set()


# --- close and a time limit (D2) -------------------------------------------------------------


@mark.asyncio
async def test_close_cancels_again_an_attempt_which_swallowed_its_time_limit() -> None:
    # D2, m1
    recording = Recording()

    @managed_task(name="stubborn")
    async def stubborn(request: Request) -> Output:
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            pass

        await asyncio.sleep(3)
        return Output()

    begun = 0.0
    async with ctx.managed_tasks(
        TaskRuntime(tasks=(stubborn,), observability=recording.observability)
    ):
        await ManagedTasks.spawn(stubborn(Request()), time_limit=0.05)
        await asyncio.sleep(0.2)
        begun = time.monotonic()

    assert time.monotonic() - begun < 1.0
    # the time limit decided the end, the close logs nothing for it
    assert recording.messages(level=ObservabilityLevel.ERROR, containing="cut off") == []


@mark.asyncio
async def test_a_released_task_logs_its_outcome_fixed() -> None:
    # m2
    recording = Recording()
    started = asyncio.Event()

    @managed_task(name="waits")
    async def waits(request: Request) -> Output:
        started.set()
        await asyncio.sleep(10)
        return Output()

    async with ctx.managed_tasks(
        TaskRuntime(tasks=(waits,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(waits(Request()))
        await started.wait()

    assert recording.messages(level=ObservabilityLevel.DEBUG, containing="outcome fixed")
    assert await task.outcome() == TaskCancelled()


# --- polling and parked runs ------------------------------------------------------------------


@mark.asyncio
async def test_an_outcome_asked_while_a_run_ends_never_hangs() -> None:
    # (a): the outcome of a task is asked for as its run settles
    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output()

    async with ctx.managed_tasks(TaskRuntime(tasks=(work,))):
        for _ in range(50):
            task: ManagedTask[Output] = await ManagedTasks.spawn(work(Request()))
            while await task.status() != "finished":
                await asyncio.sleep(0)

            async with asyncio.timeout(0.5):
                assert await task.outcome() == TaskCompleted(Output())


@mark.asyncio
async def test_a_failing_wait_for_changes_is_retried() -> None:
    # m3, FR-82
    store = CountingStore()
    store.failing_waits = 1
    recording = Recording()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output(n=1)

    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(work,), observability=recording.observability)
    ):
        await asyncio.sleep(0.3)  # the wait failed once and was called again
        task = await ManagedTasks.spawn(work(Request()))
        assert await asyncio.wait_for(task.outcome(), 1.0) == TaskCompleted(Output(n=1))

    assert len(recording.messages(level=ObservabilityLevel.ERROR, containing="changes")) == 1
    assert store.waits >= 2


@mark.asyncio
async def test_a_failing_close_does_not_swallow_the_cancellation_of_the_exit(
    caplog: LogCaptureFixture,
) -> None:
    # m4, FR-29
    inner = InMemoryTaskStore()

    class Failing:
        def open(self):
            @asynccontextmanager
            async def opened() -> AsyncGenerator[TaskStoreSession]:
                async with inner.open() as session:
                    yield session
                    await asyncio.sleep(0.2)
                    raise OSError("closing failed")

            return opened()

    released = asyncio.Event()

    async def block() -> None:
        async with ctx.managed_tasks(TaskRuntime(store=Failing())):
            released.set()

    outer = asyncio.create_task(block())
    await released.wait()
    await asyncio.sleep(0.05)
    outer.cancel()
    with raises(asyncio.CancelledError):
        await outer

    assert any("close failed" in record.message for record in caplog.records)


# --- a coordinator which fails ends the runtime ----------------------------------------------


@mark.asyncio
async def test_a_failing_coordinator_cancels_everything_and_fails_what_waits(
    monkeypatch: MonkeyPatch,
) -> None:
    store = LeasedStore(timings=LeaseTimings(lease=1.0, renewal=0.05, polling=0.05))
    cancelled = asyncio.Event()
    started = asyncio.Event()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        started.set()
        try:
            await asyncio.sleep(10)

        except asyncio.CancelledError:
            cancelled.set()
            raise

        return Output()

    def failing(self: Coordinator) -> None:
        raise RuntimeError("the coordinator failed")

    waiting: list[asyncio.Task[object]] = []
    before = asyncio.all_tasks()
    with raises(ExceptionGroup) as raised:
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
            task = await ManagedTasks.spawn(work(Request()))
            await started.wait()
            waiting.append(asyncio.ensure_future(task.outcome()))
            await asyncio.sleep(0)
            monkeypatch.setattr(Coordinator, "_renewal_tick", failing)
            await asyncio.sleep(0.3)

    assert [type(error) for error in raised.value.exceptions] == [RuntimeError]
    assert cancelled.is_set()
    with raises(RuntimeError, match="closed"):
        await waiting[0]

    assert store.closed
    assert asyncio.all_tasks() - before - {asyncio.current_task()} == set()


# --- a call of the store which ends in a cancellation of its own -----------------------------


async def cancelled_inside() -> None:
    # as a pooled connection awaits a future which another part of the driver cancelled
    future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
    future.cancel()
    await future


class CancellingStore:
    """``InMemoryTaskStore`` whose next call of the armed kinds is cancelled from inside."""

    def __init__(self) -> None:
        self.inner = InMemoryTaskStore()
        self.armed: set[str] = set()

    def open(self):
        @asynccontextmanager
        async def opened() -> AsyncGenerator[TaskStoreSession]:
            async with self.inner.open() as real:

                async def read(identifiers, /):
                    if "read" in self.armed:
                        self.armed.discard("read")
                        await cancelled_inside()

                    return await real.read(identifiers)

                async def claim(runner, names, *, limit):
                    if "claim" in self.armed:
                        self.armed.discard("claim")
                        await cancelled_inside()

                    return await real.claim(runner, names, limit=limit)

                yield TaskStoreSession(
                    insert=real.insert,
                    claim=claim,
                    renew=real.renew,
                    record=real.record,
                    release=real.release,
                    cancel=real.cancel,
                    read=read,
                    fail_unsupported=real.fail_unsupported,
                    query=real.query,
                    coordination=real.coordination,
                )

        return opened()


@mark.asyncio
async def test_a_read_cancelled_from_inside_fails_its_caller_and_the_close_ends() -> None:
    store = CancellingStore()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output()

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        await task.outcome()
        store.armed.add("read")

        with raises(RuntimeError, match="cancelled"):
            await wait_for(task.status(), 1.0)

        assert await wait_for(task.status(), 1.0) == "finished"

    # the close above returned, nothing was left waiting for a reply


@mark.asyncio
async def test_a_claim_cancelled_from_inside_is_answered_and_the_next_claim_goes_on() -> None:
    store = CancellingStore()
    store.armed.add("claim")

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output(n=2)

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))

        assert await wait_for(task.outcome(), 2.0) == TaskCompleted(Output(n=2))


@mark.asyncio
async def test_a_wait_for_changes_cancelled_from_inside_is_called_again() -> None:
    store = CountingStore()
    store.cancelled_waits = 1

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output(n=1)

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        await asyncio.sleep(0.3)  # the retry of the wait
        task = await ManagedTasks.spawn(work(Request()))

        assert await wait_for(task.outcome(), 1.0) == TaskCompleted(Output(n=1))

    assert store.waits >= 2


# --- a task scope which can not be entered (decision G1) -------------------------------------


class FailingEntry(Recording):
    """A backend which refuses to enter the task scope of ``work`` the first time."""

    def __init__(self) -> None:
        super().__init__()
        self.refusals: int = 1

    def _scope_entering(self, scope, /, *, links):
        if scope.is_root and scope.name == "work" and self.refusals > 0:
            self.refusals -= 1
            raise OSError("the backend refused the scope")

        return super()._scope_entering(scope, links=links)


@mark.asyncio
async def test_a_task_scope_which_could_not_be_entered_is_a_failure_which_is_retried() -> None:
    recording = FailingEntry()
    calls: list[int] = []

    @managed_task(name="work", retry=TaskRetryPolicy(delays=(0.0,)))
    async def work(request: Request) -> Output:
        calls.append(1)
        return Output(n=len(calls))

    async with ctx.managed_tasks(TaskRuntime(tasks=(work,), observability=recording.observability)):
        task = await ManagedTasks.spawn(work(Request()))

        assert await wait_for(task.outcome(), 2.0) == TaskCompleted(Output(n=1))

    errors = recording.messages(level=ObservabilityLevel.ERROR, containing="could not be entered")
    assert len(errors) == 1 and "builtins.OSError" in errors[0]
    assert recording.messages(level=ObservabilityLevel.WARNING, containing="retrying")
