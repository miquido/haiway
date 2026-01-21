import signal
from asyncio import AbstractEventLoop, CancelledError, Task, TaskGroup, gather, get_running_loop
from collections.abc import Callable, Collection, Coroutine, MutableMapping, MutableSet
from contextvars import Context, ContextVar, Token, copy_context
from inspect import iscoroutine
from threading import Lock
from types import FrameType, TracebackType
from typing import Any, ClassVar, cast, final

from haiway.context.observability import ContextObservability, ObservabilityLevel

__all__ = (
    "BackgroundTaskGroup",
    "ContextTaskGroup",
)


@final  # global background tasks
class BackgroundTaskGroup:
    _lock: ClassVar[Lock] = Lock()
    _loops_tasks: ClassVar[MutableMapping[AbstractEventLoop, MutableSet[Task[Any]]]] = {}

    @classmethod
    def create_task[Result](
        cls,
        coroutine: Coroutine[None, None, Result],
        /,
        *,
        context: Context | None = None,
    ) -> Task[Result]:
        loop: AbstractEventLoop = get_running_loop()

        task: Task[Any] = loop.create_task(
            coroutine,
            context=context,
        )

        loop_tasks: MutableSet[Task[Any]] | None
        with cls._lock:
            loop_tasks = cls._loops_tasks.get(loop)
            if loop_tasks is None:
                loop_tasks = set()
                cls._loops_tasks[loop] = loop_tasks

            loop_tasks.add(task)

        def handle_done(completed: Task[Any]) -> None:
            loop_tasks.discard(completed)

            try:
                exception = completed.exception()
            except CancelledError:
                return

            if exception is None:
                return

            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                "Background task failed",
                exception=exception,
            )

        task.add_done_callback(handle_done)
        return task

    @classmethod
    def shutdown(
        cls,
        *,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        if loop is None:
            loop = get_running_loop()

        loop_tasks: Collection[Task[Any]]
        with cls._lock:
            loop_tasks = tuple(cls._loops_tasks.pop(loop, ()))

        if loop.is_closed():
            return

        else:
            def cancel_tasks() -> None:
                for task in loop_tasks:
                    try:
                        task.cancel()
                    except RuntimeError:
                        pass

            async def drain_tasks() -> None:
                await gather(*loop_tasks, return_exceptions=True)

            def schedule_cleanup() -> None:
                cancel_tasks()

                try:
                    loop.create_task(drain_tasks())  # noqa: RUF006
                except RuntimeError:
                    pass

            try:
                loop.call_soon_threadsafe(schedule_cleanup)
            except RuntimeError:
                cancel_tasks()

    @classmethod
    def shutdown_all(cls) -> None:
        loops: Collection[AbstractEventLoop]
        with cls._lock:
            loops = tuple(cls._loops_tasks.keys())

        for loop in loops:
            cls.shutdown(loop=loop)


# Install best-effort signal handlers to shut down background tasks.
for signum in (
    signal.SIGINT,
    signal.SIGTERM,
):
    previous_handler: Any = signal.getsignal(signum)

    def handle_signal(
        received: int,
        frame: FrameType | None,
        *,
        previous: signal.Handlers | Callable[[int, FrameType | None], None] = previous_handler,
    ) -> None:
        BackgroundTaskGroup.shutdown_all()
        if previous is signal.SIG_IGN:
            return

        if previous is signal.SIG_DFL:
            signal.signal(received, signal.SIG_DFL)
            signal.raise_signal(received)
            return

        if callable(previous):
            previous(received, frame)

    try:
        signal.signal(signum, handle_signal)
    except (OSError, RuntimeError, ValueError):
        pass  # ignore


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

        return BackgroundTaskGroup.create_task(
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

        try:
            await self._task_group.__aexit__(
                exc_type,
                exc_val,
                exc_tb,
            )

        except ExceptionGroup as exc:  # log before propagation
            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                "Context task group exit failed",
                exception=exc,
            )
            if exc_val is not None:
                raise exc_val from exc  # reraise currently handled exception

            else:
                raise  # raise exit exception

        finally:
            ContextTaskGroup._context.reset(self._token)
            self._token = None
            self._task_group = None
