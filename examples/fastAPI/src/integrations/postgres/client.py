from typing import Self

from haiway import Dependency

from integrations.postgres.config import (
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_SSLMODE,
    POSTGRES_USER,
)
from integrations.postgres.session import PostgresClientSession

__all__ = [
    "PostgresClient",
]


class PostgresClient(
    PostgresClientSession,
    Dependency,
):
    @classmethod
    async def prepare(cls) -> Self:
        instance: Self = cls(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            database=POSTGRES_DATABASE,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
            ssl=POSTGRES_SSLMODE,
        )
        await instance.initialize()
        return instance
