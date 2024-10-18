from datetime import datetime
from typing import overload
from uuid import UUID, uuid4

from haiway import ctx
from integrations.postgres import PostgresClient, PostgresClientException

from solutions.user_tasks.types import UserTask

__all__ = [
    "postgres_task_create",
    "postgres_task_update",
    "postgres_tasks_fetch",
    "postgres_task_delete",
]


async def postgres_task_create(
    *,
    description: str,
) -> UserTask:
    postgres_client: PostgresClient = await ctx.dependency(PostgresClient)
    async with postgres_client.connection() as connection:
        try:  # actual SQL goes here...
            await connection.execute("EXAMPLE")

        except PostgresClientException as exc:
            ctx.log_debug(
                "Example postgres_task_create failed",
                exception=exc,
            )

        return UserTask(
            identifier=uuid4(),
            description=description,
            modified=datetime.now(),
            completed=False,
        )


async def postgres_task_update(
    *,
    task: UserTask,
) -> None:
    postgres_client: PostgresClient = await ctx.dependency(PostgresClient)
    async with postgres_client.connection() as connection:
        try:  # actual SQL goes here...
            await connection.execute("EXAMPLE")

        except PostgresClientException as exc:
            ctx.log_debug(
                "Example postgres_task_update failed",
                exception=exc,
            )


@overload
async def postgres_tasks_fetch(
    *,
    identifier: None = None,
) -> list[UserTask]: ...


@overload
async def postgres_tasks_fetch(
    *,
    identifier: UUID,
) -> UserTask: ...


async def postgres_tasks_fetch(
    identifier: UUID | None = None,
) -> list[UserTask] | UserTask:
    postgres_client: PostgresClient = await ctx.dependency(PostgresClient)
    async with postgres_client.connection() as connection:
        try:  # actual SQL goes here...
            await connection.execute("EXAMPLE")

        except PostgresClientException as exc:
            ctx.log_debug(
                "Example postgres_tasks_fetch failed",
                exception=exc,
            )

        if identifier:
            return UserTask(
                identifier=uuid4(),
                description="Example",
                modified=datetime.now(),
                completed=False,
            )

        else:
            return []


async def postgres_task_delete(
    *,
    identifier: UUID,
) -> None:
    postgres_client: PostgresClient = await ctx.dependency(PostgresClient)
    async with postgres_client.connection() as connection:
        try:  # actual SQL goes here...
            await connection.execute("EXAMPLE")

        except PostgresClientException as exc:
            ctx.log_debug(
                "Example postgres_task_delete failed",
                exception=exc,
            )
