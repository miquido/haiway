from types import TracebackType
from typing import Any

from asyncpg import (  # pyright: ignore[reportMissingTypeStubs]
    Connection,
    Pool,
    create_pool,  # pyright: ignore [reportUnknownVariableType]
)
from asyncpg.pool import PoolAcquireContext  # pyright: ignore[reportMissingTypeStubs]
from asyncpg.transaction import Transaction  # pyright: ignore[reportMissingTypeStubs]
from haiway import ctx

from integrations.postgres.state import (
    PostgresConnection,
    PostgresConnectionContext,
    PostgresTransactionContext,
)
from integrations.postgres.types import PostgresException

__all__ = [
    "PostgresSession",
]


from integrations.postgres.config import (
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_SSLMODE,
    POSTGRES_USER,
)
from integrations.postgres.state import PostgresClient

__all__ = [
    "PostgresSession",
]


class PostgresSession:
    def __init__(  # noqa: PLR0913
        self,
        host: str = POSTGRES_HOST,
        port: str = POSTGRES_PORT,
        database: str = POSTGRES_DATABASE,
        user: str = POSTGRES_USER,
        password: str = POSTGRES_PASSWORD,
        ssl: str = POSTGRES_SSLMODE,
        connection_limit: int = 1,
    ) -> None:
        self._pool: Pool = create_pool(
            min_size=1,
            max_size=connection_limit,
            database=database,
            user=user,
            password=password,
            host=host,
            port=port,
            ssl=ssl,
        )

    def __del__(self) -> None:
        if self._pool._initialized:  # pyright: ignore[reportPrivateUsage]
            ctx.spawn(self._pool.close)

    async def initialize(self) -> PostgresClient:
        await self._pool  # initialize pool
        return PostgresClient(connection=self.connection)

    async def dispose(self) -> None:
        if self._pool._initialized:  # pyright: ignore[reportPrivateUsage]
            await self._pool.close()

    def connection(self) -> PostgresConnectionContext:
        acquire_context: PoolAcquireContext = self._pool.acquire()  # pyright: ignore[reportUnknownMemberType]

        async def connection_acquire() -> PostgresConnection:
            acquired_connection: Connection = await acquire_context.__aenter__()  # pyright: ignore[reportUnknownVariableType]

            async def execute(
                statement: str,
                /,
                *args: Any,
            ) -> Any:
                try:
                    return await acquired_connection.execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                        statement,
                        *args,
                    )

                except Exception as exc:
                    raise PostgresException("Failed to execute SQL statement") from exc

            def transaction() -> PostgresTransactionContext:
                transaction_context: Transaction = acquired_connection.transaction()  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]

                async def transaction_enter() -> None:
                    await transaction_context.__aenter__()  # pyright: ignore[reportUnknownMemberType]

                async def transaction_exit(
                    exc_type: type[BaseException] | None,
                    exc_val: BaseException | None,
                    exc_tb: TracebackType | None,
                ) -> None:
                    await transaction_context.__aexit__(  # pyright: ignore[reportUnknownMemberType]
                        exc_type,
                        exc_val,
                        exc_tb,
                    )

                return PostgresTransactionContext(
                    enter_transaction=transaction_enter,
                    exit_transaction=transaction_exit,
                )

            return PostgresConnection(
                execute=execute,
                transaction=transaction,
            )

        async def connection_release(
            exc_type: type[BaseException] | None,
            exc_val: BaseException | None,
            exc_tb: TracebackType | None,
        ) -> None:
            await acquire_context.__aexit__(  # pyright: ignore[reportUnknownMemberType]
                exc_type,
                exc_val,
                exc_tb,
            )

        return PostgresConnectionContext(
            acquire_connection=connection_acquire,
            release_connection=connection_release,
        )
