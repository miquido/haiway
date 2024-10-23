from collections.abc import Callable, Coroutine
from types import TracebackType
from typing import final

from haiway import State

from integrations.postgres.types import PostgresExecution

__all__ = [
    "PostgresClient",
    "PostgresConnection",
    "PostgresConnectionContext",
    "PostgresTransactionContext",
]


@final
class PostgresTransactionContext:
    def __init__(
        self,
        enter_transaction: Callable[[], Coroutine[None, None, None]],
        exit_transaction: Callable[
            [type[BaseException] | None, BaseException | None, TracebackType | None],
            Coroutine[None, None, None],
        ],
    ) -> None:
        self._enter_transaction: Callable[[], Coroutine[None, None, None]] = enter_transaction
        self._exit_transaction: Callable[
            [type[BaseException] | None, BaseException | None, TracebackType | None],
            Coroutine[None, None, None],
        ] = exit_transaction

    async def __aenter__(self) -> None:
        return await self._enter_transaction()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        await self._exit_transaction(
            exc_type,
            exc_val,
            exc_tb,
        )


class PostgresConnection(State):
    execute: PostgresExecution
    transaction: Callable[[], PostgresTransactionContext]


@final
class PostgresConnectionContext:
    def __init__(
        self,
        acquire_connection: Callable[[], Coroutine[None, None, PostgresConnection]],
        release_connection: Callable[
            [type[BaseException] | None, BaseException | None, TracebackType | None],
            Coroutine[None, None, None],
        ],
    ) -> None:
        self._acquire_connection: Callable[[], Coroutine[None, None, PostgresConnection]] = (
            acquire_connection
        )
        self._release_connection: Callable[
            [type[BaseException] | None, BaseException | None, TracebackType | None],
            Coroutine[None, None, None],
        ] = release_connection

    async def __aenter__(self) -> PostgresConnection:
        return await self._acquire_connection()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None:
        await self._release_connection(
            exc_type,
            exc_val,
            exc_tb,
        )


class PostgresClient(State):
    connection: Callable[[], PostgresConnectionContext]
