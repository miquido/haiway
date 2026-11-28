import copy
import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any, Literal
from uuid import UUID

from pytest import mark, raises

from haiway import State
from haiway.context.managed import (
    TaskFilter,
    TaskKeyset,
    TasksFinished,
    TaskStatusFilter,
    TaskSummary,
    TaskSummaryFinished,
    TaskSummaryUnfinished,
    TasksUnfinished,
    TaskTimeBefore,
    TaskTimeBetween,
    TaskTimeFrom,
    TaskTimeRange,
)
from haiway.context.managed.listing import decode_task_page_token, encode_task_page_token

_T = datetime(2026, 1, 1, 12, 0, 0, 123456, tzinfo=UTC)
_NAIVE = datetime(2026, 1, 1)


@mark.parametrize(
    "build",
    (
        lambda: TaskTimeFrom(start=_NAIVE),
        lambda: TaskTimeBefore(end=_NAIVE),
        lambda: TaskTimeBetween(start=_NAIVE, end=_T),
        lambda: TaskTimeBetween(start=_T, end=_NAIVE),
        lambda: TaskTimeBetween(start=_T, end=_T),
        lambda: TaskTimeBetween(start=_T + timedelta(seconds=1), end=_T),
    ),
)
def test_time_ranges_reject_naive_and_empty_ranges(build: object) -> None:
    assert callable(build)
    with raises(ValueError):
        build()


@mark.parametrize(
    "payload",
    (
        '{"bound":"from","start":"2026-01-01T00:00:00"}',
        '{"bound":"before","end":"2026-01-01T00:00:00"}',
        '{"bound":"between","start":"2026-01-01T00:00:00","end":"2026-01-02T00:00:00+00:00"}',
        '{"bound":"between","start":"2026-01-01T00:00:00+00:00","end":"2026-01-01T00:00:00+00:00"}',
    ),
)
def test_time_ranges_are_checked_when_decoded(payload: str) -> None:
    class Holder(State):
        range: TaskTimeRange

    with raises(ValueError):
        Holder.from_json(f'{{"range":{payload}}}')


def test_time_range_with_other_offset_is_aware() -> None:
    zone = timezone(timedelta(hours=2))

    assert TaskTimeFrom(start=datetime(2026, 1, 1, tzinfo=zone)).start.utcoffset() is not None


@mark.parametrize(
    "time_range",
    (
        TaskTimeFrom(start=_T),
        TaskTimeBefore(end=_T),
        TaskTimeBetween(start=_T, end=_T + timedelta(microseconds=1)),
    ),
)
def test_time_range_union_round_trip(time_range: TaskTimeRange) -> None:
    class Holder(State):
        range: TaskTimeRange

    decoded = Holder.from_json(Holder(range=time_range).to_json())

    assert decoded.range == time_range
    assert type(decoded.range) is type(time_range)


@mark.parametrize(
    "status",
    (
        TasksUnfinished(statuses={"queued", "retry_pending"}),
        TasksUnfinished(statuses=set()),
        TasksFinished(outcomes={"failed"}),
        TasksFinished(outcomes={"completed", "cancelled"}, finished=TaskTimeFrom(start=_T)),
        TasksFinished(outcomes=set(), finished=TaskTimeBefore(end=_T)),
        TasksFinished(
            outcomes={"failed"},
            finished=TaskTimeBetween(start=_T, end=_T + timedelta(days=1)),
        ),
    ),
)
def test_status_filter_union_round_trip(status: TaskStatusFilter) -> None:
    decoded = TaskFilter.from_json(TaskFilter(status=status).to_json())

    assert decoded.status == status
    assert type(decoded.status) is type(status)
    if isinstance(status, TasksFinished):
        assert isinstance(decoded.status, TasksFinished)
        assert type(decoded.status.finished) is type(status.finished)


def test_task_filter_defaults_match_everything() -> None:
    matching = TaskFilter()

    assert matching.names is None
    assert matching.status is None
    assert matching.created is None
    assert TaskFilter.from_json(matching.to_json()) == matching


def test_task_filter_empty_sets_are_kept() -> None:
    matching = TaskFilter(names=set(), status=TasksUnfinished(statuses=set()))
    decoded = TaskFilter.from_json(matching.to_json())

    assert decoded.names == frozenset()
    assert decoded == matching
    assert decoded.names is not None


