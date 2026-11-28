from collections.abc import AsyncGenerator, Generator, Mapping, Sequence, Set
from contextlib import AbstractAsyncContextManager, asynccontextmanager, contextmanager
from re import sub
from typing import Final, Literal, final
from uuid import UUID

from haiway.context import ctx
from haiway.context.managed.listing import (
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
from haiway.context.managed.store import TaskStoreSession
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
)
from haiway.context.managed.types import (
    AttemptFailure,
    TaskCancellation,
    TaskFailure,
    TaskFailureRecord,
    TaskInterrupted,
    TaskRetryPolicy,
    TaskTimeLimitExceeded,
    TaskUnsupported,
)
from haiway.context.observability import ObservabilityLink
from haiway.postgres.client import PostgresConnectionPool
from haiway.postgres.state import (
    MIGRATIONS_ADVISORY_LOCK_STATEMENT,
    MIGRATIONS_ADVISORY_UNLOCK_STATEMENT,
    MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT,
    MIGRATIONS_LOCK_TIMEOUT_STATEMENT,
    PostgresConnection,
)
from haiway.postgres.types import (
    PostgresErrorCode,
    PostgresException,
    PostgresRow,
    PostgresValue,
)

__all__ = ("PostgresTaskStore",)

TASKS_MIGRATIONS_ADVISORY_LOCK_KEY: Final[int] = 1264456024

TASKS_MIGRATIONS_TABLE_CREATE_STATEMENT: Final[str] = """\
CREATE TABLE IF NOT EXISTS haiway_managed_tasks_migrations (
    version INTEGER PRIMARY KEY CHECK (version >= 0),
    executed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);\
"""
TASKS_MIGRATIONS_VERSIONS_STATEMENT: Final[str] = """\
SELECT version FROM haiway_managed_tasks_migrations ORDER BY version;\
"""
TASKS_MIGRATIONS_LATEST_STATEMENT: Final[str] = """\
SELECT max(version) AS version FROM haiway_managed_tasks_migrations;\
"""
TASKS_MIGRATION_COMPLETION_STATEMENT: Final[str] = """\
INSERT INTO haiway_managed_tasks_migrations (version) VALUES ($1::INTEGER);\
"""

# the version table is created by `migrate` itself, before any of these run
TASKS_SCHEMA_VERSION_1: Final[str] = """\
CREATE TABLE haiway_managed_tasks (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (name <> ''),
    argument JSONB NOT NULL CHECK (jsonb_typeof(argument) = 'object'),
    time_limit DOUBLE PRECISION CHECK (time_limit > 0 AND time_limit < 'Infinity'),
    retry_delays DOUBLE PRECISION[] NOT NULL
        CHECK (
            array_position(retry_delays, NULL) IS NULL
            AND 0 <= ALL (retry_delays)
            AND 'Infinity' > ALL (retry_delays)
        ),
    origin JSONB CHECK (jsonb_typeof(origin) = 'object'),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'retry_pending', 'finished')),
    due_at TIMESTAMPTZ,
    lease_owner UUID,
    lease_expires_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    outcome TEXT CHECK (outcome IN ('completed', 'failed', 'cancelled')),
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    CHECK ((status = 'finished') = (due_at IS NULL)),
    CHECK ((status = 'running') = (lease_owner IS NOT NULL)),
    CHECK ((status = 'running') = (lease_expires_at IS NOT NULL)),
    CHECK ((status = 'running') = (started_at IS NOT NULL)),
    CHECK (cancel_requested_at IS NULL OR status = 'running'),
    CHECK ((status = 'finished') = (outcome IS NOT NULL)),
    CHECK ((status = 'finished') = (finished_at IS NOT NULL)),
    CHECK ((outcome IS NOT DISTINCT FROM 'completed') = (result IS NOT NULL))
);

CREATE INDEX haiway_managed_tasks_open ON haiway_managed_tasks (status, due_at)
    WHERE status IN ('queued', 'retry_pending');

CREATE INDEX haiway_managed_tasks_leases ON haiway_managed_tasks (lease_expires_at)
    WHERE status = 'running';

CREATE INDEX haiway_managed_tasks_created ON haiway_managed_tasks (created_at, id);

CREATE INDEX haiway_managed_tasks_names ON haiway_managed_tasks (name, created_at, id);

CREATE TABLE haiway_managed_task_attempts (
    task_id UUID NOT NULL REFERENCES haiway_managed_tasks (id) ON DELETE CASCADE,
    number INTEGER NOT NULL CHECK (number >= 1),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL CHECK (ended_at >= started_at),
    result TEXT NOT NULL CHECK (result IN ('completed', 'failed', 'cancelled')),
    failure_kind TEXT
        CHECK (
            failure_kind IN ('raised', 'preparation', 'time_limit', 'interrupted', 'unsupported')
        ),
    exception_type TEXT,
    time_limit DOUBLE PRECISION,
    PRIMARY KEY (task_id, number),
    CHECK ((result = 'failed') = (failure_kind IS NOT NULL)),
    CHECK (((failure_kind IN ('raised', 'preparation')) IS TRUE) = (exception_type IS NOT NULL)),
    CHECK (((failure_kind = 'time_limit') IS TRUE) = (time_limit IS NOT NULL))
);\
"""

