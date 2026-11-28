from collections.abc import Mapping, Sequence
from math import isfinite
from typing import TYPE_CHECKING, Any, Literal, Self, final
from uuid import UUID

from haiway.attributes import State
from haiway.context.managed.encoding import require_json_object_or_null
from haiway.context.managed.types import (
    AttemptFailure,
    TaskFailure,
    TaskRetryPolicy,
    TaskUnsupported,
)
from haiway.context.observability import ObservabilityLink

__all__ = (
    "AttemptCancelled",
    "AttemptCompleted",
    "AttemptEnd",
    "AttemptFailed",
    "AttemptRetried",
    "ClaimedAttempt",
    "ClaimedInterrupted",
    "ClaimedTask",
    "LeaseRenewal",
    "LeaseState",
    "LeaseTimings",
    "LeasedCoordination",
    "NewTask",
    "StoredAttempt",
    "StoredCancelled",
    "StoredCompleted",
    "StoredFailed",
    "StoredFinished",
    "StoredQueued",
    "StoredRetryPending",
    "StoredRunning",
    "StoredStatus",
    "StoredTask",
)


@final
class StoredAttempt(State):
    """
    The end of one counted attempt, its number being its position in the task plus 1.

    Parameters
    ----------
    result : Literal["completed", "cancelled"] | TaskFailure
        How the attempt ended.
    """

    result: Literal["completed", "cancelled"] | TaskFailure


@final
class StoredQueued(State):
    """
    Status of a task waiting for its due moment.
    """

    status: Literal["queued"] = "queued"


@final
class StoredRunning(State):
    """
    Status of a task whose attempt is leased by a runtime.

    Parameters
    ----------
    cancel_requested : bool
        Whether a cancel was requested for the running attempt.
    """

    status: Literal["running"] = "running"
    cancel_requested: bool


@final
class StoredRetryPending(State):
    """
    Status of a task waiting for the due moment of its next attempt.
    """

    status: Literal["retry_pending"] = "retry_pending"


@final
class StoredCompleted(State):
    """
    Completed outcome of a stored task.


    Parameters
    ----------
    result : str
        JSON of the returned ``State``, or ``"null"``.

    Raises
    ------
    ValueError
        When ``result`` is not JSON text of an object or ``null``.
    """

    outcome: Literal["completed"] = "completed"
    result: str

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            require_json_object_or_null(self.result)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            require_json_object_or_null(updated.result)
            return updated

        __replace__ = updating


@final
class StoredFailed(State):
    """
    Failed outcome of a stored task, its failure and the number of attempts being derived
    from ``StoredTask.attempts``.
    """

    outcome: Literal["failed"] = "failed"


@final
class StoredCancelled(State):
    """
    Cancelled outcome of a stored task.
    """

    outcome: Literal["cancelled"] = "cancelled"


@final
class StoredFinished(State):
    """
    Status of a task which has its outcome.

    Parameters
    ----------
    outcome : StoredCompleted | StoredFailed | StoredCancelled
        The outcome of the task.
    """

    status: Literal["finished"] = "finished"
    outcome: StoredCompleted | StoredFailed | StoredCancelled


type StoredStatus = StoredQueued | StoredRunning | StoredRetryPending | StoredFinished


@final
class NewTask(State):
    """
    A task as a runtime inserts it into a store.

    Parameters
    ----------
    identifier : UUID
        Identifier of the task.
    name : str
        Registered name of the task.
    argument : str
        JSON of the argument ``State``.
    retry : TaskRetryPolicy
        The effective retry policy of the task.
    time_limit : float | None
        Seconds per attempt, ``None`` when not limited, otherwise finite and above 0.
    origin : ObservabilityLink | None
        Position of the spawning scope, ``None`` for a spawn outside any scope.

    Raises
    ------
    ValueError
        When ``time_limit`` is given and is not finite or not above 0.
    """

    identifier: UUID
    name: str
    argument: str
    retry: TaskRetryPolicy
    time_limit: float | None
    origin: ObservabilityLink | None

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_time_limit(self.time_limit)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_time_limit(updated.time_limit)
            return updated

        __replace__ = updating


