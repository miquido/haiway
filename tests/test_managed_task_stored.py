import copy
import json
import math
from collections.abc import Mapping
from typing import Any, Literal
from uuid import UUID, uuid4

from pytest import mark, raises

from haiway import State
from haiway.attributes import ValidationError
from haiway.context import ObservabilityLink
from haiway.context.managed import (
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
    TaskFailureRecord,
    TaskInterrupted,
    TaskRetryPolicy,
    TaskTimeLimitExceeded,
    TaskUnsupported,
)
from haiway.context.managed.types import TaskFailure

_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
_RAISED = TaskFailureRecord(kind="raised", exception_type="builtins.ValueError")


def _task() -> NewTask:
    return NewTask(
        identifier=UUID(int=1),
        name="r",
        argument='{"n":1}',
        retry=TaskRetryPolicy(delays=(0.0,)),
        time_limit=None,
        origin=None,
    )


def _stored(
    status: StoredStatus,
    *results: Literal["completed", "cancelled"] | TaskFailure,
) -> StoredTask:
    return StoredTask(
        task=_task(),
        status=status,
        attempts=tuple(StoredAttempt(result=result) for result in results),
    )


_COMPLETED = StoredFinished(outcome=StoredCompleted(result="null"))
_FAILED = StoredFinished(outcome=StoredFailed())
_CANCELLED = StoredFinished(outcome=StoredCancelled())


def test_stored_task_rejects_failed_without_attempts() -> None:
    with raises(ValueError):
        StoredTask(task=_task(), status=_FAILED, attempts=())


def test_stored_task_rejects_completed_after_interrupted_last() -> None:
    with raises(ValueError):
        _stored(_COMPLETED, (TaskInterrupted()))


def test_stored_completed_rejects_text_which_is_not_an_object_or_null() -> None:
    with raises(ValueError):
        StoredCompleted(result="{")


def test_stored_cancelled_accepts_a_single_failed() -> None:
    task = _stored(_CANCELLED, _RAISED)

    assert task.status == _CANCELLED


def test_stored_task_from_json_rejects_failed_without_attempts() -> None:
    payload = json.loads(_stored(_FAILED, _RAISED).to_json())
    payload["attempts"] = []

    with raises(ValueError):
        StoredTask.from_json(json.dumps(payload))


def test_stored_task_from_json_status_outside_literal_has_validation_cause() -> None:
    payload = StoredTask(task=_task(), status=StoredQueued(), attempts=()).to_json()

    with raises(ValueError) as caught:
        StoredTask.from_json(payload.replace('"queued"', '"waiting"'))

    assert isinstance(caught.value.__cause__, ValidationError)


def test_stored_task_direct_status_outside_literal_is_a_validation_error() -> None:
    bad: Any = "waiting"

    with raises(ValidationError):
        StoredQueued(status=bad)


def test_stored_task_invariants_hold_when_nested_in_a_decoded_value() -> None:
    class Holder(State):
        task: StoredTask

    valid = Holder(task=_stored(_FAILED, _RAISED))
    assert Holder.from_json(valid.to_json()) == valid

    payload = json.loads(valid.to_json())
    payload["task"]["attempts"] = []
    with raises(ValueError):
        Holder.from_json(json.dumps(payload))


def test_stored_task_unsupported_is_accepted_only_last_of_a_failed_task() -> None:
    task = _stored(_FAILED, (TaskInterrupted()), (TaskUnsupported()))

    assert StoredTask.from_json(task.to_json()) == task


@mark.parametrize(
    "build",
    (
        # retry pending ending in unsupported
        lambda: _stored(StoredRetryPending(), (TaskUnsupported())),
        # unsupported before the last attempt
        lambda: _stored(_FAILED, (TaskUnsupported()), _RAISED),
        # unsupported in tasks which did not fail
        lambda: _stored(StoredQueued(), (TaskUnsupported())),
        lambda: _stored(StoredRunning(cancel_requested=False), (TaskUnsupported())),
        lambda: _stored(_CANCELLED, (TaskUnsupported())),
        # a retry pending task needs attempts, all failed
        lambda: _stored(StoredRetryPending()),
        lambda: _stored(StoredRetryPending(), ("cancelled")),
        # queued and running tasks only have failed attempts
        lambda: _stored(StoredQueued(), ("completed")),
        lambda: _stored(StoredRunning(cancel_requested=True), ("cancelled")),
        # completed task needs its last attempt completed, and only there
        lambda: _stored(_COMPLETED),
        lambda: _stored(_COMPLETED, _RAISED),
        lambda: _stored(_COMPLETED, ("completed"), ("completed")),
        lambda: _stored(_COMPLETED, ("completed"), _RAISED),
        # failed task needs its last attempt failed
        lambda: _stored(_FAILED, ("completed")),
        lambda: _stored(_FAILED, ("cancelled")),
        # cancelled attempt only last of a cancelled task
        lambda: _stored(_FAILED, ("cancelled"), _RAISED),
        lambda: _stored(_CANCELLED, ("cancelled"), ("cancelled")),
        lambda: _stored(_CANCELLED, ("completed")),
    ),
)
def test_stored_task_rejects_invariant_violations(build: object) -> None:
    assert callable(build)
    with raises(ValueError):
        build()