# every schema version by number, the latest one is the highest
TASKS_MIGRATIONS: Final[Mapping[int, str]] = {1: TASKS_SCHEMA_VERSION_1}
TASKS_LATEST_VERSION: Final[int] = max(TASKS_MIGRATIONS)


_INTERVAL: Final[str] = "make_interval(secs => $@parameter::DOUBLE PRECISION)"


def _sql(template: str) -> str:
    # expands `@interval(n)`, the duration in seconds given as the numbered parameter
    return sub(
        r"@interval\((\d+)\)",
        lambda match: _INTERVAL.replace("@parameter", match.group(1)),
        template,
    )


# $1 id, $2 name, $3 argument, $4 time limit, $5 retry delays, $6 origin
TASK_INSERT_STATEMENT: Final[str] = """\
INSERT INTO haiway_managed_tasks (
    id, name, argument, time_limit, retry_delays, origin, status, due_at, created_at
)
VALUES (
    $1::UUID, $2::TEXT, $3::JSONB, $4::DOUBLE PRECISION, $5::DOUBLE PRECISION[], $6::JSONB,
    'queued', statement_timestamp(), statement_timestamp()
);\
"""

# $1 names, $2 limit (NULL: all), $3 runner, $4 lease seconds
TASK_CLAIM_STATEMENT: Final[str] = _sql("""\
WITH due AS (
    SELECT id FROM haiway_managed_tasks
    WHERE status IN ('queued', 'retry_pending')
      AND due_at <= statement_timestamp()
      AND name = ANY($1::TEXT[])
    ORDER BY due_at, created_at
    LIMIT $2::BIGINT
    FOR UPDATE SKIP LOCKED
),
expired AS (
    SELECT id FROM haiway_managed_tasks
    WHERE status = 'running'
      AND lease_expires_at < statement_timestamp()
      AND name = ANY($1::TEXT[])
    FOR UPDATE SKIP LOCKED
),
claimed AS (
    SELECT id, 'attempt' AS claim FROM due
    UNION
    SELECT id, 'interrupted' FROM expired
)
UPDATE haiway_managed_tasks AS t
SET status = 'running',
    lease_owner = $3::UUID,
    lease_expires_at = statement_timestamp() + @interval(4),
    started_at = CASE
        WHEN claimed.claim = 'attempt' THEN statement_timestamp() ELSE t.started_at
    END,
    cancel_requested_at = CASE
        WHEN claimed.claim = 'attempt' THEN NULL ELSE t.cancel_requested_at
    END
FROM claimed
WHERE t.id = claimed.id
RETURNING claimed.claim, t.id, t.name, t.argument::TEXT AS argument, t.time_limit,
    t.retry_delays, t.origin::TEXT AS origin,
    t.cancel_requested_at IS NOT NULL AS cancel_requested,
    t.attempts + 1 AS attempt;\
""")

# $1 runner, $2 identifiers, $3 lease seconds
TASK_RENEW_STATEMENT: Final[str] = _sql("""\
UPDATE haiway_managed_tasks
SET lease_expires_at = statement_timestamp() + @interval(3)
WHERE status = 'running'
  AND lease_owner = $1::UUID
  AND id = ANY($2::UUID[])
RETURNING id, cancel_requested_at IS NOT NULL AS cancel_requested;\
""")

