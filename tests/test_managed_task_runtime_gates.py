"""
The interleavings of the runtime which the coordinator orders, driven through the real runtime.

A test double of a store holds the replies of calls at gates the test opens, hands leases to
other runners and expires them, so that each interleaving happens in the order the test wants,
without sleeping for it to happen.
"""

import asyncio
from collections.abc import Sequence
from typing import Any
from uuid import UUID, uuid4

from pytest import mark, raises

from haiway import ObservabilityLevel, ctx
from haiway.context.managed import (
    AttemptCompleted,
    AttemptFailed,
    AttemptRetried,
    LeaseTimings,
    ManagedTasks,
    NewTask,
    StoredRetryPending,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    TaskInterrupted,
    TaskRetryPolicy,
    TaskRuntime,
    managed_task,
)
from haiway.context.managed.session import RuntimeSession
from tests.managed_helpers import (
    Gate,
    LeasedStore,
    Output,
    Recording,
    Request,
    eventually,
    holding,
    wait_for,
)

FAST = LeaseTimings(lease=1.0, renewal=0.1, polling=0.05)


def waiting_task(
    name: str,
    delays: Sequence[float] = (),
    identifier: UUID | None = None,
) -> NewTask:
    return NewTask(
        identifier=identifier if identifier is not None else uuid4(),
        name=name,
        argument="{}",
        retry=TaskRetryPolicy(delays=tuple(delays)),
        time_limit=None,
        origin=None,
    )


def task_scopes(
    recording: Recording,
    name: str,
) -> int:
    # the task scope is the root of its tree, each attempt has a scope below it
    return len([entered for entered in recording.scopes(name) if entered.scope.is_root])


class TimerSpy:
    """Notes the timers which runtimes arm on the loop, and which of them fired."""

    def __init__(self) -> None:
        self.armed: list[tuple[asyncio.TimerHandle, dict[str, bool]]] = []

    def install(self) -> None:
        loop = asyncio.get_running_loop()
        original = loop.call_later

        def call_later(delay: float, callback: Any, *args: Any, **kwargs: Any) -> Any:
            if not isinstance(getattr(callback, "__self__", None), RuntimeSession):
                return original(delay, callback, *args, **kwargs)

            state = {"fired": False}

            def fired(*arguments: Any) -> None:
                state["fired"] = True
                callback(*arguments)

            handle = original(delay, fired, *args, **kwargs)
            self.armed.append((handle, state))
            return handle

        loop.call_later = call_later

    def left(self) -> int:
        return len(
            [1 for handle, state in self.armed if not handle.cancelled() and not state["fired"]]
        )


# --- a claim whose reply arrives after a poll (the blocker) ---------------------------------