@mark.parametrize(
    "task",
    (
        _stored(StoredQueued()),
        _stored(StoredQueued(), (_RAISED), (TaskInterrupted())),
        _stored(StoredRunning(cancel_requested=True), (TaskTimeLimitExceeded(time_limit=1))),
        _stored(StoredRetryPending(), _RAISED),
        _stored(_COMPLETED, (_RAISED), ("completed")),
        _stored(_FAILED, _RAISED),
        _stored(_CANCELLED),
        _stored(_CANCELLED, (_RAISED), ("cancelled")),
        _stored(_CANCELLED, (TaskInterrupted())),
    ),
)
def test_stored_task_accepts_valid_combinations_and_round_trips(task: StoredTask) -> None:
    assert StoredTask.from_json(task.to_json()) == task


@mark.parametrize(
    "status",
    (
        StoredQueued(),
        StoredRunning(cancel_requested=True),
        StoredRetryPending(),
        _COMPLETED,
        _FAILED,
        _CANCELLED,
    ),
)
def test_stored_status_union_round_trip(status: StoredStatus) -> None:
    class Holder(State):
        status: StoredStatus

    decoded = Holder.from_json(Holder(status=status).to_json())

    assert decoded.status == status
    assert type(decoded.status) is type(status)


@mark.parametrize(
    "result",
    (
        "completed",
        "cancelled",
        _RAISED,
        TaskFailureRecord(kind="preparation", exception_type="x.Y"),
        TaskTimeLimitExceeded(time_limit=0.5),
        TaskInterrupted(),
        TaskUnsupported(),
    ),
)
def test_stored_attempt_round_trip(
    result: Literal["completed", "cancelled"] | TaskFailure,
) -> None:
    attempt = StoredAttempt(result=result)
    decoded = StoredAttempt.from_json(attempt.to_json())

    assert decoded == attempt
    assert type(decoded.result) is type(result)


@mark.parametrize("build", (AttemptCompleted, StoredCompleted))
@mark.parametrize("result", ("[]", "1", '"a"', "true", "", "{", "NaN", '{"a":Infinity}'))
def test_completed_result_must_be_an_object_or_null(
    build: type[AttemptCompleted] | type[StoredCompleted], result: str
) -> None:
    with raises(ValueError):
        build(result=result)


@mark.parametrize("build", (AttemptCompleted, StoredCompleted))
@mark.parametrize("result", ("null", "{}", '{"n":1,"nested":{"a":[1,2]}}'))
def test_completed_result_accepts_an_object_or_null(
    build: type[AttemptCompleted] | type[StoredCompleted], result: str
) -> None:
    assert build(result=result).result == result
    assert build.from_json(build(result=result).to_json()).result == result


def test_completed_result_is_checked_when_decoded() -> None:
    with raises(ValueError):
        AttemptCompleted.from_json('{"end":"completed","result":"[1]"}')


@mark.parametrize(
    "end",
    (
        AttemptCompleted(result="null"),
        AttemptRetried(failure=_RAISED, delay=0.5),
        AttemptRetried(failure=TaskInterrupted(), delay=0.0),
        AttemptFailed(failure=TaskTimeLimitExceeded(time_limit=1.0)),
        AttemptCancelled(),
    ),
)
def test_attempt_end_union_round_trip(end: AttemptEnd) -> None:
    class Holder(State):
        end: AttemptEnd

    decoded = Holder.from_json(Holder(end=end).to_json())

    assert decoded.end == end
    assert type(decoded.end) is type(end)


def test_attempt_end_cannot_carry_unsupported() -> None:
    with raises(ValueError):
        AttemptRetried.from_json('{"end":"retried","failure":{"kind":"unsupported"},"delay":1}')
    with raises(ValueError):
        AttemptFailed.from_json('{"end":"failed","failure":{"kind":"unsupported"}}')


@mark.parametrize(
    "claimed",
    (
        ClaimedAttempt(task=_task(), attempt=1),
        ClaimedInterrupted(task=_task(), attempt=2, cancel_requested=True),
    ),
)
def test_claimed_task_union_round_trip(claimed: ClaimedTask) -> None:
    class Holder(State):
        claimed: ClaimedTask

    decoded = Holder.from_json(Holder(claimed=claimed).to_json())

    assert decoded.claimed == claimed
    assert type(decoded.claimed) is type(claimed)


