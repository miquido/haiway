from asyncio import Task, TaskGroup, get_running_loop
from collections.abc import Callable, Coroutine
from contextvars import ContextVar, Token, copy_context
from types import TracebackType
from typing import Any, ClassVar

from haiway.context.variables import VariablesContext
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
        coro: Callable[Arguments, Coroutine[Any, Any, Result]] | Coroutine[Any, Any, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Run a coroutine function as a task within the current task group.

        If called within a TaskGroupContext, creates a task in that group.
        If called outside any TaskGroupContext, creates a background task.

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
            with VariablesContext(isolated=True):  # isolate variables
                return cls._context.get().create_task(
                    coro if isinstance(coro, Coroutine) else coro(*args, **kwargs),
                    context=copy_context(),
                )

        except LookupError:  # spawn task in the background as a fallback
            return cls.background_run(
                coro,
                *args,
                **kwargs,
            )

    @classmethod
    def background_run[Result, **Arguments](
        cls,
        coro: Callable[Arguments, Coroutine[Any, Any, Result]] | Coroutine[Any, Any, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Run a coroutine function as a background detached task.

        Parameters
        ----------
        function: Callable[Arguments, Coroutine[Any, Any, Result]] | Coroutine[Any, Any, Result]
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

        with VariablesContext(isolated=True):  # isolate variables
            # TODO: isolate further with detached state and observability scope?
            # TODO: manage custom background task group?
            return get_running_loop().create_task(
                coro if isinstance(coro, Coroutine) else coro(*args, **kwargs),
                context=copy_context(),
            )

    _group: TaskGroup | None = None
    _token: Token[TaskGroup] | None = None

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
        object.__setattr__(
            self,
            "_group",
            TaskGroup(),
        )

        assert self._group is not None  # nosec: B101
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
        assert self._group is not None  # nosec: B101

        try:
            TaskGroupContext._context.reset(self._token)
            await self._group.__aexit__(
                exc_type,
                exc_val,
                exc_tb,
            )

        except ExceptionGroup:
            pass  # skip task group exceptions

        finally:
            object.__setattr__(
                self,
                "_token",
                None,
            )
            object.__setattr__(
                self,
                "_group",
                None,
            )
