from asyncio import Task, TaskGroup, get_event_loop
from collections.abc import Callable, Coroutine
from contextvars import ContextVar, Token, copy_context
from types import TracebackType
from typing import Any, final

__all__ = [
    "TaskGroupContext",
]


@final
class TaskGroupContext:
    _context = ContextVar[TaskGroup]("TaskGroupContext")

    @classmethod
    def run[Result, **Arguments](
        cls,
        function: Callable[Arguments, Coroutine[None, None, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        try:
            return cls._context.get().create_task(
                function(*args, **kwargs),
                context=copy_context(),
            )

        except LookupError:  # spawn task out of group as a fallback
            return get_event_loop().create_task(
                function(*args, **kwargs),
                context=copy_context(),
            )

    __slots__ = (
        "_group",
        "_token",
    )

    def __init__(
        self,
    ) -> None:
        self._group: TaskGroup
        object.__setattr__(
            self,
            "_group",
            TaskGroup(),
        )
        self._token: Token[TaskGroup] | None
        object.__setattr__(
            self,
            "_token",
            None,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    async def __aenter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        await self._group.__aenter__()
        object.__setattr__(
            self,
            "_token",
            TaskGroupContext._context.set(self._group),
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        TaskGroupContext._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )

        try:
            await self._group.__aexit__(
                et=exc_type,
                exc=exc_val,
                tb=exc_tb,
            )

        except BaseException:
            pass  # silence TaskGroup exceptions, if there was exception already we will get it
