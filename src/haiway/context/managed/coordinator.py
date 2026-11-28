from asyncio import CancelledError
from collections.abc import Callable, Coroutine, Mapping, Sequence, Set
from contextvars import Context
from typing import Literal, Protocol, final
from uuid import UUID

from haiway.attributes import State
from haiway.context.managed.attempt import (
    AttemptCancelledLocally,
    AttemptRaised,
    AttemptResult,
    AttemptReturned,
    AttemptTerminated,
    AttemptTermination,
    is_interpreter_exit,
)
from haiway.context.managed.outcomes import exception_type_name, stored_outcome, task_outcome
from haiway.context.managed.store import LocalCoordination, StoreCoordination
from haiway.context.managed.stored import (
    AttemptCancelled,
    AttemptCompleted,
    AttemptEnd,
    AttemptFailed,
    AttemptRetried,
    ClaimedAttempt,
    ClaimedInterrupted,
    ClaimedTask,
    LeasedCoordination,
    LeaseRenewal,
    LeaseState,
    LeaseTimings,
    NewTask,
    StoredCancelled,
    StoredCompleted,
    StoredFailed,
    StoredFinished,
    StoredQueued,
    StoredRetryPending,
    StoredRunning,
    StoredStatus,
    StoredTask,
)
from haiway.context.managed.types import (
    AttemptFailure,
    TaskCancellation,
    TaskCancelled,
    TaskFailed,
    TaskFailureRecord,
    TaskInterrupted,
    TaskOutcome,
    TaskTimeLimitExceeded,
)

__all__ = (
    "AttemptEnded",
    "Attempting",
    "CallCancelled",
    "CallState",
    "CancelAsked",
    "CancelReplied",
    "CancelTask",
    "ChangedReplied",
    "ChangesCancelled",
    "ChangesRetry",
    "Claim",
    "ClaimReplied",
    "CloseRequested",
    "Closed",
    "Coordinator",
    "CutOff",
    "Discarding",
    "Effect",
    "Entering",
    "Event",
    "ExitHost",
    "Fail",
    "FailUnsupported",
    "Grace",
    "GraceElapsed",
    "Held",
    "HostGone",
    "HostLive",
    "InFlight",
    "Insert",
    "InsertReplied",
    "Interruption",
    "InterruptionRecorded",
    "Lifecycle",
    "Log",
    "Open",
    "Opened",
    "OutcomeAbandoned",
    "OutcomeAsked",
    "Parked",
    "Phase",
    "PollDue",
    "PollTick",
    "Read",
    "ReadReplied",
    "Record",
    "RecordInterruption",
    "RecordReplied",
    "Recording",
    "Release",
    "ReleaseReplied",
    "Releasing",
    "Renew",
    "RenewReplied",
    "RenewalTick",
    "Reply",
    "Resolve",
    "RetryDue",
    "Run",
    "RunAttempt",
    "ScopeEntered",
    "ScopeExited",
    "ScopeFailed",
    "SpawnAsked",
    "StartChanges",
    "StartHost",
    "StartTimer",
    "StopChanges",
    "StopTimer",
    "StopTimers",
    "StoreCall",
    "StoreCallAsked",
    "StoreCallEffect",
    "StoreCallReplied",
    "StoreFailure",
    "StoreReply",
    "TimeLimitElapsed",
    "TimerKey",
    "UnsupportedReplied",
    "Waiting",
)


# --- how the coordinator talks to the world ---------------------------------------------


class Reply(Protocol):
    """Where the coordinator hands what a caller waits for, resolved by the driver."""

    def resolve(
        self,
        value: object,
        /,
    ) -> None: ...

    def fail(
        self,
        exception: Exception,
        /,
    ) -> None: ...


@final
class StoreFailure:
    # what a call of the store raised, delivered as the reply of that call
    __slots__ = ("exception",)

    def __init__(
        self,
        exception: Exception,
        /,
    ) -> None:
        self.exception: Exception = exception


type StoreCall = Callable[[], Coroutine[None, None, object]]

type TimerKey = tuple[str] | tuple[str, UUID]


@final
class ChangesCancelled:
    # the wait for changes of a store ended by a cancellation
    __slots__ = ()


@final
class CallCancelled:
    # a call of the store which ended cancelled, by whatever cancelled it: the group of the
    # session ending, the shutdown of the event loop. It has no result, balances the call and
    # fails what waits for it, but it is not a failure of the store
    __slots__ = ()


# --- lifecycle ---------------------------------------------------------------------------


@final
class Open:
    __slots__ = ()


@final
class Grace:
    # closing, running attempts are given the grace period to end
    __slots__ = ()


@final
class CutOff:
    # closing, the grace period elapsed
    __slots__ = ()


@final
class Closed:
    __slots__ = ()


type Lifecycle = Open | Grace | CutOff | Closed

_OPEN: Open = Open()
_GRACE: Grace = Grace()
_CUT_OFF: CutOff = CutOff()
_CLOSED: Closed = Closed()


# --- runs --------------------------------------------------------------------------------


@final
class Held:
    """A claimed attempt this runtime holds the lease of, and when it was granted."""

    __slots__ = (
        "claimed",
        "granted",
    )

    def __init__(
        self,
        claimed: ClaimedAttempt,
        granted: int,
        /,
    ) -> None:
        self.claimed: ClaimedAttempt = claimed
        # the step of the coordinator which granted, or granted again, the lease
        self.granted: int = granted

    @property
    def attempt(self) -> int:
        return self.claimed.attempt


@final
class InFlight:
    # a call of the store which was started and did not return
    __slots__ = ()


@final
class Waiting:
    # a call of the store which failed, started again at the next renewal tick
    __slots__ = ()


type CallState = InFlight | Waiting

_IN_FLIGHT: InFlight = InFlight()
_WAITING: Waiting = Waiting()


@final
class Entering:
    # the host is entering the task scope, no attempt runs
    __slots__ = (
        "held",
        "termination",
    )

    def __init__(
        self,
        held: Held,
        termination: AttemptTermination,
        /,
    ) -> None:
        self.held: Held = held
        self.termination: AttemptTermination = termination


@final
class Attempting:
    __slots__ = (
        "held",
        "termination",
    )

    def __init__(
        self,
        held: Held,
        termination: AttemptTermination,
        /,
    ) -> None:
        self.held: Held = held
        self.termination: AttemptTermination = termination


@final
class Successor:
    # the attempt the store handed this runtime while another is being discarded: its lease is
    # held and renewed, its termination collects a request made before it starts
    __slots__ = (
        "held",
        "termination",
    )

    def __init__(
        self,
        held: Held,
        termination: AttemptTermination,
        /,
    ) -> None:
        self.held: Held = held
        self.termination: AttemptTermination = termination

    @property
    def attempt(self) -> int:
        return self.held.attempt


@final
class Discarding:
    # the lease of the attempt was lost: its function is cancelled, whatever it ends with
    # is discarded. The successor is the attempt the store handed this runtime meanwhile.
    __slots__ = (
        "attempt",
        "successor",
        "termination",
    )

    def __init__(
        self,
        attempt: int,
        termination: AttemptTermination,
        successor: Successor | None,
        /,
    ) -> None:
        self.attempt: int = attempt
        self.termination: AttemptTermination = termination
        self.successor: Successor | None = successor


@final
class Recording:
    __slots__ = (
        "call",
        "end",
        "failed",
        "held",
    )

    def __init__(
        self,
        held: Held,
        end: AttemptEnd,
        /,
    ) -> None:
        self.held: Held = held
        self.end: AttemptEnd = end
        self.call: CallState = _IN_FLIGHT
        # a record of this attempt failed, awaiters read the store from then on
        self.failed: bool = False


@final
class Releasing:
    __slots__ = ("held",)

    def __init__(
        self,
        held: Held,
        /,
    ) -> None:
        self.held: Held = held


@final
class Parked:
    # the recorded attempt is to be retried, the task scope stays open and holds no slot
    __slots__ = ("attempts",)

    def __init__(
        self,
        attempts: int,
        /,
    ) -> None:
        self.attempts: int = attempts


type Phase = Entering | Attempting | Discarding | Recording | Releasing | Parked


@final
class HostLive:
    """The host task serves the task scope, its context known once the scope was entered."""

    __slots__ = ("context",)

    def __init__(
        self,
        context: Context | None,
        /,
    ) -> None:
        self.context: Context | None = context


@final
class HostGone:
    __slots__ = ()


_HOST_GONE: HostGone = HostGone()


@final
class Run:
    """What the coordinator holds for a task it serves a task scope for."""

    __slots__ = (
        "host",
        "phase",
        "serial",
        "task",
    )

    def __init__(
        self,
        task: NewTask,
        serial: int,
        host: HostLive | HostGone,
        phase: Phase,
        /,
    ) -> None:
        self.task: NewTask = task
        # tells this run from an earlier or later one of the same task
        self.serial: int = serial
        self.host: HostLive | HostGone = host
        self.phase: Phase = phase


@final
class Interruption:
    # an interrupted attempt of another runtime, recorded by this one: it has no slot,
    # no scope and no host, and its lease is renewed and its record retried like any
    __slots__ = (
        "call",
        "claimed",
        "end",
    )

    def __init__(
        self,
        claimed: ClaimedInterrupted,
        end: AttemptEnd,
        /,
    ) -> None:
        self.claimed: ClaimedInterrupted = claimed
        self.end: AttemptEnd = end
        self.call: CallState = _IN_FLIGHT