def _check_time_limit(time_limit: float | None) -> None:
    if time_limit is not None and (not isfinite(time_limit) or time_limit <= 0):
        raise ValueError(f"NewTask time_limit has to be finite and above 0: {time_limit}")


def _check_attempt_number(
    attempt: int,
    owner: str,
) -> None:
    if attempt < 1:
        raise ValueError(f"{owner} attempt has to be at least 1: {attempt}")


def _check_retry_delay(delay: float) -> None:
    if not isfinite(delay) or delay < 0:
        raise ValueError(f"AttemptRetried delay has to be finite and at least 0: {delay}")


def _is_failure(attempt: StoredAttempt) -> bool:
    return not isinstance(attempt.result, str)


def _requires_attempts(status: StoredStatus) -> bool:
    match status:
        case StoredQueued() | StoredRunning():
            return False

        case StoredRetryPending():
            return True

        case StoredFinished():
            match status.outcome:
                case StoredCancelled():
                    return False

                case StoredCompleted() | StoredFailed():
                    return True


def _accepts_last(
    status: StoredStatus,
    attempt: StoredAttempt,
) -> bool:
    result: Literal["completed", "cancelled"] | TaskFailure = attempt.result
    match status:
        case StoredQueued() | StoredRunning() | StoredRetryPending():
            return _is_failure(attempt) and not isinstance(result, TaskUnsupported)

        case StoredFinished():
            match status.outcome:
                case StoredCompleted():
                    return result == "completed"

                case StoredFailed():
                    return _is_failure(attempt)

                case StoredCancelled():
                    # the last attempt can also be cancelled
                    return result == "cancelled" or (
                        _is_failure(attempt) and not isinstance(result, TaskUnsupported)
                    )


def _check_stored_task(
    status: StoredStatus,
    attempts: Sequence[StoredAttempt],
) -> None:
    # only the last attempt can end otherwise than failed, and only the last
    # attempt of a failed task can be unsupported
    for attempt in attempts[:-1]:
        if not _is_failure(attempt) or isinstance(attempt.result, TaskUnsupported):
            raise ValueError("Only the last attempt of a StoredTask can end otherwise than failed")

    if not attempts:
        if _requires_attempts(status):
            raise ValueError("A retry pending, completed or failed StoredTask needs attempts")

    elif not _accepts_last(status, attempts[-1]):
        raise ValueError("The last attempt of a StoredTask does not agree with its status")


@final
class StoredTask(State):
    """
    What a store holds for a registered task.

    The status and the attempts have to agree: every combination no transition produces
    is rejected at construction, also when decoded or nested in another decoded value.

    Parameters
    ----------
    task : NewTask
        The task as inserted.
    status : StoredStatus
        The status of the task.
    attempts : Sequence[StoredAttempt]
        The counted attempts, in number order. A failed task's failure and its count
        of attempts are derived from them.

    Raises
    ------
    ValueError
        When the status and the attempts do not agree.
    """

    task: NewTask
    status: StoredStatus
    attempts: Sequence[StoredAttempt]

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_stored_task(self.status, self.attempts)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_stored_task(updated.status, updated.attempts)
            return updated

        __replace__ = updating


@final
class ClaimedAttempt(State):
    """
    A due task claimed for a new attempt.

    Parameters
    ----------
    task : NewTask
        The claimed task.
    attempt : int
        Number of the attempt, ``len(attempts) + 1``.
    Raises
    ------
    ValueError
        When ``attempt`` is below 1.
    """

    claim: Literal["attempt"] = "attempt"
    task: NewTask
    attempt: int

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_attempt_number(self.attempt, "ClaimedAttempt")

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_attempt_number(updated.attempt, "ClaimedAttempt")
            return updated

        __replace__ = updating


