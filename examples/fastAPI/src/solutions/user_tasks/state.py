from haiway import Structure

from solutions.user_tasks.postgres import (
    postgres_task_create,
    postgres_task_delete,
    postgres_task_update,
    postgres_tasks_fetch,
)
from solutions.user_tasks.types import (
    UserTaskCreation,
    UserTaskDeletion,
    UserTaskFetching,
    UserTaskUpdating,
)

__all__ = [
    "UserTasks",
]


class UserTasks(Structure):
    fetch: UserTaskFetching = postgres_tasks_fetch
    create: UserTaskCreation = postgres_task_create
    update: UserTaskUpdating = postgres_task_update
    delete: UserTaskDeletion = postgres_task_delete
