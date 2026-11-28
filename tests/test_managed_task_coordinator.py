"""
One test for each cell of the transition table of the coordinator, no event loop.

Every state is reached by feeding events from a fresh coordinator, never by building phases.
"""

from asyncio import CancelledError
from collections.abc import Mapping, Sequence
from contextvars import copy_context
from typing import Any, Literal, cast
from uuid import UUID

from pytest import mark

from haiway.attributes import State
from haiway.context.managed import (
    AttemptCancelled,
    AttemptCompleted,
    AttemptFailed,
    AttemptRetried,
    ClaimedAttempt,
    ClaimedInterrupted,
    ClaimedTask,
    LeaseRenewal,
    LeaseState,
    LeaseTimings,
    NewTask,
    StoredAttempt,
    StoredCancelled,
    StoredCompleted,
    StoredFailed,
    StoredFinished,
    StoredQueued,
    StoredRetryPending,
    StoredRunning,
    StoredStatus,
    StoredTask,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    TaskFailureRecord,
    TaskInterrupted,
    TaskRetryPolicy,
    TaskTimeLimitExceeded,
)
from haiway.context.managed.attempt import (
    AttemptCancelledLocally,
    AttemptRaised,
    AttemptResult,
    AttemptReturned,
    AttemptTerminated,
    AttemptTermination,
)
from haiway.context.managed.coordinator import (
    AttemptEnded,
    Attempting,
    CallCancelled,
    CancelAsked,
    CancelReplied,
    CancelTask,
    ChangedReplied,
    ChangesCancelled,
    Claim,
    ClaimReplied,
    Closed,
    CloseRequested,
    Coordinator,
    CutOff,
    Discarding,
    Effect,
    Entering,
    Event,
    ExitHost,
    Fail,
    FailUnsupported,
    GraceElapsed,
    Insert,
    InsertReplied,
    InterruptionRecorded,
    Log,
    Open,
    Opened,
    OutcomeAbandoned,
    OutcomeAsked,
    Parked,
    Phase,
    PollDue,
    PollTick,
    Read,
    ReadReplied,
    Record,
    Recording,
    RecordInterruption,
    RecordReplied,
    Release,
    ReleaseReplied,
    Releasing,
    Renew,
    RenewalTick,
    RenewReplied,
    Resolve,
    RetryDue,
    RunAttempt,
    ScopeEntered,
    ScopeExited,
    ScopeFailed,
    SpawnAsked,
    StartChanges,
    StartHost,
    StartTimer,
    StopChanges,
    StopTimer,
    StopTimers,
    StoreCallAsked,
    StoreCallReplied,
    StoreFailure,
    TimeLimitElapsed,
    UnsupportedReplied,
    Waiting,
)
from tests.managed_helpers import coordination_of

LEASED = LeaseTimings(lease=1.0, renewal=0.3, polling=0.1)
FAILURE = TaskFailureRecord(kind="raised", exception_type="builtins.ValueError")


class Reply:
    def __init__(self) -> None:
        self.values: list[object] = []
        self.exceptions: list[Exception] = []

    def resolve(
        self,
        value: object,
        /,
    ) -> None:
        self.values.append(value)

    def fail(
        self,
        exception: Exception,
        /,
    ) -> None:
        self.exceptions.append(exception)


class StubTask:
    """What an attempt task is for the termination: something which can be cancelled."""

    def __init__(self) -> None:
        self.cancellations: int = 0

    def done(self) -> bool:
        return False

    def cancel(self) -> bool:
        self.cancellations += 1
        return True


class Harness:
    def __init__(
        self,
        *,
        limit: int | None = None,
        grace: float = 0.5,
        timings: LeaseTimings | None = LEASED,
        unsupported: Literal["leave", "fail"] = "leave",
        results: Mapping[str, type[State] | type[None]] | None = None,
    ) -> None:
        self.coordinator: Coordinator = Coordinator(
            results={"t": type(None)} if results is None else results,
            limit=limit,
            grace_period=grace,
            coordination=coordination_of(timings),
            unsupported=unsupported,
        )
        self.claims: int = 0
        self.failings: int = 0
        self.history: list[Effect] = []
        self.tasks: int = 0
        self.serials: dict[UUID, int] = {}
        self.terminations: dict[UUID, AttemptTermination] = {}
        self.stubs: dict[UUID, StubTask] = {}
        self.feed(Opened())

    def feed(
        self,
        event: Event,
    ) -> Sequence[Effect]:
        effects: Sequence[Effect] = self.coordinator.handle(event)
        self.history.extend(effects)
        if isinstance(event, ClaimReplied):
            self.claims -= 1

        if isinstance(event, UnsupportedReplied):
            self.failings -= 1

        for effect in effects:
            if isinstance(effect, Claim):
                self.claims += 1

            elif isinstance(effect, FailUnsupported):
                self.failings += 1

            elif isinstance(effect, StartHost):
                self.serials[effect.task.identifier] = effect.serial

            elif isinstance(effect, RunAttempt):
                self._started(effect)

        return effects

    def _started(
        self,
        effect: RunAttempt,
    ) -> None:
        identifier: UUID = effect.claimed.task.identifier
        stub: StubTask = StubTask()
        self.stubs[identifier] = stub
        self.terminations[identifier] = effect.termination
        effect.termination.bind(cast(Any, stub))

    def new(
        self,
        *,
        delays: Sequence[float] = (),
        time_limit: float | None = None,
    ) -> NewTask:
        self.tasks += 1
        return NewTask(
            identifier=UUID(int=self.tasks),
            name="t",
            argument="{}",
            retry=TaskRetryPolicy(delays=tuple(delays)),
            time_limit=time_limit,
            origin=None,
        )

    def claim(
        self,
        *items: ClaimedTask,
    ) -> Sequence[Effect]:
        if self.claims == 0:
            self.feed(RetryDue())

        assert self.claims == 1
        return self.feed(ClaimReplied(items))

    def grant(
        self,
        task: NewTask,
        attempt: int = 1,
    ) -> Sequence[Effect]:
        return self.claim(ClaimedAttempt(task=task, attempt=attempt))

    def interrupt(
        self,
        task: NewTask,
        attempt: int = 1,
        *,
        cancel: bool = False,
    ) -> Sequence[Effect]:
        return self.claim(ClaimedInterrupted(task=task, attempt=attempt, cancel_requested=cancel))

    def phase(
        self,
        task: NewTask,
    ) -> Phase | None:
        return self.coordinator.phase(task.identifier)

    def serial(
        self,
        task: NewTask,
    ) -> int:
        return self.serials[task.identifier]

    # --- reaching states -----------------------------------------------------------------

    def entering(
        self,
        task: NewTask,
        attempt: int = 1,
    ) -> None:
        self.grant(task, attempt)

    def attempting(
        self,
        task: NewTask,
    ) -> Sequence[Effect]:
        self.entering(task)
        return self.feed(ScopeEntered(task.identifier, self.serial(task), copy_context()))

    def ended(
        self,
        task: NewTask,
        result: AttemptResult,
        attempt: int = 1,
    ) -> Sequence[Effect]:
        return self.feed(AttemptEnded(task.identifier, self.serial(task), attempt, result))

    def recording(
        self,
        task: NewTask,
        result: AttemptResult | None = None,
    ) -> Sequence[Effect]:
        self.attempting(task)
        return self.ended(task, result if result is not None else AttemptReturned("null"))

    def recorded(
        self,
        task: NewTask,
        status: StoredStatus | StoreFailure | CallCancelled | None,
        attempt: int = 1,
    ) -> Sequence[Effect]:
        return self.feed(RecordReplied(task.identifier, self.serial(task), attempt, status))

    def parked(
        self,
        task: NewTask,
    ) -> None:
        self.recording(task, AttemptRaised(FAILURE))
        self.recorded(task, StoredRetryPending())

    def discarding(
        self,
        task: NewTask,
    ) -> None:
        self.attempting(task)
        self.grant(task, 2)

    def releasing(
        self,
        task: NewTask,
    ) -> None:
        self.entering(task)
        self.feed(CloseRequested())

    def settle_claims(self) -> None:
        while self.claims:
            self.feed(ClaimReplied(()))

    def await_outcome(
        self,
        task: NewTask,
    ) -> Reply:
        reply: Reply = Reply()
        self.feed(OutcomeAsked(task.identifier, reply))
        return reply


def of[T](
    effects: Sequence[Effect],
    kind: type[T],
) -> list[T]:
    return [effect for effect in effects if isinstance(effect, kind)]


def logs(
    effects: Sequence[Effect],
    level: str,
) -> list[str]:
    return [
        effect.message % effect.arguments
        for effect in effects
        if isinstance(effect, Log) and effect.level == level
    ]


def finished(
    outcome: StoredCompleted | StoredFailed | StoredCancelled,
) -> StoredFinished:
    return StoredFinished(outcome=outcome)


COMPLETED = finished(StoredCompleted(result="null"))
FAILED = finished(StoredFailed())
CANCELLED = finished(StoredCancelled())


def stored(
    task: NewTask,
    status: StoredStatus,
    attempts: int = 0,
) -> StoredTask:
    results: list[Any] = [FAILURE] * attempts
    if isinstance(status, StoredFinished):
        if isinstance(status.outcome, StoredCompleted):
            results = [*results[:-1], "completed"] if results else ["completed"]

        elif isinstance(status.outcome, StoredFailed) and not results:
            results = [FAILURE]

    return StoredTask(
        task=task,
        status=status,
        attempts=tuple(StoredAttempt(result=result) for result in results),
    )


# --- granted ClaimedAttempt ----------------------------------------------------------------


