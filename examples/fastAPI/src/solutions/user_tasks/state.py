from collections.abc import Sequence
from typing import overload
from uuid import UUID

from haiway import State, ctx

from solutions.user_tasks.postgres import (
    postgres_task_create,
    postgres_task_delete,
    postgres_task_update,
    postgres_tasks_fetch,
)
from solutions.user_tasks.types import (
    UserTask,
    UserTaskCreation,
    UserTaskDeletion,
    UserTaskFetching,
    UserTaskUpdating,
)

__all__ = [
    "UserTasks",
]


class UserTasks(State):
    @classmethod
    async def create_task(
        cls,
        description: str,
    ) -> UserTask:
        return await ctx.state(UserTasks).create(description=description)

    @classmethod
    async def update_task(
        cls,
        task: UserTask,
        /,
    ) -> None:
        await ctx.state(cls).update(task=task)

    @overload
    @classmethod
    async def fetch_tasks(
        cls,
        *,
        identifier: None = None,
    ) -> Sequence[UserTask]: ...

    @overload
    @classmethod
    async def fetch_tasks(
        cls,
        *,
        identifier: UUID,
    ) -> UserTask: ...

    @classmethod
    async def fetch_tasks(
        cls,
        *,
        identifier: UUID | None = None,
    ) -> Sequence[UserTask] | UserTask:
        return await ctx.state(cls).fetch(identifier=identifier)

    @classmethod
    async def delete_task(
        cls,
        *,
        identifier: UUID,
    ) -> None:
        return await ctx.state(cls).delete(identifier=identifier)

    fetch: UserTaskFetching = postgres_tasks_fetch
    create: UserTaskCreation = postgres_task_create
    update: UserTaskUpdating = postgres_task_update
    delete: UserTaskDeletion = postgres_task_delete