# $1 runner, $2 identifier, $3 attempt, $4 end ('completed', 'retried', 'failed', 'cancelled'),
# $5 result JSON, $6 retry delay seconds, $7 failure kind, $8 exception type, $9 failure time limit
TASK_RECORD_STATEMENT: Final[str] = _sql("""\
WITH held AS (
    SELECT id, started_at, cancel_requested_at, attempts FROM haiway_managed_tasks
    WHERE id = $2::UUID
      AND status = 'running'
      AND lease_owner = $1::UUID
      AND attempts + 1 = $3::BIGINT
    FOR UPDATE
),
updated AS (
    UPDATE haiway_managed_tasks AS t
    SET status = CASE
            WHEN held.cancel_requested_at IS NULL AND $4::TEXT = 'retried' THEN 'retry_pending'
            ELSE 'finished'
        END,
        due_at = CASE
            WHEN held.cancel_requested_at IS NULL AND $4::TEXT = 'retried'
                THEN statement_timestamp() + @interval(6)
            ELSE NULL
        END,
        attempts = held.attempts + 1,
        lease_owner = NULL,
        lease_expires_at = NULL,
        started_at = NULL,
        cancel_requested_at = NULL,
        outcome = CASE
            WHEN held.cancel_requested_at IS NOT NULL THEN 'cancelled'
            WHEN $4::TEXT = 'retried' THEN NULL
            ELSE $4::TEXT
        END,
        result = CASE
            WHEN held.cancel_requested_at IS NULL AND $4::TEXT = 'completed' THEN $5::JSONB
            ELSE NULL
        END,
        finished_at = CASE
            WHEN held.cancel_requested_at IS NULL AND $4::TEXT = 'retried' THEN NULL
            ELSE statement_timestamp()
        END
    FROM held
    WHERE t.id = held.id
    RETURNING t.status, t.outcome, t.result::TEXT AS result
),
attempt AS (
    INSERT INTO haiway_managed_task_attempts (
        task_id, number, started_at, ended_at, result, failure_kind, exception_type, time_limit
    )
    SELECT held.id,
        held.attempts + 1,
        held.started_at,
        greatest(statement_timestamp(), held.started_at),
        CASE
            WHEN held.cancel_requested_at IS NOT NULL THEN 'cancelled'
            WHEN $4::TEXT IN ('retried', 'failed') THEN 'failed'
            ELSE $4::TEXT
        END,
        CASE
            WHEN held.cancel_requested_at IS NULL AND $4::TEXT IN ('retried', 'failed')
                THEN $7::TEXT
            ELSE NULL
        END,
        CASE
            WHEN held.cancel_requested_at IS NULL AND $4::TEXT IN ('retried', 'failed')
                THEN $8::TEXT
            ELSE NULL
        END,
        CASE
            WHEN held.cancel_requested_at IS NULL AND $4::TEXT IN ('retried', 'failed')
                THEN $9::DOUBLE PRECISION
            ELSE NULL
        END
    FROM held
)
SELECT status, outcome, result FROM updated;\
""")

# $1 runner, $2 identifier, $3 attempt
TASK_RELEASE_STATEMENT: Final[str] = _sql("""\
UPDATE haiway_managed_tasks
SET status = CASE WHEN cancel_requested_at IS NOT NULL THEN 'finished' ELSE 'queued' END,
    due_at = CASE WHEN cancel_requested_at IS NOT NULL THEN NULL ELSE statement_timestamp() END,
    lease_owner = NULL,
    lease_expires_at = NULL,
    started_at = NULL,
    cancel_requested_at = NULL,
    outcome = CASE WHEN cancel_requested_at IS NOT NULL THEN 'cancelled' ELSE NULL END,
    finished_at = CASE WHEN cancel_requested_at IS NOT NULL THEN statement_timestamp() ELSE NULL END
WHERE id = $2::UUID
  AND status = 'running'
  AND lease_owner = $1::UUID
  AND attempts + 1 = $3::BIGINT
RETURNING status, outcome;\
""")

# $1 identifier
TASK_CANCEL_STATEMENT: Final[str] = """\
WITH target AS (
    SELECT id, status FROM haiway_managed_tasks WHERE id = $1::UUID FOR UPDATE
),
updated AS (
    UPDATE haiway_managed_tasks AS t
    SET status = CASE
            WHEN t.status IN ('queued', 'retry_pending') THEN 'finished' ELSE t.status
        END,
        due_at = CASE WHEN t.status IN ('queued', 'retry_pending') THEN NULL ELSE t.due_at END,
        outcome = CASE
            WHEN t.status IN ('queued', 'retry_pending') THEN 'cancelled' ELSE t.outcome
        END,
        finished_at = CASE
            WHEN t.status IN ('queued', 'retry_pending') THEN statement_timestamp()
            ELSE t.finished_at
        END,
        cancel_requested_at = CASE
            WHEN t.status = 'running' THEN COALESCE(t.cancel_requested_at, statement_timestamp())
            ELSE t.cancel_requested_at
        END
    FROM target
    WHERE t.id = target.id AND t.status <> 'finished'
    RETURNING t.id
)
SELECT CASE
    WHEN target.status IN ('queued', 'retry_pending') THEN 'cancelled'
    WHEN target.status = 'running' THEN 'pending'
    ELSE 'finished'
END AS cancellation
FROM target;\
"""

