from asyncio import Task, TaskGroup, get_running_loop
from collections.abc import Callable, Coroutine
from contextvars import ContextVar, Token, copy_context
from inspect import iscoroutine
from types import TracebackType
from typing import ClassVar, cast

from haiway.context.observability import ObservabilityContext, ObservabilityLevel
from haiway.context.variables import VariablesContext
from haiway.types import Immutable

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
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
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
        coroutine: Coroutine[None, None, Result]
        if iscoroutine(coro):
            coroutine = cast(Coroutine[None, None, Result], coro)

        else:
            coroutine = cast(Callable[Arguments, Coroutine[None, None, Result]], coro)(
                *args, **kwargs
            )

        try:
            with VariablesContext(isolated=True):  # isolate variables
                return cls._context.get().create_task(
                    coroutine,
                    context=copy_context(),
                )

        except LookupError:  # spawn task in the background as a fallback
            return cls.background_run(
                coroutine,
                *args,
                **kwargs,
            )

    @classmethod
    def background_run[Result, **Arguments](
        cls,
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
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

        coroutine: Coroutine[None, None, Result]
        if iscoroutine(coro):
            coroutine = cast(Coroutine[None, None, Result], coro)

        else:
            coroutine = cast(Callable[Arguments, Coroutine[None, None, Result]], coro)(
                *args, **kwargs
            )

        with VariablesContext(isolated=True):  # isolate variables
            # TODO: isolate further with detached state and observability scope?
            # TODO: manage custom background task group?
            return get_running_loop().create_task(
                coroutine,
                context=copy_context(),
            )

    _group: TaskGroup | None = None
    _token: Token[TaskGroup] | None = None

    async def __aenter__(self) -> None:
        """
        Enter this task group context.

        Enters the underlying task group and sets this context as current.
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
        """
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        assert self._group is not None  # nosec: B101

        TaskGroupContext._context.reset(self._token)
        try:
            await self._group.__aexit__(
                exc_type,
                exc_val,
                exc_tb,
            )

        except BaseExceptionGroup as exc:  # do not propagate group errors
            ObservabilityContext.record_log(
                ObservabilityLevel.ERROR,
                "Scope task group exit failed",
                exception=exc,
            )

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
