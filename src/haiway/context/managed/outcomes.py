from collections.abc import Sequence

from haiway.attributes import State
from haiway.context.managed.stored import (
    StoredAttempt,
    StoredCancelled,
    StoredCompleted,
    StoredFailed,
    StoredFinished,
    StoredQueued,
    StoredRetryPending,
    StoredRunning,
    StoredTask,
)
from haiway.context.managed.types import (
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    TaskFailure,
    TaskOutcome,
)

__all__ = (
    "exception_type_name",
    "stored_outcome",
    "task_outcome",
)


def exception_type_name(
    exception: BaseException,
    /,
) -> str:
    return f"{type(exception).__module__}.{type(exception).__qualname__}"


def _decode_result(
    text: str,
    result: type[State] | type[None],
    /,
) -> State | None:
    if result is type(None):
        if text != "null":
            raise ValueError("The stored result of a task returning None has to be null")

        return None

    return result.from_json(text)


def task_outcome(
    outcome: StoredCompleted | StoredFailed | StoredCancelled,
    /,
    *,
    failure: TaskFailure | None,
    attempts: int,
    result: type[State] | type[None],
) -> TaskOutcome[State | None]:
    # derives the outcome of a finished task from what its store fixed, the one place outcomes
    # are built, so that every handle sees equal ones
    match outcome:
        case StoredCompleted(result=text):
            return TaskCompleted(_decode_result(text, result))

        case StoredFailed():
            if failure is None:
                raise ValueError("A failed task has no failure of its last attempt")

            return TaskFailed(
                failure=failure,
                attempts=attempts,
            )

        case StoredCancelled():
            return TaskCancelled()


def _last_failure(
    attempts: Sequence[StoredAttempt],
    /,
) -> TaskFailure | None:
    if not attempts:
        return None

    last: object = attempts[-1].result
    if isinstance(last, str):
        return None  # completed or cancelled

    return last


def stored_outcome(
    stored: StoredTask,
    /,
    *,
    result: type[State] | type[None],
) -> TaskOutcome[State | None] | None:
    # none while the task is not finished
    match stored.status:
        case StoredFinished(outcome=outcome):
            return task_outcome(
                outcome,
                failure=_last_failure(stored.attempts),
                attempts=len(stored.attempts),
                result=result,
            )

        case StoredQueued() | StoredRunning() | StoredRetryPending():
            return None
