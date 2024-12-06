from collections.abc import Mapping, Sequence
from datetime import date, datetime, time
from typing import Any, Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "PostgresException",
    "PostgresExecution",
    "PostgresValue",
]

type PostgresValue = UUID | datetime | date | time | str | bytes | float | int | bool | None
type PostgresRow = Mapping[str, Any]


@runtime_checkable
class PostgresExecution(Protocol):
    async def __call__(
        self,
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]: ...


class PostgresException(Exception):
    pass