# $1 identifiers
TASK_READ_STATEMENT: Final[str] = """\
SELECT t.id, t.name, t.argument::TEXT AS argument, t.time_limit, t.retry_delays,
    t.origin::TEXT AS origin, t.status, t.outcome,
    t.cancel_requested_at IS NOT NULL AS cancel_requested, t.result::TEXT AS result,
    a.number AS attempt_number, a.result AS attempt_result, a.failure_kind,
    a.exception_type, a.time_limit AS attempt_time_limit
FROM haiway_managed_tasks AS t
LEFT JOIN haiway_managed_task_attempts AS a ON a.task_id = t.id
WHERE t.id = ANY($1::UUID[])
ORDER BY t.id, a.number;\
"""

# $1 names, always an array, never NULL
TASK_FAIL_UNSUPPORTED_STATEMENT: Final[str] = _sql("""\
WITH unsupported AS (
    SELECT id, attempts FROM haiway_managed_tasks
    WHERE status IN ('queued', 'retry_pending')
      AND due_at <= statement_timestamp()
      AND NOT (name = ANY($1::TEXT[]))
    FOR UPDATE SKIP LOCKED
),
failed AS (
    UPDATE haiway_managed_tasks AS t
    SET status = 'finished',
        due_at = NULL,
        attempts = t.attempts + 1,
        outcome = 'failed',
        finished_at = statement_timestamp()
    FROM unsupported
    WHERE t.id = unsupported.id
    RETURNING t.id, t.name
),
attempt AS (
    INSERT INTO haiway_managed_task_attempts (
        task_id, number, started_at, ended_at, result, failure_kind
    )
    SELECT u.id,
        u.attempts + 1,
        statement_timestamp(), statement_timestamp(), 'failed', 'unsupported'
    FROM unsupported AS u
)
SELECT id, name FROM failed;\
""")

# the listing statement is built from these fragments and the given filter parts only
TASK_QUERY_SELECT: Final[str] = """\
SELECT t.id, t.name, t.status, t.outcome, t.created_at, t.finished_at,
    t.attempts
FROM haiway_managed_tasks AS t\
"""
TASK_QUERY_ORDER: Final[str] = "ORDER BY t.created_at, t.id"


class _Query:
    # the statement text and its parameters, numbered as they are added
    __slots__ = (
        "arguments",
        "conditions",
    )

    def __init__(self) -> None:
        self.conditions: list[str] = []
        self.arguments: list[PostgresValue] = []

    def bind(
        self,
        value: PostgresValue,
        type_name: str,
    ) -> str:
        self.arguments.append(value)
        return "$" + str(len(self.arguments)) + "::" + type_name

    def require(
        self,
        column: str,
        operator: Literal["=", ">=", "<"],
        value: PostgresValue,
        type_name: str,
    ) -> None:
        self.conditions.append(column + " " + operator + " " + self.bind(value, type_name))

    def require_any(
        self,
        column: str,
        values: Sequence[str],
    ) -> None:
        self.conditions.append(column + " = ANY(" + self.bind(list(values), "TEXT[]") + ")")


def _require_time_range(
    query: _Query,
    column: str,
    bounds: TaskTimeRange,
) -> None:
    match bounds:
        case TaskTimeFrom(start=start):
            query.require(column, ">=", start, "TIMESTAMPTZ")

        case TaskTimeBefore(end=end):
            query.require(column, "<", end, "TIMESTAMPTZ")

        case TaskTimeBetween(start=start, end=end):
            query.require(column, ">=", start, "TIMESTAMPTZ")
            query.require(column, "<", end, "TIMESTAMPTZ")


def _require_status(
    query: _Query,
    matching: TaskStatusFilter,
) -> None:
    match matching:
        case TasksUnfinished(statuses=statuses):
            query.require_any("t.status", sorted(statuses))

        case TasksFinished(outcomes=outcomes, finished=finished):
            query.conditions.append("t.status = 'finished'")
            query.require_any("t.outcome", sorted(outcomes))
            if finished is not None:
                _require_time_range(query, "t.finished_at", finished)


