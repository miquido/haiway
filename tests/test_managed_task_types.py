import copy
import math
from typing import Any

from pytest import mark, raises

from haiway import State
from haiway.context.managed import (
    AttemptFailure,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    TaskFailure,
    TaskFailureRecord,
    TaskInterrupted,
    TaskRetryPolicy,
    TaskTimeLimitExceeded,
    TaskUnsupported,
)


class Result(State):
    n: int


class AttemptFailureHolder(State):
    failure: AttemptFailure


class TaskFailureHolder(State):
    failure: TaskFailure


_ATTEMPT_FAILURES: tuple[AttemptFailure, ...] = (
    TaskFailureRecord(kind="raised", exception_type="builtins.ValueError"),
    TaskFailureRecord(kind="preparation", exception_type="builtins.ValueError"),
    TaskTimeLimitExceeded(time_limit=1.5),
    TaskInterrupted(),
)


@mark.parametrize("failure", _ATTEMPT_FAILURES)
def test_attempt_failure_union_round_trip(failure: AttemptFailure) -> None:
    decoded = AttemptFailureHolder.from_json(AttemptFailureHolder(failure=failure).to_json())

    assert decoded.failure == failure
    assert type(decoded.failure) is type(failure)


@mark.parametrize("failure", (*_ATTEMPT_FAILURES, TaskUnsupported()))
def test_task_failure_union_round_trip(failure: TaskFailure) -> None:
    decoded = TaskFailureHolder.from_json(TaskFailureHolder(failure=failure).to_json())

    assert decoded.failure == failure
    assert type(decoded.failure) is type(failure)


def test_attempt_failure_does_not_accept_unsupported() -> None:
    with raises(ValueError):
        AttemptFailureHolder.from_json('{"failure":{"kind":"unsupported"}}')


def test_failure_record_kind_is_closed() -> None:
    with raises(ValueError):
        TaskFailureHolder.from_json(
            '{"failure":{"kind":"other","exception_type":"builtins.ValueError"}}'
        )


@mark.parametrize(
    "outcome",
    (
        TaskCompleted(Result(n=5)),
        TaskFailed(failure=TaskInterrupted(), attempts=2),
        TaskCancelled(),
    ),
)
def test_outcomes_reject_attribute_assignment_and_deletion(
    outcome: TaskCompleted[Result] | TaskFailed | TaskCancelled,
) -> None:
    with raises(AttributeError):
        outcome.anything = 1
    with raises(AttributeError):
        del outcome.anything
    assert copy.copy(outcome) is outcome
    assert copy.deepcopy(outcome) is outcome


def test_completed_value_is_read_only() -> None:
    completed = TaskCompleted(Result(n=5))

    assert completed.value == Result(n=5)
    target: Any = completed
    with raises(AttributeError):
        target.value = Result(n=6)
    with raises(AttributeError):
        target._value = Result(n=6)


@mark.parametrize(
    ("left", "right", "equal"),
    (
        (TaskCompleted(Result(n=5)), TaskCompleted(Result(n=5)), True),
        (TaskCompleted(Result(n=5)), TaskCompleted(Result(n=6)), False),
        (TaskCompleted(None), TaskCompleted(None), True),
        (TaskCompleted(None), TaskCancelled(), False),
        (
            TaskFailed(failure=TaskTimeLimitExceeded(time_limit=1.0), attempts=3),
            TaskFailed(failure=TaskTimeLimitExceeded(time_limit=1.0), attempts=3),
            True,
        ),
        (
            TaskFailed(failure=TaskInterrupted(), attempts=3),
            TaskFailed(failure=TaskInterrupted(), attempts=2),
            False,
        ),
        (
            TaskFailed(failure=TaskInterrupted(), attempts=1),
            TaskFailed(failure=TaskUnsupported(), attempts=1),
            False,
        ),
        (TaskCancelled(), TaskCancelled(), True),
    ),
)
def test_outcomes_compare_by_content(left: object, right: object, equal: bool) -> None:
    assert (left == right) is equal
    if equal:
        assert hash(left) == hash(right)


def test_task_failed_requires_an_attempt() -> None:
    with raises(ValueError):
        TaskFailed(failure=TaskInterrupted(), attempts=0)


def test_task_failed_keeps_its_data() -> None:
    failed = TaskFailed(
        failure=TaskFailureRecord(kind="raised", exception_type="builtins.ValueError"),
        attempts=1,
    )

    assert failed.attempts == 1
    assert failed.failure == TaskFailureRecord(kind="raised", exception_type="builtins.ValueError")


def test_retry_policy_defaults_to_no_retry() -> None:
    assert TaskRetryPolicy().delays == ()


def test_retry_policy_round_trip() -> None:
    policy = TaskRetryPolicy(delays=(0.0, 0.05, 1))

    assert TaskRetryPolicy.from_json(policy.to_json()) == policy


@mark.parametrize(
    "delays",
    (
        (math.nan,),
        (0.1, math.inf),
        (-0.1,),
    ),
)
def test_retry_policy_rejects_delays_out_of_range(delays: tuple[float, ...]) -> None:
    with raises(ValueError, match="delays"):
        TaskRetryPolicy(delays=delays)


def test_retry_policy_rejects_delays_out_of_range_when_decoded() -> None:
    with raises(ValueError, match="delays"):
        TaskRetryPolicy.from_json('{"delays":[-1]}')


@mark.parametrize(
    "outcome",
    (
        TaskCompleted(Result(n=1)),
        TaskFailed(failure=TaskUnsupported(), attempts=1),
        TaskCancelled(),
    ),
)
def test_outcomes_have_a_readable_form(outcome: object) -> None:
    assert type(outcome).__name__ in repr(outcome)


@mark.parametrize("delays", ((math.nan,), (-1.0,), (0.1, math.inf)))
def test_retry_policy_updating_and_replace_are_checked(delays: tuple[float, ...]) -> None:
    policy = TaskRetryPolicy(delays=(0.1,))

    with raises(ValueError, match="delays"):
        policy.updating(delays=delays)
    with raises(ValueError, match="delays"):
        copy.replace(policy, delays=delays)


def test_retry_policy_updating_and_replace_accept_valid_values() -> None:
    policy = TaskRetryPolicy(delays=(0.1,))

    assert policy.updating(delays=(0.2, 0.3)).delays == (0.2, 0.3)
    assert copy.replace(policy, delays=()).delays == ()


def test_failed_outcomes_which_are_equal_hash_alike() -> None:
    left = TaskFailed(failure=TaskTimeLimitExceeded(time_limit=0.0), attempts=1)
    right = TaskFailed(failure=TaskTimeLimitExceeded(time_limit=-0.0), attempts=1)

    assert left == right
    assert hash(left) == hash(right)


def test_failed_outcome_with_nan_limit_is_hashable() -> None:
    assert isinstance(
        hash(TaskFailed(failure=TaskTimeLimitExceeded(time_limit=math.nan), attempts=1)), int
    )