@mark.parametrize("closing", ["grace", "cut_off"])
def test_a_claim_granted_while_closing_is_released_and_never_run(closing: str) -> None:
    # not Open, any phase: orphan release
    harness = Harness(grace=0.5 if closing == "grace" else 0.0)
    task = harness.new()
    harness.feed(RetryDue())  # a claim in flight when the close begins
    harness.feed(CloseRequested())

    effects = harness.claim(ClaimedAttempt(task=task, attempt=1))

    assert of(effects, Release) == [Release(task.identifier, None, 1, None)]
    assert harness.phase(task) is None
    assert of(effects, StartHost) == []


def test_a_claim_with_no_run_starts_a_run_entering_with_its_time_limit() -> None:
    harness = Harness()
    task = harness.new(time_limit=0.5)

    effects = harness.grant(task)

    assert isinstance(harness.phase(task), Entering)
    assert len(of(effects, StartHost)) == 1
    timers = of(effects, StartTimer)
    assert [(timer.key, timer.delay) for timer in timers] == [(("limit", task.identifier), 0.5)]
    assert isinstance(timers[0].event, TimeLimitElapsed)
    assert harness.coordinator.slots == 1


def test_a_claim_for_a_parked_run_continues_in_its_scope() -> None:
    # Parked(k), n > k
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.parked(task)
    serial = harness.serial(task)

    effects = harness.grant(task, 2)

    phase = harness.phase(task)
    assert isinstance(phase, Attempting)
    assert phase.held.attempt == 2
    assert of(effects, StartHost) == []
    assert [effect.serial for effect in of(effects, RunAttempt)] == [serial]


@mark.parametrize("attempt", [1, 2])
def test_a_claim_of_an_attempt_already_recorded_is_released_with_an_error(attempt: int) -> None:
    # Parked(k), n <= k: impossible
    harness = Harness()
    task = harness.new(delays=(0.1, 0.1))
    harness.parked(task)
    harness.grant(task, 2)
    harness.ended(task, AttemptRaised(FAILURE), 2)
    harness.recorded(task, StoredRetryPending(), 2)

    effects = harness.grant(task, attempt)

    assert of(effects, Release) == [Release(task.identifier, None, attempt, None)]
    assert len(logs(effects, "error")) == 1
    assert isinstance(harness.phase(task), Parked)


@mark.parametrize("phase", ["entering", "attempting", "recording"])
def test_the_same_lease_granted_again_keeps_the_phase(phase: str) -> None:
    harness = Harness()
    task = harness.new()
    {
        "entering": harness.entering,
        "attempting": harness.attempting,
        "recording": harness.recording,
    }[phase](task)
    before = harness.phase(task)
    assert isinstance(before, Entering | Attempting | Recording)
    granted = before.held.granted

    effects = harness.grant(task, 1)

    assert harness.phase(task) is before
    assert before.held.granted > granted
    assert of(effects, Release) == []
    assert of(effects, RunAttempt) == []


@mark.parametrize("phase", ["entering", "attempting", "recording"])
def test_an_older_lease_than_the_one_held_is_released_with_an_error(phase: str) -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.parked(task)
    harness.grant(task, 2)  # attempting 2
    if phase == "recording":
        harness.ended(task, AttemptReturned("null"), 2)

    if phase == "entering":
        task = harness.new()
        harness.entering(task, 3)

    effects = harness.grant(task, 1)

    assert of(effects, Release) == [Release(task.identifier, None, 1, None)]
    assert len(logs(effects, "error")) == 1


def test_a_newer_claim_replaces_an_attempt_never_started() -> None:
    # Entering(a), n > a
    harness = Harness()
    task = harness.new(time_limit=0.5)
    harness.entering(task)

    effects = harness.grant(task, 2)

    phase = harness.phase(task)
    assert isinstance(phase, Entering)
    assert phase.held.attempt == 2
    assert [timer.key for timer in of(effects, StartTimer)] == [("limit", task.identifier)]
    assert of(effects, StartHost) == []


def test_a_newer_claim_during_an_attempt_discards_it() -> None:
    # Attempting(a), n > a
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    effects = harness.grant(task, 2)

    phase = harness.phase(task)
    assert isinstance(phase, Discarding)
    assert phase.attempt == 1
    assert phase.successor is not None and phase.successor.attempt == 2
    assert harness.stubs[task.identifier].cancellations == 1
    assert harness.terminations[task.identifier].reason is None
    assert len(logs(effects, "warning")) == 1
    assert harness.coordinator.slots == 2


@mark.parametrize("hosted", [True, False])
def test_a_newer_claim_while_recording_moves_on_at_once(hosted: bool) -> None:
    # Recording(a), n > a
    harness = Harness()
    task = harness.new()
    harness.recording(task)
    if not hosted:
        harness.feed(ScopeExited(task.identifier, harness.serial(task), RuntimeError()))

    effects = harness.grant(task, 2)

    phase = harness.phase(task)
    assert isinstance(phase, Attempting) if hosted else isinstance(phase, Entering)
    assert phase is not None and phase.held.attempt == 2
    assert len(of(effects, RunAttempt)) == (1 if hosted else 0)
    assert len(of(effects, StartHost)) == (0 if hosted else 1)
    # the record of attempt 1 which was in flight is dropped by its attempt
    stale = harness.feed(RecordReplied(task.identifier, harness.serials[task.identifier], 1, None))
    assert of(stale, Release) == []
    assert harness.phase(task) is phase


def test_a_claim_during_a_discard_is_kept_and_the_one_before_it_released() -> None:
    # Discarding: orphan-release the previous successor, stash n
    harness = Harness()
    task = harness.new(delays=(0.1, 0.1))
    harness.discarding(task)

    effects = harness.grant(task, 3)

    phase = harness.phase(task)
    assert isinstance(phase, Discarding)
    assert phase.successor is not None and phase.successor.attempt == 3
    assert of(effects, Release) == [Release(task.identifier, None, 2, None)]


def test_a_claim_while_releasing_is_released() -> None:
    harness = Harness()
    task = harness.new()
    harness.releasing(task)
    assert isinstance(harness.phase(task), Releasing)

    harness.feed(RetryDue())
    harness.feed(ClaimReplied(()))  # a claim in flight may come back with a grant
    effects = harness.coordinator.handle(ClaimReplied((ClaimedAttempt(task=task, attempt=2),)))

    assert of(effects, Release) == [Release(task.identifier, None, 2, None)]


# --- ClaimedInterrupted --------------------------------------------------------------------


@mark.parametrize("phase", ["entering", "attempting", "recording"])
@mark.parametrize("cancel", [False, True])
def test_an_interruption_of_the_attempt_this_runtime_holds_is_kept(
    phase: str,
    cancel: bool,
) -> None:
    harness = Harness()
    task = harness.new()
    {
        "entering": harness.entering,
        "attempting": harness.attempting,
        "recording": harness.recording,
    }[phase](task)

    effects = harness.interrupt(task, 1, cancel=cancel)

    assert of(effects, RecordInterruption) == []
    assert harness.coordinator.interruptions == frozenset()
    requested = cancel and phase != "recording"
    if phase != "recording":
        termination = harness.coordinator.phase(task.identifier)
        assert isinstance(termination, Entering | Attempting)
        assert (termination.termination.reason == "cancel") is requested


def test_a_second_claim_of_an_interruption_being_recorded_is_discarded() -> None:
    harness = Harness()
    task = harness.new()
    first = harness.interrupt(task)
    second = harness.interrupt(task)

    assert len(of(first, RecordInterruption)) == 1
    assert of(second, RecordInterruption) == []
    assert harness.coordinator.interruptions == {(task.identifier, 1)}


@mark.parametrize("state", ["no_run", "parked", "discarding"])
@mark.parametrize("delays", [(), (0.2,)])
def test_an_interruption_of_another_runtime_is_recorded_by_policy(
    state: str,
    delays: tuple[float, ...],
) -> None:
    harness = Harness()
    task = harness.new(delays=(0.1, *delays) if state != "no_run" else delays)
    attempt = 1
    if state == "parked":
        harness.parked(task)
        attempt = 2

    elif state == "discarding":
        # the attempt being discarded, not the one which follows it
        harness.discarding(task)

    effects = harness.interrupt(task, attempt)

    records = of(effects, RecordInterruption)
    assert len(records) == 1
    expected = (
        AttemptRetried(failure=TaskInterrupted(), delay=tuple(task.retry.delays)[attempt - 1])
        if attempt <= len(task.retry.delays)
        else AttemptFailed(failure=TaskInterrupted())
    )
    assert records[0].end == expected


# --- host events ---------------------------------------------------------------------------


def test_a_scope_entered_starts_the_attempt() -> None:
    harness = Harness()
    task = harness.new()
    harness.entering(task)

    effects = harness.feed(ScopeEntered(task.identifier, harness.serial(task), copy_context()))

    assert isinstance(harness.phase(task), Attempting)
    runs = of(effects, RunAttempt)
    assert len(runs) == 1 and runs[0].claimed.attempt == 1
    assert len(logs(effects, "debug")) == 1


@mark.parametrize(
    ("reason", "record"),
    [
        ("cancel", AttemptCancelled()),
        ("time_limit", AttemptFailed(failure=TaskTimeLimitExceeded(time_limit=0.5))),
        ("close", None),
    ],
)
def test_a_scope_entered_after_a_request_does_not_call_the_function(
    reason: str,
    record: Any,
) -> None:
    harness = Harness()
    task = harness.new(time_limit=0.5)
    harness.entering(task)
    phase = harness.phase(task)
    assert isinstance(phase, Entering)
    assert phase.termination.request(cast(Any, reason))

    effects = harness.feed(ScopeEntered(task.identifier, harness.serial(task), copy_context()))

    assert of(effects, RunAttempt) == []
    if record is None:
        assert [type(effect) for effect in of(effects, Release)] == [Release]
        assert isinstance(harness.phase(task), Releasing)

    else:
        assert [effect.end for effect in of(effects, Record)] == [record]
        assert isinstance(harness.phase(task), Recording)


