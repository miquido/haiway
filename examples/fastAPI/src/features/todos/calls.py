from uuid import UUID

from features.todos.state import Todos
from haiway import ctx

__all__ = [
    "complete_todo",
]


async def complete_todo(
    *,
    identifier: UUID,
) -> None:
    await ctx.state(Todos).complete(identifier=identifier)
