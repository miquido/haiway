from datetime import datetime
from typing import Protocol, overload, runtime_checkable
from uuid import UUID

from haiway import State

__all__ = [
    "UserTask",
    "UserTaskCreation",
    "UserTaskDeletion",
    "UserTaskFetching",
    "UserTaskUpdating",
]


class UserTask(State):
    identifier: UUID
    modified: datetime
    description: str
    completed: bool


@runtime_checkable
class UserTaskCreation(Protocol):
    async def __call__(
        self,
        *,
        description: str,
    ) -> UserTask: ...


@runtime_checkable
class UserTaskUpdating(Protocol):
    async def __call__(
        self,
        *,
        task: UserTask,
    ) -> None: ...


@runtime_checkable
class UserTaskFetching(Protocol):
    @overload
    async def __call__(
        self,
        *,
        identifier: None = None,
    ) -> list[UserTask]: ...

    @overload
    async def __call__(
        self,
        *,
        identifier: UUID,
    ) -> UserTask: ...

    async def __call__(
        self,
        *,
        identifier: UUID | None = None,
    ) -> list[UserTask] | UserTask: ...


@runtime_checkable
class UserTaskDeletion(Protocol):
    async def __call__(
        self,
        *,
        identifier: UUID,
    ) -> None: ...
