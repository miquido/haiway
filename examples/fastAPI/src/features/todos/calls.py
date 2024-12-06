from uuid import UUID

from haiway import ctx

from features.todos.state import Todos

__all__ = [
    "complete_todo",
]


async def complete_todo(
    *,
    identifier: UUID,
) -> None:
    await ctx.state(Todos).complete(identifier=identifier)
