from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from types import TracebackType
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from uuid import UUID

if TYPE_CHECKING:
    from integrations.postgres.state import PostgresConnection

__all__ = [
    "PostgresConnectionContext",
    "PostgresConnectionPreparing",
    "PostgresException",
    "PostgresStatementExecuting",
    "PostgresTransactionContext",
    "PostgresTransactionPreparing",
    "PostgresValue",
]

type PostgresValue = UUID | datetime | date | time | str | bytes | float | int | bool | None
type PostgresRow = Mapping[str, Any]


@runtime_checkable
class PostgresStatementExecuting(Protocol):
    async def __call__(
        self,
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]: ...


@runtime_checkable
class PostgresTransactionContext(Protocol):
    async def __aenter__(self) -> None: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class PostgresTransactionPreparing(Protocol):
    def __call__(self) -> PostgresTransactionContext: ...


@runtime_checkable
class PostgresConnectionContext(Protocol):
    async def __aenter__(self) -> "PostgresConnection": ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> bool | None: ...


@runtime_checkable
class PostgresConnectionPreparing(Protocol):
    def __call__(self) -> PostgresConnectionContext: ...


class PostgresException(Exception):
    pass
