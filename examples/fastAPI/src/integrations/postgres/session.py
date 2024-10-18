from asyncpg import (  # pyright: ignore[reportMissingTypeStubs]
    Pool,
    create_pool,  # pyright: ignore [reportUnknownVariableType]
)

from integrations.postgres.connection import PostgresConnectionContext

__all__ = [
    "PostgresClientSession",
]


class PostgresClientSession:
    def __init__(  # noqa: PLR0913
        self,
        host: str,
        port: str,
        database: str,
        user: str,
        password: str,
        ssl: str,
    ) -> None:
        # using python replicas - keep only a single connection per replica to avoid errors
        self._pool: Pool = create_pool(
            min_size=1,
            max_size=1,
            database=database,
            user=user,
            password=password,
            host=host,
            port=port,
            ssl=ssl,
        )

    async def initialize(self) -> None:
        await self._pool  # initialize pool

    async def dispose(self) -> None:
        if self._pool._initialized:  # pyright: ignore[reportPrivateUsage]
            await self._pool.close()

    def connection(self) -> PostgresConnectionContext:
        return PostgresConnectionContext(pool=self._pool)
