from collections.abc import Sequence
from types import TracebackType
from typing import final

from asyncpg import (  # pyright: ignore[reportMissingTypeStubs]
    Connection,
    Pool,
    create_pool,  # pyright: ignore [reportUnknownVariableType]
)
from asyncpg.pool import PoolAcquireContext  # pyright: ignore[reportMissingTypeStubs]
from asyncpg.transaction import Transaction  # pyright: ignore[reportMissingTypeStubs]

from integrations.postgres.config import (
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_SSLMODE,
    POSTGRES_USER,
)
from integrations.postgres.state import (
    Postgres,
    PostgresConnection,
    PostgresConnectionContext,
    PostgresTransactionContext,
)
from integrations.postgres.types import (
    PostgresException,
    PostgresRow,
    PostgresValue,
)

__all__ = [
    "PostgresConnectionPool",
]


@final
class PostgresConnectionPool:
    __slots__ = (
        "_connection_limit",
        "_database",
        "_host",
        "_password",
        "_pool",
        "_port",
        "_ssl",
        "_user",
    )

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
        self._host: str = host
        self._port: str = port
        self._database: str = database
        self._user: str = user
        self._password: str = password
        self._ssl: str = ssl
        self._connection_limit: int = connection_limit
        self._pool: Pool

    async def __aenter__(self) -> Postgres:
        self._pool = create_pool(
            min_size=1,
            max_size=self._connection_limit,
            database=self._database,
            user=self._user,
            password=self._password,
            host=self._host,
            port=self._port,
            ssl=self._ssl,
        )
        await self._pool  # initialize pool
        return Postgres(prepare_connection=self.prepare_connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._pool._initialized:  # pyright: ignore[reportPrivateUsage]
            await self._pool.close()

    def prepare_connection(self) -> PostgresConnectionContext:
        return _ConnectionContext(pool_context=self._pool.acquire())  # pyright: ignore[reportUnknownMemberType]


@final
class _TransactionContext:
    __slots__ = ("_transaction_context",)

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
    __slots__ = ("_pool_context",)

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
        ) -> Sequence[PostgresRow]:
            try:
                return [
                    dict(record)  # convert to dict to allow match patterns
                    for record in await acquired_connection.fetch(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                        statement,
                        *args,
                    )
                ]

            except Exception as exc:
                raise PostgresException("Failed to execute SQL statement") from exc

        def transaction() -> PostgresTransactionContext:
            return _TransactionContext(transaction_context=acquired_connection.transaction())  # pyright: ignore[reportUnknownMemberType, reportUnknownArgumentType]

        return PostgresConnection(
            execute_statement=execute,
            prepare_transaction=transaction,
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