def test_a_scope_entered_in_another_phase_stores_the_context_only() -> None:
    harness = Harness()
    task = harness.new()
    harness.releasing(task)

    effects = harness.feed(ScopeEntered(task.identifier, harness.serial(task), copy_context()))

    assert of(effects, RunAttempt) == []
    assert isinstance(harness.phase(task), Releasing)


def test_a_scope_which_could_not_be_entered_records_a_preparation_failure_by_policy() -> None:
    # decision G1
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.entering(task)

    effects = harness.feed(ScopeFailed(task.identifier, harness.serial(task), OSError()))

    records = of(effects, Record)
    assert len(records) == 1
    assert records[0].end == AttemptRetried(
        failure=TaskFailureRecord(kind="preparation", exception_type="builtins.OSError"),
        delay=0.1,
    )
    assert len(logs(effects, "error")) == 1
    assert isinstance(harness.phase(task), Recording)
    assert harness.coordinator.hosts == 0


def test_a_scope_which_could_not_be_entered_after_a_request_follows_the_request() -> None:
    harness = Harness()
    task = harness.new()
    harness.entering(task)
    phase = harness.phase(task)
    assert isinstance(phase, Entering)
    phase.termination.request("cancel")

    effects = harness.feed(ScopeFailed(task.identifier, harness.serial(task), OSError()))

    assert [effect.end for effect in of(effects, Record)] == [AttemptCancelled()]


def test_a_scope_failure_in_another_phase_drops_the_host_only() -> None:
    harness = Harness()
    task = harness.new()
    harness.releasing(task)

    effects = harness.feed(ScopeFailed(task.identifier, harness.serial(task), OSError()))

    assert of(effects, Record) == []
    assert isinstance(harness.phase(task), Releasing)
    assert harness.coordinator.hosts == 0


@mark.parametrize(
    ("result", "end"),
    [
        (AttemptReturned("null"), AttemptCompleted(result="null")),
        (AttemptRaised(FAILURE), AttemptFailed(failure=FAILURE)),
        (AttemptCancelledLocally(), AttemptCancelled()),
        (AttemptTerminated("cancel", None), AttemptCancelled()),
        (
            AttemptTerminated("time_limit", "raised"),
            AttemptFailed(failure=TaskTimeLimitExceeded(time_limit=0.5)),
        ),
    ],
)
def test_an_attempt_which_ended_is_recorded(result: AttemptResult, end: Any) -> None:
    harness = Harness()
    task = harness.new(time_limit=0.5)
    harness.attempting(task)

    effects = harness.ended(task, result)

    assert isinstance(harness.phase(task), Recording)
    assert [effect.end for effect in of(effects, Record)] == [end]
    assert [effect.key for effect in of(effects, StopTimer)] == [("limit", task.identifier)]


def test_an_attempt_ended_by_a_discarded_request_logs_what_it_did() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    effects = harness.ended(task, AttemptTerminated("cancel", "returned"))

    assert len(logs(effects, "warning")) == 1


def test_an_attempt_ended_by_the_close_is_released() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    effects = harness.ended(task, AttemptTerminated("close", None))

    assert isinstance(harness.phase(task), Releasing)
    assert of(effects, Release) == [
        Release(task.identifier, harness.serial(task), 1, of(effects, Release)[0].context)
    ]
    assert of(effects, Record) == []


@mark.parametrize("opened", [True, False])
@mark.parametrize("successor", [True, False])
def test_the_end_of_a_discarded_attempt_is_dropped(opened: bool, successor: bool) -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.attempting(task)
    if successor:
        harness.grant(task, 2)

    else:
        renewed(harness, task, None)

    if not opened:
        harness.feed(CloseRequested())

    effects = harness.ended(task, AttemptReturned("null"))

    assert of(effects, Record) == []
    if successor and opened:
        phase = harness.phase(task)
        assert isinstance(phase, Attempting) and phase.held.attempt == 2
        assert len(of(effects, RunAttempt)) == 1

    else:
        # the successor was given back when the close began
        assert harness.phase(task) is None
        assert len(of(effects, ExitHost)) == 1
        assert of(effects, Release) == []


@mark.parametrize("attempt", [1, 2])
def test_an_end_of_an_attempt_which_is_not_current_is_an_error_and_ignored(attempt: int) -> None:
    harness = Harness()
    task = harness.new()
    harness.recording(task)

    effects = harness.ended(task, AttemptReturned("null"), attempt)

    assert len(logs(effects, "error")) == 1
    assert isinstance(harness.phase(task), Recording)


def test_the_exit_of_a_host_of_a_run_which_ended_is_dropped() -> None:
    harness = Harness()
    task = harness.new()
    harness.recording(task)
    harness.recorded(task, COMPLETED)
    assert harness.phase(task) is None
    assert harness.coordinator.hosts == 1

    effects = harness.feed(ScopeExited(task.identifier, harness.serial(task), None))

    assert effects == []
    assert harness.coordinator.hosts == 0


def test_the_exit_of_a_host_of_a_run_which_ended_logs_its_exception() -> None:
    harness = Harness()
    task = harness.new()
    harness.recording(task)
    harness.recorded(task, COMPLETED)

    effects = harness.feed(ScopeExited(task.identifier, harness.serial(task), OSError()))

    assert len(logs(effects, "error")) == 1


@mark.parametrize("phase", ["entering", "attempting", "discarding", "parked"])
def test_a_host_which_exited_under_its_run_gives_its_claims_back(phase: str) -> None:
    harness = Harness()
    task = harness.new(delays=(0.1, 0.1))
    held = 1
    if phase == "parked":
        harness.parked(task)

    elif phase == "discarding":
        harness.discarding(task)
        held = 2

    else:
        {"entering": harness.entering, "attempting": harness.attempting}[phase](task)

    effects = harness.feed(ScopeExited(task.identifier, harness.serial(task), RuntimeError()))

    assert harness.phase(task) is None
    released = [release.attempt for release in of(effects, Release)]
    assert released == ([] if phase == "parked" else [held])
    assert of(effects, ExitHost) == []  # the host is gone
    assert harness.coordinator.hosts == 0


@mark.parametrize("phase", ["recording", "releasing"])
def test_a_host_which_exited_under_its_run_does_not_stop_a_record_or_a_release(
    phase: str,
) -> None:
    harness = Harness()
    task = harness.new()
    if phase == "recording":
        harness.recording(task)

    else:
        harness.releasing(task)

    effects = harness.feed(ScopeExited(task.identifier, harness.serial(task), RuntimeError()))

    assert isinstance(harness.phase(task), Recording | Releasing)
    assert of(effects, Release) == []
    assert harness.coordinator.hosts == 0


@mark.parametrize("exception", [SystemExit(), KeyboardInterrupt()])
def test_an_interpreter_exit_records_and_releases_nothing(exception: BaseException) -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    effects = harness.feed(ScopeExited(task.identifier, harness.serial(task), exception))

    assert harness.phase(task) is None
    assert of(effects, Record) == []
    assert of(effects, Release) == []


# --- RecordReplied -------------------------------------------------------------------------


@mark.parametrize(
    ("status", "outcome", "errors"),
    [
        (COMPLETED, TaskCompleted(None), 0),
        (CANCELLED, TaskCancelled(), 0),
        (FAILED, "failed", 1),
    ],
)
def test_a_fixed_outcome_resolves_the_awaiters_and_ends_the_run(
    status: StoredFinished,
    outcome: Any,
    errors: int,
) -> None:
    harness = Harness()
    task = harness.new()
    result = AttemptRaised(FAILURE) if status is FAILED else AttemptReturned("null")
    harness.recording(task, result)
    first, second = harness.await_outcome(task), harness.await_outcome(task)

    effects = harness.recorded(task, status)

    assert harness.phase(task) is None
    resolved = [effect.value for effect in of(effects, Resolve)]
    expected = TaskFailed(failure=FAILURE, attempts=1) if outcome == "failed" else outcome
    assert resolved == [expected, expected]
    assert {effect.reply for effect in of(effects, Resolve)} == {first, second}
    assert len(logs(effects, "error")) == errors
    assert len(logs(effects, "debug")) == 1
    assert [type(effect) for effect in effects].index(ExitHost) > max(
        index for index, effect in enumerate(effects) if isinstance(effect, Log)
    )


def test_a_cancel_the_store_accepted_over_a_returned_value_is_logged_without_it() -> None:
    harness = Harness()
    task = harness.new()
    harness.recording(task)

    effects = harness.recorded(task, CANCELLED)

    warnings = logs(effects, "warning")
    assert len(warnings) == 1 and "null" not in warnings[0]


@mark.parametrize("state", ["open", "closing", "no_host"])
def test_a_retry_pending_parks_the_run_only_while_open_and_hosted(state: str) -> None:
    harness = Harness()
    task = harness.new(delays=(0.25,))
    harness.recording(task, AttemptRaised(FAILURE))
    if state == "closing":
        harness.feed(CloseRequested())

    elif state == "no_host":
        harness.feed(ScopeExited(task.identifier, harness.serial(task), RuntimeError()))

    effects = harness.recorded(task, StoredRetryPending())

    assert len(logs(effects, "warning")) == 1
    if state == "open":
        assert isinstance(harness.phase(task), Parked)
        timers = of(effects, StartTimer)
        assert [(timer.key, timer.delay) for timer in timers] == [
            (("retry", task.identifier), 0.25)
        ]
        assert harness.coordinator.slots == 0

    else:
        assert harness.phase(task) is None
        assert of(effects, StartTimer) == []