@mark.asyncio
async def test_a_claim_reply_arriving_after_a_poll_runs_the_retry_in_the_same_scope() -> None:
    store = LeasedStore(timings=FAST)
    recording = Recording()
    calls: list[int] = []

    @managed_task(name="flaky", retry=TaskRetryPolicy(delays=(0.2,)))
    async def flaky(request: Request) -> Output:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("x")

        return Output(n=len(calls))

    # the first read, which is the one of the parked run, looks at the store late
    read_gate = Gate(after=False)
    store.read_gate = read_gate
    claim_gate = Gate(after=True)
    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(flaky,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(flaky(Request()))
        await wait_for(read_gate.arrived.wait())
        # the retry becomes due, its claim commits and its reply is held
        store.claim_gate = claim_gate
        await wait_for(claim_gate.arrived.wait())
        # the read now sees the task running, which the claim reply has not yet told the run
        read_gate.release()
        await wait_for(store.read_returned.wait())
        # its reply is posted and handled within a few turns of the loop, before the claim reply
        for _ in range(10):
            await asyncio.sleep(0)

        claim_gate.release()

        assert await wait_for(task.outcome(), 3.0) == TaskCompleted(Output(n=2))

    assert len(calls) == 2
    assert task_scopes(recording, "flaky") == 1
    warnings = recording.messages(level=ObservabilityLevel.WARNING)
    assert len(warnings) == 1 and "failed, retrying" in warnings[0]


# --- a record whose reply arrives after the retry was claimed (B1) --------------------------


@mark.asyncio
async def test_a_record_reply_arriving_after_the_retry_was_claimed_does_not_record_twice() -> None:
    store = LeasedStore(timings=FAST)
    calls: list[int] = []

    @managed_task(name="flaky", retry=TaskRetryPolicy(delays=(0.0,)))
    async def flaky(request: Request) -> Output:
        calls.append(1)
        if len(calls) == 1:
            raise ValueError("x")

        return Output(n=len(calls))

    record_gate = Gate(after=True)
    store.record_gate = record_gate
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(flaky,))):
        task = await ManagedTasks.spawn(flaky(Request()))
        # the record of attempt 1 committed, its reply is held, the retry is claimed meanwhile
        await wait_for(record_gate.arrived.wait())
        await eventually(lambda: store.committed == [1, 2], timeout=3.0)
        record_gate.release()

        assert await wait_for(task.outcome(), 3.0) == TaskCompleted(Output(n=2))

    assert len(calls) == 2
    assert store.committed == [1, 2]
    assert [type(end) for end in store.recorded] == [AttemptRetried, AttemptCompleted]


# --- an entry cancelled while a claim is in flight (B2) -------------------------------------


@mark.asyncio
async def test_an_entry_cancelled_while_a_claim_is_in_flight_releases_what_it_claimed() -> None:
    store = LeasedStore(timings=FAST)
    events: list[str] = []

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        events.append("started")
        return Output()

    async with holding(store.inner) as holder:
        await holder.insert(waiting_task("work"))

    claim_gate = Gate(after=True)
    store.claim_gate = claim_gate

    async def block() -> None:
        async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
            await asyncio.sleep(5)

    before = asyncio.all_tasks()
    outer = asyncio.create_task(block())
    await wait_for(claim_gate.arrived.wait())
    outer.cancel()
    await asyncio.sleep(0.05)  # the close is under way, waiting for the claim
    claim_gate.release()
    with raises(asyncio.CancelledError):
        await outer

    assert events == []
    assert store.calls["release"] == 1
    assert asyncio.all_tasks() - before - {asyncio.current_task()} == set()


# --- a store call during the drain (B3) -----------------------------------------------------


@mark.asyncio
async def test_a_store_call_posted_as_the_close_ends_is_refused_unserved() -> None:
    store = LeasedStore(timings=FAST)

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output()

    read_gate = Gate(after=False)
    returned: list[bool] = []
    refused: list[str] = []

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        await task.outcome()
        store.read_gate = read_gate

        async def status() -> None:
            await task.status()
            returned.append(store.closed)

        reading = asyncio.create_task(status())
        await wait_for(read_gate.arrived.wait())

        async def after_the_close_began() -> None:
            await asyncio.sleep(0.05)
            reads = store.calls["read"]
            read_gate.release()
            # the first call ends, its reply is posted by a callback of the next iteration
            # of the loop, and this call is posted right behind it
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            try:
                await task.status()

            except RuntimeError as exc:
                refused.append(str(exc))

            assert store.calls["read"] == reads

        refusing = asyncio.create_task(after_the_close_began())

    await reading
    await refusing
    assert returned == [False]
    # refused, either as a call posted before the close ended or as one made after it
    assert len(refused) == 1 and "closed" in refused[0]


# --- an outcome asked while the run decides it (the lost reply of a record) -----------------


