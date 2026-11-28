from collections.abc import Set
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal, Self, final
from uuid import UUID

from haiway.attributes import State
from haiway.utils.pagination import PaginationToken

__all__ = (
    "TaskFilter",
    "TaskKeyset",
    "TaskStatusFilter",
    "TaskSummary",
    "TaskSummaryFinished",
    "TaskSummaryUnfinished",
    "TaskTimeBefore",
    "TaskTimeBetween",
    "TaskTimeFrom",
    "TaskTimeRange",
    "TasksFinished",
    "TasksUnfinished",
    "decode_task_page_token",
    "encode_task_page_token",
)


def _require_aware(
    value: datetime,
    *,
    name: str,
    owner: str,
) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{owner} {name} has to be timezone-aware")


def _check_between(
    start: datetime,
    end: datetime,
) -> None:
    _require_aware(start, name="start", owner="TaskTimeBetween")
    _require_aware(end, name="end", owner="TaskTimeBetween")
    if start >= end:
        raise ValueError("TaskTimeBetween start has to be earlier than end")


def _check_summary(
    status: TaskSummaryUnfinished | TaskSummaryFinished,
    attempts: int,
    created: datetime,
) -> None:
    _require_aware(created, name="created", owner="TaskSummary")
    if attempts < 0:
        raise ValueError(f"TaskSummary attempts has to be at least 0: {attempts}")

    if attempts == 0:
        match status:
            case TaskSummaryUnfinished():
                if status.status == "retry_pending":
                    raise ValueError("A retry pending TaskSummary has at least 1 attempt")

            case TaskSummaryFinished():
                if status.outcome != "cancelled":
                    raise ValueError("A completed or failed TaskSummary has at least 1 attempt")


@final
class TaskTimeFrom(State):
    """
    Time range from a moment on.


    Parameters
    ----------
    start : datetime
        Inclusive bound, timezone-aware.

    Raises
    ------
    ValueError
        When ``start`` is naive.
    """

    bound: Literal["from"] = "from"
    start: datetime

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _require_aware(self.start, name="start", owner="TaskTimeFrom")

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _require_aware(updated.start, name="start", owner="TaskTimeFrom")
            return updated

        __replace__ = updating


@final
class TaskTimeBefore(State):
    """
    Time range up to a moment.


    Parameters
    ----------
    end : datetime
        Exclusive bound, timezone-aware.

    Raises
    ------
    ValueError
        When ``end`` is naive.
    """

    bound: Literal["before"] = "before"
    end: datetime

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _require_aware(self.end, name="end", owner="TaskTimeBefore")

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _require_aware(updated.end, name="end", owner="TaskTimeBefore")
            return updated

        __replace__ = updating


@final
class TaskTimeBetween(State):
    """
    Time range between two moments.


    Parameters
    ----------
    start : datetime
        Inclusive bound, timezone-aware.
    end : datetime
        Exclusive bound, timezone-aware, later than ``start``.

    Raises
    ------
    ValueError
        When a bound is naive or ``start`` is not earlier than ``end``.
    """

    bound: Literal["between"] = "between"
    start: datetime
    end: datetime

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_between(self.start, self.end)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_between(updated.start, updated.end)
            return updated

        __replace__ = updating


type TaskTimeRange = TaskTimeFrom | TaskTimeBefore | TaskTimeBetween


@final
class TasksUnfinished(State):
    """
    Filter for tasks which are not finished.

    Parameters
    ----------
    statuses : Set[Literal["queued", "running", "retry_pending"]]
        The statuses to match. An empty set matches no task.
    """

    status: Literal["unfinished"] = "unfinished"
    statuses: Set[Literal["queued", "running", "retry_pending"]]


@final
class TasksFinished(State):
    """
    Filter for finished tasks.

    Parameters
    ----------
    outcomes : Set[Literal["completed", "failed", "cancelled"]]
        The outcomes to match. An empty set matches no task.
    finished : TaskTimeRange | None, default=None
        Range of the finish time. When absent, any finish time matches.
    """

    status: Literal["finished"] = "finished"
    outcomes: Set[Literal["completed", "failed", "cancelled"]]
    finished: TaskTimeRange | None = None