@mark.parametrize("state", ["open_retried", "open_completed", "closing", "no_host"])
def test_a_record_which_was_not_held_leaves_the_outcome_to_the_store(state: str) -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.recording(
        task,
        AttemptReturned("null") if state == "open_completed" else AttemptRaised(FAILURE),
    )
    if state == "closing":
        harness.feed(CloseRequested())

    elif state == "no_host":
        harness.feed(ScopeExited(task.identifier, harness.serial(task), RuntimeError()))

    awaiter = harness.await_outcome(task)

    effects = harness.recorded(task, None)

    assert len(logs(effects, "warning")) == 1
    if state == "open_retried":
        # the next read decides, held back until the claim it wants is answered
        assert isinstance(harness.phase(task), Parked)
        assert of(effects, Read) == []
        assert len(of(effects, Claim)) == 1
        assert len(of(harness.feed(ClaimReplied(())), Read)) == 1

    else:
        assert harness.phase(task) is None

    # awaiters read the store from now on, they are not resolved here
    assert of(effects, Resolve) == []
    assert awaiter.values == []


@mark.parametrize("status", [StoredQueued(), StoredRunning(cancel_requested=False)])
def test_a_record_leaving_the_task_unfinished_is_an_error(status: StoredStatus) -> None:
    harness = Harness()
    task = harness.new()
    harness.recording(task)

    effects = harness.recorded(task, status)

    assert harness.phase(task) is None
    assert len(logs(effects, "error")) == 1


@mark.parametrize("lifecycle", ["open", "grace", "cut_off"])
def test_a_failing_record_is_kept_for_the_next_renewal_until_the_cut_off(lifecycle: str) -> None:
    harness = Harness(grace=0.5 if lifecycle != "cut_off" else 0.0)
    task = harness.new()
    harness.recording(task)
    awaiter = harness.await_outcome(task)
    if lifecycle != "open":
        harness.feed(CloseRequested())

    effects = harness.recorded(task, StoreFailure(OSError()))

    errors = logs(effects, "error")
    if lifecycle == "cut_off":
        assert harness.phase(task) is None
        assert len(errors) == 2 and "abandoned" in errors[1]

    else:
        phase = harness.phase(task)
        assert isinstance(phase, Recording) and phase.failed
        assert isinstance(phase.call, Waiting)
        assert len(errors) == 1
        # awaiters read the store from now on
        assert len(of(effects, Read)) == 1

    assert awaiter.values == []


# --- the other replies ---------------------------------------------------------------------


def test_a_release_fixing_a_cancel_resolves_the_awaiters_and_is_kept() -> None:
    harness = Harness()
    task = harness.new()
    harness.releasing(task)
    awaiter = harness.await_outcome(task)

    effects = harness.feed(ReleaseReplied(task.identifier, harness.serial(task), 1, CANCELLED))

    assert awaiter.values == [] and [effect.value for effect in of(effects, Resolve)] == [
        TaskCancelled()
    ]
    assert harness.coordinator.released(task.identifier) == TaskCancelled()
    assert len(logs(effects, "debug")) == 1
    assert harness.phase(task) is None


def test_a_failing_release_leaves_the_lease_to_expire_and_ends_the_run() -> None:
    harness = Harness()
    task = harness.new()
    harness.releasing(task)

    effects = harness.feed(
        ReleaseReplied(task.identifier, harness.serial(task), 1, StoreFailure(OSError()))
    )

    assert len(logs(effects, "error")) == 1
    assert harness.phase(task) is None
    assert harness.coordinator.released(task.identifier) is None


def test_a_release_of_a_claim_without_a_run_only_fixes_a_cancel() -> None:
    harness = Harness()
    task = harness.new()
    harness.feed(RetryDue())
    harness.feed(CloseRequested())
    harness.claim(ClaimedAttempt(task=task, attempt=1))

    effects = harness.feed(ReleaseReplied(task.identifier, None, 1, CANCELLED))

    assert harness.coordinator.released(task.identifier) == TaskCancelled()
    assert of(effects, ExitHost) == []


def test_a_failed_recording_of_an_interruption_is_retried_and_dropped_at_the_cut_off() -> None:
    harness = Harness()
    task = harness.new()
    harness.interrupt(task)

    effects = harness.feed(InterruptionRecorded(task.identifier, 1, StoreFailure(OSError())))
    assert len(logs(effects, "error")) == 1
    assert harness.coordinator.interruptions == {(task.identifier, 1)}

    retried = harness.feed(RenewalTick())
    assert len(of(retried, RecordInterruption)) == 1
    harness.feed(CloseRequested())
    abandoned = harness.feed(GraceElapsed())
    assert harness.coordinator.interruptions == {(task.identifier, 1)}  # in flight: not abandoned

    effects = harness.feed(InterruptionRecorded(task.identifier, 1, StoreFailure(OSError())))
    assert harness.coordinator.interruptions == frozenset()
    assert len(logs(effects, "error")) == 2 and logs(abandoned, "error") == []


def test_an_interruption_which_the_store_did_not_hold_is_dropped() -> None:
    harness = Harness()
    task = harness.new()
    harness.interrupt(task)

    effects = harness.feed(InterruptionRecorded(task.identifier, 1, None))

    assert harness.coordinator.interruptions == frozenset()
    assert of(effects, Resolve) == []


def test_a_finished_interruption_resolves_the_awaiters_and_ends_a_parked_run() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.parked(task)
    awaiter = harness.await_outcome(task)
    harness.interrupt(task, 2)

    effects = harness.feed(InterruptionRecorded(task.identifier, 2, FAILED))

    assert awaiter.values == []
    assert [effect.value for effect in of(effects, Resolve)] == [
        TaskFailed(failure=TaskInterrupted(), attempts=2)
    ]
    assert harness.phase(task) is None
    assert harness.coordinator.interruptions == frozenset()


def test_a_retried_interruption_moves_the_park_and_arms_the_retry() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1, 0.2))
    harness.parked(task)
    harness.interrupt(task, 2)

    effects = harness.feed(InterruptionRecorded(task.identifier, 2, StoredRetryPending()))

    phase = harness.phase(task)
    assert isinstance(phase, Parked) and phase.attempts == 2
    timers = of(effects, StartTimer)
    assert [(timer.key, timer.delay) for timer in timers] == [(("retry", task.identifier), 0.2)]


def test_a_retried_interruption_arms_nothing_while_closing() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.interrupt(task)
    harness.feed(CloseRequested())

    effects = harness.feed(InterruptionRecorded(task.identifier, 1, StoredRetryPending()))

    assert of(effects, StartTimer) == []


def renewed(
    harness: Harness,
    task: NewTask,
    state: LeaseState | None,
    *,
    attempt: int = 1,
    stale: bool = False,
) -> Sequence[Effect]:
    effects = harness.feed(RenewalTick())
    renew = of(effects, Renew)[0]
    if stale:
        harness.grant(task, attempt)

    leases: Mapping[UUID, LeaseState] = {} if state is None else {task.identifier: state}
    return harness.feed(RenewReplied(renew.asked, renew.issued, LeaseRenewal(leases=leases)))


def test_a_lease_lost_during_an_attempt_discards_it() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    effects = renewed(harness, task, None)

    phase = harness.phase(task)
    assert isinstance(phase, Discarding) and phase.successor is None
    assert harness.stubs[task.identifier].cancellations == 1
    assert harness.terminations[task.identifier].reason is None
    assert len(logs(effects, "warning")) == 1
    assert of(effects, Record) == []


def test_a_lease_lost_before_the_attempt_started_ends_the_run() -> None:
    harness = Harness()
    task = harness.new()
    harness.entering(task)

    effects = renewed(harness, task, None)

    assert harness.phase(task) is None
    assert len(of(effects, ExitHost)) == 1
    assert len(logs(effects, "warning")) == 1


@mark.parametrize("phase", ["entering", "attempting"])
def test_a_cancel_requested_in_a_renewal_is_requested_of_the_attempt(phase: str) -> None:
    harness = Harness()
    task = harness.new()
    {"entering": harness.entering, "attempting": harness.attempting}[phase](task)

    renewed(harness, task, "cancel_requested")

    current = harness.phase(task)
    assert isinstance(current, Entering | Attempting)
    assert current.termination.reason == "cancel"


def test_a_held_lease_changes_nothing() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    effects = renewed(harness, task, "held")

    assert isinstance(harness.phase(task), Attempting)
    assert logs(effects, "warning") == []


def test_a_renewal_result_older_than_the_grant_is_ignored() -> None:
    # the lease was granted again after the renewal was issued
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    renewed(harness, task, None, attempt=1, stale=True)

    assert isinstance(harness.phase(task), Attempting)
    assert harness.stubs[task.identifier].cancellations == 0


def test_a_renewal_does_not_decide_a_run_which_is_recording() -> None:
    harness = Harness()
    task = harness.new()
    harness.recording(task)

    renewed(harness, task, None)

    assert isinstance(harness.phase(task), Recording)


def test_a_failing_renewal_is_logged_and_the_next_one_counted_from_its_return() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)
    renew = of(harness.feed(RenewalTick()), Renew)[0]

    effects = harness.feed(RenewReplied(renew.asked, renew.issued, StoreFailure(OSError())))

    assert len(logs(effects, "error")) == 1
    assert [(timer.key, timer.delay) for timer in of(effects, StartTimer)] == [(("renew",), 0.3)]