@final
class ClaimedInterrupted(State):
    """
    A running task whose lease expired, claimed to record its interrupted attempt.

    Parameters
    ----------
    task : NewTask
        The claimed task.
    attempt : int
        Number of the interrupted attempt, ``len(attempts) + 1``.
    cancel_requested : bool
        Whether a cancel was requested for the task.
    Raises
    ------
    ValueError
        When ``attempt`` is below 1.
    """

    claim: Literal["interrupted"] = "interrupted"
    task: NewTask
    attempt: int
    cancel_requested: bool

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_attempt_number(self.attempt, "ClaimedInterrupted")

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_attempt_number(updated.attempt, "ClaimedInterrupted")
            return updated

        __replace__ = updating


type ClaimedTask = ClaimedAttempt | ClaimedInterrupted

type LeaseState = Literal["held", "cancel_requested"]


@final
class LeaseRenewal(State):
    """
    Result of a lease renewal.

    Parameters
    ----------
    leases : Mapping[UUID, LeaseState]
        The requested leases still held, by task identifier. A task which is absent
        is not held.
    """

    leases: Mapping[UUID, LeaseState]


@final
class AttemptCompleted(State):
    """
    End of an attempt which returned.


    Parameters
    ----------
    result : str
        JSON of the returned ``State``, or ``"null"``.

    Raises
    ------
    ValueError
        When ``result`` is not JSON text of an object or ``null``.
    """

    end: Literal["completed"] = "completed"
    result: str

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            require_json_object_or_null(self.result)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            require_json_object_or_null(updated.result)
            return updated

        __replace__ = updating


@final
class AttemptRetried(State):
    """
    End of an attempt which failed, a retry following.

    Parameters
    ----------
    failure : AttemptFailure
        Why the attempt failed.
    delay : float
        Seconds until the next attempt is due.
    Raises
    ------
    ValueError
        When ``delay`` is not finite or is below 0.
    """

    end: Literal["retried"] = "retried"
    failure: AttemptFailure
    delay: float

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_retry_delay(self.delay)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_retry_delay(updated.delay)
            return updated

        __replace__ = updating


@final
class AttemptFailed(State):
    """
    End of an attempt which failed, no retry following.

    Parameters
    ----------
    failure : AttemptFailure
        Why the attempt failed.
    """

    end: Literal["failed"] = "failed"
    failure: AttemptFailure


@final
class AttemptCancelled(State):
    """
    End of an attempt which a cancel requested in the running runtime ended.
    """

    end: Literal["cancelled"] = "cancelled"


type AttemptEnd = AttemptCompleted | AttemptRetried | AttemptFailed | AttemptCancelled


def _check_lease_timings(
    lease: float,
    renewal: float,
    polling: float,
) -> None:
    if not isfinite(lease) or lease <= 0:
        raise ValueError(f"LeaseTimings lease has to be finite and above 0: {lease}")

    if not isfinite(renewal) or renewal <= 0 or renewal >= lease:
        raise ValueError(
            f"LeaseTimings renewal has to be finite, above 0 and below lease: {renewal}"
        )

    if not isfinite(polling) or polling <= 0:
        raise ValueError(f"LeaseTimings polling has to be finite and above 0: {polling}")


@final
class LeaseTimings(State):
    """
    The timings of the leases of a store.


    Parameters
    ----------
    lease : float
        Seconds a claim or renewal holds a lease, finite and above 0.
    renewal : float
        Seconds between renewals, finite, above 0 and below ``lease``.
    polling : float
        Seconds between claim rounds and outcome polls, finite and above 0.

    Raises
    ------
    ValueError
        When a timing is out of its range.
    """

    lease: float
    renewal: float
    polling: float

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_lease_timings(self.lease, self.renewal, self.polling)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_lease_timings(updated.lease, updated.renewal, updated.polling)
            return updated

        __replace__ = updating


@final
class LeasedCoordination(State):
    """
    Coordination of a store with lease timings.

    Parameters
    ----------
    timings : LeaseTimings
        The lease timings, owned by the store.
    """

    coordination: Literal["leased"] = "leased"
    timings: LeaseTimings
