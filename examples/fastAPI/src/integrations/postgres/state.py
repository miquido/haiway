from collections.abc import Callable
from types import TracebackType
from typing import Any, Protocol, runtime_checkable

from haiway import State

from integrations.postgres.types import PostgresExecution, PostgresRow

__all__ = [
    "Postgres",
    "PostgresConnection",
    "PostgresConnectionContext",
    "PostgresTransactionContext",
]


@runtime_checkable
class PostgresTransactionContext(Protocol):
    async def __aenter__(self) -> None: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None: ...


class PostgresConnection(State):
    execute: PostgresExecution
    transaction: Callable[[], PostgresTransactionContext]

    async def fetch_one(
        self,
        query: str,
        /,
        *args: Any,
    ) -> PostgresRow | None:
        return next(
            (
                result
                for result in await self.execute(
                    query,
                    *args,
                )
            ),
            None,
        )


@runtime_checkable
class PostgresConnectionContext(Protocol):
    async def __aenter__(self) -> PostgresConnection: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None: ...


class Postgres(State):
    connection: Callable[[], PostgresConnectionContext]