@mark.parametrize(
    "item",
    [
        "missing",
        "finished",
        "other_attempts",
        "running_stable",
    ],
)
def test_a_read_ends_a_park_the_store_moved_past(item: str) -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    read = parked_with_read(harness, task)
    result: dict[UUID, StoredTask] = {}
    if item == "finished":
        result[task.identifier] = stored(task, COMPLETED, 1)

    elif item == "other_attempts":
        result[task.identifier] = stored(task, StoredRetryPending(), 2)

    elif item == "running_stable":
        result[task.identifier] = stored(task, StoredRunning(cancel_requested=False), 1)

    effects = harness.feed(ReadReplied(read.identifiers, read.parks, read.epoch, result))

    assert harness.phase(task) is None
    assert of(effects, ExitHost) != []


def parked_with_read(
    harness: Harness,
    task: NewTask,
) -> Read:
    harness.parked(task)
    # the park wants a claim, and a read is held back while one is in flight
    effects = harness.feed(ClaimReplied(())) if harness.claims else []
    return of(effects, Read)[0]


def read_replied(
    harness: Harness,
    read: Read,
    task: NewTask,
    status: StoredStatus,
    attempts: int = 1,
    epoch: int | Literal["issued"] | None = "issued",
) -> Sequence[Effect]:
    return harness.feed(
        ReadReplied(
            read.identifiers,
            read.parks,
            read.epoch if epoch == "issued" else epoch,
            {task.identifier: stored(task, status, attempts)},
        )
    )


def test_a_read_which_may_have_preceded_a_claim_does_not_end_a_park_on_running() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    read = parked_with_read(harness, task)

    read_replied(harness, read, task, StoredRunning(cancel_requested=False), epoch=None)
    assert isinstance(harness.phase(task), Parked)

    read_replied(harness, read, task, StoredRunning(cancel_requested=False), epoch=-1)
    assert isinstance(harness.phase(task), Parked)


def test_a_park_stays_while_an_interruption_job_of_its_task_exists() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1, 0.1))
    read = parked_with_read(harness, task)
    harness.interrupt(task, 2)

    read_replied(harness, read, task, StoredRunning(cancel_requested=False))

    assert isinstance(harness.phase(task), Parked)


@mark.parametrize("snapshot", ["finished", "missing"])
def test_a_read_asked_for_an_earlier_park_does_not_end_the_next_one(snapshot: str) -> None:
    harness = Harness()
    task = harness.new(delays=(0.1, 0.1))
    read = parked_with_read(harness, task)
    harness.grant(task, 2)
    harness.ended(task, AttemptRaised(FAILURE), 2)
    harness.recorded(task, StoredRetryPending(), 2)
    result = {task.identifier: stored(task, COMPLETED, 1)} if snapshot == "finished" else {}

    harness.feed(ReadReplied(read.identifiers, read.parks, read.epoch, result))

    phase = harness.phase(task)
    assert isinstance(phase, Parked) and phase.attempts == 2


def test_a_retry_pending_store_leaves_a_park_waiting() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    read = parked_with_read(harness, task)

    read_replied(harness, read, task, StoredRetryPending())

    assert isinstance(harness.phase(task), Parked)


def test_a_read_resolves_the_awaiters_of_a_finished_task_and_fails_those_of_a_missing_one() -> None:
    harness = Harness()
    finished_task, missing_task = harness.new(), harness.new()
    first = harness.await_outcome(finished_task)
    harness.settle_claims()
    # the first awaiter asked a read at once, the second one waits for the next
    second = harness.await_outcome(missing_task)
    read = of(harness.history, Read)[0]
    assert read.identifiers == {finished_task.identifier}

    effects = harness.feed(
        ReadReplied(
            read.identifiers,
            read.parks,
            read.epoch,
            {finished_task.identifier: stored(finished_task, COMPLETED, 1)},
        )
    )
    assert {effect.reply: effect.value for effect in of(effects, Resolve)} == {
        first: TaskCompleted(None)
    }
    assert of(effects, Fail) == []

    read = of(harness.feed(PollDue()), Read)[0]
    assert read.identifiers == {missing_task.identifier}
    effects = harness.feed(ReadReplied(read.identifiers, read.parks, read.epoch, {}))

    failed = of(effects, Fail)
    assert [effect.reply for effect in failed] == [second]
    assert isinstance(failed[0].exception, LookupError)


def test_a_failing_read_is_logged_and_asked_again_at_the_next_tick() -> None:
    harness = Harness()
    task = harness.new()
    harness.await_outcome(task)
    read = of(harness.history, Read)[0]

    effects = harness.feed(
        ReadReplied(read.identifiers, read.parks, read.epoch, StoreFailure(OSError()))
    )

    assert len(logs(effects, "error")) == 1
    harness.feed(PollDue())
    assert len(of(harness.feed(PollTick()), Read)) == 1


def test_reads_are_at_most_one_for_each_polling_interval() -> None:
    harness = Harness()
    task = harness.new()
    first = harness.feed(OutcomeAsked(task.identifier, Reply()))
    assert len(of(first, Read)) == 1
    assert [timer.delay for timer in of(first, StartTimer) if timer.key == ("read",)] == [0.1]
    harness.feed(ReadReplied({task.identifier}, {}, 0, {}))

    assert of(harness.feed(PollTick()), Read) == []
    assert of(harness.feed(OutcomeAsked(task.identifier, Reply())), Read) == []
    assert len(of(harness.feed(PollDue()), Read)) == 1


def test_a_read_waits_for_a_claim_in_flight_while_a_run_is_parked() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.parked(task)
    assert harness.claims == 1  # the park wants a claim

    assert of(harness.feed(PollTick()), Read) == []

    # the tick wanted another claim, which holds the read back again
    assert of(harness.feed(ClaimReplied(())), Read) == []
    assert len(of(harness.feed(ClaimReplied(())), Read)) == 1


def test_a_cancel_of_an_unknown_task_raises_lookup_error() -> None:
    harness = Harness()
    task = harness.new()
    reply = Reply()
    asked = harness.feed(CancelAsked(task.identifier, reply, copy_context()))
    assert len(of(asked, CancelTask)) == 1

    effects = harness.feed(CancelReplied(task.identifier, reply, None))

    failed = of(effects, Fail)
    assert len(failed) == 1 and isinstance(failed[0].exception, LookupError)
    assert str(task.identifier) in str(failed[0].exception)


@mark.parametrize("phase", ["entering", "attempting"])
def test_a_pending_cancel_requests_the_cancellation_of_the_attempt(phase: str) -> None:
    harness = Harness()
    task = harness.new()
    {"entering": harness.entering, "attempting": harness.attempting}[phase](task)
    reply = Reply()
    harness.feed(CancelAsked(task.identifier, reply, copy_context()))

    effects = harness.feed(CancelReplied(task.identifier, reply, "pending"))

    assert [effect.value for effect in of(effects, Resolve)] == ["pending"]
    current = harness.phase(task)
    assert isinstance(current, Entering | Attempting)
    assert current.termination.reason == "cancel"


def test_a_cancel_of_a_waiting_task_resolves_its_awaiters_and_ends_a_parked_run() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.parked(task)
    awaiter = harness.await_outcome(task)
    reply = Reply()
    harness.feed(CancelAsked(task.identifier, reply, copy_context()))

    effects = harness.feed(CancelReplied(task.identifier, reply, "cancelled"))

    resolved = {effect.reply: effect.value for effect in of(effects, Resolve)}
    assert resolved == {reply: "cancelled", awaiter: TaskCancelled()}
    assert harness.phase(task) is None


def test_a_cancel_of_a_finished_task_changes_nothing() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)
    reply = Reply()
    harness.feed(CancelAsked(task.identifier, reply, copy_context()))

    effects = harness.feed(CancelReplied(task.identifier, reply, "finished"))

    assert [effect.value for effect in of(effects, Resolve)] == ["finished"]
    current = harness.phase(task)
    assert isinstance(current, Attempting) and current.termination.reason is None


def test_a_failing_cancel_reaches_the_caller_unchanged() -> None:
    harness = Harness()
    task = harness.new()
    reply = Reply()
    harness.feed(CancelAsked(task.identifier, reply, copy_context()))
    failure = OSError("cancel")

    effects = harness.feed(CancelReplied(task.identifier, reply, StoreFailure(failure)))

    assert [effect.exception for effect in of(effects, Fail)] == [failure]


def test_an_insert_resolves_the_spawn_and_wants_a_claim() -> None:
    harness = Harness()
    task = harness.new()
    harness.settle_claims()
    reply = Reply()
    asked = harness.feed(SpawnAsked(task, reply, copy_context()))
    assert [effect.task for effect in of(asked, Insert)] == [task]

    effects = harness.feed(InsertReplied(reply, None))

    assert [effect.value for effect in of(effects, Resolve)] == [None]
    assert len(of(effects, Claim)) == 1


def test_a_failing_insert_reaches_the_caller_and_claims_nothing() -> None:
    harness = Harness()
    task = harness.new()
    harness.settle_claims()
    reply = Reply()
    harness.feed(SpawnAsked(task, reply, copy_context()))
    failure = OSError("insert")

    effects = harness.feed(InsertReplied(reply, StoreFailure(failure)))

    assert [effect.exception for effect in of(effects, Fail)] == [failure]
    assert of(effects, Claim) == []


@mark.parametrize("closing", [True, False])
def test_a_spawn_is_refused_while_closing(closing: bool) -> None:
    harness = Harness()
    task = harness.new()
    if closing:
        harness.feed(CloseRequested())

    effects = harness.feed(SpawnAsked(task, Reply(), copy_context()))

    assert (of(effects, Insert) == []) is closing
    assert (len(of(effects, Fail)) == 1) is closing


