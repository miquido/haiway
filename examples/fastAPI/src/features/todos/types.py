from typing import Protocol, runtime_checkable
from uuid import UUID

__all__ = [
    "TodoCompletion",
]


@runtime_checkable
class TodoCompletion(Protocol):
    async def __call__(
        self,
        *,
        identifier: UUID,
    ) -> None: ...
