from collections.abc import Mapping, Sequence
from types import TracebackType
from typing import Any, NamedTuple
from uuid import UUID, uuid4

import pytest
from pytest import mark

pytest.importorskip("asyncpg", reason="requires the postgres extra")

from asyncpg.exceptions import UniqueViolationError

from haiway import ctx
from haiway.context import (
    ContextIdentifier,
    Observability,
    ObservabilityAttribute,
    ObservabilityLevel,
    ObservabilityMetricKind,
)
from haiway.postgres import Postgres, PostgresConnection, PostgresErrorCode, PostgresException
from haiway.postgres.client import (
    _ConnectionContext,  # pyright: ignore[reportPrivateUsage]
    _TransactionContext,  # pyright: ignore[reportPrivateUsage]
)
from haiway.postgres.observability import (
    CONNECTION_TIMEOUTS_METRIC,
    CONNECTION_WAIT_TIME_METRIC,
    OPERATION_DURATION_METRIC,
    OPERATION_RETRIES_METRIC,
    RETURNED_ROWS_METRIC,
    TRANSACTION_DURATION_METRIC,
)
from haiway.postgres.types import PostgresRow, PostgresValue


class _Metric(NamedTuple):
    name: str
    value: float | int
    unit: str | None
    kind: ObservabilityMetricKind
    attributes: Mapping[str, ObservabilityAttribute]


class _Event(NamedTuple):
    name: str
    attributes: Mapping[str, ObservabilityAttribute]


class _Recorder:
    """Observability capturing what the adapter records."""

    def __init__(self) -> None:
        self.metrics: list[_Metric] = []
        self.events: list[_Event] = []

    def observability(self) -> Observability:
        def trace_identifying(scope: ContextIdentifier, /) -> UUID:
            return uuid4()

        def log_recording(
            scope: ContextIdentifier,
            /,
            level: ObservabilityLevel,
            message: str,
            *args: Any,
            exception: BaseException | None,
        ) -> None:
            return None

        def metric_recording(
            scope: ContextIdentifier,
            /,
            level: ObservabilityLevel,
            *,
            metric: str,
            value: float | int,
            unit: str | None,
            kind: ObservabilityMetricKind,
            attributes: Mapping[str, ObservabilityAttribute],
        ) -> None:
            self.metrics.append(_Metric(metric, value, unit, kind, attributes))

        def event_recording(
            scope: ContextIdentifier,
            /,
            level: ObservabilityLevel,
            *,
            event: str,
            attributes: Mapping[str, ObservabilityAttribute],
        ) -> None:
            self.events.append(_Event(event, attributes))

        def attributes_recording(
            scope: ContextIdentifier,
            /,
            level: ObservabilityLevel,
            attributes: Mapping[str, ObservabilityAttribute],
        ) -> None:
            return None

        def scope_entering(scope: ContextIdentifier, /) -> str:
            return ""

        def scope_exiting(
            scope: ContextIdentifier,
            /,
            *,
            exception: BaseException | None,
        ) -> None:
            return None

        return Observability(
            trace_identifying=trace_identifying,
            log_recording=log_recording,
            metric_recording=metric_recording,
            event_recording=event_recording,
            attributes_recording=attributes_recording,
            scope_entering=scope_entering,
            scope_exiting=scope_exiting,
        )

    def named(self, name: str) -> list[_Metric]:
        return [metric for metric in self.metrics if metric.name == name]


def _connection(
    rows: Sequence[PostgresRow] = (),
    failures: Sequence[PostgresException] = (),
) -> PostgresConnection:
    pending = list(failures)

    async def fetch(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]:
        if pending:
            raise pending.pop(0)

        return rows

    async def execute(
        statement: str,
        /,
        *args: PostgresValue,
    ) -> str:
        if pending:
            raise pending.pop(0)

        return "UPDATE 1"

    def transaction(**kwargs: object) -> object:  # pragma: no cover - unused here
        raise NotImplementedError

    return PostgresConnection(
        statement_fetching=fetch,
        statement_executing=execute,
        transaction_preparing=transaction,  # pyright: ignore[reportArgumentType]
    )


