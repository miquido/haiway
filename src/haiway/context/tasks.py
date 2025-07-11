from asyncio import Task, TaskGroup, get_event_loop
from collections.abc import Callable, Coroutine
from contextvars import ContextVar, Token, copy_context
from types import TracebackType
from typing import Any, ClassVar

from haiway.state import Immutable

__all__ = ("TaskGroupContext",)


class TaskGroupContext(Immutable):
    """
    Context manager for managing task groups within a scope.

    Provides a way to create and manage asyncio tasks within a context,
    ensuring proper task lifecycle management and context propagation.
    This class is immutable after initialization.
    """

    _context: ClassVar[ContextVar[TaskGroup]] = ContextVar[TaskGroup]("TaskGroupContext")

    @classmethod
    def run[Result, **Arguments](
        cls,
        function: Callable[Arguments, Coroutine[Any, Any, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Run a coroutine function as a task within the current task group.

        If called within a TaskGroupContext, creates a task in that group.
        If called outside any TaskGroupContext, creates a detached task.

        Parameters
        ----------
        function: Callable[Arguments, Coroutine[Any, Any, Result]]
            The coroutine function to run
        *args: Arguments.args
            Positional arguments to pass to the function
        **kwargs: Arguments.kwargs
            Keyword arguments to pass to the function

        Returns
        -------
        Task[Result]
            The created task
        """
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

    _group: TaskGroup
    _token: Token[TaskGroup] | None = None

    def __init__(
        self,
        task_group: TaskGroup | None = None,
    ) -> None:
        """
        Initialize a task group context.

        Parameters
        ----------
        task_group: TaskGroup | None
            The task group to use, or None to create a new one
        """
        object.__setattr__(
            self,
            "_group",
            task_group if task_group is not None else TaskGroup(),
        )
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
        """
        Enter this task group context.

        Enters the underlying task group and sets this context as current.

        Raises
        ------
        AssertionError
            If attempting to re-enter an already active context
        """
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
        """
        Exit this task group context.

        Restores the previous task group context and exits the underlying task group.
        Silently ignores task group exceptions to avoid masking existing exceptions.

        Parameters
        ----------
        exc_type: type[BaseException] | None
            Type of exception that caused the exit
        exc_val: BaseException | None
            Exception instance that caused the exit
        exc_tb: TracebackType | None
            Traceback for the exception

        Raises
        ------
        AssertionError
            If the context is not active
        """
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