def test_task_filter_full_round_trip() -> None:
    matching = TaskFilter(
        names={"a", "b"},
        status=TasksFinished(outcomes={"failed"}, finished=TaskTimeFrom(start=_T)),
        created=TaskTimeBetween(start=_T, end=_T + timedelta(hours=1)),
    )

    assert TaskFilter.from_json(matching.to_json()) == matching


def test_status_filters_have_distinct_tags() -> None:
    with raises(ValueError):
        TaskFilter.from_json('{"status":{"status":"running","statuses":["queued"]}}')


def _finished(
    outcome: Literal["completed", "failed", "cancelled"] = "completed",
) -> TaskSummaryFinished:
    return TaskSummaryFinished(outcome=outcome, finished=_T)


def test_summary_of_a_retry_pending_task() -> None:
    summary = TaskSummary(
        identifier=UUID(int=1),
        name="r",
        status=TaskSummaryUnfinished(status="retry_pending"),
        attempts=1,
        created=_T,
    )

    assert summary.attempts == 1
    assert TaskSummary.from_json(summary.to_json()) == summary


def test_summary_of_a_completed_task() -> None:
    summary = TaskSummary(
        identifier=UUID(int=1),
        name="r",
        status=_finished(),
        attempts=2,
        created=_T,
    )
    decoded = TaskSummary.from_json(summary.to_json())

    assert decoded == summary
    assert isinstance(decoded.status, TaskSummaryFinished)
    assert decoded.status.finished == _T


@mark.parametrize(
    "status",
    (
        TaskSummaryUnfinished(status="queued"),
        TaskSummaryUnfinished(status="running"),
        TaskSummaryUnfinished(status="retry_pending"),
        TaskSummaryFinished(outcome="completed", finished=_T),
        TaskSummaryFinished(outcome="failed", finished=_T),
        TaskSummaryFinished(outcome="cancelled", finished=_T),
    ),
)
def test_summary_status_union_round_trip(
    status: TaskSummaryUnfinished | TaskSummaryFinished,
) -> None:
    summary = TaskSummary(identifier=UUID(int=1), name="r", status=status, attempts=1, created=_T)
    decoded = TaskSummary.from_json(summary.to_json())

    assert decoded == summary
    assert type(decoded.status) is type(status)


@mark.parametrize(
    "status",
    (
        TaskSummaryUnfinished(status="queued"),
        TaskSummaryUnfinished(status="running"),
        TaskSummaryFinished(outcome="cancelled", finished=_T),
    ),
)
def test_summary_without_attempts_is_accepted_for_queued_running_and_cancelled(
    status: TaskSummaryUnfinished | TaskSummaryFinished,
) -> None:
    summary = TaskSummary(identifier=UUID(int=1), name="r", status=status, attempts=0, created=_T)

    assert TaskSummary.from_json(summary.to_json()) == summary


@mark.parametrize(
    ("status", "attempts", "created"),
    (
        (TaskSummaryUnfinished(status="queued"), -1, _T),
        (TaskSummaryUnfinished(status="retry_pending"), 0, _T),
        (TaskSummaryFinished(outcome="failed", finished=_T), 0, _T),
        (TaskSummaryFinished(outcome="completed", finished=_T), 0, _T),
        (TaskSummaryUnfinished(status="queued"), 0, _NAIVE),
    ),
)
def test_summary_rejects_invariant_violations(
    status: TaskSummaryUnfinished | TaskSummaryFinished, attempts: int, created: datetime
) -> None:
    with raises(ValueError):
        TaskSummary(
            identifier=UUID(int=1), name="r", status=status, attempts=attempts, created=created
        )


def test_summary_finished_rejects_naive_finish_time() -> None:
    with raises(ValueError):
        TaskSummaryFinished(outcome="failed", finished=_NAIVE)


def test_summary_invariants_are_checked_when_decoded() -> None:
    summary = TaskSummary(
        identifier=UUID(int=1),
        name="r",
        status=TaskSummaryUnfinished(status="queued"),
        attempts=0,
        created=_T,
    )
    payload = json.loads(summary.to_json())

    with raises(ValueError):
        TaskSummary.from_json(json.dumps({**payload, "attempts": -1}))
    with raises(ValueError):
        TaskSummary.from_json(json.dumps({**payload, "created": "2026-01-01T00:00:00"}))
    with raises(ValueError):
        TaskSummary.from_json(
            json.dumps({**payload, "status": {"status": "retry_pending"}, "attempts": 0})
        )


