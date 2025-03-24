from uuid import UUID

from haiway import State
from haiway.context import ctx

from features.todos.types import TodoCompletion
from features.todos.user_tasks import complete_todo_task

__all__ = [
    "Todos",
]


class Todos(State):
    @classmethod
    async def complete_todo(
        cls,
        *,
        identifier: UUID,
    ) -> None:
        await ctx.state(cls).complete(identifier=identifier)

    complete: TodoCompletion = complete_todo_task
