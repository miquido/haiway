try:
    import asyncpg  # pyright: ignore[reportUnusedImport]

except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "haiway.postgres requires the 'postgres' extra (asyncpg). "
        "Install via `pip install haiway[postgres]`."
    ) from exc

from haiway.postgres.client import PostgresConnectionPool
from haiway.postgres.configuration import PostgresConfigurationRepository
from haiway.postgres.state import Postgres, PostgresConnection
from haiway.postgres.types import PostgresException, PostgresRow, PostgresValue

__all__ = (
    "Postgres",
    "PostgresConfigurationRepository",
    "PostgresConnection",
    "PostgresConnectionPool",
    "PostgresException",
    "PostgresRow",
    "PostgresValue",
)