def _query_statement(
    matching: TaskFilter,
    after: TaskKeyset | None,
    limit: int,
) -> tuple[str, Sequence[PostgresValue]]:
    query = _Query()
    if matching.names is not None:
        if len(matching.names) == 1:
            (name,) = matching.names
            query.require("t.name", "=", name, "TEXT")

        else:
            query.require_any("t.name", sorted(matching.names))

    if matching.status is not None:
        _require_status(query, matching.status)

    if matching.created is not None:
        _require_time_range(query, "t.created_at", matching.created)

    if after is not None:
        created: str = query.bind(after.created, "TIMESTAMPTZ")
        identifier: str = query.bind(after.identifier, "UUID")
        query.conditions.append("(t.created_at, t.id) > (" + created + ", " + identifier + ")")

    parts: list[str] = [TASK_QUERY_SELECT]
    if query.conditions:
        parts.append("WHERE " + "\n  AND ".join(query.conditions))

    parts.append(TASK_QUERY_ORDER)
    parts.append("LIMIT " + query.bind(limit, "BIGINT") + ";")
    return "\n".join(parts), query.arguments


def _unexpected(
    what: str,
    value: object,
) -> PostgresException:
    # the value is a column of the task tables, never an argument or a result
    return PostgresException(f"Unexpected {what} in the stored tasks: {value!r}")


def _failure(
    kind: str | None,
    exception_type: str | None,
    time_limit: float | None,
) -> TaskFailure:
    match kind:
        case "raised" | "preparation" if exception_type is not None:
            return TaskFailureRecord(kind=kind, exception_type=exception_type)

        case "time_limit" if time_limit is not None:
            return TaskTimeLimitExceeded(time_limit=time_limit)

        case "interrupted":
            return TaskInterrupted()

        case "unsupported":
            return TaskUnsupported()

        case _:
            raise _unexpected("failure kind", kind)


def _attempt_failure(
    failure: AttemptFailure,
) -> tuple[str, str | None, float | None]:
    match failure:
        case TaskFailureRecord(kind=kind, exception_type=exception_type):
            return kind, exception_type, None

        case TaskTimeLimitExceeded(time_limit=time_limit):
            return "time_limit", None, time_limit

        case TaskInterrupted():
            return "interrupted", None, None


def _end_arguments(
    end: AttemptEnd,
) -> tuple[PostgresValue, ...]:
    # end, result, delay, failure kind, exception type, failure time limit
    match end:
        case AttemptCompleted(result=result):
            return "completed", result, None, None, None, None

        case AttemptRetried(failure=failure, delay=delay):
            kind, exception_type, time_limit = _attempt_failure(failure)
            return "retried", None, delay, kind, exception_type, time_limit

        case AttemptFailed(failure=failure):
            kind, exception_type, time_limit = _attempt_failure(failure)
            return "failed", None, None, kind, exception_type, time_limit

        case AttemptCancelled():
            return "cancelled", None, None, None, None, None


def _outcome(
    outcome: str | None,
    result: str | None,
) -> StoredCompleted | StoredFailed | StoredCancelled:
    match outcome:
        case "completed" if result is not None:
            return StoredCompleted(result=result)

        case "failed":
            return StoredFailed()

        case "cancelled":
            return StoredCancelled()

        case _:
            raise _unexpected("outcome", outcome)


def _status(
    status: str | None,
    *,
    outcome: str | None,
    result: str | None,
    cancel_requested: bool,
) -> StoredStatus:
    match status:
        case "queued":
            return StoredQueued()

        case "running":
            return StoredRunning(cancel_requested=cancel_requested)

        case "retry_pending":
            return StoredRetryPending()

        case "finished":
            return StoredFinished(outcome=_outcome(outcome, result))

        case _:
            raise _unexpected("status", status)


def _new_task(row: PostgresRow) -> NewTask:
    delays: PostgresValue = row["retry_delays"]
    if not isinstance(delays, Sequence) or isinstance(delays, str | bytes):
        raise _unexpected("retry delays", delays)

    origin: str | None = row.get_str("origin")
    return NewTask(
        identifier=row.get_uuid("id", required=True),
        name=row.get_str("name", required=True),
        argument=row.get_str("argument", required=True),
        retry=TaskRetryPolicy(delays=tuple(float(delay) for delay in delays)),
        time_limit=row.get_float("time_limit"),
        origin=None if origin is None else ObservabilityLink.from_json(origin),
    )


def _claimed(row: PostgresRow) -> ClaimedTask:
    task: NewTask = _new_task(row)
    attempt: int = row.get_int("attempt", required=True)
    match row.get_str("claim"):
        case "attempt":
            return ClaimedAttempt(task=task, attempt=attempt)

        case "interrupted":
            return ClaimedInterrupted(
                task=task,
                attempt=attempt,
                cancel_requested=row.get_bool("cancel_requested", required=True),
            )

        case claim:
            raise _unexpected("claim", claim)


