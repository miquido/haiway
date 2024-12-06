from uuid import UUID

from haiway import ctx

from solutions.user_tasks import UserTask, UserTasks

__all__ = [
    "complete_todo_task",
]


async def complete_todo_task(
    *,
    identifier: UUID,
) -> None:
    task: UserTask = await ctx.state(UserTasks).fetch(identifier=identifier)
    await ctx.state(UserTasks).update(task=task.updated(completed=True))
