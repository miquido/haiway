import signal
from asyncio import AbstractEventLoop, Task, TaskGroup, get_running_loop
from collections.abc import Callable, Collection, Coroutine, MutableMapping, MutableSet
from contextvars import Context, ContextVar, Token, copy_context
from inspect import iscoroutine
from threading import Lock
from types import FrameType, TracebackType
from typing import Any, ClassVar, Final, cast, final

from haiway.context.observability import ContextObservability, ObservabilityLevel

__all__ = ("ContextTaskGroup",)

_SHUTDOWN_SIGNALS: tuple[int, ...] = (
    signal.SIGINT,
    signal.SIGTERM,
    getattr(signal, "SIGBREAK", signal.SIGTERM),
)


@final
class BackgroundTaskGroup:
    __slots__ = (
        "_lock",
        "_loops_tasks",
    )

    def __init__(self) -> None:
        self._loops_tasks: MutableMapping[AbstractEventLoop, MutableSet[Task[Any]]] = {}
        self._lock: Lock = Lock()

    def create_task[Result](
        self,
        coroutine: Coroutine[None, None, Result],
        /,
        *,
        context: Context | None = None,
    ) -> Task[Result]:
        loop: AbstractEventLoop = get_running_loop()
        loop_tasks: MutableSet[Task[Any]] | None
        install_handlers: bool = False
        with self._lock:
            loop_tasks = self._loops_tasks.get(loop)
            if loop_tasks is None:
                loop_tasks = set()
                self._loops_tasks[loop] = loop_tasks
                install_handlers = True

        if install_handlers:
            # Install best-effort signal handlers to shut down background tasks.
            self._install_loop_handlers(loop)

        task: Task[Any] = loop.create_task(
            coroutine,
            context=context,
        )
        loop_tasks.add(task)
        task.add_done_callback(loop_tasks.discard)
        return task

    def shutdown(
        self,
        *,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        if loop is None:
            loop = get_running_loop()

        loop_tasks: Collection[Task[Any]]
        with self._lock:
            loop_tasks = tuple(self._loops_tasks.pop(loop, ()))

        if loop.is_closed():
            return  # can't do anything at this state

        def clear_tasks() -> None:
            for task in loop_tasks:
                task.cancel()

        loop.call_soon_threadsafe(clear_tasks)

    def _install_loop_handlers(
        self,
        loop: AbstractEventLoop,
        /,
    ) -> None:
        for signum in _SHUTDOWN_SIGNALS:

            def schedule_shutdown(
                *,
                _loop: AbstractEventLoop = loop,
            ) -> None:
                self.shutdown(loop=_loop)

            try:
                loop.add_signal_handler(signum, schedule_shutdown)

            except (NotImplementedError, RuntimeError, ValueError):
                previous_handler: Any = signal.getsignal(signum)

                if callable(previous_handler):

                    def _composed_handler(
                        _signum: int,
                        _frame: FrameType | None,
                        *,
                        _previous: Callable[[int, FrameType | None], Any] = previous_handler,
                    ) -> None:
                        schedule_shutdown()
                        _previous(_signum, _frame)

                    try:
                        signal.signal(signum, _composed_handler)

                    except ValueError:
                        continue
                else:

                    def _handler(
                        _signum: int,
                        _frame: FrameType | None,
                    ) -> None:
                        schedule_shutdown()

                    try:
                        signal.signal(signum, _handler)

                    except ValueError:
                        continue


BACKGROUND_TASKS: Final[BackgroundTaskGroup] = BackgroundTaskGroup()


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
        try:
            task_group = cls._context.get()

        except LookupError:  # spawn task in the background as a fallback
            return cls.background_run(
                coro,
                *args,
                **kwargs,
            )

        coroutine: Coroutine[None, None, Result]
        if iscoroutine(coro):
            coroutine = cast(Coroutine[None, None, Result], coro)

        else:
            coroutine = cast(Callable[Arguments, Coroutine[None, None, Result]], coro)(
                *args, **kwargs
            )

        return task_group.create_task(
            coroutine,
            context=copy_context(),
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

        return BACKGROUND_TASKS.create_task(
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

        except ExceptionGroup as exc:  # do not propagate group errors
            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                "Context task group exit failed",
                exception=exc,
            )

        finally:
            self._task_group = None