def _stored_attempt(row: PostgresRow) -> StoredAttempt:
    match row.get_str("attempt_result"):
        case "completed":
            return StoredAttempt(result="completed")

        case "cancelled":
            return StoredAttempt(result="cancelled")

        case "failed":
            return StoredAttempt(
                result=_failure(
                    row.get_str("failure_kind"),
                    row.get_str("exception_type"),
                    row.get_float("attempt_time_limit"),
                )
            )

        case result:
            raise _unexpected("attempt result", result)


def _stored_tasks(rows: Sequence[PostgresRow]) -> Mapping[UUID, StoredTask]:
    # rows of one task are adjacent, its attempts in number order
    grouped: dict[UUID, tuple[PostgresRow, list[StoredAttempt]]] = {}
    for row in rows:
        identifier: UUID = row.get_uuid("id", required=True)
        if identifier not in grouped:
            grouped[identifier] = (row, [])

        if row.get_int("attempt_number") is not None:
            grouped[identifier][1].append(_stored_attempt(row))

    return {
        identifier: StoredTask(
            task=_new_task(row),
            status=_status(
                row.get_str("status"),
                outcome=row.get_str("outcome"),
                result=row.get_str("result"),
                cancel_requested=row.get_bool("cancel_requested", required=True),
            ),
            attempts=attempts,
        )
        for identifier, (row, attempts) in grouped.items()
    }


def _summary(row: PostgresRow) -> TaskSummary:
    status: TaskSummaryUnfinished | TaskSummaryFinished
    match row.get_str("status"):
        case "queued" | "running" | "retry_pending" as unfinished:
            status = TaskSummaryUnfinished(status=unfinished)

        case "finished":
            match row.get_str("outcome"):
                case "completed" | "failed" | "cancelled" as outcome:
                    status = TaskSummaryFinished(
                        outcome=outcome,
                        finished=row.get_datetime("finished_at", required=True),
                    )

                case other:
                    raise _unexpected("outcome", other)

        case other:
            raise _unexpected("status", other)

    return TaskSummary(
        identifier=row.get_uuid("id", required=True),
        name=row.get_str("name", required=True),
        status=status,
        attempts=row.get_int("attempts", required=True),
        created=row.get_datetime("created_at", required=True),
    )


@contextmanager
def _operation(
    name: str,
    identifier: UUID | None = None,
) -> Generator[None]:
    # names the operation and the task, never an argument or a result: a driver error keeps
    # its sqlstate, and a value which cannot be decoded is reported as a storage failure
    subject: str = "" if identifier is None else f" of {identifier}"
    try:
        yield

    except PostgresException as exc:
        raise PostgresException(
            f"Task store {name}{subject} failed",
            sqlstate=exc.sqlstate,
        ) from exc

    except (ValueError, TypeError) as exc:
        raise PostgresException(
            f"Task store {name}{subject} read a value it cannot decode",
        ) from exc