def test_summary_holds_nothing_but_listing_data() -> None:
    summary = TaskSummary(
        identifier=UUID(int=1),
        name="r",
        status=TaskSummaryUnfinished(status="queued"),
        attempts=0,
        created=_T,
    )

    assert set(json.loads(summary.to_json())) == {
        "identifier",
        "name",
        "status",
        "attempts",
        "created",
    }


def test_keyset_round_trip_is_the_page_token() -> None:
    keyset = TaskKeyset(created=_T, identifier=UUID(int=7))
    token = encode_task_page_token(keyset)

    assert token == keyset.to_json()
    assert decode_task_page_token(token) == keyset


def test_keyset_keeps_microseconds_and_offset() -> None:
    zone = timezone(timedelta(hours=-5))
    keyset = TaskKeyset(
        created=datetime(2026, 3, 4, 5, 6, 7, 8, tzinfo=zone), identifier=UUID(int=7)
    )
    decoded = decode_task_page_token(encode_task_page_token(keyset))

    assert decoded is not None
    assert decoded.created == keyset.created
    assert decoded.created.microsecond == 8


def test_page_token_none_decodes_to_no_position() -> None:
    assert decode_task_page_token(None) is None


def test_keyset_rejects_naive_time() -> None:
    with raises(ValueError):
        TaskKeyset(created=_NAIVE, identifier=UUID(int=7))


@mark.parametrize(
    "token",
    (
        "",
        "not json",
        "[]",
        "{}",
        '{"created":"2026-01-01T00:00:00","identifier":"00000000-0000-0000-0000-000000000007"}',
        '{"created":"2026-01-01T00:00:00+00:00","identifier":"nope"}',
        UUID(int=1),
        12,
    ),
)
def test_page_token_that_is_not_a_keyset_is_rejected(token: str | UUID | int) -> None:
    with raises(ValueError):
        decode_task_page_token(token)


_NAIVE_UPDATES: tuple[tuple[Any, dict[str, Any]], ...] = (
    (TaskTimeFrom(start=_T), {"start": _NAIVE}),
    (TaskTimeBefore(end=_T), {"end": _NAIVE}),
    (TaskTimeBetween(start=_T, end=_T + timedelta(days=1)), {"start": _NAIVE}),
    (TaskTimeBetween(start=_T, end=_T + timedelta(days=1)), {"end": _T}),
    (TaskTimeBetween(start=_T, end=_T + timedelta(days=1)), {"start": _T + timedelta(days=2)}),
    (TaskSummaryFinished(outcome="failed", finished=_T), {"finished": _NAIVE}),
    (TaskKeyset(created=_T, identifier=UUID(int=1)), {"created": _NAIVE}),
    (
        TaskSummary(
            identifier=UUID(int=1),
            name="r",
            status=TaskSummaryUnfinished(status="queued"),
            attempts=1,
            created=_T,
        ),
        {"attempts": -5},
    ),
    (
        TaskSummary(
            identifier=UUID(int=1),
            name="r",
            status=TaskSummaryUnfinished(status="queued"),
            attempts=1,
            created=_T,
        ),
        {"status": TaskSummaryUnfinished(status="retry_pending"), "attempts": 0},
    ),
    (
        TaskSummary(
            identifier=UUID(int=1),
            name="r",
            status=TaskSummaryUnfinished(status="queued"),
            attempts=1,
            created=_T,
        ),
        {"created": _NAIVE},
    ),
)


@mark.parametrize(("value", "changes"), _NAIVE_UPDATES)
def test_updating_and_replace_are_checked(value: Any, changes: dict[str, Any]) -> None:
    with raises(ValueError):
        value.updating(**changes)
    with raises(ValueError):
        copy.replace(value, **changes)


def test_updating_and_replace_accept_valid_values() -> None:
    keyset = TaskKeyset(created=_T, identifier=UUID(int=1))
    later = _T + timedelta(seconds=1)

    assert keyset.updating(created=later).created == later
    assert copy.replace(keyset, created=later).created == later
    assert TaskTimeFrom(start=_T).updating(start=later).start == later
