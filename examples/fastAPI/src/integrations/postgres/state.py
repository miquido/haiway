from collections.abc import Sequence
from typing import Any

from haiway import State, ctx

from integrations.postgres.types import (
    PostgresConnectionContext,
    PostgresConnectionPreparing,
    PostgresRow,
    PostgresStatementExecuting,
    PostgresTransactionContext,
    PostgresTransactionPreparing,
)

__all__ = [
    "Postgres",
    "PostgresConnection",
]


class PostgresConnection(State):
    execute_statement: PostgresStatementExecuting
    prepare_transaction: PostgresTransactionPreparing

    @classmethod
    async def fetch_one(
        cls,
        statement: str,
        /,
        *args: Any,
    ) -> PostgresRow | None:
        return next(
            (
                result
                for result in await ctx.state(cls).execute_statement(
                    statement,
                    *args,
                )
            ),
            None,
        )

    @classmethod
    async def fetch(
        cls,
        statement: str,
        /,
        *args: Any,
    ) -> Sequence[PostgresRow]:
        return await ctx.state(cls).execute_statement(
            statement,
            *args,
        )

    @classmethod
    async def execute(
        cls,
        statement: str,
        /,
        *args: Any,
    ) -> None:
        await ctx.state(cls).execute_statement(
            statement,
            *args,
        )

    @classmethod
    def transaction(cls) -> PostgresTransactionContext:
        return ctx.state(cls).prepare_transaction()


class Postgres(State):
    @classmethod
    def connection(cls) -> PostgresConnectionContext:
        return ctx.state(cls).prepare_connection()

    prepare_connection: PostgresConnectionPreparing