def test_a_store_call_is_started_and_its_result_handed_to_the_caller() -> None:
    harness = Harness()
    harness.settle_claims()
    first, second = Reply(), Reply()

    async def call() -> object:
        return 1

    asked = harness.feed(StoreCallAsked(call, first, copy_context()))
    harness.feed(StoreCallAsked(call, second, copy_context()))
    assert len(asked) == 1 and harness.coordinator.pending == 2

    effects = harness.feed(StoreCallReplied(first, 1))
    failed = harness.feed(StoreCallReplied(second, StoreFailure(OSError())))

    assert [effect.value for effect in of(effects, Resolve)] == [1]
    assert len(of(failed, Fail)) == 1
    assert harness.coordinator.pending == 0


@mark.parametrize(
    ("limit_task", "elapsed_serial", "elapsed_attempt", "requested"),
    [
        (True, 0, 1, True),
        (True, 1, 1, False),
        (True, 0, 2, False),
    ],
)
def test_a_time_limit_requests_the_termination_of_the_matching_attempt(
    limit_task: bool,
    elapsed_serial: int,
    elapsed_attempt: int,
    requested: bool,
) -> None:
    harness = Harness()
    task = harness.new(time_limit=0.5)
    harness.attempting(task)
    serial = harness.serial(task) + elapsed_serial

    harness.feed(TimeLimitElapsed(task.identifier, serial, elapsed_attempt))

    phase = harness.phase(task)
    assert isinstance(phase, Attempting)
    assert (phase.termination.reason == "time_limit") is requested
    assert harness.stubs[task.identifier].cancellations == (1 if requested else 0)


def test_a_time_limit_during_the_entering_is_kept_for_the_scope() -> None:
    harness = Harness()
    task = harness.new(time_limit=0.5)
    harness.entering(task)

    harness.feed(TimeLimitElapsed(task.identifier, harness.serial(task), 1))
    effects = harness.feed(ScopeEntered(task.identifier, harness.serial(task), copy_context()))

    assert of(effects, RunAttempt) == []
    assert isinstance(of(effects, Record)[0].end, AttemptFailed)


@mark.parametrize("open_", [True, False])
def test_a_poll_tick_wants_a_claim_while_open_and_arms_the_next_tick(open_: bool) -> None:
    harness = Harness()
    harness.settle_claims()
    if not open_:
        harness.feed(CloseRequested())

    effects = harness.feed(PollTick())

    # a close with nothing to wait for is over at once, and nothing is served after it
    assert len(of(effects, Claim)) == (1 if open_ else 0)
    expected = [(("poll",), 0.1)] if open_ else []
    assert [(timer.key, timer.delay) for timer in of(effects, StartTimer)] == expected


def test_a_retry_due_wants_a_claim() -> None:
    harness = Harness()
    harness.feed(ClaimReplied(()))

    effects = harness.feed(RetryDue())

    assert len(of(effects, Claim)) == 1


def test_a_renewal_tick_issues_the_records_waiting_and_a_renewal() -> None:
    harness = Harness()
    task, other = harness.new(), harness.new()
    harness.recording(task)
    harness.recorded(task, StoreFailure(OSError()))
    harness.interrupt(other)
    harness.feed(InterruptionRecorded(other.identifier, 1, StoreFailure(OSError())))

    effects = harness.feed(RenewalTick())

    assert len(of(effects, Record)) == 1
    assert len(of(effects, RecordInterruption)) == 1
    renews = of(effects, Renew)
    assert len(renews) == 1
    assert renews[0].asked == {task.identifier: 1, other.identifier: 1}
    # the next tick is counted from the return of the renewal
    assert of(effects, StartTimer) == []


def test_a_renewal_tick_with_nothing_to_renew_arms_the_next_one() -> None:
    harness = Harness()

    effects = harness.feed(RenewalTick())

    assert of(effects, Renew) == []
    assert [(timer.key, timer.delay) for timer in of(effects, StartTimer)] == [(("renew",), 0.3)]


def test_a_change_of_a_local_store_is_the_polling_and_the_renewal_tick() -> None:
    harness = Harness(timings=None)
    task = harness.new()
    harness.attempting(task)
    harness.settle_claims()
    awaited = harness.new()
    harness.await_outcome(awaited)
    read = of(harness.history, Read)[-1]
    unfinished = {awaited.identifier: stored(awaited, StoredQueued())}
    harness.feed(ReadReplied(read.identifiers, read.parks, read.epoch, unfinished))

    effects = harness.feed(ChangedReplied(5))

    assert [effect.seen for effect in of(effects, StartChanges)] == [5]
    assert len(of(effects, Renew)) == 1
    assert len(of(effects, Read)) == 1
    assert len(of(effects, Claim)) == 1
    assert of(effects, StartTimer) == []


def test_a_failing_wait_for_changes_is_logged_and_called_again_later() -> None:
    harness = Harness(timings=None)

    effects = harness.feed(ChangedReplied(StoreFailure(OSError())))

    assert len(logs(effects, "error")) == 1
    timers = of(effects, StartTimer)
    assert [timer.key for timer in timers] == [("changes",)]
    assert of(harness.feed(timers[0].event), StartChanges) != []


def test_a_local_store_is_waited_for_changes_from_the_open() -> None:
    fresh = Coordinator(
        results={"t": type(None)},
        limit=None,
        grace_period=0.0,
        coordination=coordination_of(None),
        unsupported="leave",
    )

    opened = fresh.handle(Opened())

    assert [effect.seen for effect in of(opened, StartChanges)] == [-1]
    assert of(opened, StartTimer) == []
    assert len(of(opened, Claim)) == 1


def test_a_close_stops_the_retry_timers_and_the_wait_for_changes() -> None:
    harness = Harness(timings=None)

    effects = harness.feed(CloseRequested())

    assert StopTimers("retry") in effects
    assert len(of(effects, StopChanges)) == 1


def test_a_close_ends_parked_runs_and_releases_the_runs_entering() -> None:
    harness = Harness()
    parked, entering = harness.new(delays=(0.1,)), harness.new()
    harness.parked(parked)
    harness.entering(entering)

    effects = harness.feed(CloseRequested())

    assert harness.phase(parked) is None
    assert isinstance(harness.phase(entering), Releasing)
    assert [release.identifier for release in of(effects, Release)] == [entering.identifier]
    assert harness.coordinator.hosts == 2


def test_a_close_releases_the_successor_of_a_discarded_attempt() -> None:
    harness = Harness()
    task = harness.new()
    harness.discarding(task)

    effects = harness.feed(CloseRequested())

    phase = harness.phase(task)
    assert isinstance(phase, Discarding) and phase.successor is None
    assert of(effects, Release) == [Release(task.identifier, None, 2, None)]


@mark.parametrize("grace", [0.0, 0.5])
def test_the_grace_period_is_a_timer_unless_it_is_zero(grace: float) -> None:
    harness = Harness(grace=grace)
    task = harness.new()
    harness.attempting(task)

    effects = harness.feed(CloseRequested())

    timers = [timer for timer in of(effects, StartTimer) if timer.key == ("grace",)]
    if grace:
        assert [timer.delay for timer in timers] == [grace]
        assert isinstance(harness.coordinator.lifecycle, Open) is False
        assert harness.terminations[task.identifier].reason is None

    else:
        assert timers == []
        assert harness.terminations[task.identifier].reason == "close"


def test_a_failing_attempt_during_the_grace_is_recorded_and_its_run_ends_after_it() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.attempting(task)
    harness.feed(CloseRequested())

    harness.ended(task, AttemptRaised(FAILURE))
    effects = harness.recorded(task, StoredRetryPending())

    assert harness.phase(task) is None
    assert of(effects, StartTimer) == []
    assert len(logs(effects, "warning")) == 1


def test_the_grace_elapsing_cuts_off_attempts_and_reports_the_close_only_when_it_decides() -> None:
    harness = Harness()
    plain, limited, discarded = harness.new(), harness.new(time_limit=0.5), harness.new()
    harness.attempting(plain)
    harness.attempting(limited)
    harness.feed(TimeLimitElapsed(limited.identifier, harness.serial(limited), 1))
    harness.discarding(discarded)
    harness.feed(CloseRequested())

    effects = harness.feed(GraceElapsed())

    assert isinstance(harness.coordinator.lifecycle, CutOff)
    assert harness.terminations[plain.identifier].reason == "close"
    assert harness.terminations[limited.identifier].reason == "time_limit"
    assert harness.stubs[limited.identifier].cancellations == 2  # delivered again
    assert harness.stubs[discarded.identifier].cancellations == 2
    errors = logs(effects, "error")
    assert len(errors) == 1 and str(plain.identifier) in errors[0]


def test_the_grace_elapsing_abandons_the_records_and_jobs_waiting() -> None:
    harness = Harness()
    task, other = harness.new(), harness.new()
    harness.recording(task)
    harness.recorded(task, StoreFailure(OSError()))
    harness.interrupt(other)
    harness.feed(InterruptionRecorded(other.identifier, 1, StoreFailure(OSError())))
    harness.feed(CloseRequested())

    effects = harness.feed(GraceElapsed())

    assert harness.phase(task) is None
    assert harness.coordinator.interruptions == frozenset()
    assert len(logs(effects, "error")) == 2


def test_an_attempt_cut_off_is_released() -> None:
    harness = Harness(grace=0.0)
    task = harness.new()
    harness.attempting(task)
    harness.feed(CloseRequested())

    effects = harness.ended(task, AttemptTerminated("close", None))

    assert isinstance(harness.phase(task), Releasing)
    assert len(of(effects, Release)) == 1


def test_a_task_not_run_by_this_runtime_is_left_alone_by_a_close() -> None:
    harness = Harness()

    effects = harness.feed(CloseRequested())

    assert of(effects, Release) == [] and of(effects, Record) == []


