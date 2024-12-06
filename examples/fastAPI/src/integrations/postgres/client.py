from types import TracebackType
from typing import final

from asyncpg import (  # pyright: ignore[reportMissingTypeStubs]
    Connection,
    Pool,
    create_pool,  # pyright: ignore [reportUnknownVariableType]
)
from asyncpg.pool import PoolAcquireContext  # pyright: ignore[reportMissingTypeStubs]
from asyncpg.transaction import Transaction  # pyright: ignore[reportMissingTypeStubs]

from integrations.postgres.state import (
    PostgresConnection,
    PostgresConnectionContext,
    PostgresTransactionContext,
)
from integrations.postgres.types import PostgresException, PostgresRow, PostgresValue

__all__ = [
    "PostgresClient",
]


from integrations.postgres.config import (
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_SSLMODE,
    POSTGRES_USER,
)
from integrations.postgres.state import Postgres

__all__ = [
    "PostgresClient",
]


@final
class PostgresClient:
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

    async def __aenter__(self) -> Postgres:
        await self._pool  # initialize pool
        return Postgres(connection=self.connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._pool._initialized:  # pyright: ignore[reportPrivateUsage]
            await self._pool.close()

    def connection(self) -> PostgresConnectionContext:
        return _ConnectionContext(pool_context=self._pool.acquire())  # pyright: ignore[reportUnknownMemberType]


@final
class _TransactionContext:
    def __init__(
        self,
        transaction_context: Transaction,
    ) -> None:
        self._transaction_context: Transaction = transaction_context

    async def __aenter__(self) -> None:
        await self._transaction_context.__aenter__()  # pyright: ignore[reportUnknownMemberType]

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._transaction_context.__aexit__(  # pyright: ignore[reportUnknownMemberType]
            exc_type,
            exc_val,
            exc_tb,
        )


@final
class _ConnectionContext:
    def __init__(
        self,
        pool_context: PoolAcquireContext,
    ) -> None:
        self._pool_context: PoolAcquireContext = pool_context

    async def __aenter__(self) -> PostgresConnection:
        acquired_connection: Connection = await self._pool_context.__aenter__()  # pyright: ignore[reportUnknownVariableType]

        async def execute(
            statement: str,
            /,
            *args: PostgresValue,
        ) -> list[PostgresRow]:
            try:
                return await acquired_connection.fetch(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    statement,
                    *args,
                )

            except Exception as exc:
                raise PostgresException("Failed to execute SQL statement") from exc

        def transaction() -> PostgresTransactionContext:
            return _TransactionContext(transaction_context=acquired_connection.transaction())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

        return PostgresConnection(
            execute=execute,
            transaction=transaction,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._pool_context.__aexit__(  # pyright: ignore[reportUnknownMemberType]
            exc_type,
            exc_val,
            exc_tb,
        )