# --- events ------------------------------------------------------------------------------


@final
class Opened:
    __slots__ = ()


@final
class CloseRequested:
    __slots__ = ()


@final
class SpawnAsked:
    __slots__ = (
        "context",
        "reply",
        "task",
    )

    def __init__(
        self,
        task: NewTask,
        reply: Reply,
        context: Context,
        /,
    ) -> None:
        self.task: NewTask = task
        self.reply: Reply = reply
        self.context: Context = context


@final
class CancelAsked:
    __slots__ = (
        "context",
        "identifier",
        "reply",
    )

    def __init__(
        self,
        identifier: UUID,
        reply: Reply,
        context: Context,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.reply: Reply = reply
        self.context: Context = context


@final
class OutcomeAsked:
    __slots__ = (
        "identifier",
        "reply",
    )

    def __init__(
        self,
        identifier: UUID,
        reply: Reply,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.reply: Reply = reply


@final
class OutcomeAbandoned:
    __slots__ = (
        "identifier",
        "reply",
    )

    def __init__(
        self,
        identifier: UUID,
        reply: Reply,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.reply: Reply = reply


@final
class StoreCallAsked:
    __slots__ = (
        "call",
        "context",
        "reply",
    )

    def __init__(
        self,
        call: StoreCall,
        reply: Reply,
        context: Context,
        /,
    ) -> None:
        self.call: StoreCall = call
        self.reply: Reply = reply
        self.context: Context = context


@final
class ClaimReplied:
    __slots__ = ("result",)

    def __init__(
        self,
        result: Sequence[ClaimedTask] | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self.result: Sequence[ClaimedTask] | StoreFailure | CallCancelled = result


@final
class UnsupportedReplied:
    __slots__ = ("result",)

    def __init__(
        self,
        result: Mapping[UUID, str] | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self.result: Mapping[UUID, str] | StoreFailure | CallCancelled = result


@final
class RenewReplied:
    __slots__ = (
        "asked",
        "issued",
        "result",
    )

    def __init__(
        self,
        asked: Mapping[UUID, int],
        issued: int,
        result: LeaseRenewal | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self.asked: Mapping[UUID, int] = asked
        self.issued: int = issued
        self.result: LeaseRenewal | StoreFailure | CallCancelled = result


@final
class ReadReplied:
    __slots__ = (
        "asked",
        "epoch",
        "parks",
        "result",
    )

    def __init__(
        self,
        asked: Set[UUID],
        parks: Mapping[UUID, tuple[int, int]],
        epoch: int | None,
        result: Mapping[UUID, StoredTask] | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self.asked: Set[UUID] = asked
        self.parks: Mapping[UUID, tuple[int, int]] = parks
        self.epoch: int | None = epoch
        self.result: Mapping[UUID, StoredTask] | StoreFailure | CallCancelled = result


@final
class RecordReplied:
    __slots__ = (
        "attempt",
        "identifier",
        "result",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int,
        attempt: int,
        result: StoredStatus | StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.serial: int = serial
        self.attempt: int = attempt
        self.result: StoredStatus | StoreFailure | CallCancelled | None = result


@final
class InterruptionRecorded:
    __slots__ = (
        "attempt",
        "identifier",
        "result",
    )

    def __init__(
        self,
        identifier: UUID,
        attempt: int,
        result: StoredStatus | StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.attempt: int = attempt
        self.result: StoredStatus | StoreFailure | CallCancelled | None = result


@final
class ReleaseReplied:
    __slots__ = (
        "attempt",
        "identifier",
        "result",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int | None,
        attempt: int,
        result: StoredStatus | StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.serial: int | None = serial
        self.attempt: int = attempt
        self.result: StoredStatus | StoreFailure | CallCancelled | None = result


@final
class InsertReplied:
    __slots__ = (
        "reply",
        "result",
    )

    def __init__(
        self,
        reply: Reply,
        result: StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        self.reply: Reply = reply
        self.result: StoreFailure | CallCancelled | None = result


@final
class CancelReplied:
    __slots__ = (
        "identifier",
        "reply",
        "result",
    )

    def __init__(
        self,
        identifier: UUID,
        reply: Reply,
        result: TaskCancellation | StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.reply: Reply = reply
        self.result: TaskCancellation | StoreFailure | CallCancelled | None = result


@final
class StoreCallReplied:
    __slots__ = (
        "reply",
        "result",
    )

    def __init__(
        self,
        reply: Reply,
        result: object | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self.reply: Reply = reply
        self.result: object | StoreFailure | CallCancelled = result


type StoreReply = (
    ClaimReplied
    | UnsupportedReplied
    | RenewReplied
    | ReadReplied
    | RecordReplied
    | InterruptionRecorded
    | ReleaseReplied
    | InsertReplied
    | CancelReplied
    | StoreCallReplied
)


@final
class ChangedReplied:
    __slots__ = ("result",)

    def __init__(
        self,
        result: int | StoreFailure | ChangesCancelled,
        /,
    ) -> None:
        self.result: int | StoreFailure | ChangesCancelled = result


@final
class ScopeEntered:
    __slots__ = (
        "context",
        "identifier",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int,
        context: Context,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.serial: int = serial
        self.context: Context = context


@final
class ScopeFailed:
    __slots__ = (
        "exception",
        "identifier",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int,
        exception: BaseException,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.serial: int = serial
        self.exception: BaseException = exception


@final
class AttemptEnded:
    __slots__ = (
        "attempt",
        "identifier",
        "result",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int,
        attempt: int,
        result: AttemptResult,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.serial: int = serial
        self.attempt: int = attempt
        self.result: AttemptResult = result


@final
class ScopeExited:
    __slots__ = (
        "exception",
        "identifier",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int,
        exception: BaseException | None,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.serial: int = serial
        self.exception: BaseException | None = exception


@final
class PollTick:
    __slots__ = ()

    def __eq__(
        self,
        other: object,
        /,
    ) -> bool:
        return isinstance(other, PollTick)

    def __hash__(self) -> int:
        return hash(PollTick)


@final
class RenewalTick:
    __slots__ = ()


@final
class RetryDue:
    __slots__ = ()


@final
class PollDue:
    __slots__ = ()


@final
class ChangesRetry:
    __slots__ = ()


@final
class GraceElapsed:
    __slots__ = ()

    def __eq__(
        self,
        other: object,
        /,
    ) -> bool:
        return isinstance(other, GraceElapsed)

    def __hash__(self) -> int:
        return hash(GraceElapsed)


@final
class TimeLimitElapsed:
    __slots__ = (
        "attempt",
        "identifier",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int,
        attempt: int,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.serial: int = serial
        self.attempt: int = attempt


type Event = (
    Opened
    | CloseRequested
    | SpawnAsked
    | CancelAsked
    | OutcomeAsked
    | OutcomeAbandoned
    | StoreCallAsked
    | ClaimReplied
    | UnsupportedReplied
    | RenewReplied
    | ReadReplied
    | RecordReplied
    | InterruptionRecorded
    | ReleaseReplied
    | InsertReplied
    | CancelReplied
    | StoreCallReplied
    | ChangedReplied
    | ScopeEntered
    | ScopeFailed
    | AttemptEnded
    | ScopeExited
    | PollTick
    | RenewalTick
    | RetryDue
    | PollDue
    | ChangesRetry
    | GraceElapsed
    | TimeLimitElapsed
)


# --- effects -----------------------------------------------------------------------------


@final
class Claim:
    __slots__ = ("limit",)

    def __init__(
        self,
        limit: int | None,
        /,
    ) -> None:
        self.limit: int | None = limit

    def __eq__(
        self,
        other: object,
        /,
    ) -> bool:
        return isinstance(other, Claim) and self.limit == other.limit

    def __hash__(self) -> int:
        return hash((self.limit,))


@final
class FailUnsupported:
    __slots__ = ("names",)

    def __init__(
        self,
        names: Set[str],
        /,
    ) -> None:
        # the registered names the runtime lists, the tasks of any other are failed
        self.names: Set[str] = names


@final
class Renew:
    __slots__ = (
        "asked",
        "issued",
    )

    def __init__(
        self,
        asked: Mapping[UUID, int],
        issued: int,
        /,
    ) -> None:
        self.asked: Mapping[UUID, int] = asked
        self.issued: int = issued


@final
class Read:
    __slots__ = (
        "epoch",
        "identifiers",
        "parks",
    )

    def __init__(
        self,
        identifiers: Set[UUID],
        parks: Mapping[UUID, tuple[int, int]],
        epoch: int | None,
        /,
    ) -> None:
        self.identifiers: Set[UUID] = identifiers
        # the parked runs the read is asked for: their serial and attempts
        self.parks: Mapping[UUID, tuple[int, int]] = parks
        # the claim epoch the read was issued at, none when a claim was in flight
        self.epoch: int | None = epoch


@final
class Record:
    __slots__ = (
        "attempt",
        "context",
        "end",
        "identifier",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int,
        attempt: int,
        end: AttemptEnd,
        context: Context | None,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.serial: int = serial
        self.attempt: int = attempt
        self.end: AttemptEnd = end
        self.context: Context | None = context


@final
class RecordInterruption:
    __slots__ = (
        "attempt",
        "end",
        "identifier",
    )

    def __init__(
        self,
        identifier: UUID,
        attempt: int,
        end: AttemptEnd,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.attempt: int = attempt
        self.end: AttemptEnd = end


@final
class Release:
    __slots__ = (
        "attempt",
        "context",
        "identifier",
        "serial",
    )

    def __init__(
        self,
        identifier: UUID,
        serial: int | None,
        attempt: int,
        context: Context | None,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        # none for a release no run waits for
        self.serial: int | None = serial
        self.attempt: int = attempt
        self.context: Context | None = context

    def __eq__(
        self,
        other: object,
        /,
    ) -> bool:
        return (
            isinstance(other, Release)
            and self.identifier == other.identifier
            and self.serial == other.serial
            and self.attempt == other.attempt
            and self.context == other.context
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.identifier,
                self.serial,
                self.attempt,
                self.context,
            )
        )


@final
class Insert:
    __slots__ = (
        "context",
        "reply",
        "task",
    )

    def __init__(
        self,
        task: NewTask,
        reply: Reply,
        context: Context,
        /,
    ) -> None:
        self.task: NewTask = task
        self.reply: Reply = reply
        self.context: Context = context


@final
class CancelTask:
    __slots__ = (
        "context",
        "identifier",
        "reply",
    )

    def __init__(
        self,
        identifier: UUID,
        reply: Reply,
        context: Context,
        /,
    ) -> None:
        self.identifier: UUID = identifier
        self.reply: Reply = reply
        self.context: Context = context


@final
class StoreCallEffect:
    __slots__ = (
        "call",
        "context",
        "reply",
    )

    def __init__(
        self,
        call: StoreCall,
        reply: Reply,
        context: Context,
        /,
    ) -> None:
        self.call: StoreCall = call
        self.reply: Reply = reply
        self.context: Context = context


@final
class StartChanges:
    __slots__ = ("seen",)

    def __init__(
        self,
        seen: int,
        /,
    ) -> None:
        self.seen: int = seen


@final
class StopChanges:
    __slots__ = ()


@final
class StartHost:
    __slots__ = (
        "serial",
        "task",
    )

    def __init__(
        self,
        task: NewTask,
        serial: int,
        /,
    ) -> None:
        self.task: NewTask = task
        self.serial: int = serial


@final
class RunAttempt:
    __slots__ = (
        "claimed",
        "serial",
        "termination",
    )

    def __init__(
        self,
        serial: int,
        claimed: ClaimedAttempt,
        termination: AttemptTermination,
        /,
    ) -> None:
        self.serial: int = serial
        self.claimed: ClaimedAttempt = claimed
        self.termination: AttemptTermination = termination


@final
class ExitHost:
    __slots__ = ("serial",)

    def __init__(
        self,
        serial: int,
        /,
    ) -> None:
        self.serial: int = serial

    def __eq__(
        self,
        other: object,
        /,
    ) -> bool:
        return isinstance(other, ExitHost) and self.serial == other.serial

    def __hash__(self) -> int:
        return hash((self.serial,))


@final
class StartTimer:
    __slots__ = (
        "delay",
        "event",
        "key",
    )

    def __init__(
        self,
        key: TimerKey,
        delay: float,
        event: Event,
        /,
    ) -> None:
        self.key: TimerKey = key
        self.delay: float = delay
        self.event: Event = event


@final
class StopTimer:
    __slots__ = ("key",)

    def __init__(
        self,
        key: TimerKey,
        /,
    ) -> None:
        self.key: TimerKey = key

    def __eq__(
        self,
        other: object,
        /,
    ) -> bool:
        return isinstance(other, StopTimer) and self.key == other.key

    def __hash__(self) -> int:
        return hash((self.key,))


@final
class StopTimers:
    __slots__ = ("kind",)

    def __init__(
        self,
        kind: str,
        /,
    ) -> None:
        self.kind: str = kind

    def __eq__(
        self,
        other: object,
        /,
    ) -> bool:
        return isinstance(other, StopTimers) and self.kind == other.kind

    def __hash__(self) -> int:
        return hash((self.kind,))


@final
class Resolve:
    __slots__ = (
        "reply",
        "value",
    )

    def __init__(
        self,
        reply: Reply,
        value: object,
        /,
    ) -> None:
        self.reply: Reply = reply
        self.value: object = value


@final
class Fail:
    __slots__ = (
        "exception",
        "reply",
    )

    def __init__(
        self,
        reply: Reply,
        exception: Exception,
        /,
    ) -> None:
        self.reply: Reply = reply
        self.exception: Exception = exception


@final
class Log:
    __slots__ = (
        "arguments",
        "context",
        "level",
        "message",
    )

    def __init__(
        self,
        level: Literal["debug", "warning", "error"],
        message: str,
        arguments: tuple[object, ...],
        context: Context | None,
        /,
    ) -> None:
        self.level: Literal["debug", "warning", "error"] = level
        self.message: str = message
        self.arguments: tuple[object, ...] = arguments
        # the scope context of the run, none for the scope of the runtime
        self.context: Context | None = context


type Effect = (
    Claim
    | FailUnsupported
    | Renew
    | Read
    | Record
    | RecordInterruption
    | Release
    | Insert
    | CancelTask
    | StoreCallEffect
    | StartChanges
    | StopChanges
    | StartHost
    | RunAttempt
    | ExitHost
    | StartTimer
    | StopTimer
    | StopTimers
    | Resolve
    | Fail
    | Log
)


@final
class _Leased:
    # a store whose leases expire: its runtimes act on the ticks of these timings
    __slots__ = ("timings",)

    def __init__(
        self,
        timings: LeaseTimings,
        /,
    ) -> None:
        self.timings: LeaseTimings = timings


@final
class _Local:
    # a store whose runtimes wake on its changes
    __slots__ = ()


type _Coordination = _Leased | _Local

# the retry of a failing wait for changes of a store
_CHANGES_RETRY: float = 0.1


def _cancelled_call() -> RuntimeError:
    return RuntimeError("A call of the store was cancelled")


def _failure_description(
    failure: AttemptFailure,
    /,
) -> str:
    match failure:
        case TaskFailureRecord(kind=kind, exception_type=exception_type):
            return f"{kind} {exception_type}"

        case TaskTimeLimitExceeded(time_limit=time_limit):
            return f"time limit {time_limit}"

        case TaskInterrupted():
            return "interrupted"


def _end_failure(
    end: AttemptEnd,
    /,
) -> AttemptFailure | None:
    match end:
        case AttemptFailed(failure=failure) | AttemptRetried(failure=failure):
            return failure

        case AttemptCompleted() | AttemptCancelled():
            return None


def _failed_end(
    task: NewTask,
    attempt: int,
    failure: AttemptFailure,
    /,
) -> AttemptEnd:
    delays: Sequence[float] = tuple(task.retry.delays)
    if attempt <= len(delays):
        return AttemptRetried(failure=failure, delay=delays[attempt - 1])

    return AttemptFailed(failure=failure)


def _timer_key(
    kind: str,
    identifier: UUID | None = None,
    /,
) -> TimerKey:
    return (kind,) if identifier is None else (kind, identifier)


@final
class Coordinator:
    """
    The one writer of the state of a runtime: its runs, phases, slots, awaiters and timers.

    ``handle`` takes one event, changes the state as the transition table says, and returns
    the effects to carry out. It reads no clock, starts nothing and waits for nothing: the
    driver of a runtime posts events and carries effects out, and I/O replies come back as
    events which carry the identity they were issued for. A reply which does not fit the
    state any more is dropped.
    """

    __slots__ = (
        "_awaiters",
        "_changes_inflight",
        "_changes_seen",
        "_claim_epoch",
        "_claim_inflight",
        "_claim_wanted",
        "_coordination",
        "_effects",
        "_grace_period",
        "_hosts",
        "_interruptions",
        "_io",
        "_lifecycle",
        "_limit",
        "_poll_wanted",
        "_read_cooling",
        "_read_inflight",
        "_released",
        "_renew_inflight",
        "_results",
        "_runs",
        "_serials",
        "_step",
        "_unsupported",
        "_unsupported_inflight",
    )

    def __init__(
        self,
        *,
        results: Mapping[str, type[State] | type[None]],
        limit: int | None,
        grace_period: float,
        coordination: StoreCoordination,
        unsupported: Literal["leave", "fail"],
    ) -> None:
        # the result classes of the registered names the runtime lists
        self._results: Mapping[str, type[State] | type[None]] = results
        self._limit: int | None = limit
        self._grace_period: float = grace_period
        self._coordination: _Coordination
        match coordination:
            case LeasedCoordination(timings=timings):
                self._coordination = _Leased(timings)

            case LocalCoordination():
                self._coordination = _Local()
        self._unsupported: Literal["leave", "fail"] = unsupported
        self._lifecycle: Lifecycle = _OPEN
        self._runs: dict[UUID, Run] = {}
        self._interruptions: dict[tuple[UUID, int], Interruption] = {}
        self._awaiters: dict[UUID, list[Reply]] = {}
        # the outcomes a close fixed, the only ones handles return once it is done
        self._released: dict[UUID, TaskOutcome[State | None]] = {}
        self._hosts: set[int] = set()
        self._serials: int = 0
        self._step: int = 0
        # store calls and the wait for changes in flight, which a close waits for
        self._io: int = 0
        self._claim_wanted: bool = False
        self._claim_inflight: bool = False
        # grows at every claim started and every claim reply, see ``_stable``
        self._claim_epoch: int = 0
        # the call failing unsupported tasks, part of the round of a claim
        self._unsupported_inflight: bool = False
        self._poll_wanted: bool = False
        self._read_inflight: bool = False
        self._read_cooling: bool = False
        self._renew_inflight: bool = False
        self._changes_inflight: bool = False
        self._changes_seen: int = -1
        self._effects: list[Effect] = []

    # --- what is visible from outside ----------------------------------------------------

    @property
    def lifecycle(self) -> Lifecycle:
        return self._lifecycle

    @property
    def runs(self) -> Set[UUID]:
        return frozenset(self._runs)

    @property
    def slots(self) -> int:
        return sum(self._slots(run) for run in self._runs.values())

    @property
    def pending(self) -> int:
        return self._io

    @property
    def hosts(self) -> int:
        return len(self._hosts)

    @property
    def interruptions(self) -> Set[tuple[UUID, int]]:
        return frozenset(self._interruptions)

    def phase(
        self,
        identifier: UUID,
        /,
    ) -> Phase | None:
        run: Run | None = self._runs.get(identifier)
        return None if run is None else run.phase

    def released(
        self,
        identifier: UUID,
        /,
    ) -> TaskOutcome[State | None] | None:
        return self._released.get(identifier)

    # --- the step ------------------------------------------------------------------------

    def handle(
        self,
        event: Event,
        /,
    ) -> Sequence[Effect]:
        if isinstance(self._lifecycle, Closed):
            return self._refused(event)

        self._step += 1
        self._effects = []
        self._on(event)
        self._pump()
        effects: list[Effect] = self._effects
        self._effects = []
        return effects

    def abort(self) -> Sequence[Effect]:
        # the end of a runtime whose driver failed: nothing is waited for, what waits fails
        if isinstance(self._lifecycle, Closed):
            return ()

        self._effects = []
        self._finalize()
        effects: list[Effect] = self._effects
        self._effects = []
        return effects

    def _refused(
        self,
        event: Event,
        /,
    ) -> Sequence[Effect]:
        # what a caller posted before the close ended and the driver reads after it
        match event:
            case SpawnAsked(reply=reply) | StoreCallAsked(reply=reply):
                return (
                    Fail(reply, RuntimeError("ManagedTasks runtime is closed, nothing was served")),
                )

            case CancelAsked(identifier=identifier, reply=reply):
                if identifier in self._released:
                    return (Resolve(reply, "finished"),)

                return (
                    Fail(reply, RuntimeError("ManagedTasks runtime is closed, nothing was served")),
                )

            case OutcomeAsked(identifier=identifier, reply=reply):
                released: TaskOutcome[State | None] | None = self._released.get(identifier)
                if released is not None:
                    return (Resolve(reply, released),)

                return (Fail(reply, RuntimeError("ManagedTasks runtime was closed")),)

            case _:
                return ()

    def _on(
        self,
        event: Event,
        /,
    ) -> None:
        match event:
            case Opened():
                self._opened()

            case CloseRequested():
                self._close()

            case GraceElapsed():
                self._cut_off()

            case (
                SpawnAsked()
                | CancelAsked()
                | OutcomeAsked()
                | OutcomeAbandoned()
                | StoreCallAsked()
            ):
                self._on_caller(event)

            case (
                ClaimReplied()
                | UnsupportedReplied()
                | RenewReplied()
                | ReadReplied()
                | RecordReplied()
                | InterruptionRecorded()
                | ReleaseReplied()
            ):
                self._on_store(event)

            case InsertReplied() | CancelReplied() | StoreCallReplied() | ChangedReplied():
                self._on_call(event)

            case ScopeEntered() | ScopeFailed() | AttemptEnded() | ScopeExited():
                self._on_host(event)

            case PollTick() | RenewalTick() | RetryDue() | PollDue() | ChangesRetry():
                self._on_tick(event)

            case TimeLimitElapsed(identifier=identifier, serial=serial, attempt=attempt):
                self._time_limit_elapsed(identifier, serial, attempt)

    def _on_caller(
        self,
        event: SpawnAsked | CancelAsked | OutcomeAsked | OutcomeAbandoned | StoreCallAsked,
        /,
    ) -> None:
        match event:
            case SpawnAsked(task=task, reply=reply, context=context):
                self._spawn_asked(task, reply, context)

            case CancelAsked(identifier=identifier, reply=reply, context=context):
                self._io += 1
                self._emit(CancelTask(identifier, reply, context))

            case OutcomeAsked(identifier=identifier, reply=reply):
                self._awaiters.setdefault(identifier, []).append(reply)
                if not self._local(identifier):
                    self._poll_wanted = True

            case OutcomeAbandoned(identifier=identifier, reply=reply):
                self._abandoned(identifier, reply)

            case StoreCallAsked(call=call, reply=reply, context=context):
                self._io += 1
                self._emit(StoreCallEffect(call, reply, context))

    def _on_store(
        self,
        event: ClaimReplied
        | UnsupportedReplied
        | RenewReplied
        | ReadReplied
        | RecordReplied
        | InterruptionRecorded
        | ReleaseReplied,
        /,
    ) -> None:
        match event:
            case ClaimReplied(result=result):
                self._claim_replied(result)

            case UnsupportedReplied(result=result):
                self._unsupported_replied(result)

            case RenewReplied(asked=asked, issued=issued, result=result):
                self._renew_replied(asked, issued, result)

            case ReadReplied(asked=asked, parks=parks, epoch=epoch, result=result):
                self._read_replied(asked, parks, epoch, result)

            case RecordReplied(
                identifier=identifier,
                serial=serial,
                attempt=attempt,
                result=result,
            ):
                self._io -= 1
                self._record_replied(identifier, serial, attempt, result)

            case InterruptionRecorded(identifier=identifier, attempt=attempt, result=result):
                self._io -= 1
                self._interruption_recorded(identifier, attempt, result)

            case ReleaseReplied(
                identifier=identifier,
                serial=serial,
                attempt=attempt,
                result=result,
            ):
                self._io -= 1
                self._release_replied(identifier, serial, attempt, result)

    def _on_call(
        self,
        event: InsertReplied | CancelReplied | StoreCallReplied | ChangedReplied,
        /,
    ) -> None:
        match event:
            case InsertReplied(reply=reply, result=result):
                self._insert_replied(reply, result)

            case CancelReplied(identifier=identifier, reply=reply, result=result):
                self._cancel_replied(identifier, reply, result)

            case StoreCallReplied(reply=reply, result=result):
                self._io -= 1
                match result:
                    case StoreFailure(exception=exception):
                        self._emit(Fail(reply, exception))

                    case CallCancelled():
                        self._emit(Fail(reply, _cancelled_call()))

                    case _:
                        self._emit(Resolve(reply, result))

            case ChangedReplied(result=result):
                self._changed_replied(result)

    def _on_host(
        self,
        event: ScopeEntered | ScopeFailed | AttemptEnded | ScopeExited,
        /,
    ) -> None:
        match event:
            case ScopeEntered(identifier=identifier, serial=serial, context=context):
                self._scope_entered(identifier, serial, context)

            case ScopeFailed(identifier=identifier, serial=serial, exception=exception):
                self._scope_failed(identifier, serial, exception)

            case AttemptEnded(identifier=identifier, serial=serial, attempt=attempt, result=result):
                self._attempt_ended(identifier, serial, attempt, result)

            case ScopeExited(identifier=identifier, serial=serial, exception=exception):
                self._scope_exited(identifier, serial, exception)

    def _on_tick(
        self,
        event: PollTick | RenewalTick | RetryDue | PollDue | ChangesRetry,
        /,
    ) -> None:
        match event:
            case PollTick():
                self._poll_tick()
                match self._coordination:
                    case _Leased(timings=timings):
                        self._emit(StartTimer(_timer_key("poll"), timings.polling, PollTick()))

                    case _Local():
                        pass

            case RenewalTick():
                self._renewal_tick()

            case RetryDue():
                self._claim_wanted = True

            case PollDue():
                self._read_cooling = False

            case ChangesRetry():
                if isinstance(self._lifecycle, Open):
                    self._start_changes()

    # --- helpers -------------------------------------------------------------------------

    def _emit(
        self,
        effect: Effect,
        /,
    ) -> None:
        self._effects.append(effect)

    def _log(
        self,
        level: Literal["debug", "warning", "error"],
        run: Run | None,
        message: str,
        /,
        *arguments: object,
    ) -> None:
        context: Context | None = None
        if run is not None and isinstance(run.host, HostLive):
            context = run.host.context

        self._emit(Log(level, message, arguments, context))

    def _run(
        self,
        identifier: UUID,
        serial: int,
        /,
    ) -> Run | None:
        run: Run | None = self._runs.get(identifier)
        if run is None or run.serial != serial:
            return None

        return run

    def _slots(
        self,
        run: Run,
        /,
    ) -> int:
        match run.phase:
            case Entering() | Attempting() | Recording() | Releasing():
                return 1

            case Discarding(successor=successor):
                return 1 if successor is None else 2

            case Parked():
                return 0

    def _local(
        self,
        identifier: UUID,
        /,
    ) -> bool:
        # the outcome reaches awaiters from the record of this runtime
        run: Run | None = self._runs.get(identifier)
        if run is None:
            return False

        match run.phase:
            case Entering() | Attempting() | Releasing():
                return True

            case Recording(failed=failed):
                return not failed

            case Discarding() | Parked():
                return False

    def _parks(self) -> Mapping[UUID, tuple[int, int]]:
        return {
            identifier: (run.serial, run.phase.attempts)
            for identifier, run in self._runs.items()
            if isinstance(run.phase, Parked)
        }

    def _can_park(
        self,
        run: Run,
        /,
    ) -> bool:
        return isinstance(self._lifecycle, Open) and isinstance(run.host, HostLive)

    def _resolve(
        self,
        identifier: UUID,
        outcome: TaskOutcome[State | None],
        /,
    ) -> None:
        for reply in self._awaiters.pop(identifier, ()):
            self._emit(Resolve(reply, outcome))

    def _fail_awaiters(
        self,
        identifier: UUID,
        exception: Exception,
        /,
    ) -> None:
        for reply in self._awaiters.pop(identifier, ()):
            self._emit(Fail(reply, exception))

    def _abandoned(
        self,
        identifier: UUID,
        reply: Reply,
        /,
    ) -> None:
        replies: list[Reply] | None = self._awaiters.get(identifier)
        if replies is None:
            return

        remaining: list[Reply] = [waiting for waiting in replies if waiting is not reply]
        if remaining:
            self._awaiters[identifier] = remaining

        else:
            del self._awaiters[identifier]

    def _end(
        self,
        run: Run,
        /,
    ) -> None:
        # the logs of the run are emitted before, none is delivered once its scope exited
        del self._runs[run.task.identifier]
        self._emit(StopTimer(_timer_key("limit", run.task.identifier)))
        if isinstance(run.phase, Parked):
            self._emit(StopTimer(_timer_key("retry", run.task.identifier)))

        if isinstance(run.host, HostLive):
            self._emit(ExitHost(run.serial))

        self._claim_wanted = True
        if run.task.identifier in self._awaiters:
            self._poll_wanted = True

    def _orphan_release(
        self,
        task: NewTask,
        attempt: int,
        /,
    ) -> None:
        # a claim which is not run: released by the attempt, no run waits for its reply
        self._io += 1
        self._emit(Release(task.identifier, None, attempt, None))

    def _impossible(
        self,
        run: Run | None,
        task: NewTask,
        attempt: int,
        what: str,
        /,
    ) -> None:
        self._log(
            "error",
            run,
            "Managed task %s attempt %d impossible state, %s: %s",
            task.name,
            attempt,
            what,
            task.identifier,
        )

    # --- opening, ticks ------------------------------------------------------------------

    def _opened(self) -> None:
        self._claim_wanted = True
        match self._coordination:
            case _Local():
                self._start_changes()

            case _Leased(timings=timings):
                self._emit(StartTimer(_timer_key("poll"), timings.polling, PollTick()))
                self._emit(StartTimer(_timer_key("renew"), timings.renewal, RenewalTick()))

    def _start_changes(self) -> None:
        if self._changes_inflight:
            return

        self._changes_inflight = True
        self._io += 1
        self._emit(StartChanges(self._changes_seen))

    def _changed_replied(
        self,
        result: int | StoreFailure | ChangesCancelled,
        /,
    ) -> None:
        self._io -= 1
        self._changes_inflight = False
        match result:
            case ChangesCancelled():
                return

            case StoreFailure(exception=exception):
                self._log(
                    "error",
                    None,
                    "Managed tasks waiting for changes failed: %s",
                    exception_type_name(exception),
                )
                if isinstance(self._lifecycle, Open):
                    self._emit(StartTimer(_timer_key("changes"), _CHANGES_RETRY, ChangesRetry()))

            case int() as counter:
                self._changes_seen = counter
                # every change is the polling and the renewal tick of its store
                self._poll_tick()
                self._renewal_tick()
                if isinstance(self._lifecycle, Open):
                    self._start_changes()

    def _poll_tick(self) -> None:
        if isinstance(self._lifecycle, Open):
            self._claim_wanted = True

        self._poll_wanted = True

    def _renewal_tick(self) -> None:
        for run in self._runs.values():
            phase: Phase = run.phase
            if isinstance(phase, Recording) and isinstance(phase.call, Waiting):
                phase.call = _IN_FLIGHT
                self._io += 1
                self._emit(
                    Record(
                        run.task.identifier,
                        run.serial,
                        phase.held.attempt,
                        phase.end,
                        self._context(run),
                    )
                )

        for key, interruption in self._interruptions.items():
            if isinstance(interruption.call, Waiting):
                interruption.call = _IN_FLIGHT
                self._io += 1
                self._emit(RecordInterruption(key[0], key[1], interruption.end))

        if not self._renew_inflight:
            asked: Mapping[UUID, int] = self._leases()
            if asked:
                self._renew_inflight = True
                self._io += 1
                self._emit(Renew(asked, self._step))
                # the next tick is counted from the return of the renewal
                return

        if not self._renew_inflight:
            match self._coordination:
                case _Leased(timings=timings):
                    self._emit(StartTimer(_timer_key("renew"), timings.renewal, RenewalTick()))

                case _Local():
                    pass

    def _leases(self) -> Mapping[UUID, int]:
        # the leases this runtime holds: attempts being entered, run and recorded, the next
        # attempt of a discarded one, and the interrupted attempts of others being recorded
        asked: dict[UUID, int] = {
            identifier: run.phase.held.attempt
            for identifier, run in self._runs.items()
            if isinstance(run.phase, Entering | Attempting | Recording)
        }
        for identifier, run in self._runs.items():
            if isinstance(run.phase, Discarding) and run.phase.successor is not None:
                asked[identifier] = run.phase.successor.attempt

        for identifier, attempt in self._interruptions:
            asked.setdefault(identifier, attempt)

        return asked

    def _context(
        self,
        run: Run,
        /,
    ) -> Context | None:
        return run.host.context if isinstance(run.host, HostLive) else None

    # --- the pump ------------------------------------------------------------------------

    def _pump(self) -> None:
        if (
            isinstance(self._lifecycle, Open)
            and self._claim_wanted
            and not self._claim_inflight
            and not self._unsupported_inflight
            and (self._results or self._unsupported == "fail")
        ):
            # a round: the claim of the names the runtime lists, when it lists any, and the
            # failing of the tasks of the others, when the runtime was asked to
            self._claim_wanted = False
            if self._results:
                self._claim_inflight = True
                self._claim_epoch += 1
                self._io += 1
                free: int | None = None if self._limit is None else max(self._limit - self.slots, 0)
                self._emit(Claim(free))

            if self._unsupported == "fail":
                self._unsupported_inflight = True
                self._io += 1
                self._emit(FailUnsupported(frozenset(self._results)))

        if self._poll_wanted and not self._read_inflight and not self._read_cooling:
            parks: Mapping[UUID, tuple[int, int]] = self._parks()
            wanted: set[UUID] = set(parks) | {
                identifier for identifier in self._awaiters if not self._local(identifier)
            }
            if not wanted:
                self._poll_wanted = False

            elif not (self._claim_inflight and parks):
                # a read held back while a claim is in flight and a run is parked: the claim
                # may be committing the very attempt the run waits for
                self._poll_wanted = False
                self._read_inflight = True
                self._io += 1
                self._emit(Read(frozenset(wanted), parks, self._stable_epoch()))
                match self._coordination:
                    case _Leased(timings=timings):
                        # one read for each polling interval at most, whoever asks for it
                        self._read_cooling = True
                        self._emit(StartTimer(_timer_key("read"), timings.polling, PollDue()))

                    case _Local():
                        pass

        if (
            isinstance(self._lifecycle, Grace | CutOff)
            and not self._runs
            and not self._interruptions
            and self._io == 0
            and not self._hosts
        ):
            self._finalize()

    def _stable_epoch(self) -> int | None:
        return None if self._claim_inflight else self._claim_epoch

    def _finalize(self) -> None:
        self._lifecycle = _CLOSED
        self._emit(StopTimers("poll"))
        self._emit(StopTimers("renew"))
        self._emit(StopTimers("read"))
        self._emit(StopTimers("retry"))
        self._emit(StopTimers("round"))
        self._emit(StopTimers("grace"))
        self._emit(StopTimers("changes"))
        for identifier in tuple(self._awaiters):
            self._fail_awaiters(identifier, RuntimeError("ManagedTasks runtime was closed"))

    # --- callers -------------------------------------------------------------------------

    def _spawn_asked(
        self,
        task: NewTask,
        reply: Reply,
        context: Context,
        /,
    ) -> None:
        if not isinstance(self._lifecycle, Open):
            self._emit(
                Fail(reply, RuntimeError("ManagedTasks runtime is closing, nothing is spawned"))
            )
            return

        self._io += 1
        self._emit(Insert(task, reply, context))

    def _insert_replied(
        self,
        reply: Reply,
        result: StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        self._io -= 1
        match result:
            case StoreFailure(exception=exception):
                self._emit(Fail(reply, exception))

            case CallCancelled():
                self._emit(Fail(reply, _cancelled_call()))

            case None:
                self._emit(Resolve(reply, None))
                self._claim_wanted = True

    def _cancel_replied(
        self,
        identifier: UUID,
        reply: Reply,
        result: TaskCancellation | StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        self._io -= 1
        run: Run | None = self._runs.get(identifier)
        match result:
            case StoreFailure(exception=exception):
                self._emit(Fail(reply, exception))

            case CallCancelled():
                self._emit(Fail(reply, _cancelled_call()))

            case None:
                self._emit(
                    Fail(reply, LookupError(f"Managed task {identifier} is not held by the store"))
                )

            case "pending":
                self._emit(Resolve(reply, result))
                if run is not None and isinstance(run.phase, Entering | Attempting):
                    run.phase.termination.request("cancel")

            case "cancelled":
                self._emit(Resolve(reply, result))
                self._resolve(identifier, TaskCancelled())
                if run is not None and isinstance(run.phase, Parked):
                    self._end(run)

            case "finished":
                self._emit(Resolve(reply, result))

    # --- close ---------------------------------------------------------------------------

    def _close(self) -> None:
        if not isinstance(self._lifecycle, Open):
            return

        self._lifecycle = _GRACE
        self._emit(StopTimers("retry"))
        self._emit(StopTimers("round"))
        self._emit(StopTimers("changes"))
        if self._changes_inflight:
            self._emit(StopChanges())

        for run in tuple(self._runs.values()):
            phase: Phase = run.phase
            if isinstance(phase, Parked):
                self._end(run)

            elif isinstance(phase, Entering):
                # nothing was run, the claim is given back
                self._emit(StopTimer(_timer_key("limit", run.task.identifier)))
                run.phase = Releasing(phase.held)
                self._io += 1
                self._emit(
                    Release(run.task.identifier, run.serial, phase.held.attempt, self._context(run))
                )

            elif isinstance(phase, Discarding) and phase.successor is not None:
                self._orphan_release(run.task, phase.successor.attempt)
                phase.successor = None

        if self._grace_period <= 0:
            self._cut_off()

        else:
            self._emit(StartTimer(_timer_key("grace"), self._grace_period, GraceElapsed()))

    def _cut_off(self) -> None:
        if not isinstance(self._lifecycle, Grace):
            return

        self._lifecycle = _CUT_OFF
        for run in tuple(self._runs.values()):
            phase: Phase = run.phase
            if isinstance(phase, Attempting):
                # delivered to every attempt still running, a time limit requested before
                # included, though only the request which decides the end is reported
                if phase.termination.request("close"):
                    # in the scope of the runtime, as the close itself
                    self._log(
                        "error",
                        None,
                        "Managed task cut off by close: %s, named %s, status running",
                        run.task.identifier,
                        run.task.name,
                    )

            elif isinstance(phase, Discarding):
                phase.termination.deliver()

            elif isinstance(phase, Recording) and isinstance(phase.call, Waiting):
                self._abandon_record(run.task, phase.held.attempt, run)
                self._end(run)

        for key, interruption in tuple(self._interruptions.items()):
            if isinstance(interruption.call, Waiting):
                self._abandon_record(interruption.claimed.task, key[1], None)
                del self._interruptions[key]

    def _abandon_record(
        self,
        task: NewTask,
        attempt: int,
        run: Run | None,
        /,
    ) -> None:
        self._log(
            "error",
            run,
            "Managed task %s attempt %d record abandoned by the close: %s",
            task.name,
            attempt,
            task.identifier,
        )

    # --- claims --------------------------------------------------------------------------

    def _claim_replied(
        self,
        result: Sequence[ClaimedTask] | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self._io -= 1
        self._claim_inflight = False
        self._claim_epoch += 1
        match result:
            case StoreFailure(exception=exception):
                # retried at the next tick, or by the timer of a store with no ticks, the
                # error itself is for the backend of the store
                self._log(
                    "error",
                    None,
                    "Managed tasks claim round failed: %s",
                    exception_type_name(exception),
                )
                self._retry_round()
                return

            case CallCancelled():
                return

            case _:
                pass

        for claimed in result:
            match claimed:
                case ClaimedAttempt():
                    self._granted(Held(claimed, self._step))

                case ClaimedInterrupted():
                    self._interrupted(claimed)

    def _retry_round(self) -> None:
        # a store whose runtimes wake on its changes has no tick to retry a failed round,
        # and a change may never come
        if isinstance(self._coordination, _Local) and isinstance(self._lifecycle, Open):
            self._emit(StartTimer(_timer_key("round"), _CHANGES_RETRY, RetryDue()))

    def _unsupported_replied(
        self,
        result: Mapping[UUID, str] | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self._io -= 1
        self._unsupported_inflight = False
        match result:
            case StoreFailure(exception=exception):
                # retried at the next tick, as a claim round
                self._log(
                    "error",
                    None,
                    "Managed tasks claim round failed: %s",
                    exception_type_name(exception),
                )
                self._retry_round()
                return

            case CallCancelled():
                return

            case _:
                pass

        for identifier, name in result.items():
            # in the scope of the runtime, no task scope exists for a task which never ran
            self._log(
                "error",
                None,
                "Managed task failed: %s, named %s, failure unsupported",
                identifier,
                name,
            )

    def _new_attempt(
        self,
        run: Run,
        held: Held,
        /,
    ) -> None:
        termination: AttemptTermination = AttemptTermination()
        run.phase = Attempting(held, termination)
        self._arm_limit(run, held)
        self._announce(run, held, termination)

    def _announce(
        self,
        run: Run,
        held: Held,
        termination: AttemptTermination,
        /,
    ) -> None:
        self._log(
            "debug",
            run,
            "Managed task %s attempt %d started: %s",
            run.task.name,
            held.attempt,
            run.task.identifier,
        )
        self._emit(RunAttempt(run.serial, held.claimed, termination))

    def _begin_successor(
        self,
        run: Run,
        successor: Successor,
        /,
    ) -> None:
        run.phase = Attempting(successor.held, successor.termination)
        reason = successor.termination.reason
        if reason is None:
            self._announce(run, successor.held, successor.termination)

        else:
            # asked to end before it started, as an attempt entering is
            self._finish_attempt(run, successor.held, AttemptTerminated(reason, None))

    def _arm_limit(
        self,
        run: Run,
        held: Held,
        /,
    ) -> None:
        # counted from the claim of the attempt
        if run.task.time_limit is not None:
            self._emit(
                StartTimer(
                    _timer_key("limit", run.task.identifier),
                    run.task.time_limit,
                    TimeLimitElapsed(run.task.identifier, run.serial, held.attempt),
                )
            )

    def _granted(
        self,
        held: Held,
        /,
    ) -> None:
        task: NewTask = held.claimed.task
        if not isinstance(self._lifecycle, Open):
            # claimed as the close began, no attempt starts during a close
            self._orphan_release(task, held.attempt)
            return

        run: Run | None = self._runs.get(task.identifier)
        if run is None:
            self._serials += 1
            run = Run(
                task,
                self._serials,
                HostLive(None),
                Entering(held, AttemptTermination()),
            )
            self._runs[task.identifier] = run
            self._hosts.add(run.serial)
            self._arm_limit(run, held)
            self._emit(StartHost(task, run.serial))
            return

        phase: Phase = run.phase
        if isinstance(phase, Parked):
            self._granted_parked(run, phase, held)

        elif isinstance(phase, Releasing):
            self._orphan_release(task, held.attempt)

        elif isinstance(phase, Discarding):
            if phase.successor is not None:
                self._orphan_release(task, phase.successor.attempt)

            phase.successor = Successor(held, AttemptTermination())
            # counted from the claim
            self._arm_limit(run, held)

        else:
            self._granted_held(run, phase, held)

    def _granted_parked(
        self,
        run: Run,
        phase: Parked,
        held: Held,
        /,
    ) -> None:
        if held.attempt > phase.attempts:
            self._emit(StopTimer(_timer_key("retry", run.task.identifier)))
            self._new_attempt(run, held)

        else:
            self._impossible(run, run.task, held.attempt, "claimed an attempt already recorded")
            self._orphan_release(run.task, held.attempt)

    def _granted_held(
        self,
        run: Run,
        phase: Entering | Attempting | Recording,
        held: Held,
        /,
    ) -> None:
        task: NewTask = run.task
        if held.attempt == phase.held.attempt:
            # the same lease, granted again
            phase.held.granted = self._step

        elif held.attempt < phase.held.attempt:
            self._impossible(run, task, held.attempt, "claimed an older attempt")
            self._orphan_release(task, held.attempt)

        elif isinstance(phase, Entering):
            # the attempt never started
            run.phase = Entering(held, AttemptTermination())
            self._arm_limit(run, held)

        elif isinstance(phase, Attempting):
            self._log(
                "warning",
                run,
                "Managed task %s attempt %d lease lost, its attempt is cancelled and discarded: %s",
                task.name,
                phase.held.attempt,
                task.identifier,
            )
            phase.termination.deliver()
            self._emit(StopTimer(_timer_key("limit", task.identifier)))
            run.phase = Discarding(
                phase.held.attempt,
                phase.termination,
                Successor(held, AttemptTermination()),
            )
            self._arm_limit(run, held)

        elif isinstance(run.host, HostLive):
            # the store moved past the attempt which is being recorded
            self._new_attempt(run, held)

        else:
            self._end(run)
            self._granted(held)

    def _interrupted(
        self,
        claimed: ClaimedInterrupted,
        /,
    ) -> None:
        task: NewTask = claimed.task
        attempt: int = claimed.attempt
        run: Run | None = self._runs.get(task.identifier)
        if run is not None and isinstance(run.phase, Discarding):
            # the attempt this runtime holds next, kept under the renewed lease
            successor = run.phase.successor
            if successor is not None and successor.attempt == attempt:
                successor.held.granted = self._step
                if claimed.cancel_requested:
                    successor.termination.request("cancel")

                return

        if (
            run is not None
            and isinstance(run.phase, Entering | Attempting | Recording)
            and run.phase.held.attempt == attempt
        ):
            # this runtime still serves the attempt, kept under the renewed lease
            run.phase.held.granted = self._step
            if claimed.cancel_requested and not isinstance(run.phase, Recording):
                run.phase.termination.request("cancel")

            return

        if (task.identifier, attempt) in self._interruptions:
            return

        end: AttemptEnd = _failed_end(task, attempt, TaskInterrupted())
        self._interruptions[task.identifier, attempt] = Interruption(claimed, end)
        self._io += 1
        self._emit(RecordInterruption(task.identifier, attempt, end))

    # --- host ----------------------------------------------------------------------------

    def _scope_entered(
        self,
        identifier: UUID,
        serial: int,
        context: Context,
        /,
    ) -> None:
        run: Run | None = self._run(identifier, serial)
        if run is None:
            return

        run.host = HostLive(context)
        phase: Phase = run.phase
        if not isinstance(phase, Entering):
            return

        reason = phase.termination.reason
        if reason is None:
            run.phase = Attempting(phase.held, phase.termination)
            self._announce(run, phase.held, phase.termination)

        else:
            self._finish_attempt(run, phase.held, AttemptTerminated(reason, None))

    def _scope_failed(
        self,
        identifier: UUID,
        serial: int,
        exception: BaseException,
        /,
    ) -> None:
        self._hosts.discard(serial)
        run: Run | None = self._run(identifier, serial)
        if run is None:
            return

        run.host = _HOST_GONE
        phase: Phase = run.phase
        if not isinstance(phase, Entering):
            return

        reason = phase.termination.reason
        if reason is not None:
            self._finish_attempt(run, phase.held, AttemptTerminated(reason, None))
            return

        self._log(
            "error",
            None,
            "Managed task %s scope could not be entered: %s, %s",
            run.task.name,
            identifier,
            exception_type_name(exception),
        )
        self._finish_attempt(
            run,
            phase.held,
            AttemptRaised(
                TaskFailureRecord(
                    kind="preparation",
                    exception_type=exception_type_name(exception),
                )
            ),
        )

    def _scope_exited(
        self,
        identifier: UUID,
        serial: int,
        exception: BaseException | None,
        /,
    ) -> None:
        self._hosts.discard(serial)
        run: Run | None = self._run(identifier, serial)
        if run is None:
            if exception is not None and not isinstance(exception, CancelledError):
                self._log(
                    "error",
                    None,
                    "Managed task run failed: %s",
                    exception_type_name(exception),
                )

            return

        if exception is not None and is_interpreter_exit(exception):
            # an interpreter exit, which records and releases nothing: the lease is left
            # to expire and the attempt is found as interrupted when the store outlives it
            run.host = _HOST_GONE
            self._end(run)
            return

        if exception is not None and not isinstance(exception, CancelledError):
            # a cancelled host is the end of a run which the runtime itself cut short
            self._log(
                "error",
                None,
                "Managed task run failed: %s",
                exception_type_name(exception),
            )

        run.host = _HOST_GONE
        phase: Phase = run.phase
        if isinstance(phase, Entering | Attempting):
            if isinstance(phase, Attempting):
                phase.termination.deliver()

            self._orphan_release(run.task, phase.held.attempt)
            self._end(run)

        elif isinstance(phase, Discarding):
            phase.termination.deliver()
            if phase.successor is not None:
                self._orphan_release(run.task, phase.successor.attempt)

            self._end(run)

        elif isinstance(phase, Parked):
            self._end(run)

        # recording and releasing go on without the host

    def _attempt_ended(
        self,
        identifier: UUID,
        serial: int,
        attempt: int,
        result: AttemptResult,
        /,
    ) -> None:
        run: Run | None = self._run(identifier, serial)
        if run is None:
            return

        phase: Phase = run.phase
        if isinstance(phase, Attempting) and phase.held.attempt == attempt:
            self._finish_attempt(run, phase.held, result)

        elif isinstance(phase, Discarding) and phase.attempt == attempt:
            successor: Successor | None = phase.successor
            if successor is not None and isinstance(self._lifecycle, Open):
                self._begin_successor(run, successor)

            else:
                if successor is not None:
                    self._orphan_release(run.task, successor.attempt)

                self._end(run)

        else:
            self._impossible(run, run.task, attempt, "an attempt ended which is not the current")

    def _finish_attempt(
        self,
        run: Run,
        held: Held,
        result: AttemptResult,
        /,
    ) -> None:
        self._emit(StopTimer(_timer_key("limit", run.task.identifier)))
        end: AttemptEnd | None = self._attempt_end(run, held, result)
        if end is None:
            run.phase = Releasing(held)
            self._io += 1
            self._emit(Release(run.task.identifier, run.serial, held.attempt, self._context(run)))
            return

        recording: Recording = Recording(held, end)
        run.phase = recording
        self._io += 1
        self._emit(Record(run.task.identifier, run.serial, held.attempt, end, self._context(run)))

    def _attempt_end(
        self,
        run: Run,
        held: Held,
        result: AttemptResult,
        /,
    ) -> AttemptEnd | None:
        # none when the attempt is released instead of recorded
        task: NewTask = run.task
        match result:
            case AttemptReturned(text=text):
                return AttemptCompleted(result=text)

            case AttemptRaised(failure=failure):
                return _failed_end(task, held.attempt, failure)

            case AttemptCancelledLocally():
                return AttemptCancelled()

            case AttemptTerminated(reason=reason, discarded=discarded):
                if discarded is not None:
                    self._log(
                        "warning",
                        run,
                        "Managed task %s attempt %d %s after %s was requested,"
                        " what it did is discarded: %s",
                        task.name,
                        held.attempt,
                        discarded,
                        reason,
                        task.identifier,
                    )

                match reason:
                    case "cancel":
                        return AttemptCancelled()

                    case "time_limit":
                        assert task.time_limit is not None  # nosec: B101
                        return _failed_end(
                            task,
                            held.attempt,
                            TaskTimeLimitExceeded(time_limit=task.time_limit),
                        )

                    case "close":
                        return None

    def _time_limit_elapsed(
        self,
        identifier: UUID,
        serial: int,
        attempt: int,
        /,
    ) -> None:
        run: Run | None = self._run(identifier, serial)
        if run is None:
            return

        phase: Phase = run.phase
        if isinstance(phase, Entering | Attempting) and phase.held.attempt == attempt:
            phase.termination.request("time_limit")

        elif (
            isinstance(phase, Discarding)
            and phase.successor is not None
            and phase.successor.attempt == attempt
        ):
            phase.successor.termination.request("time_limit")

    # --- store replies -------------------------------------------------------------------

    def _record_replied(
        self,
        identifier: UUID,
        serial: int,
        attempt: int,
        result: StoredStatus | StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        run: Run | None = self._run(identifier, serial)
        if run is None or not isinstance(run.phase, Recording) or run.phase.held.attempt != attempt:
            return

        if isinstance(result, StoreFailure | CallCancelled):
            self._record_failed(run, run.phase, result)

        elif result is None:
            self._record_unheld(run, run.phase)

        else:
            self._record_stored(run, run.phase, result)

    def _record_failed(
        self,
        run: Run,
        phase: Recording,
        failure: StoreFailure | CallCancelled,
        /,
    ) -> None:
        task: NewTask = run.task
        if isinstance(failure, StoreFailure):
            self._log(
                "error",
                run,
                "Managed task %s attempt %d could not be recorded, retrying: %s, %s",
                task.name,
                phase.held.attempt,
                task.identifier,
                exception_type_name(failure.exception),
            )

        # awaiters read the store from now on, the lease is renewed meanwhile
        phase.failed = True
        self._poll_wanted = True
        if isinstance(self._lifecycle, CutOff):
            self._abandon_record(task, phase.held.attempt, run)
            self._end(run)

        else:
            phase.call = _WAITING

    def _record_unheld(
        self,
        run: Run,
        phase: Recording,
        /,
    ) -> None:
        # another runtime holds the task, or this attempt is not its current one: what this
        # attempt ended with is discarded, the store knows the outcome
        self._log(
            "warning",
            run,
            "Managed task %s attempt %d not recorded, its lease is not held,"
            " the outcome is unknown here and is read from the store: %s",
            run.task.name,
            phase.held.attempt,
            run.task.identifier,
        )
        self._poll_wanted = True
        if isinstance(phase.end, AttemptRetried) and self._can_park(run):
            run.phase = Parked(phase.held.attempt)
            self._claim_wanted = True

        else:
            self._end(run)

    def _record_stored(
        self,
        run: Run,
        phase: Recording,
        status: StoredStatus,
        /,
    ) -> None:
        match status:
            case StoredFinished(outcome=stored):
                self._finished(run, phase, stored)

            case StoredRetryPending():
                self._record_retried(run, phase)

            case StoredQueued() | StoredRunning():
                self._impossible(
                    run,
                    run.task,
                    phase.held.attempt,
                    "a record left the task queued or running",
                )
                self._end(run)

    def _record_retried(
        self,
        run: Run,
        phase: Recording,
        /,
    ) -> None:
        task: NewTask = run.task
        attempt: int = phase.held.attempt
        end: AttemptEnd = phase.end
        if not isinstance(end, AttemptRetried):
            self._impossible(run, task, attempt, "a retry pending after an end without one")
            self._end(run)
            return

        self._log(
            "warning",
            run,
            "Managed task %s attempt %d failed, retrying in %s s: %s, %s",
            task.name,
            attempt,
            end.delay,
            _failure_description(end.failure),
            task.identifier,
        )
        if self._can_park(run):
            run.phase = Parked(attempt)
            self._emit(StartTimer(_timer_key("retry", task.identifier), end.delay, RetryDue()))
            self._poll_wanted = True
            self._claim_wanted = True

        else:
            self._end(run)

    def _finished(
        self,
        run: Run,
        phase: Recording,
        stored: StoredCompleted | StoredFailed | StoredCancelled,
        /,
    ) -> None:
        task: NewTask = run.task
        attempt: int = phase.held.attempt
        if isinstance(phase.end, AttemptCompleted) and isinstance(stored, StoredCancelled):
            # a cancel the store accepted wins over what the attempt returned
            self._log(
                "warning",
                run,
                "Managed task %s attempt %d returned after its cancellation was accepted,"
                " what it returned is discarded: %s",
                task.name,
                attempt,
                task.identifier,
            )

        self._fix_outcome(run, task, stored, _end_failure(phase.end), attempt)
        self._end(run)

    def _fix_outcome(
        self,
        run: Run | None,
        task: NewTask,
        stored: StoredCompleted | StoredFailed | StoredCancelled,
        failure: AttemptFailure | None,
        attempts: int,
        /,
    ) -> None:
        try:
            outcome: TaskOutcome[State | None] = task_outcome(
                stored,
                failure=failure,
                attempts=attempts,
                result=self._results[task.name],
            )

        except Exception as exc:
            self._fail_awaiters(task.identifier, exc)
            return

        if isinstance(outcome, TaskFailed) and failure is not None:
            self._log(
                "error",
                run,
                "Managed task %s failed after %d attempts, last: %s: %s",
                task.name,
                outcome.attempts,
                _failure_description(failure),
                task.identifier,
            )

        self._log("debug", run, "Managed task %s outcome fixed: %s", task.name, task.identifier)
        self._resolve(task.identifier, outcome)

    def _interruption_recorded(
        self,
        identifier: UUID,
        attempt: int,
        result: StoredStatus | StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        interruption: Interruption | None = self._interruptions.get((identifier, attempt))
        if interruption is None:
            return

        task: NewTask = interruption.claimed.task
        if isinstance(result, StoreFailure | CallCancelled):
            if isinstance(result, StoreFailure):
                self._log(
                    "error",
                    None,
                    "Managed task %s attempt %d interruption could not be recorded, retrying:"
                    " %s, %s",
                    task.name,
                    attempt,
                    identifier,
                    exception_type_name(result.exception),
                )

            if isinstance(self._lifecycle, CutOff):
                self._abandon_record(task, attempt, None)
                del self._interruptions[identifier, attempt]

            else:
                interruption.call = _WAITING

            return

        del self._interruptions[identifier, attempt]
        run: Run | None = self._runs.get(identifier)
        parked: Run | None = run if run is not None and isinstance(run.phase, Parked) else None
        match result:
            case StoredFinished(outcome=stored):
                self._fix_outcome(None, task, stored, _end_failure(interruption.end), attempt)
                if parked is not None:
                    self._end(parked)

            case StoredRetryPending():
                self._interruption_retried(parked, interruption, attempt)

            case _:
                pass  # taken by another runtime, or nothing to wait for

    def _interruption_retried(
        self,
        parked: Run | None,
        interruption: Interruption,
        attempt: int,
        /,
    ) -> None:
        task: NewTask = interruption.claimed.task
        end: AttemptEnd = interruption.end
        if not isinstance(end, AttemptRetried):
            return

        self._log(
            "warning",
            None,
            "Managed task %s attempt %d failed, retrying in %s s: %s, %s",
            task.name,
            attempt,
            end.delay,
            _failure_description(end.failure),
            task.identifier,
        )
        if parked is not None and isinstance(parked.phase, Parked):
            if parked.phase.attempts == attempt - 1:
                parked.phase.attempts = attempt

        if isinstance(self._lifecycle, Open):
            self._emit(StartTimer(_timer_key("retry", task.identifier), end.delay, RetryDue()))

    def _release_replied(
        self,
        identifier: UUID,
        serial: int | None,
        attempt: int,
        result: StoredStatus | StoreFailure | CallCancelled | None,
        /,
    ) -> None:
        run: Run | None = None if serial is None else self._run(identifier, serial)
        if isinstance(result, StoreFailure):
            # the lease is left to expire
            self._log(
                "error",
                run,
                "Managed task attempt %d could not be released: %s, %s",
                attempt,
                identifier,
                exception_type_name(result.exception),
            )

        elif isinstance(result, CallCancelled):
            pass  # the lease is left to expire, nothing the store did

        elif isinstance(result, StoredFinished) and isinstance(result.outcome, StoredCancelled):
            self._released[identifier] = TaskCancelled()
            name: str = "?" if run is None else run.task.name
            self._log("debug", run, "Managed task %s outcome fixed: %s", name, identifier)
            self._resolve(identifier, TaskCancelled())

        if (
            run is not None
            and isinstance(run.phase, Releasing)
            and run.phase.held.attempt == attempt
        ):
            self._end(run)

    def _renew_replied(
        self,
        asked: Mapping[UUID, int],
        issued: int,
        result: LeaseRenewal | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self._io -= 1
        self._renew_inflight = False
        match self._coordination:
            case _Leased(timings=timings):
                # a fixed delay after the renewal returns
                self._emit(StartTimer(_timer_key("renew"), timings.renewal, RenewalTick()))

            case _Local():
                pass

        match result:
            case StoreFailure(exception=exception):
                self._log(
                    "error",
                    None,
                    "Managed tasks lease renewal failed: %s",
                    exception_type_name(exception),
                )
                return

            case CallCancelled():
                return

            case LeaseRenewal():
                pass

        for identifier, attempt in asked.items():
            run: Run | None = self._runs.get(identifier)
            if run is not None and isinstance(run.phase, Discarding):
                self._successor_lease(run, run.phase, attempt, issued, result, identifier)

            elif (
                run is not None
                and isinstance(run.phase, Entering | Attempting)
                and run.phase.held.attempt == attempt
                and run.phase.held.granted < issued
            ):
                self._lease(run, run.phase, result.leases.get(identifier))

    def _successor_lease(
        self,
        run: Run,
        phase: Discarding,
        attempt: int,
        issued: int,
        result: LeaseRenewal,
        identifier: UUID,
        /,
    ) -> None:
        successor: Successor | None = phase.successor
        if (
            successor is None
            or successor.attempt != attempt
            or successor.held.granted >= issued
            or identifier in result.leases
        ):
            return

        # the lease is gone, nothing is left to release
        self._log(
            "warning",
            run,
            "Managed task %s attempt %d lease lost before it started, it is dropped: %s",
            run.task.name,
            attempt,
            identifier,
        )
        phase.successor = None
        self._emit(StopTimer(_timer_key("limit", identifier)))

    def _lease(
        self,
        run: Run,
        phase: Entering | Attempting,
        state: LeaseState | None,
        /,
    ) -> None:
        task: NewTask = run.task
        if state is None:
            self._log(
                "warning",
                run,
                "Managed task %s attempt %d lease lost, its attempt is cancelled and discarded: %s",
                task.name,
                phase.held.attempt,
                task.identifier,
            )
            if isinstance(phase, Entering):
                self._end(run)

            else:
                phase.termination.deliver()
                self._emit(StopTimer(_timer_key("limit", task.identifier)))
                run.phase = Discarding(phase.held.attempt, phase.termination, None)

        elif state == "cancel_requested":
            phase.termination.request("cancel")

    def _read_replied(
        self,
        asked: Set[UUID],
        parks: Mapping[UUID, tuple[int, int]],
        epoch: int | None,
        result: Mapping[UUID, StoredTask] | StoreFailure | CallCancelled,
        /,
    ) -> None:
        self._io -= 1
        self._read_inflight = False
        match result:
            case StoreFailure(exception=exception):
                self._log(
                    "error",
                    None,
                    "Managed tasks outcome poll failed: %s",
                    exception_type_name(exception),
                )
                return

            case CallCancelled():
                return

            case _:
                pass

        stable: bool = epoch is not None and epoch == self._claim_epoch
        for identifier in asked:
            item: StoredTask | None = result.get(identifier)
            self._unpark(identifier, parks.get(identifier), item, stable)
            if identifier in self._awaiters:
                self._read_outcome(identifier, item)

    def _unpark(
        self,
        identifier: UUID,
        park: tuple[int, int] | None,
        item: StoredTask | None,
        stable: bool,
        /,
    ) -> None:
        run: Run | None = self._runs.get(identifier)
        # only the run still parked as it was when the read was asked for
        if (
            run is None
            or park is None
            or not isinstance(run.phase, Parked)
            or run.serial != park[0]
            or run.phase.attempts != park[1]
        ):
            return

        if item is None or len(item.attempts) != park[1] or isinstance(item.status, StoredFinished):
            self._end(run)  # run elsewhere, finished, or no more

        elif item.status.status == "running":
            # a read which could have been issued before a claim committed is not trusted
            if stable and not any(key[0] == identifier for key in self._interruptions):
                self._end(run)

    def _read_outcome(
        self,
        identifier: UUID,
        item: StoredTask | None,
        /,
    ) -> None:
        if item is None:
            self._fail_awaiters(
                identifier,
                LookupError(f"Managed task {identifier} is not held by the store"),
            )
            return

        result: type[State] | type[None] | None = self._results.get(item.task.name)
        if result is None:
            return

        try:
            outcome: TaskOutcome[State | None] | None = stored_outcome(item, result=result)

        except Exception as exc:
            self._fail_awaiters(identifier, exc)
            return

        if outcome is not None:
            self._resolve(identifier, outcome)