@mark.asyncio
async def test_successful_operation_records_its_duration() -> None:
    recorder = _Recorder()

    async with ctx.scope("test", _connection(), observability=recorder.observability()):
        await PostgresConnection.execute("UPDATE a SET b = 1")

    duration = recorder.named(OPERATION_DURATION_METRIC)
    assert len(duration) == 1
    assert duration[0].kind == "histogram"
    assert duration[0].unit == "s"
    assert duration[0].value >= 0
    assert duration[0].attributes["db.operation.name"] == "execute"
    assert duration[0].attributes["db.system.name"] == "postgresql"
    # a success has no failure to attribute
    assert "db.response.status_code" not in duration[0].attributes


@mark.asyncio
async def test_failed_operation_records_its_sqlstate() -> None:
    recorder = _Recorder()
    failure = PostgresException("duplicate key", sqlstate=PostgresErrorCode.unique_violation)

    async with ctx.scope(
        "test", _connection(failures=(failure,)), observability=recorder.observability()
    ):
        with pytest.raises(PostgresException):
            await PostgresConnection.fetch("INSERT INTO a VALUES (1) RETURNING id")

    duration = recorder.named(OPERATION_DURATION_METRIC)
    assert len(duration) == 1
    assert duration[0].attributes["db.response.status_code"] == PostgresErrorCode.unique_violation
    assert duration[0].attributes["error.type"] == "PostgresException"
    assert duration[0].attributes["db.operation.name"] == "fetch"


@mark.asyncio
async def test_fetch_records_the_rows_it_transferred() -> None:
    recorder = _Recorder()
    rows: tuple[PostgresRow, ...] = tuple(
        PostgresRow({"id": index})  # pyright: ignore[reportArgumentType]
        for index in range(3)
    )

    async with ctx.scope("test", _connection(rows), observability=recorder.observability()):
        await PostgresConnection.fetch("SELECT id FROM a")

    returned = recorder.named(RETURNED_ROWS_METRIC)
    assert len(returned) == 1
    assert returned[0].value == 3
    assert returned[0].kind == "histogram"


@mark.asyncio
async def test_fetch_one_records_every_row_it_had_to_transfer() -> None:
    # the point of the metric - fetch_one keeps one row but pays for all of them
    recorder = _Recorder()
    rows: tuple[PostgresRow, ...] = tuple(
        PostgresRow({"id": index})  # pyright: ignore[reportArgumentType]
        for index in range(50)
    )

    async with ctx.scope("test", _connection(rows), observability=recorder.observability()):
        await PostgresConnection.fetch_one("SELECT id FROM a")

    returned = recorder.named(RETURNED_ROWS_METRIC)
    assert len(returned) == 1
    assert returned[0].value == 50
    assert returned[0].attributes["db.operation.name"] == "fetch_one"


@mark.asyncio
async def test_retries_are_counted_and_evented() -> None:
    recorder = _Recorder()
    failure = PostgresException(
        "could not serialize",
        sqlstate=PostgresErrorCode.serialization_failure,
    )

    async with ctx.scope(
        "test",
        _connection(failures=(failure,)),
        observability=recorder.observability(),
    ):
        await PostgresConnection.execute("UPDATE a SET b = 1", retries=2)

    retries = recorder.named(OPERATION_RETRIES_METRIC)
    assert len(retries) == 1
    assert retries[0].kind == "counter"
    assert retries[0].value == 1
    assert retries[0].attributes["db.response.status_code"] == (
        PostgresErrorCode.serialization_failure
    )
    assert [event.name for event in recorder.events] == ["db.client.operation.retried"]
    assert recorder.events[0].attributes["db.client.operation.attempt"] == 1

    # the operation succeeded, so its duration carries no failure
    duration = recorder.named(OPERATION_DURATION_METRIC)
    assert len(duration) == 1
    assert "db.response.status_code" not in duration[0].attributes


@mark.asyncio
async def test_recorded_attributes_never_carry_the_statement_or_parameters() -> None:
    recorder = _Recorder()
    secret = "s3cret@example.com"

    async with ctx.scope("test", _connection(), observability=recorder.observability()):
        await PostgresConnection.execute("INSERT INTO users(email) VALUES($1)", secret)

    assert recorder.metrics
    for metric in recorder.metrics:
        rendered = f"{metric.name}{metric.attributes}"
        assert secret not in rendered
        assert "INSERT" not in rendered