type TaskStatusFilter = TasksUnfinished | TasksFinished


@final
class TaskFilter(State):
    """
    Conditions a listed task has to meet, every given part at once.

    Creation and finish times are the listing timestamps of the store, which with a
    database store is the clock of the database server and can differ from the caller's.

    Parameters
    ----------
    names : Set[str] | None, default=None
        Registered names. When absent, every name matches.
    status : TaskStatusFilter | None, default=None
        Status or outcome. When absent, every status matches.
    created : TaskTimeRange | None, default=None
        Creation time. When absent, any time matches.

    Notes
    -----
    An empty set in ``names``, ``statuses`` or ``outcomes`` matches no task.
    """

    names: Set[str] | None = None
    status: TaskStatusFilter | None = None
    created: TaskTimeRange | None = None


@final
class TaskSummaryUnfinished(State):
    """
    Status of a listed task which is not finished.

    Parameters
    ----------
    status : Literal["queued", "running", "retry_pending"]
        The status of the task.
    """

    status: Literal["queued", "running", "retry_pending"]


@final
class TaskSummaryFinished(State):
    """
    Finished status of a listed task.


    Parameters
    ----------
    outcome : Literal["completed", "failed", "cancelled"]
        The kind of the outcome.
    finished : datetime
        Finish time as the listing timestamp of the store, timezone-aware.

    Raises
    ------
    ValueError
        When ``finished`` is naive.
    """

    status: Literal["finished"] = "finished"
    outcome: Literal["completed", "failed", "cancelled"]
    finished: datetime

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _require_aware(self.finished, name="finished", owner="TaskSummaryFinished")

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _require_aware(updated.finished, name="finished", owner="TaskSummaryFinished")
            return updated

        __replace__ = updating


@final
class TaskSummary(State):
    """
    What a listing returns per task, and nothing else of the task.


    Parameters
    ----------
    identifier : UUID
        Identifier of the task.
    name : str
        Registered name of the task.
    status : TaskSummaryUnfinished | TaskSummaryFinished
        Status, with the outcome kind and the finish time when finished.
    attempts : int
        The number of counted attempts, at least 0.
    created : datetime
        Creation time as the listing timestamp of the store, timezone-aware.

    Raises
    ------
    ValueError
        When ``created`` is naive, ``attempts`` is below 0, or ``attempts`` is 0 with a
        status of ``retry_pending`` or finished with outcome ``completed`` or ``failed``.
    """

    identifier: UUID
    name: str
    status: TaskSummaryUnfinished | TaskSummaryFinished
    attempts: int
    created: datetime

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _check_summary(self.status, self.attempts, self.created)

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _check_summary(updated.status, updated.attempts, updated.created)
            return updated

        __replace__ = updating


@final
class TaskKeyset(State):
    """
    Position of a task in the listing order, which as JSON is the page token.


    Parameters
    ----------
    created : datetime
        Creation time of the task, timezone-aware.
    identifier : UUID
        Identifier of the task.

    Raises
    ------
    ValueError
        When ``created`` is naive.
    """

    created: datetime
    identifier: UUID

    if not TYPE_CHECKING:

        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            _require_aware(self.created, name="created", owner="TaskKeyset")

        def updating(self, **kwargs: Any) -> Self:
            """Copy with the updates applied, which re-runs the construction checks."""
            updated = super().updating(**kwargs)
            _require_aware(updated.created, name="created", owner="TaskKeyset")
            return updated

        __replace__ = updating


def encode_task_page_token(
    keyset: TaskKeyset,
    /,
) -> str:
    return keyset.to_json()


def decode_task_page_token(
    token: PaginationToken | None,
    /,
) -> TaskKeyset | None:
    if token is None:
        return None

    if not isinstance(token, str):
        raise ValueError("Task page token has to be a string")

    return TaskKeyset.from_json(token)
