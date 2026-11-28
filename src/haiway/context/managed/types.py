from collections.abc import Sequence
from math import isfinite
from typing import TYPE_CHECKING, Any, Literal, Self, cast, final

from haiway.attributes import State
from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = (
    "AttemptFailure",
    "TaskCancellation",
    "TaskCancelled",
    "TaskCompleted",
    "TaskFailed",
    "TaskFailure",
    "TaskFailureRecord",
    "TaskInterrupted",
    "TaskOutcome",
    "TaskRetryPolicy",
    "TaskStatus",
    "TaskTimeLimitExceeded",
    "TaskUnsupported",
)


def _check_delays(delays: Sequence[float]) -> None:
    for delay in delays:
        if not isfinite(delay) or delay < 0:
            raise ValueError(f"TaskRetryPolicy delays must be finite and at least 0: {delay}")


type TaskStatus = Literal["queued", "running", "retry_pending", "finished"]

type TaskCancellation = Literal["cancelled", "pending", "finished"]


@final
class TaskFailureRecord(State):
    """
    Failure of an attempt which raised or could not be prepared.

    Parameters
    ----------
    kind : Literal["raised", "preparation"]
        ``raised`` when the function, or the exit of its attempt scope, raised,
        ``preparation`` when preparing the attempt raised.
    exception_type : str
        ``__module__`` and ``__qualname__`` of the exception joined by ``.``. Neither the
        message nor the traceback of the exception is recorded.
    """

    kind: Literal["raised", "preparation"]
    exception_type: str


@final
class TaskTimeLimitExceeded(State):
    """
    Failure of an attempt whose time limit elapsed first.

    Parameters
    ----------
    time_limit : float
        The limit in seconds, as given at spawn.
    """

    kind: Literal["time_limit"] = "time_limit"
    time_limit: float


@final
class TaskInterrupted(State):
    """Failure of an attempt whose runtime stopped without closing. Carries nothing."""

    kind: Literal["interrupted"] = "interrupted"


@final
class TaskUnsupported(State):
    """
    Failure of a task whose registered name the failing runtime does not list.

    Only the attempt recorded by a store when it fails an unsupported task ends with it,
    it is never retried. Carries nothing, the name being the task's.
    """

    kind: Literal["unsupported"] = "unsupported"


type AttemptFailure = TaskFailureRecord | TaskTimeLimitExceeded | TaskInterrupted

type TaskFailure = AttemptFailure | TaskUnsupported


@final
class TaskCompleted[Result: State | None]:
    """
    Outcome of a task whose attempt returned.

    Parameters
    ----------
    value : Result
        The result decoded from the store. Read only, which keeps ``Result`` covariant.
    """

    __slots__ = ("_value",)

    def __init__(
        self,
        value: Result,
        /,
    ) -> None:
        self._value: Result
        object.__setattr__(
            self,
            "_value",
            value,
        )

    @property
    def value(self) -> Result:
        """The result decoded from the store."""
        return self._value

    def __eq__(
        self,
        other: object,
    ) -> bool:
        if not isinstance(other, TaskCompleted):
            return NotImplemented

        other_value: object = cast(TaskCompleted[State | None], other).value
        return self._value == other_value

    def __hash__(self) -> int:
        return hash((TaskCompleted, self._value))

    def __str__(self) -> str:
        return f"TaskCompleted(value: {self._value})"

    def __repr__(self) -> str:
        return str(self)

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


@final
class TaskFailed:
    """
    Outcome of a task whose last attempt failed with no retry remaining, or which
    a runtime failed as unsupported.

    Parameters
    ----------
    failure : TaskFailure
        The failure of the last attempt.
    attempts : int
        The number of counted attempts, at least 1.

    Raises
    ------
    ValueError
        When ``attempts`` is below 1.
    """

    __slots__ = (
        "attempts",
        "failure",
    )

    def __init__(
        self,
        *,
        failure: TaskFailure,
        attempts: int,
    ) -> None:
        if attempts < 1:
            raise ValueError("TaskFailed attempts must be at least 1")

        self.failure: TaskFailure
        object.__setattr__(
            self,
            "failure",
            failure,
        )
        self.attempts: int
        object.__setattr__(
            self,
            "attempts",
            attempts,
        )

    def __eq__(
        self,
        other: object,
    ) -> bool:
        if not isinstance(other, TaskFailed):
            return NotImplemented

        return self.failure == other.failure and self.attempts == other.attempts

    def __hash__(self) -> int:
        return hash((TaskFailed, self.attempts, self.failure))

    def __str__(self) -> str:
        return f"TaskFailed(failure: {self.failure}, attempts: {self.attempts})"

    def __repr__(self) -> str:
        return str(self)

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


@final
class TaskCancelled:
    """Outcome of a task cancelled through ``cancel()``. Carries nothing."""

    __slots__ = ()

    def __eq__(
        self,
        other: object,
    ) -> bool:
        return isinstance(other, TaskCancelled)

    def __hash__(self) -> int:
        return hash(TaskCancelled)

    def __str__(self) -> str:
        return "TaskCancelled()"

    def __repr__(self) -> str:
        return str(self)

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


type TaskOutcome[Result: State | None] = TaskCompleted[Result] | TaskFailed | TaskCancelled


@final
class TaskRetryPolicy(State):
    """
    The delays before each retry of a task, its length being the number of retries.


    Parameters
    ----------
    delays : Sequence[float], default=()
        Seconds before retry 1, 2, ... Every element has to be finite and at least 0.

    Raises
    ------
    ValueError
        When an element of ``delays`` is not finite or is below 0.
    """

    delays: Sequence[float] = ()

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_delays(self.delays)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_delays(updated.delays)
            return updated

        __replace__ = updating