def test_the_close_ends_once_everything_is_done() -> None:
    harness = Harness(grace=0.0)
    task = harness.new()
    waiting = harness.await_outcome(task)
    read = of(harness.history, Read)[0]
    harness.attempting(task)
    harness.settle_claims()
    running = {task.identifier: stored(task, StoredRunning(cancel_requested=False))}
    harness.feed(ReadReplied(read.identifiers, read.parks, read.epoch, running))
    harness.feed(CloseRequested())
    assert not isinstance(harness.coordinator.lifecycle, Closed)

    harness.ended(task, AttemptTerminated("close", None))
    harness.feed(ReleaseReplied(task.identifier, harness.serial(task), 1, None))
    assert not isinstance(harness.coordinator.lifecycle, Closed)  # the host is still live
    effects = harness.feed(ScopeExited(task.identifier, harness.serial(task), None))

    assert isinstance(harness.coordinator.lifecycle, Closed)
    failed = of(effects, Fail)
    assert [effect.reply for effect in failed] == [waiting]
    assert isinstance(failed[0].exception, RuntimeError)
    assert harness.coordinator.pending == 0 and harness.coordinator.hosts == 0
    for kind in ("poll", "renew", "read", "retry", "grace", "changes"):
        assert StopTimers(kind) in effects


def test_a_wait_for_changes_cancelled_by_the_close_counts_as_ended() -> None:
    harness = Harness(timings=None, grace=0.0)
    harness.feed(ClaimReplied(()))

    effects = harness.feed(CloseRequested())
    assert len(of(effects, StopChanges)) == 1
    assert not isinstance(harness.coordinator.lifecycle, Closed)

    harness.feed(ChangedReplied(ChangesCancelled()))

    assert isinstance(harness.coordinator.lifecycle, Closed)


def test_what_is_posted_after_the_close_ended_is_refused_or_dropped() -> None:
    harness = Harness(grace=0.0)
    harness.feed(ClaimReplied(()))
    harness.feed(CloseRequested())
    assert isinstance(harness.coordinator.lifecycle, Closed)
    task = harness.new()
    spawned, called, asked, cancelled = Reply(), Reply(), Reply(), Reply()

    async def call() -> object:
        return 1

    effects = [
        *harness.feed(SpawnAsked(task, spawned, copy_context())),
        *harness.feed(StoreCallAsked(call, called, copy_context())),
        *harness.feed(OutcomeAsked(task.identifier, asked)),
        *harness.feed(CancelAsked(task.identifier, cancelled, copy_context())),
        *harness.feed(PollTick()),
        *harness.feed(OutcomeAbandoned(task.identifier, asked)),
    ]

    assert [effect.reply for effect in of(effects, Fail)] == [spawned, called, asked, cancelled]
    assert all(isinstance(effect.exception, RuntimeError) for effect in of(effects, Fail))
    assert len(effects) == 4


def test_an_outcome_fixed_by_the_close_is_still_returned_after_it() -> None:
    harness = Harness(grace=0.0)
    task = harness.new()
    harness.attempting(task)
    harness.settle_claims()
    harness.feed(CloseRequested())
    harness.ended(task, AttemptTerminated("close", None))
    harness.feed(ReleaseReplied(task.identifier, harness.serial(task), 1, CANCELLED))
    harness.feed(ScopeExited(task.identifier, harness.serial(task), None))
    assert isinstance(harness.coordinator.lifecycle, Closed)
    asked, cancelled = Reply(), Reply()

    effects = [
        *harness.feed(OutcomeAsked(task.identifier, asked)),
        *harness.feed(CancelAsked(task.identifier, cancelled, copy_context())),
    ]

    assert {effect.reply: effect.value for effect in of(effects, Resolve)} == {
        asked: TaskCancelled(),
        cancelled: "finished",
    }


def test_an_abandoned_awaiter_is_not_resolved() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)
    abandoned, kept = harness.await_outcome(task), harness.await_outcome(task)
    harness.feed(OutcomeAbandoned(task.identifier, abandoned))

    harness.ended(task, AttemptReturned("null"))
    effects = harness.recorded(task, COMPLETED)

    assert [effect.reply for effect in of(effects, Resolve)] == [kept]


def test_a_claim_reply_which_failed_is_logged_and_claims_nothing() -> None:
    harness = Harness()

    effects = harness.feed(ClaimReplied(StoreFailure(OSError())))

    assert len(logs(effects, "error")) == 1
    assert of(effects, Claim) == []
    # a store with ticks retries at the next one
    assert of(effects, StartTimer) == []


@mark.parametrize("failing", ["claim", "unsupported"])
def test_a_failed_round_of_a_local_store_is_retried_by_a_timer(failing: str) -> None:
    harness = Harness(timings=None, unsupported="fail")
    reply: Event = (
        ClaimReplied(StoreFailure(OSError()))
        if failing == "claim"
        else UnsupportedReplied(StoreFailure(OSError()))
    )
    # the other call of the round is answered first
    harness.feed(UnsupportedReplied({}) if failing == "claim" else ClaimReplied(()))

    effects = harness.feed(reply)

    timers = [timer for timer in of(effects, StartTimer) if timer.key == ("round",)]
    assert len(timers) == 1
    assert len(of(harness.feed(timers[0].event), Claim)) == 1


def test_a_failed_round_of_a_local_store_is_not_retried_while_closing() -> None:
    harness = Harness(timings=None, unsupported="fail", grace=0.5)
    task = harness.new()
    harness.feed(UnsupportedReplied({}))
    harness.grant(task)
    harness.feed(UnsupportedReplied({}))
    harness.feed(RetryDue())
    harness.feed(CloseRequested())

    effects = harness.feed(ClaimReplied(StoreFailure(OSError())))

    assert [timer for timer in of(effects, StartTimer) if timer.key == ("round",)] == []


@mark.parametrize(
    "replied",
    [
        ClaimReplied(CallCancelled()),
        UnsupportedReplied(CallCancelled()),
    ],
)
def test_a_cancelled_call_balances_the_call_without_a_failure_or_a_retry(
    replied: ClaimReplied | UnsupportedReplied,
) -> None:
    harness = Harness(timings=None, unsupported="fail")
    pending = harness.coordinator.pending

    effects = harness.feed(replied)

    assert harness.coordinator.pending == pending - 1
    assert logs(effects, "error") == []
    assert [timer for timer in of(effects, StartTimer) if timer.key == ("round",)] == []


def test_every_call_cancelled_lets_a_close_end() -> None:
    harness = Harness(timings=None, unsupported="fail", grace=0.0)
    harness.feed(CloseRequested())
    assert not isinstance(harness.coordinator.lifecycle, Closed)

    harness.feed(ClaimReplied(CallCancelled()))
    harness.feed(UnsupportedReplied(CallCancelled()))
    harness.feed(ChangedReplied(ChangesCancelled()))

    assert isinstance(harness.coordinator.lifecycle, Closed)


def test_a_cancelled_call_of_a_caller_fails_what_waits_for_it() -> None:
    harness = Harness()
    reply = Reply()

    async def call() -> object:
        return None

    harness.feed(StoreCallAsked(call, reply, copy_context()))

    effects = harness.feed(StoreCallReplied(reply, CallCancelled()))

    failed = of(effects, Fail)
    assert [effect.reply for effect in failed] == [reply]
    assert [type(effect.exception) for effect in failed] == [RuntimeError]
    assert logs(effects, "error") == []


def test_a_cancelled_record_is_kept_for_the_next_renewal_without_a_failure_log() -> None:
    harness = Harness()
    task = harness.new()
    harness.recording(task)

    effects = harness.recorded(task, CallCancelled())

    phase = harness.phase(task)
    assert isinstance(phase, Recording) and phase.failed
    assert isinstance(phase.call, Waiting)
    assert logs(effects, "error") == []
    assert len(of(harness.feed(RenewalTick()), Record)) == 1


def test_a_cancelled_record_at_the_cut_off_logs_only_the_abandonment() -> None:
    harness = Harness(grace=0.0)
    task = harness.new()
    harness.recording(task)
    harness.feed(CloseRequested())

    effects = harness.recorded(task, CallCancelled())

    errors = logs(effects, "error")
    assert len(errors) == 1 and "abandoned" in errors[0]
    assert harness.phase(task) is None


def test_a_cancelled_release_leaves_the_lease_to_expire_without_a_failure_log() -> None:
    harness = Harness()
    task = harness.new()
    harness.releasing(task)

    effects = harness.feed(
        ReleaseReplied(task.identifier, harness.serial(task), 1, CallCancelled())
    )

    assert logs(effects, "error") == []
    assert harness.phase(task) is None
    assert harness.coordinator.released(task.identifier) is None


def test_a_cancelled_record_of_an_interruption_is_retried_without_a_failure_log() -> None:
    harness = Harness()
    task = harness.new()
    harness.interrupt(task)

    effects = harness.feed(InterruptionRecorded(task.identifier, 1, CallCancelled()))

    assert logs(effects, "error") == []
    assert harness.coordinator.interruptions == {(task.identifier, 1)}
    assert len(of(harness.feed(RenewalTick()), RecordInterruption)) == 1


def test_a_cancelled_renewal_is_not_logged_and_the_next_one_is_counted_from_its_return() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)
    renew = of(harness.feed(RenewalTick()), Renew)[0]

    effects = harness.feed(RenewReplied(renew.asked, renew.issued, CallCancelled()))

    assert logs(effects, "error") == []
    assert [(timer.key, timer.delay) for timer in of(effects, StartTimer)] == [(("renew",), 0.3)]