@mark.asyncio
async def test_an_outcome_asked_as_a_record_reply_is_lost_resolves_within_two_polls() -> None:
    store = LeasedStore(timings=FAST)

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output(n=1)

    record_gate = Gate(after=True)
    store.record_gate = record_gate
    store.record_error_after_commit = 1
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        await wait_for(record_gate.arrived.wait())
        # the record committed, the run still decides the outcome itself
        waiting = asyncio.create_task(task.outcome())
        await asyncio.sleep(0)
        asked = asyncio.get_running_loop().time()
        record_gate.release()  # the reply of the record is lost from here

        assert await wait_for(waiting, 2.0) == TaskCompleted(Output(n=1))
        assert asyncio.get_running_loop().time() - asked < 2 * FAST.polling + 0.1


# --- a poll which comes back for a run which moved on ---------------------------------------


@mark.asyncio
async def test_a_poll_asked_for_an_earlier_park_does_not_end_the_next_one() -> None:
    store = LeasedStore(timings=FAST)
    recording = Recording()
    calls: list[int] = []

    @managed_task(name="flaky", retry=TaskRetryPolicy(delays=(0.05, 0.5)))
    async def flaky(request: Request) -> Output:
        calls.append(1)
        if len(calls) < 3:
            raise ValueError("x")

        return Output(n=len(calls))

    read_gate = Gate(after=True)
    store.read_gate = read_gate
    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(flaky,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(flaky(Request()))
        # the read of the first park looked at the store and its reply is held
        await wait_for(read_gate.arrived.wait())
        # the run goes on to attempt 2, fails again and parks again, for 0.5 s
        await eventually(lambda: store.committed == [1, 2], timeout=3.0)
        await asyncio.sleep(0.02)
        read_gate.release()  # the reply says the store waits for a retry after one attempt

        assert await wait_for(task.outcome(), 4.0) == TaskCompleted(Output(n=3))

    assert len(calls) == 3
    assert task_scopes(recording, "flaky") == 1


# --- a retry timer left by a task failing during the grace period (m5) ----------------------


@mark.asyncio
async def test_a_task_failing_during_the_grace_period_leaves_no_timer_and_no_claim() -> None:
    store = LeasedStore(timings=FAST)
    started = asyncio.Event()
    spy = TimerSpy()
    spy.install()

    @managed_task(name="work", retry=TaskRetryPolicy(delays=(0.1,)))
    async def work(request: Request) -> Output:
        started.set()
        await asyncio.sleep(0.1)
        raise ValueError("x")

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,), grace_period=0.5)):
        task = await ManagedTasks.spawn(work(Request()))
        await started.wait()

    assert store.committed == [1]
    assert spy.armed and spy.left() == 0
    claims = store.calls["claim"]
    await asyncio.sleep(0.3)  # the retry delay and several polling intervals pass
    assert store.calls["claim"] == claims
    async with holding(store.inner) as holder:
        stored = (await holder.read({task.identifier}))[task.identifier]

    assert isinstance(stored.status, StoredRetryPending)


# --- a cancel which lands after the function returned (FR-26) -------------------------------