@mark.asyncio
async def test_service_helper_records_one_duration_per_operation() -> None:
    # Postgres.fetch delegates to the connection, which is the only layer that
    # records - one duration per operation, not one per layer it passed through
    recorder = _Recorder()
    connection = _connection()

    class _Context:
        async def __aenter__(self) -> PostgresConnection:
            return connection

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            return None

    postgres = Postgres(connection_acquiring=_Context)
    async with ctx.scope("test", postgres, observability=recorder.observability()):
        await Postgres.fetch("SELECT 1")

    assert len(recorder.named(OPERATION_DURATION_METRIC)) == 1


class _Transaction:
    """asyncpg transaction stand-in whose completion can be made to fail."""

    def __init__(
        self,
        *,
        failing: bool = False,
    ) -> None:
        self.failing = failing

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self.failing:
            raise UniqueViolationError("deferred constraint violated at commit")


def _transaction_context(*, failing: bool = False) -> Any:
    return _TransactionContext(_transaction_context=_Transaction(failing=failing))  # pyright: ignore[reportArgumentType]


@mark.asyncio
async def test_committed_transaction_records_its_outcome() -> None:
    recorder = _Recorder()

    async with ctx.scope("test", observability=recorder.observability()):
        async with _transaction_context():
            pass

    recorded = recorder.named(TRANSACTION_DURATION_METRIC)
    assert len(recorded) == 1
    assert recorded[0].attributes["db.transaction.outcome"] == "committed"


@mark.asyncio
async def test_rolled_back_transaction_records_its_outcome() -> None:
    recorder = _Recorder()

    async with ctx.scope("test", observability=recorder.observability()):
        with pytest.raises(ValueError):
            async with _transaction_context():
                raise ValueError("body failed")

    recorded = recorder.named(TRANSACTION_DURATION_METRIC)
    assert len(recorded) == 1
    assert recorded[0].attributes["db.transaction.outcome"] == "rolled_back"


@mark.asyncio
async def test_failed_commit_is_not_recorded_as_committed() -> None:
    # the block left cleanly, so the intent was to commit - but the commit failed
    # and the work is lost, which is the outcome worth alerting on
    recorder = _Recorder()

    async with ctx.scope("test", observability=recorder.observability()):
        with pytest.raises(PostgresException, match="Failed to commit"):
            async with _transaction_context(failing=True):
                pass

    recorded = recorder.named(TRANSACTION_DURATION_METRIC)
    assert len(recorded) == 1
    assert recorded[0].attributes["db.transaction.outcome"] == "commit_failed"


@mark.asyncio
async def test_failed_rollback_records_its_own_outcome() -> None:
    recorder = _Recorder()

    async with ctx.scope("test", observability=recorder.observability()):
        with pytest.raises(ValueError):
            async with _transaction_context(failing=True):
                raise ValueError("body failed")

    recorded = recorder.named(TRANSACTION_DURATION_METRIC)
    assert len(recorded) == 1
    assert recorded[0].attributes["db.transaction.outcome"] == "rollback_failed"


@mark.asyncio
async def test_exhausted_pool_records_a_timeout() -> None:
    # pool exhaustion is invisible in the wait histogram, which only records a
    # wait that produced a connection
    recorder = _Recorder()

    class _TimingOutPool:
        def acquire(self, *, timeout: float | None = None) -> Any:
            class _Context:
                async def __aenter__(self) -> Any:
                    raise TimeoutError

                async def __aexit__(self, *_: Any) -> None:
                    return None

            return _Context()

    context = _ConnectionContext(
        _pool=_TimingOutPool(),  # pyright: ignore[reportArgumentType]
        _acquire_timeout=0.01,
    )

    async with ctx.scope("test", observability=recorder.observability()):
        with pytest.raises(PostgresException, match="Failed to acquire"):
            async with context:
                pass  # pragma: no cover - the acquire never succeeds

    timeouts = recorder.named(CONNECTION_TIMEOUTS_METRIC)
    assert len(timeouts) == 1
    assert timeouts[0].kind == "counter"
    assert [event.name for event in recorder.events] == ["db.client.connection.failed"]
    assert recorder.events[0].attributes["error.type"] == "TimeoutError"
    # a failed acquire produced no connection, so there is no wait to report
    assert recorder.named(CONNECTION_WAIT_TIME_METRIC) == []