def test_lease_renewal_round_trip() -> None:
    leases: Mapping[UUID, LeaseState] = {uuid4(): "held", uuid4(): "cancel_requested"}
    renewal = LeaseRenewal(leases=leases)

    assert LeaseRenewal.from_json(renewal.to_json()) == renewal
    with raises(ValueError):
        LeaseRenewal.from_json(f'{{"leases":{{"{uuid4()}":"expired"}}}}')


def test_leased_coordination_round_trip() -> None:
    coordination = LeasedCoordination(timings=LeaseTimings(lease=30, renewal=10, polling=1))

    assert LeasedCoordination.from_json(coordination.to_json()) == coordination
    assert coordination.coordination == "leased"


@mark.parametrize(
    ("lease", "renewal", "polling", "setting"),
    (
        (0, 1, 1, "lease"),
        (math.inf, 1, 1, "lease"),
        (math.nan, 1, 1, "lease"),
        (-1, 1, 1, "lease"),
        (10, 10, 1, "renewal"),
        (10, 11, 1, "renewal"),
        (10, 0, 1, "renewal"),
        (10, math.nan, 1, "renewal"),
        (10, 5, 0, "polling"),
        (10, 5, math.nan, "polling"),
        (10, 5, math.inf, "polling"),
    ),
)
def test_lease_timings_reject_values_out_of_range(
    lease: float, renewal: float, polling: float, setting: str
) -> None:
    with raises(ValueError, match=setting):
        LeaseTimings(lease=lease, renewal=renewal, polling=polling)


def test_lease_timings_are_checked_when_decoded() -> None:
    with raises(ValueError, match="renewal"):
        LeaseTimings.from_json('{"lease":1,"renewal":1,"polling":1}')


def test_new_task_round_trip() -> None:
    task = _task()

    assert NewTask.from_json(task.to_json()) == task


_VALID_TASK = _stored(StoredQueued())
_BAD_UPDATES: tuple[tuple[Any, dict[str, Any]], ...] = (
    (_VALID_TASK, {"status": _COMPLETED}),
    (_VALID_TASK, {"attempts": (StoredAttempt(result="completed"),)}),
    (StoredCompleted(result="null"), {"result": "["}),
    (StoredCompleted(result="null"), {"result": "[]"}),
    (AttemptCompleted(result="null"), {"result": "1"}),
    (LeaseTimings(lease=10, renewal=5, polling=1), {"renewal": 10}),
    (LeaseTimings(lease=10, renewal=5, polling=1), {"lease": math.nan}),
    (LeaseTimings(lease=10, renewal=5, polling=1), {"polling": 0}),
    (ClaimedAttempt(task=_task(), attempt=1), {"attempt": 0}),
    (ClaimedInterrupted(task=_task(), attempt=1, cancel_requested=False), {"attempt": -1}),
    (AttemptRetried(failure=_RAISED, delay=1), {"delay": math.nan}),
    (AttemptRetried(failure=_RAISED, delay=1), {"delay": -1}),
)


@mark.parametrize(("value", "changes"), _BAD_UPDATES)
def test_updating_and_replace_are_checked(value: Any, changes: dict[str, Any]) -> None:
    with raises(ValueError):
        value.updating(**changes)
    with raises(ValueError):
        copy.replace(value, **changes)


def test_updating_and_replace_accept_valid_values() -> None:
    task = _stored(StoredQueued())

    assert task.updating(status=StoredRunning(cancel_requested=True)).status == StoredRunning(
        cancel_requested=True
    )
    assert copy.replace(
        task, status=StoredRetryPending(), attempts=(StoredAttempt(result=_RAISED),)
    ).status == (StoredRetryPending())
    assert StoredCompleted(result="null").updating(result="{}").result == "{}"
    assert ClaimedAttempt(task=_task(), attempt=1).updating(attempt=3).attempt == 3


@mark.parametrize("claimed", (0, -1))
def test_claims_reject_attempt_below_one(claimed: int) -> None:
    with raises(ValueError, match="attempt"):
        ClaimedAttempt(task=_task(), attempt=claimed)
    with raises(ValueError, match="attempt"):
        ClaimedInterrupted(task=_task(), attempt=claimed, cancel_requested=False)


@mark.parametrize("delay", (math.nan, math.inf, -0.1))
def test_attempt_retried_rejects_delay_out_of_range(delay: float) -> None:
    with raises(ValueError, match="delay"):
        AttemptRetried(failure=_RAISED, delay=delay)


def test_new_task_round_trip_with_origin() -> None:
    task = _task().updating(
        origin=ObservabilityLink(
            trace_id=UUID(int=3),
            scope_id=UUID(int=4),
            trace_context={
                "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
            },
        )
    )
    decoded = NewTask.from_json(task.to_json())

    assert decoded == task
    assert decoded.origin is not None
    assert decoded.origin.trace_id == UUID(int=3)
