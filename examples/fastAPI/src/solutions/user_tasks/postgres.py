from collections.abc import Sequence
from datetime import datetime
from typing import overload
from uuid import UUID

from haiway import ctx

from integrations.postgres import PostgresConnection, PostgresException
from solutions.user_tasks.types import UserTask

__all__ = [
    "postgres_task_create",
    "postgres_task_delete",
    "postgres_task_update",
    "postgres_tasks_fetch",
]

CREATE_TODO_STATEMENT: str = """\
INSERT INTO
    todos (
        description
    )

VALUES
    (
        $1
    )

RETURNING
    id,
    modified,
    description,
    completed
;
"""


async def postgres_task_create(
    *,
    description: str,
) -> UserTask:
    try:  # actual SQL goes here...
        match await PostgresConnection.execute(
            CREATE_TODO_STATEMENT,
            description,
        ):
            case {
                "id": str() as identifier,
                "description": str() as description,
                "modified": datetime() as modified,
                "completed": bool() as completed,
            }:
                return UserTask(
                    identifier=identifier,
                    description=description,
                    modified=modified,
                    completed=completed,
                )

            case other:
                raise ValueError(f"Invalid database record:\n{other}")

    except PostgresException as exc:
        ctx.log_debug(
            "postgres_task_create failed",
            exception=exc,
        )
        raise exc


UPDATE_TODO_STATEMENT: str = """\
UPDATE
    todos

SET
    modified = now(),
    description = $2,
    completed =$3

WHERE
    id = $1
;
"""


async def postgres_task_update(
    *,
    task: UserTask,
) -> None:
    try:  # actual SQL goes here...
        await PostgresConnection.execute(
            UPDATE_TODO_STATEMENT,
            task.identifier,
            task.description,
            task.completed,
        )

    except PostgresException as exc:
        ctx.log_debug(
            "postgres_task_update failed",
            exception=exc,
        )
        raise exc


@overload
async def postgres_tasks_fetch(
    *,
    identifier: None = None,
) -> Sequence[UserTask]: ...


@overload
async def postgres_tasks_fetch(
    *,
    identifier: UUID,
) -> UserTask: ...


FETCH_TODOS_STATEMENT: str = """\
SELECT
    id,
    modified,
    description,
    completed

FROM
    todos
;
"""

FETCH_TODO_STATEMENT: str = """\
SELECT
    modified,
    description,
    completed

FROM
    todos

WHERE
    id = $1

LIMIT
    1
;
"""


async def postgres_tasks_fetch(
    identifier: UUID | None = None,
) -> Sequence[UserTask] | UserTask | None:
    try:  # actual SQL goes here...
        if identifier:
            match await PostgresConnection.fetch_one(FETCH_TODO_STATEMENT, identifier):
                case None:
                    return None

                case {
                    "modified": datetime() as modified,
                    "description": str() as description,
                    "completed": bool() as completed,
                }:
                    return UserTask(
                        identifier=identifier,
                        description=description,
                        modified=modified,
                        completed=completed,
                    )

                case other:
                    raise ValueError(f"Invalid database record:\n{other}")

        else:
            return [
                UserTask(
                    identifier=task["id"],
                    description=task["description"],
                    modified=task["modified"],
                    completed=task["completed"],
                )
                for task in await PostgresConnection.fetch(FETCH_TODOS_STATEMENT)
            ]

    except PostgresException as exc:
        ctx.log_debug(
            "postgres_tasks_fetch failed",
            exception=exc,
        )
        raise exc


DELETE_TODO_STATEMENT: str = """\
DELETE
    todos

WHERE
    id = $1
;
"""


async def postgres_task_delete(
    *,
    identifier: UUID,
) -> None:
    try:  # actual SQL goes here...
        await PostgresConnection.execute(
            DELETE_TODO_STATEMENT,
            identifier,
        )

    except PostgresException as exc:
        ctx.log_debug(
            "postgres_task_delete failed",
            exception=exc,
        )
        raise exc
