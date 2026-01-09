from asyncio import Task, TaskGroup, get_running_loop
from collections.abc import Callable, Coroutine
from contextvars import ContextVar, Token, copy_context
from inspect import iscoroutine
from types import TracebackType
from typing import ClassVar, cast, final

from haiway.context.observability import ContextObservability, ObservabilityLevel

__all__ = ("ContextTaskGroup",)


@final  # consider immutable
class ContextTaskGroup:
    @classmethod
    def run[Result, **Arguments](
        cls,
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        coroutine: Coroutine[None, None, Result]
        if iscoroutine(coro):
            coroutine = cast(Coroutine[None, None, Result], coro)

        else:
            coroutine = cast(Callable[Arguments, Coroutine[None, None, Result]], coro)(
                *args, **kwargs
            )

        try:
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
        coroutine: Coroutine[None, None, Result]
        if iscoroutine(coro):
            coroutine = cast(Coroutine[None, None, Result], coro)

        else:
            coroutine = cast(Callable[Arguments, Coroutine[None, None, Result]], coro)(
                *args, **kwargs
            )

        # TODO: isolate further with detached observability scope?
        # TODO: manage custom background task group?
        return get_running_loop().create_task(
            coroutine,
            context=copy_context(),
        )

    _context: ClassVar[ContextVar[TaskGroup]] = ContextVar[TaskGroup]("ContextTaskGroup")
    __slots__ = (
        "_task_group",
        "_token",
    )

    def __init__(self) -> None:
        self._task_group: TaskGroup | None = None
        self._token: Token[TaskGroup] | None = None

    async def __aenter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        assert self._task_group is None  # nosec: B101
        self._task_group = TaskGroup()
        await self._task_group.__aenter__()
        self._token = ContextTaskGroup._context.set(self._task_group)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        assert self._task_group is not None  # nosec: B101

        ContextTaskGroup._context.reset(self._token)
        self._token = None

        try:
            await self._task_group.__aexit__(
                exc_type,
                exc_val,
                exc_tb,
            )

        except BaseExceptionGroup as exc:  # do not propagate group errors
            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                "Context task group exit failed",
                exception=exc,
            )

        finally:
            self._task_group = None
