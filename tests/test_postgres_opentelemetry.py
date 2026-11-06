from collections.abc import Iterator
from types import TracebackType
from typing import Any

import pytest

pytest.importorskip("asyncpg", reason="requires the postgres extra")
pytest.importorskip("opentelemetry")

from asyncpg.exceptions import InvalidTextRepresentationError
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import InMemoryLogRecordExporter, SimpleLogRecordProcessor
from pytest import MonkeyPatch, fixture, mark, raises

from haiway import ctx
from haiway.opentelemetry import OpenTelemetry, observability
from haiway.postgres import PostgresException
from tests.test_postgres_client import _pool_connection_context  # pyright: ignore[reportPrivateUsage]


@fixture
def logs(monkeypatch: MonkeyPatch) -> Iterator[InMemoryLogRecordExporter]:
    exporter = InMemoryLogRecordExporter()
    provider = LoggerProvider(shutdown_on_exit=False)
    provider.add_log_record_processor(SimpleLogRecordProcessor(exporter))
    monkeypatch.setattr(observability, "get_logger_provider", lambda: provider)
    snapshot = (
        OpenTelemetry._tracer_provider,
        OpenTelemetry._meter_provider,
        OpenTelemetry._logger_provider,
        OpenTelemetry._logger,
    )
    OpenTelemetry.autoconfigure(service="test-service")

    yield exporter

    (
        OpenTelemetry._tracer_provider,
        OpenTelemetry._meter_provider,
        OpenTelemetry._logger_provider,
        OpenTelemetry._logger,
    ) = snapshot
    provider.shutdown()


@mark.asyncio
@mark.parametrize("operation", ("fetch", "execute"))
async def test_values_quoted_by_the_driver_never_reach_exported_telemetry(
    logs: InMemoryLogRecordExporter,
    operation: str,
) -> None:
    # assembled at runtime - a stack trace carries source lines, which would
    # otherwise contain the very literal searched for
    secret: str = "-".join(("hunter", "2"))

    class _FailingDriverConnection:
        async def fetch(self, statement: str, /, *args: Any) -> Any:
            raise InvalidTextRepresentationError(
                f'invalid input syntax for type integer: "{secret}"'
            )

        async def execute(self, statement: str, /, *args: Any) -> str:
            raise InvalidTextRepresentationError(
                f'invalid input syntax for type integer: "{secret}"'
            )

    class _PoolContext:
        async def __aenter__(self) -> _FailingDriverConnection:
            return _FailingDriverConnection()

        async def __aexit__(
            self,
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            return None

    with raises(PostgresException):
        async with ctx.scope("failing", observability=OpenTelemetry.observability()):
            async with _pool_connection_context(_PoolContext()) as connection:
                await getattr(connection, operation)("SELECT 1 WHERE id = $1", secret)

    exported: str = repr(
        [
            (entry.log_record.body, dict(entry.log_record.attributes or {}))
            for entry in logs.get_finished_logs()
        ]
    )
    assert "exception" in exported  # the failure is reported, by its type and frames
    assert "InvalidTextRepresentationError: <redacted>" in exported
    assert secret not in exported
