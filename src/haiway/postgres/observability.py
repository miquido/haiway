from collections.abc import Mapping
from typing import Final

from haiway.context import ObservabilityAttribute
from haiway.postgres.types import PostgresException

__all__ = (
    "CONNECTION_COUNT_METRIC",
    "CONNECTION_FAILED_EVENT",
    "CONNECTION_TIMEOUTS_METRIC",
    "CONNECTION_WAIT_TIME_METRIC",
    "OPERATION_DURATION_METRIC",
    "OPERATION_RETRIED_EVENT",
    "OPERATION_RETRIES_METRIC",
    "RETURNED_ROWS_METRIC",
    "TRANSACTION_DURATION_METRIC",
    "operation_attributes",
)

# Telemetry vocabulary for the Postgres adapter.
#
# Names follow OpenTelemetry's database semantic conventions where one exists, so
# `db.response.status_code` carries the SQLSTATE those conventions specify for
# PostgreSQL and existing dashboards read it without translation. The transaction
# instruments have no convention to follow and keep the same prefix.
#
# Recorded values are operation names, SQLSTATEs, exception types and counts -
# statements and their parameters are not among them.
#
# Metrics are recorded at INFO because both backends gate metrics on level and
# OpenTelemetry defaults to INFO, so anything lower is invisible in production.
# Events carry the level their severity deserves.

DB_SYSTEM_NAME: Final[str] = "postgresql"

OPERATION_DURATION_METRIC: Final[str] = "db.client.operation.duration"
"""End to end duration seen by the caller, including connection acquisition and retry delays."""

RETURNED_ROWS_METRIC: Final[str] = "db.client.response.returned_rows"
"""Rows transferred by a fetch, counted before `fetch_one` discards any."""

OPERATION_RETRIES_METRIC: Final[str] = "db.client.operation.retries"
"""Retries spent on transient failures, which are otherwise invisible."""

CONNECTION_WAIT_TIME_METRIC: Final[str] = "db.client.connection.wait_time"
"""Time spent waiting for a pooled connection, separating contention from slow statements."""

CONNECTION_COUNT_METRIC: Final[str] = "db.client.connection.count"
"""Sampled pool occupancy, split by the `state` attribute into `used` and `idle`."""

CONNECTION_TIMEOUTS_METRIC: Final[str] = "db.client.connection.timeouts"
"""Acquisitions which gave up waiting, the signal that the pool is exhausted."""

TRANSACTION_DURATION_METRIC: Final[str] = "db.client.transaction.duration"
"""Transaction lifetime, split by the `db.transaction.outcome` attribute."""

OPERATION_RETRIED_EVENT: Final[str] = "db.client.operation.retried"
CONNECTION_FAILED_EVENT: Final[str] = "db.client.connection.failed"


def operation_attributes(
    operation: str,
    /,
    error: PostgresException | None = None,
) -> Mapping[str, ObservabilityAttribute]:
    """Build the attributes shared by every operation instrument.

    Parameters
    ----------
    operation : str
        Name of the adapter operation, such as ``"fetch"`` or ``"execute"``.
    error : PostgresException | None, optional
        Failure to attribute, when the operation did not succeed.

    Returns
    -------
    Mapping[str, ObservabilityAttribute]
        Attributes holding the operation name and, for a failure, its SQLSTATE
        and exception type.
    """
    if error is None:
        return {
            "db.system.name": DB_SYSTEM_NAME,
            "db.operation.name": operation,
        }

    return {
        "db.system.name": DB_SYSTEM_NAME,
        "db.operation.name": operation,
        "db.response.status_code": error.sqlstate,
        "error.type": type(error).__qualname__,
    }
