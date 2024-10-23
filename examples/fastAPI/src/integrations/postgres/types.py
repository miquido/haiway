from collections.abc import Iterable, Mapping
from datetime import date, datetime, time
from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "PostgresValue",
    "PostgresExecution",
    "PostgresException",
]

type PostgresValue = UUID | datetime | date | time | str | bytes | float | int | bool | None
type PostgresRow = Mapping[str, PostgresValue]


@runtime_checkable
class PostgresExecution(Protocol):
    async def __call__(
        self,
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Iterable[PostgresRow]: ...


class PostgresException(Exception):
    pass
