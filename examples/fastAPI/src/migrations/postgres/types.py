from typing import Protocol

__all__ = [
    "Migration",
]


class Migration(Protocol):
    async def __call__(self) -> None: ...