@final
class PostgresTaskStore:
    """
    Task store keeping tasks in PostgreSQL, shared by every runtime opening the same database.

    Every runtime using one database is a worker of one shared work queue: any open runtime
    claims any due task whose registered name it lists, so registered names have to be unique
    per database. Tasks survive a restart of the process. Leases are columns of the task row,
    compared with the clock of the database server, which is also the clock of every stored
    time and of the creation and finish times a listing returns. The coordination is
    ``LeasedCoordination``. Rows are never deleted by the store.

    The store owns ``pool``: ``open`` enters it and the session's exit exits it. The pool
    needs at least 2 connections, since claims, renewals, records and polls of one runtime
    queue behind each other on one, and has to be used by nothing else: neither entered by
    the caller, nor shared with task bodies. The store never uses a connection found in the
    context. Use one store object for each runtime.

    Every operation is one statement on one connection, and so one transaction. Later schema
    versions are additive and compatible with stores of earlier versions: a store opens a
    schema of a higher version than its own. PostgreSQL 16 is the minimum verified version.

    Parameters
    ----------
    pool : PostgresConnectionPool
        The pool of the store, with at least 2 connections.
    lease : float
        Seconds a claim or renewal holds a lease, finite and above 0.
    lease_renewal : float
        Seconds between renewals, finite, above 0 and below ``lease``.
    polling : float
        Seconds between claim rounds and outcome polls, finite and above 0.

    Raises
    ------
    ValueError
        When a timing is out of its range, and from ``claim`` and ``query`` for a negative
        limit.
    PostgresException
        From ``open`` when the pool cannot be created, is entered already, or the schema is
        missing or older than the store's, from every operation when a statement fails or no
        connection is acquired in time, and from ``insert`` of an identifier the table holds.
    """

    __slots__ = (
        "_pool",
        "_timings",
    )

    def __init__(
        self,
        pool: PostgresConnectionPool,
        /,
        *,
        lease: float,
        lease_renewal: float,
        polling: float,
    ) -> None:
        self._pool: PostgresConnectionPool = pool
        self._timings: LeaseTimings = LeaseTimings(
            lease=lease,
            renewal=lease_renewal,
            polling=polling,
        )

    def open(self) -> AbstractAsyncContextManager[TaskStoreSession]:
        """
        Open the store for a runtime.

        Returns
        -------
        AbstractAsyncContextManager[TaskStoreSession]
            Enters the pool, checks the schema version and yields the session of the
            runtime; exits the pool on exit.

        Raises
        ------
        PostgresException
            On entry, when the pool cannot be created or is entered already, or when the
            schema is missing or its version is below the store's latest.
        """
        return self._session()

    async def migrate(
        self,
        *,
        timeout: int = 300,
    ) -> None:
        """
        Install or upgrade the schema of the store.

        Enters the store's own pool, runs every migration not recorded yet under an advisory
        lock, each in its own transaction, and exits the pool. It needs no ``Postgres`` in the
        context and records nothing in the ``migrations`` table of ``execute_migrations``.

        Parameters
        ----------
        timeout : int, default=300
            Seconds any statement may wait for a lock, as PostgreSQL ``lock_timeout``.
            ``0`` disables the bound.

        Raises
        ------
        PostgresException
            When a statement fails, the lock is not acquired within ``timeout``, or the
            store is open. A failed migration is rolled back.
        """
        async with self._pool, self._pool.acquire_connection() as connection:
            ctx.log_info("Preparing task store migrations...")
            try:
                with _operation("migrate"):
                    await connection.execute(MIGRATIONS_LOCK_TIMEOUT_STATEMENT, f"{int(timeout)}s")
                    await connection.execute(
                        MIGRATIONS_ADVISORY_LOCK_STATEMENT,
                        TASKS_MIGRATIONS_ADVISORY_LOCK_KEY,
                    )
                    await connection.execute(TASKS_MIGRATIONS_TABLE_CREATE_STATEMENT)
                    recorded: Set[int] = {
                        row.get_int("version", required=True)
                        for row in await connection.fetch(TASKS_MIGRATIONS_VERSIONS_STATEMENT)
                    }
                    pending: Sequence[int] = [
                        version for version in sorted(TASKS_MIGRATIONS) if version not in recorded
                    ]
                    ctx.log_info(
                        f"...current schema version: {max(recorded, default=0)},"
                        f" migrations to apply: {len(pending)}..."
                    )
                    for version in pending:
                        ctx.log_info(f"...executing task store migration {version}...")
                        try:
                            async with connection.transaction():
                                await connection.execute(TASKS_MIGRATIONS[version])
                                await connection.execute(
                                    TASKS_MIGRATION_COMPLETION_STATEMENT,
                                    version,
                                )

                        except Exception as exc:
                            ctx.log_error(
                                f"...task store migration {version} failed...",
                                exception=exc,
                            )
                            raise

                        ctx.log_info(f"...task store migration {version} completed...")

                    ctx.log_info("...task store migrations completed successfully!")

            finally:
                # best effort: releasing the connection resets both as well, and a failure
                # here must not replace the failure of the migration
                try:
                    await connection.execute(
                        MIGRATIONS_ADVISORY_UNLOCK_STATEMENT,
                        TASKS_MIGRATIONS_ADVISORY_LOCK_KEY,
                    )

                except Exception as exc:
                    ctx.log_error("Failed to release task store migration lock", exception=exc)

                try:
                    await connection.execute(MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT)

                except Exception as exc:
                    ctx.log_error("Failed to reset task store lock timeout", exception=exc)

    @asynccontextmanager
    async def _session(self) -> AsyncGenerator[TaskStoreSession]:
        async with self._pool:
            await self._check_schema()
            yield TaskStoreSession(
                insert=self._insert,
                claim=self._claim,
                renew=self._renew,
                record=self._record,
                release=self._release,
                cancel=self._cancel,
                read=self._read,
                fail_unsupported=self._fail_unsupported,
                query=self._query,
                coordination=LeasedCoordination(timings=self._timings),
            )

    async def _check_schema(self) -> None:
        recorded: int | None
        with _operation("schema check"):
            try:
                rows: Sequence[PostgresRow] = await self._fetch(TASKS_MIGRATIONS_LATEST_STATEMENT)
                recorded = rows[0].get_int("version") if rows else None

            except PostgresException as exc:
                if exc.sqlstate != PostgresErrorCode.undefined_table:
                    raise

                recorded = None

        if recorded is None or recorded < TASKS_LATEST_VERSION:
            message: str = (
                "Task store schema version is "
                f"{'missing' if recorded is None else recorded}, the store needs"
                f" {TASKS_LATEST_VERSION} - run migrate()"
            )
            ctx.log_error(message)
            raise PostgresException(message)

    async def _fetch(
        self,
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]:
        connection: PostgresConnection
        async with self._pool.acquire_connection() as connection:
            return await connection.fetch(statement, *args)

    async def _insert(
        self,
        task: NewTask,
        /,
    ) -> None:
        with _operation("insert", task.identifier):
            connection: PostgresConnection
            async with self._pool.acquire_connection() as connection:
                await connection.execute(
                    TASK_INSERT_STATEMENT,
                    task.identifier,
                    task.name,
                    task.argument,
                    task.time_limit,
                    list(task.retry.delays),
                    None if task.origin is None else task.origin.to_json(),
                )

    async def _claim(
        self,
        runner: UUID,
        names: Set[str],
        *,
        limit: int | None,
    ) -> Sequence[ClaimedTask]:
        if limit is not None and limit < 0:
            raise ValueError(f"PostgresTaskStore claim limit cannot be negative: {limit}")

        with _operation("claim"):
            return tuple(
                _claimed(row)
                for row in await self._fetch(
                    TASK_CLAIM_STATEMENT,
                    sorted(names),
                    limit,
                    runner,
                    self._timings.lease,
                )
            )

    async def _renew(
        self,
        runner: UUID,
        identifiers: Set[UUID],
    ) -> LeaseRenewal:
        with _operation("renew"):
            leases: dict[UUID, LeaseState] = {}
            for row in await self._fetch(
                TASK_RENEW_STATEMENT,
                runner,
                sorted(identifiers),
                self._timings.lease,
            ):
                leases[row.get_uuid("id", required=True)] = (
                    "cancel_requested"
                    if row.get_bool("cancel_requested", required=True)
                    else "held"
                )

            return LeaseRenewal(leases=leases)

    async def _record(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,
        end: AttemptEnd,
        /,
    ) -> StoredStatus | None:
        with _operation("record", identifier):
            rows: Sequence[PostgresRow] = await self._fetch(
                TASK_RECORD_STATEMENT,
                runner,
                identifier,
                attempt,
                *_end_arguments(end),
            )
            if not rows:
                return None

            return _status(
                rows[0].get_str("status"),
                outcome=rows[0].get_str("outcome"),
                result=rows[0].get_str("result"),
                cancel_requested=False,
            )

    async def _release(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,
        /,
    ) -> StoredStatus | None:
        with _operation("release", identifier):
            rows: Sequence[PostgresRow] = await self._fetch(
                TASK_RELEASE_STATEMENT,
                runner,
                identifier,
                attempt,
            )
            if not rows:
                return None

            return _status(
                rows[0].get_str("status"),
                outcome=rows[0].get_str("outcome"),
                result=None,
                cancel_requested=False,
            )

    async def _cancel(
        self,
        identifier: UUID,
        /,
    ) -> TaskCancellation | None:
        with _operation("cancel", identifier):
            rows: Sequence[PostgresRow] = await self._fetch(TASK_CANCEL_STATEMENT, identifier)
            if not rows:
                return None

            match rows[0].get_str("cancellation"):
                case "cancelled" | "pending" | "finished" as cancellation:
                    return cancellation

                case other:
                    raise _unexpected("cancellation", other)

    async def _read(
        self,
        identifiers: Set[UUID],
        /,
    ) -> Mapping[UUID, StoredTask]:
        with _operation("read"):
            return _stored_tasks(await self._fetch(TASK_READ_STATEMENT, sorted(identifiers)))

    async def _fail_unsupported(
        self,
        names: Set[str],
        /,
    ) -> Mapping[UUID, str]:
        with _operation("fail_unsupported"):
            return {
                row.get_uuid("id", required=True): row.get_str("name", required=True)
                for row in await self._fetch(TASK_FAIL_UNSUPPORTED_STATEMENT, sorted(names))
            }

    async def _query(
        self,
        matching: TaskFilter,
        /,
        *,
        after: TaskKeyset | None,
        limit: int,
    ) -> Sequence[TaskSummary]:
        if limit < 0:
            raise ValueError(f"PostgresTaskStore query limit cannot be negative: {limit}")

        with _operation("query"):
            statement, arguments = _query_statement(matching, after, limit)
            return tuple(_summary(row) for row in await self._fetch(statement, *arguments))
