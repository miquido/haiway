from collections.abc import Callable, Mapping
from types import TracebackType
from typing import Any, final

from asyncpg import Connection, Pool  # pyright: ignore[reportMissingTypeStubs]
from asyncpg.pool import PoolAcquireContext  # pyright: ignore[reportMissingTypeStubs]
from asyncpg.transaction import Transaction  # pyright: ignore[reportMissingTypeStubs]

from integrations.postgres.types import PostgresClientException

__all__ = [
    "PostgresConnection",
    "PostgresTransaction",
    "PostgresConnectionContext",
]


@final
class PostgresConnection:
    def __init__(
        self,
        connection: Connection,
    ) -> None:
        self._connection: Connection = connection

    async def execute(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> str:
        try:
            return await self._connection.execute(  # pyright: ignore[reportUnknownMemberType]
                statement,
                *args,
            )

        except Exception as exc:
            raise PostgresClientException("Failed to execute SQL statement") from exc

    async def fetch(
        self,
        query: str,
        /,
        *args: Any,
    ) -> list[Mapping[str, Any]]:
        try:
            return await self._connection.fetch(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
                query,
                *args,
            )

        except Exception as exc:
            raise PostgresClientException("Failed to execute SQL statement") from exc

    async def fetch_one(
        self,
        query: str,
        /,
        *args: Any,
    ) -> Mapping[str, Any] | None:
        try:
            return next(
                (
                    result
                    for result in await self.fetch(
                        query,
                        *args,
                    )
                ),
                None,
            )

        except Exception as exc:
            raise PostgresClientException("Failed to execute SQL statement") from exc

    def transaction(self) -> "PostgresTransaction":
        return PostgresTransaction(
            connection=self,
            transaction=self._connection.transaction(),  # pyright: ignore[reportUnknownMemberType]
        )

    async def set_type_codec(
        self,
        type_name: str,
        /,
        encoder: Callable[[str], Any],
        decoder: Callable[[str], Any],
        schema_name: str = "pg_catalog",
    ) -> None:
        await self._connection.set_type_codec(  # pyright: ignore[reportUnknownMemberType]
            type_name,
            decoder=decoder,
            encoder=encoder,
            schema=schema_name,
            format="text",
        )


@final
class PostgresTransaction:
    def __init__(
        self,
        connection: PostgresConnection,
        transaction: Transaction,
    ) -> None:
        self._connection: PostgresConnection = connection
        self._transaction: Transaction = transaction

    async def __aenter__(self) -> PostgresConnection:
        await self._transaction.__aenter__()
        return self._connection

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._transaction.__aexit__(  # pyright: ignore[reportUnknownMemberType]
            exc_type,
            exc_val,
            exc_tb,
        )


@final
class PostgresConnectionContext:
    def __init__(
        self,
        pool: Pool,
    ) -> None:
        self._pool: Pool = pool
        self._context: PoolAcquireContext

    async def __aenter__(self) -> PostgresConnection:
        assert not hasattr(self, "_context") or self._context is None  # nosec: B101
        self._context: PoolAcquireContext = (await self._pool or self._pool).acquire()  # pyright: ignore[reportUnknownMemberType]

        return PostgresConnection(
            connection=await self._context.__aenter__(),  # pyright: ignore[reportUnknownArgumentType]
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert hasattr(self, "_context") and self._context is not None  # nosec: B101

        try:
            await self._context.__aexit__(  # pyright: ignore[reportUnknownMemberType]
                exc_type,
                exc_val,
                exc_tb,
            )

        finally:
            del self._context
