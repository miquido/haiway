from uuid import UUID

from solutions.user_tasks import UserTasks

__all__ = [
    "complete_todo_task",
]


async def complete_todo_task(
    *,
    identifier: UUID,
) -> None:
    match await UserTasks.fetch(identifier=identifier):
        case None:
            pass

        case task:
            await UserTasks.update(task=task.updated(completed=True))
