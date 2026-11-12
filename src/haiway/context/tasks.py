from asyncio import (
    AbstractEventLoop,
    CancelledError,
    Task,
    TaskGroup,
    get_running_loop,
    wait,
)
from collections.abc import (
    Callable,
    Collection,
    Coroutine,
    Generator,
    MutableMapping,
    MutableSet,
    Sequence,
)
from contextlib import contextmanager
from contextvars import Context, ContextVar, Token, copy_context
from inspect import iscoroutine
from types import CoroutineType, FunctionType, MethodType, TracebackType
from typing import Any, ClassVar, Final, Self, cast, final
from weakref import WeakKeyDictionary

from haiway.context.observability import ContextObservability, ObservabilityLevel

__all__ = (
    "BackgroundTaskGroup",
    "ContextTaskGroup",
    "ScopeTaskGroup",
)


@final  # global background tasks
class BackgroundTaskGroup:
    # tasks can only be created from within a running loop, i.e. from its own thread, and are
    # discarded by done callbacks running on that same thread - multithreading is not supported,
    # hence no lock is required to guard the mapping.
    # keeping the loops weakly lets a loop completing without an explicit shutdown drop its entry
    # instead of leaking it - pending tasks reference their loop, keeping it alive until they end.
    _loops_tasks: ClassVar[MutableMapping[AbstractEventLoop, MutableSet[Task[Any]]]] = (
        WeakKeyDictionary()
    )

    @classmethod
    def create_task[Result](
        cls,
        coroutine: Coroutine[None, None, Result],
        /,
        *,
        context: Context | None = None,
    ) -> Task[Result]:
        loop: AbstractEventLoop = get_running_loop()

        task: Task[Result] = loop.create_task(
            coroutine,
            context=context,
        )

        tasks: MutableSet[Task[Any]]
        loop_tasks: MutableSet[Task[Any]] | None = cls._loops_tasks.get(loop)
        if loop_tasks is None:
            tasks = set()
            cls._loops_tasks[loop] = tasks

        else:
            tasks = loop_tasks

        tasks.add(task)

        def handle_done(completed: Task[Any]) -> None:
            tasks.discard(completed)

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

        # run detached like the task itself - the spawning scope may be long gone
        task.add_done_callback(handle_done, context=Context())
        return task

    @classmethod
    def shutdown(
        cls,
        *,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        if loop is None:
            loop = get_running_loop()

        loop_tasks: Collection[Task[Any]] = tuple(cls._loops_tasks.pop(loop, ()))

        if loop.is_closed():
            return

        else:
            # the failures of the tasks are retrieved by their done callbacks, nothing is
            # left to wait for here - cancelling is all of it

            def cancel_tasks() -> None:
                for task in loop_tasks:
                    try:
                        task.cancel()

                    except RuntimeError:
                        pass  # the loop closed meanwhile, it runs nothing anymore

            try:  # within the loop of the tasks - cancelling is not thread safe
                loop.call_soon_threadsafe(cancel_tasks)

            except RuntimeError:
                cancel_tasks()

    @classmethod
    def shutdown_all(cls) -> None:
        for loop in tuple(cls._loops_tasks.keys()):
            cls.shutdown(loop=loop)


def _coroutine[Result, **Arguments](
    coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
    /,
    *args: Arguments.args,
    **kwargs: Arguments.kwargs,
) -> Coroutine[None, None, Result]:
    # the coroutine a task runs - given, or made by calling what was given. a coroutine
    # and a function, what is given the most, are told apart by their type: a function
    # or a method is never a coroutine, and `iscoroutine` falls back to an abstract check
    # for anything which is not one. the types are spelled for the checker alone, as
    # they would be built anew on every call
    if type(coro) is CoroutineType:
        return cast("Coroutine[None, None, Result]", coro)

    if type(coro) in (FunctionType, MethodType) or not iscoroutine(coro):
        return cast("Callable[Arguments, Coroutine[None, None, Result]]", coro)(*args, **kwargs)

    return cast("Coroutine[None, None, Result]", coro)


# a scope living long, a server for instance, would otherwise keep every failure of its tasks
# until it ends - every failure is logged when it happens, the ones past the limit are then
# only counted
_MAX_COLLECTED_ERRORS: Final[int] = 32


@final
class _ScopeTasks:
    __slots__ = (
        "_cancelling",
        "_closed",
        "_dropped",
        "_errors",
        "_loop",
        "_tasks",
    )

    def __init__(self) -> None:
        self._loop: AbstractEventLoop = get_running_loop()
        self._tasks: MutableSet[Task[Any]] = set()
        self._errors: list[BaseException] = []
        self._dropped: int = 0
        self._closed: bool = False
        self._cancelling: bool = False

    def create_task[Result](
        self,
        coroutine: Coroutine[None, None, Result],
        /,
        *,
        context: Context,
    ) -> Task[Result]:
        if self._closed:
            coroutine.close()  # never started
            raise RuntimeError("Context scope tasks are already joined, spawn rejected")

        task: Task[Result] = self._loop.create_task(
            coroutine,
            context=context,
        )
        self._tasks.add(task)
        task.add_done_callback(self._completed)
        if self._cancelling:
            task.cancel()  # spawned while cancelling, e.g. by a cleanup of another task

        return task

    def _completed(
        self,
        task: Task[Any],
        /,
    ) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return

        # retrieving it here also marks it as handled - it is raised on join instead
        if (exception := task.exception()) is None:
            return

        # reported right away, a failure of a task must not stay unnoticed until the scope ends
        ContextObservability.record_log(
            ObservabilityLevel.ERROR,
            "Context scope task failed",
            exception=exception,
        )
        if len(self._errors) < _MAX_COLLECTED_ERRORS:
            self._errors.append(exception)

        else:
            self._dropped += 1

    async def join(
        self,
        *,
        cancelling: bool,
        shielded: bool,
    ) -> tuple[Sequence[BaseException], int, CancelledError | None]:
        """
        Wait until all of the tasks complete, including the ones spawned meanwhile.

        A failed task never affects its siblings or the code which spawned it, its
        error is only collected. Tasks are cancelled when ``cancelling`` is requested
        or when the joining itself gets cancelled - unless ``shielded`` which lets
        them finish and still reports the cancellation once they did. Joining is
        never abandoned, a cancellation can only postpone its result.
        """
        cancellation: CancelledError | None = None
        if cancelling:
            self._cancel()

        while self._tasks:
            try:
                await wait(tuple(self._tasks))

            except CancelledError as exc:
                cancellation = cancellation or exc
                if not shielded:
                    self._cancel()

        self._closed = True
        return tuple(self._errors), self._dropped, cancellation

    def _cancel(self) -> None:
        self._cancelling = True
        for task in tuple(self._tasks):
            task.cancel()


@final
class ScopeTaskGroup:
    @classmethod
    def run[Result, **Arguments](
        cls,
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        tasks: _ScopeTasks | None
        try:
            tasks = cls._context.get()

        except LookupError:  # spawn task in the background as a fallback
            ContextObservability.record_log(
                ObservabilityLevel.DEBUG,
                "Task spawned outside of a context scope, running it in the background",
                exception=None,
            )
            return ContextTaskGroup.background_run(
                coro,
                *args,
                **kwargs,
            )

        if tasks is None:
            if iscoroutine(coro):
                coro.close()  # never started

            raise RuntimeError(
                "Tasks can't be spawned while disposables are being prepared or disposed, "
                "they would outlive the scope owning them - use ctx.spawn_background instead"
            )

        coroutine: Coroutine[None, None, Result] = _coroutine(coro, *args, **kwargs)

        return tasks.create_task(
            coroutine,
            context=copy_context(),
        )

    @classmethod
    def spawning(cls) -> bool:
        # `False` only within a region refusing scoped tasks, outside of any scope
        # tasks fall back to running in the background
        return cls._context.get(True) is not None

    # `None` marks a region where spawning scoped tasks is not allowed
    _context: ClassVar[ContextVar[_ScopeTasks | None]] = ContextVar[_ScopeTasks | None](
        "ContextTaskGroup"
    )

    @classmethod
    @contextmanager
    def forbidding(cls) -> Generator[None]:
        token: Token[_ScopeTasks | None] = cls._context.set(None)
        try:
            yield

        finally:
            cls._context.reset(token)

    __slots__ = (
        "_isolated",
        "_tasks",
        "_token",
    )

    def __init__(
        self,
        *,
        isolated: bool = False,
    ) -> None:
        self._isolated: bool = isolated
        self._tasks: _ScopeTasks | None = None
        self._token: Token[_ScopeTasks | None] | None = None

    def enter(self) -> None:
        # entering awaits nothing - a scope enters it without the coroutine of `__aenter__`
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        assert self._tasks is None  # nosec: B101
        self._tasks = _ScopeTasks()
        self._token = ScopeTaskGroup._context.set(self._tasks)

    async def __aenter__(self) -> Self:
        self.enter()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        assert self._tasks is not None  # nosec: B101

        errors: Sequence[BaseException]
        dropped: int
        cancellation: CancelledError | None
        try:
            # tasks are cancelled together with a failing body, a cancelled body of an
            # isolated scope is the only exception - its tasks are shielded from it
            errors, dropped, cancellation = await self._tasks.join(
                cancelling=exc_val is not None
                and not (self._isolated and isinstance(exc_val, CancelledError)),
                shielded=self._isolated,
            )

        finally:
            ScopeTaskGroup._context.reset(self._token)
            self._token = None
            self._tasks = None

        # not logged again - each of the errors was logged when its task failed
        failure: BaseException | None = self._failure(errors, dropped) if errors else None
        if cancellation is not None:
            # cancellation requested while joining is never replaced by task errors
            if cancellation is exc_val:
                return  # already propagating

            raise cancellation

        if failure is None:
            return  # nothing to raise

        if exc_val is None:
            raise failure

        # the exception propagating through the body takes precedence over the errors of
        # the tasks which failed alongside it - they are chained as its cause instead
        # an explicit cause of the body error is never replaced - the failure was logged
        # the body reraising the error of a task it awaited is that error itself
        if (
            not isinstance(exc_val, CancelledError)
            and exc_val.__cause__ is None
            and not _contains(failure, exc_val)
        ):
            exc_val.__cause__ = failure

    @staticmethod
    def _failure(
        errors: Sequence[BaseException],
        dropped: int,
        /,
    ) -> BaseException:
        message: str = "Context task group failed"
        if dropped:
            message = f"{message}, {dropped} more errors were logged only"

        match errors:
            case [error] if not dropped:
                return error  # a single error is raised on its own

            case _:
                if all(isinstance(error, Exception) for error in errors):
                    return ExceptionGroup(
                        message,
                        cast(list[Exception], list(errors)),
                    )

                return BaseExceptionGroup(message, list(errors))


def _contains(
    failure: BaseException,
    error: BaseException,
    /,
) -> bool:
    if failure is error:
        return True

    if isinstance(failure, BaseExceptionGroup):
        group: BaseExceptionGroup[BaseException] = cast(BaseExceptionGroup[BaseException], failure)
        return any(_contains(element, error) for element in group.exceptions)

    return False


@final
class ContextTaskGroup:
    @classmethod
    def run[Result, **Arguments](
        cls,
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        task_group: TaskGroup
        try:
            task_group = cls._context.get()

        except LookupError:  # spawn task in the background as a fallback
            return cls.background_run(
                coro,
                *args,
                **kwargs,
            )

        coroutine: Coroutine[None, None, Result] = _coroutine(coro, *args, **kwargs)

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
        coroutine: Coroutine[None, None, Result] = _coroutine(coro, *args, **kwargs)

        # detached tasks run outside of any scope - inheriting the spawning
        # context would bind them to a scope which is free to complete while
        # they still run, silently voiding their state, events and records
        return BackgroundTaskGroup.create_task(
            coroutine,
            context=Context(),
        )

    _context: ClassVar[ContextVar[TaskGroup]] = ContextVar[TaskGroup]("ContextTaskGroup")

    __slots__ = (
        "_task_group",
        "_token",
    )

    def __init__(self) -> None:
        self._task_group: TaskGroup | None = None
        self._token: Token[TaskGroup] | None = None

    async def __aenter__(self) -> Self:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        assert self._task_group is None  # nosec: B101
        self._task_group = TaskGroup()
        await self._task_group.__aenter__()
        self._token = ContextTaskGroup._context.set(self._task_group)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        assert self._task_group is not None  # nosec: B101

        failure: BaseException | None = None
        try:
            await self._task_group.__aexit__(
                exc_type,
                exc_val,
                exc_tb,
            )

        except BaseExceptionGroup as exc:
            # the group of the TaskGroup wraps the errors of its tasks with the non-cancellation
            # exception propagating through its body, the latter twice when the body reraised
            # the error of a failed task - unwrap it to surface the actual failures instead
            failure = self._failure(exc, body_exception=exc_val)

        finally:
            ContextTaskGroup._context.reset(self._token)
            self._token = None
            self._task_group = None

        if failure is None:
            return  # nothing to raise

        if failure is exc_val:
            # reraising the exception already propagating through the body does not chain it
            raise failure

        if exc_val is not None:
            # the body was cancelled by the group on task failure - that cancellation is
            # not a cause of the failure, hence not chained as its context
            failure.__suppress_context__ = True

        raise failure

    @staticmethod
    def _failure(
        group: BaseExceptionGroup[BaseException],
        /,
        *,
        body_exception: BaseException | None,
    ) -> BaseException:
        errors: list[BaseException] = []
        for error in group.exceptions:
            if error is body_exception or any(error is collected for collected in errors):
                continue  # skip the body exception and duplicates

            errors.append(error)

        if not errors:
            # the group holds nothing but the exception already propagating through the body -
            # a closing `GeneratorExit` or the reraised error of a failed task - there is no
            # failure to report beyond it, which was logged where it was raised
            assert body_exception is not None  # nosec: B101
            return body_exception

        collected: BaseException
        match errors:
            case [error]:
                collected = error  # a single error is raised on its own

            case _:
                if all(isinstance(error, Exception) for error in errors):
                    collected = ExceptionGroup(
                        "Context task group failed",
                        cast(list[Exception], errors),
                    )

                else:
                    collected = BaseExceptionGroup("Context task group failed", errors)

        ContextObservability.record_log(
            ObservabilityLevel.ERROR,
            "Context task group exit failed",
            exception=collected,
        )

        if body_exception is None or isinstance(body_exception, CancelledError):
            # a cancellation is never included in the group - it is the task group cancelling
            # the body on task failure, propagating it would discard the errors of the tasks
            return collected

        # the exception propagating through the body takes precedence over the errors of the
        # tasks, which failed alongside it - they are chained as its cause instead of being lost
        # an explicit cause is never replaced - the failure was logged above
        if body_exception.__cause__ is None:
            body_exception.__cause__ = collected

        return body_exception