@mark.asyncio
async def test_a_cancel_after_the_function_returned_wins_and_logs_the_lost_value_once() -> None:
    store = LeasedStore(timings=FAST)
    recording = Recording()

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        return Output(n=4242)

    record_gate = Gate(after=False)
    store.record_gate = record_gate
    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(work,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(work(Request()))
        await wait_for(record_gate.arrived.wait())  # returned, its record waits
        assert await task.cancel() == "pending"
        record_gate.release()

        assert await wait_for(task.outcome(), 2.0) == TaskCancelled()

    warnings = recording.messages(level=ObservabilityLevel.WARNING, containing="discarded")
    assert len(warnings) == 1
    assert "4242" not in warnings[0]
    assert recording.messages(level=ObservabilityLevel.ERROR) == []


# --- a lease handed to another runner (FR-83) -----------------------------------------------


@mark.asyncio
async def test_a_lease_lost_cancels_the_attempt_without_a_record_and_the_next_claim_continues() -> (
    None
):
    store = LeasedStore(timings=FAST)
    recording = Recording()
    calls: list[int] = []
    dying: asyncio.Event = asyncio.Event()
    cancelled_at: list[float] = []
    entered = asyncio.Event()

    @managed_task(name="work", retry=TaskRetryPolicy(delays=(0.0,)))
    async def work(request: Request) -> Output:
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            try:
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                cancelled_at.append(asyncio.get_running_loop().time())
                await dying.wait()  # slow to end
                raise

        return Output(n=len(calls))

    async with ctx.managed_tasks(
        TaskRuntime(store=store, tasks=(work,), observability=recording.observability)
    ):
        task = await ManagedTasks.spawn(work(Request()))
        await wait_for(entered.wait())
        lost = asyncio.get_running_loop().time()
        other = uuid4()
        store.hand_lease_to(task.identifier, other)
        await eventually(lambda: bool(cancelled_at), timeout=2.0)
        assert cancelled_at[0] - lost < FAST.renewal + 0.2

        # the runner which holds the lease ends attempt 1 as a failure which is retried
        store.claim_committed.clear()
        async with holding(store.inner) as holder:
            status = await holder.record(
                other,
                task.identifier,
                1,
                AttemptRetried(failure=TaskInterrupted(), delay=0.0),
            )

        assert isinstance(status, StoredRetryPending)
        # this runtime claims attempt 2 while attempt 1 is still ending
        await wait_for(store.claim_committed.wait())
        dying.set()

        assert await wait_for(task.outcome(), 3.0) == TaskCompleted(Output(n=2))

    assert calls == [1, 1]
    assert store.committed == [2]  # what attempt 1 ended with was never recorded here
    lost_warnings = recording.messages(level=ObservabilityLevel.WARNING, containing="lease lost")
    assert len(lost_warnings) == 1
    assert task_scopes(recording, "work") == 1


# --- the lease of an attempt this runtime runs expires (FR-84, own) -------------------------


@mark.asyncio
async def test_an_expired_lease_claimed_back_by_its_own_runtime_records_no_interruption() -> None:
    store = LeasedStore(timings=FAST)
    calls: list[int] = []
    release = asyncio.Event()
    entered = asyncio.Event()

    @managed_task(name="work", retry=TaskRetryPolicy(delays=(0.0,)))
    async def work(request: Request) -> Output:
        calls.append(1)
        entered.set()
        await release.wait()
        return Output(n=1)

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        await wait_for(entered.wait())
        store.expire(task.identifier)
        await eventually(lambda: store.interruptions == [(task.identifier, 1)], timeout=2.0)
        await asyncio.sleep(3 * FAST.polling)  # the claim was discarded, nothing follows it
        assert store.calls["record"] == 0
        release.set()

        assert await wait_for(task.outcome(), 2.0) == TaskCompleted(Output(n=1))

    assert calls == [1]
    assert [type(end) for end in store.recorded] == [AttemptCompleted]


# --- the lease of an attempt of another runtime expires (FR-84, other) ----------------------


@mark.asyncio
@mark.parametrize("retries", [True, False])
async def test_an_interrupted_attempt_of_another_runtime_is_recorded_by_the_retry_policy(
    retries: bool,
) -> None:
    store = LeasedStore(timings=FAST)
    delays: tuple[float, ...] = (0.0,) if retries else ()

    @managed_task(name="work", retry=TaskRetryPolicy(delays=delays))
    async def work(request: Request) -> Output:
        return Output(n=2)

    new = waiting_task("work", delays)
    async with holding(store.inner) as holder:
        await holder.insert(new)
        claimed = await holder.claim(uuid4(), {"work"}, limit=None)

    assert len(claimed) == 1  # running in another runtime, which stopped without closing
    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        handle = await ManagedTasks.task(new.identifier)
        assert handle is not None
        expired = asyncio.get_running_loop().time()
        store.expire(new.identifier)
        await eventually(lambda: bool(store.recorded), timeout=2.0)
        assert asyncio.get_running_loop().time() - expired < FAST.polling + 0.2

        outcome = await wait_for(handle.outcome(), 3.0)

    if retries:
        assert store.recorded[0] == AttemptRetried(failure=TaskInterrupted(), delay=0.0)
        assert outcome == TaskCompleted(Output(n=2))

    else:
        assert store.recorded == [AttemptFailed(failure=TaskInterrupted())]
        assert outcome == TaskFailed(failure=TaskInterrupted(), attempts=1)


# --- a renewal result which is older than the grant of the lease ----------------------------


@mark.asyncio
async def test_a_renewal_result_older_than_the_grant_does_not_cancel_the_attempt() -> None:
    store = LeasedStore(timings=FAST)
    release = asyncio.Event()
    entered = asyncio.Event()
    cancellations: list[int] = []

    @managed_task(name="work")
    async def work(request: Request) -> Output:
        entered.set()
        try:
            await release.wait()

        except asyncio.CancelledError:
            cancellations.append(1)
            raise

        return Output(n=1)

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        await wait_for(entered.wait())
        runner = store.runner
        assert runner is not None
        # a renewal finds the lease gone, and its result is held
        renew_gate = Gate(after=True)
        store.hand_lease_to(task.identifier, uuid4())
        store.renew_gate = renew_gate
        await wait_for(renew_gate.arrived.wait())
        # the lease is claimed back, which grants it again
        store.hand_lease_to(task.identifier, runner)
        store.expire(task.identifier)
        await eventually(lambda: store.interruptions == [(task.identifier, 1)], timeout=2.0)
        renew_gate.release()
        await asyncio.sleep(3 * FAST.renewal)
        assert cancellations == []
        release.set()

        assert await wait_for(task.outcome(), 2.0) == TaskCompleted(Output(n=1))

    assert store.committed == [1]


# --- the lease of the attempt which waits behind a discarded one ----------------------------


@mark.asyncio
async def test_an_attempt_waiting_behind_a_discarded_one_is_renewed_and_kept() -> None:
    store = LeasedStore(timings=FAST)
    calls: list[int] = []
    dying = asyncio.Event()
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    @managed_task(name="work", retry=TaskRetryPolicy(delays=(0.0,)))
    async def work(request: Request) -> Output:
        calls.append(1)
        if len(calls) == 1:
            entered.set()
            try:
                await asyncio.sleep(10)

            except asyncio.CancelledError:
                cancelled.set()
                await dying.wait()  # swallows the cancellation for longer than a lease
                raise

        return Output(n=len(calls))

    async with ctx.managed_tasks(TaskRuntime(store=store, tasks=(work,))):
        task = await ManagedTasks.spawn(work(Request()))
        await wait_for(entered.wait())
        other = uuid4()
        store.hand_lease_to(task.identifier, other)
        await wait_for(cancelled.wait())
        store.claim_committed.clear()
        async with holding(store.inner) as holder:
            await holder.record(
                other,
                task.identifier,
                1,
                AttemptRetried(failure=TaskInterrupted(), delay=0.0),
            )

        await wait_for(store.claim_committed.wait())  # attempt 2 is held, behind attempt 1
        renewals = len(store.renewals)
        await eventually(lambda: len(store.renewals) > renewals + 1, timeout=2.0)
        assert all(task.identifier in renewed for renewed in store.renewals[renewals:])

        # its lease expires while attempt 1 is still ending: claimed back, not recorded
        store.expire(task.identifier)
        await eventually(lambda: store.interruptions == [(task.identifier, 2)], timeout=2.0)
        await asyncio.sleep(3 * FAST.polling)
        assert store.calls["record"] == 0
        dying.set()

        assert await wait_for(task.outcome(), 3.0) == TaskCompleted(Output(n=2))

    assert calls == [1, 1]
    assert store.committed == [2]
