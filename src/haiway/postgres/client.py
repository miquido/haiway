"""AsyncPG-backed connection pooling for the Postgres state integration."""

from collections.abc import Sequence
from types import TracebackType

from asyncpg import (  # pyright: ignore[reportMissingTypeStubs]
    Connection,
    Pool,
    create_pool,  # pyright: ignore [reportUnknownVariableType]
)
from asyncpg.pool import PoolAcquireContext  # pyright: ignore[reportMissingTypeStubs]
from asyncpg.transaction import Transaction  # pyright: ignore[reportMissingTypeStubs]

from haiway.postgres.config import (
    POSTGRES_CONNECTIONS,
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_SSLMODE,
    POSTGRES_USER,
)
from haiway.postgres.state import (
    Postgres,
    PostgresConnection,
    PostgresConnectionContext,
    PostgresTransactionContext,
)
from haiway.postgres.types import (
    PostgresException,
    PostgresRow,
    PostgresValue,
)
from haiway.types import Immutable

__all__ = ("PostgresConnectionPool",)


class PostgresConnectionPool(Immutable):
    """Disposable that instantiates a connection pool."""

    host: str = POSTGRES_HOST
    port: str = POSTGRES_PORT
    database: str = POSTGRES_DATABASE
    user: str = POSTGRES_USER
    password: str = POSTGRES_PASSWORD
    ssl: str = POSTGRES_SSLMODE
    connection_limit: int = POSTGRES_CONNECTIONS
    _pool: Pool | None = None  # initialized on demand

    async def __aenter__(self) -> Postgres:
        object.__setattr__(
            self,
            "_pool",
            await create_pool(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                ssl=self.ssl,
                min_size=1,
                max_size=self.connection_limit,
            ),
        )

        return Postgres(connection_acquiring=self.acquire_connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._pool is not None, "Postgres connection pool is not initialized"  # nosec: B101
        if self._pool._initialized:
            await self._pool.close()

    def acquire_connection(self) -> PostgresConnectionContext:
        """Return an async context manager yielding a ``PostgresConnection``."""

        assert self._pool is not None, "Postgres connection pool is not initialized"  # nosec: B101
        return _ConnectionContext(_pool_context=self._pool.acquire())


class _TransactionContext(Immutable):
    _transaction_context: Transaction

    async def __aenter__(self) -> None:
        await self._transaction_context.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._transaction_context.__aexit__(
            exc_type,
            exc_val,
            exc_tb,
        )


class _ConnectionContext(Immutable):
    _pool_context: PoolAcquireContext

    async def __aenter__(self) -> PostgresConnection:
        acquired_connection: Connection = await self._pool_context.__aenter__()

        async def execute(
            statement: str,
            /,
            *args: PostgresValue,
        ) -> Sequence[PostgresRow]:
            try:
                return tuple(
                    PostgresRow(record)
                    for record in await acquired_connection.fetch(
                        statement,
                        *args,
                    )
                )

            except Exception as exc:
                raise PostgresException("Failed to execute SQL statement") from exc

        def transaction() -> PostgresTransactionContext:
            return _TransactionContext(_transaction_context=acquired_connection.transaction())

        return PostgresConnection(
            statement_executing=execute,
            transaction_preparing=transaction,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._pool_context.__aexit__(
            exc_type,
            exc_val,
            exc_tb,
        )