def test_a_cancelled_read_is_not_logged_and_asked_again_at_the_next_tick() -> None:
    harness = Harness()
    task = harness.new()
    harness.await_outcome(task)
    read = of(harness.history, Read)[0]

    effects = harness.feed(ReadReplied(read.identifiers, read.parks, read.epoch, CallCancelled()))

    assert logs(effects, "error") == []
    harness.feed(PollDue())
    assert len(of(harness.feed(PollTick()), Read)) == 1


def test_a_cancelled_insert_and_a_cancelled_cancel_fail_what_waits_for_them() -> None:
    harness = Harness()
    task = harness.new()
    harness.settle_claims()
    spawn, cancel = Reply(), Reply()
    harness.feed(SpawnAsked(task, spawn, copy_context()))
    harness.feed(CancelAsked(task.identifier, cancel, copy_context()))

    inserted = harness.feed(InsertReplied(spawn, CallCancelled()))
    cancelled = harness.feed(CancelReplied(task.identifier, cancel, CallCancelled()))

    assert [effect.reply for effect in of(inserted, Fail)] == [spawn]
    assert [effect.reply for effect in of(cancelled, Fail)] == [cancel]
    assert of(inserted, Claim) == []


def test_a_host_which_was_cancelled_is_not_logged_as_a_failure() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    effects = harness.feed(ScopeExited(task.identifier, harness.serial(task), CancelledError()))

    assert logs(effects, "error") == []


def test_a_limit_is_the_number_of_free_slots_of_the_next_claim() -> None:
    harness = Harness(limit=2)
    first, second = harness.new(), harness.new()
    harness.attempting(first)
    assert harness.claims == 0

    assert [effect.limit for effect in of(harness.feed(RetryDue()), Claim)] == [1]
    harness.feed(ClaimReplied((ClaimedAttempt(task=second, attempt=1),)))
    harness.feed(ScopeEntered(second.identifier, harness.serial(second), copy_context()))

    assert [effect.limit for effect in of(harness.feed(RetryDue()), Claim)] == [0]


def test_a_cancelled_host_gives_its_claim_back_as_any_host_which_exited() -> None:
    harness = Harness()
    task = harness.new()
    harness.attempting(task)

    effects = harness.feed(ScopeExited(task.identifier, harness.serial(task), CancelledError()))

    assert harness.phase(task) is None
    assert [release.attempt for release in of(effects, Release)] == [1]


# --- the attempt which follows a discarded one ----------------------------------------------


def test_the_successor_of_a_discarded_attempt_is_renewed_and_its_lease_is_own() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.discarding(task)

    renews = of(harness.feed(RenewalTick()), Renew)
    assert renews[0].asked == {task.identifier: 2}

    effects = harness.interrupt(task, 2)

    assert of(effects, RecordInterruption) == []
    assert harness.coordinator.interruptions == frozenset()


def test_a_successor_whose_lease_is_gone_is_dropped_and_the_run_ends_with_the_discard() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,))
    harness.discarding(task)
    renew = of(harness.feed(RenewalTick()), Renew)[0]

    effects = harness.feed(RenewReplied(renew.asked, renew.issued, LeaseRenewal(leases={})))

    phase = harness.phase(task)
    assert isinstance(phase, Discarding) and phase.successor is None
    assert len(logs(effects, "warning")) == 1
    assert of(effects, Release) == []
    ended = harness.ended(task, AttemptReturned("null"))
    assert harness.phase(task) is None and of(ended, Record) == []


def test_the_time_limit_of_a_successor_counts_from_its_claim_and_is_kept_for_its_start() -> None:
    harness = Harness()
    task = harness.new(delays=(0.1,), time_limit=0.5)
    harness.attempting(task)

    effects = harness.grant(task, 2)
    assert [(timer.key, timer.delay) for timer in of(effects, StartTimer)] == [
        (("limit", task.identifier), 0.5)
    ]

    harness.feed(TimeLimitElapsed(task.identifier, harness.serial(task), 2))
    ended = harness.ended(task, AttemptReturned("null"))

    assert of(ended, RunAttempt) == []
    assert isinstance(of(ended, Record)[0].end, AttemptRetried | AttemptFailed)
    assert of(ended, Record)[0].attempt == 2


def test_the_retry_timer_of_a_park_stops_when_the_park_ends_or_is_granted() -> None:
    harness = Harness()
    ended, granted = harness.new(delays=(0.1,)), harness.new(delays=(0.1,))
    harness.parked(ended)
    harness.parked(granted)

    closing = harness.feed(CloseRequested())
    assert StopTimer(("retry", ended.identifier)) in closing

    other = Harness()
    task = other.new(delays=(0.1,))
    other.parked(task)
    assert StopTimer(("retry", task.identifier)) in other.grant(task, 2)


def test_events_and_effects_of_different_types_are_never_equal() -> None:
    assert ExitHost(1) != Claim(1)
    assert PollTick() != GraceElapsed()
    assert PollTick() == PollTick()
    assert Claim(1) == Claim(1)


# --- unsupported tasks (FR-92, FR-93) ------------------------------------------------------


def settled(harness: Harness) -> Harness:
    # the round of the opening answered
    harness.feed(ClaimReplied(()))
    harness.feed(UnsupportedReplied({}))
    return harness


def test_a_runtime_failing_unsupported_tasks_runs_a_claim_and_a_failing_in_its_first_round() -> (
    None
):
    harness = Harness(unsupported="fail")

    assert len(of(harness.history, Claim)) == 1
    assert [effect.names for effect in of(harness.history, FailUnsupported)] == [{"t"}]
    assert harness.coordinator.pending == 2


def test_a_runtime_leaving_unsupported_tasks_never_fails_them() -> None:
    harness = Harness(unsupported="leave")
    harness.feed(ClaimReplied(()))

    harness.feed(RetryDue())
    harness.feed(PollTick())
    harness.feed(ChangedReplied(3))

    assert len(of(harness.history, Claim)) >= 2
    assert of(harness.history, FailUnsupported) == []


def test_a_round_without_a_free_slot_still_fails_unsupported_tasks() -> None:
    harness = Harness(limit=1, unsupported="fail")
    task = harness.new()
    harness.feed(UnsupportedReplied({}))
    harness.grant(task)
    harness.feed(UnsupportedReplied({}))

    effects = harness.feed(RetryDue())

    assert [effect.limit for effect in of(effects, Claim)] == [0]
    assert len(of(effects, FailUnsupported)) == 1


def test_a_runtime_listing_no_task_runs_rounds_with_no_claim() -> None:
    harness = Harness(unsupported="fail", results={})

    assert of(harness.history, Claim) == []
    assert [effect.names for effect in of(harness.history, FailUnsupported)] == [set()]
    harness.feed(UnsupportedReplied({}))
    assert len(of(harness.feed(PollTick()), FailUnsupported)) == 1


def test_a_runtime_leaving_unsupported_tasks_and_listing_none_runs_no_round() -> None:
    harness = Harness(unsupported="leave", results={})

    assert of(harness.history, Claim) == []
    assert of(harness.history, FailUnsupported) == []
    assert of(harness.feed(PollTick()), FailUnsupported) == []


def test_rounds_do_not_overlap() -> None:
    harness = Harness(unsupported="fail")

    assert of(harness.feed(RetryDue()), FailUnsupported) == []
    harness.feed(ClaimReplied(()))
    # the claim is back, the failing is not: the round is not over
    assert of(harness.feed(RetryDue()), Claim) == []
    effects = harness.feed(UnsupportedReplied({}))

    assert len(of(effects, Claim)) == 1
    assert len(of(effects, FailUnsupported)) == 1


def test_a_round_is_not_started_while_closing() -> None:
    harness = Harness(unsupported="fail")
    task = harness.new()
    harness.feed(UnsupportedReplied({}))
    harness.grant(task)
    harness.feed(UnsupportedReplied({}))
    history = len(harness.history)

    harness.feed(CloseRequested())
    harness.feed(PollTick())
    harness.feed(RetryDue())
    harness.feed(UnsupportedReplied({}))

    assert not isinstance(harness.coordinator.lifecycle, Open)
    assert of(harness.history[history:], FailUnsupported) == []


def test_a_call_failing_unsupported_tasks_in_flight_holds_the_close() -> None:
    harness = Harness(unsupported="fail")

    harness.feed(CloseRequested())
    harness.feed(ClaimReplied(()))
    assert not isinstance(harness.coordinator.lifecycle, Closed)

    effects = harness.feed(UnsupportedReplied({}))

    assert isinstance(harness.coordinator.lifecycle, Closed)
    assert of(effects, FailUnsupported) == []


def test_each_task_failed_is_logged_in_the_scope_of_the_runtime() -> None:
    harness = Harness(unsupported="fail")
    first, second = UUID(int=100), UUID(int=101)
    harness.feed(ClaimReplied(()))

    effects = harness.feed(UnsupportedReplied({first: "x", second: "y"}))

    errors = [effect for effect in of(effects, Log) if effect.level == "error"]
    assert [effect.context for effect in errors] == [None, None]
    assert logs(effects, "error") == [
        f"Managed task failed: {first}, named x, failure unsupported",
        f"Managed task failed: {second}, named y, failure unsupported",
    ]
    assert harness.coordinator.runs == frozenset()


def test_a_failing_of_unsupported_tasks_which_raised_is_logged_and_retried_at_the_next_tick() -> (
    None
):
    harness = Harness(unsupported="fail")
    harness.feed(ClaimReplied(()))

    effects = harness.feed(UnsupportedReplied(StoreFailure(OSError("secret"))))

    assert logs(effects, "error") == ["Managed tasks claim round failed: builtins.OSError"]
    assert of(effects, FailUnsupported) == []
    assert len(of(harness.feed(PollTick()), FailUnsupported)) == 1
