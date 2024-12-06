from typing import overload
from uuid import UUID

from haiway import ctx

from solutions.user_tasks.state import UserTasks
from solutions.user_tasks.types import UserTask

__all__ = [
    "create_task",
    "delete_task",
    "fetch_tasks",
    "update_task",
]


async def create_task(
    description: str,
) -> UserTask:
    return await ctx.state(UserTasks).create(description=description)


async def update_task(
    task: UserTask,
    /,
) -> None:
    await ctx.state(UserTasks).update(task=task)


@overload
async def fetch_tasks(
    *,
    identifier: None = None,
) -> list[UserTask]: ...


@overload
async def fetch_tasks(
    *,
    identifier: UUID,
) -> UserTask: ...


async def fetch_tasks(
    identifier: UUID | None = None,
) -> list[UserTask] | UserTask:
    return await ctx.state(UserTasks).fetch(identifier=identifier)


async def delete_task(
    *,
    identifier: UUID,
) -> None:
    return await ctx.state(UserTasks).delete(identifier=identifier)
