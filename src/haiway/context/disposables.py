from asyncio import CancelledError, Future, ensure_future, gather, shield, sleep
from collections.abc import Collection, Coroutine, Iterable, Iterator, MutableSequence, Sequence
from types import TracebackType
from typing import Final, Protocol, Self, cast, final, runtime_checkable

from haiway.attributes import State
from haiway.context.observability import ContextObservability, ObservabilityLevel
from haiway.context.state import ContextState
from haiway.context.tasks import ScopeTaskGroup
from haiway.utils.exceptions import raise_collected

__all__ = (
    "ContextDisposables",
    "Disposable",
    "DisposableState",
    "Disposables",
)


@runtime_checkable
class Disposable(Protocol):
    async def __aenter__(self) -> Iterable[State] | State: ...
    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


@runtime_checkable
class DisposableStatePreparing(Protocol):
    async def __call__(self) -> Iterable[State] | State: ...


class DisposableState:
    @classmethod
    def of(
        cls,
        *state: DisposableStatePreparing | State,
    ) -> Self:
        async def preparing() -> Iterable[State]:
            results: MutableSequence[State] = []
            for element in state:
                if isinstance(element, State):
                    results.append(element)

                else:
                    result: Iterable[State] | State = await element()
                    if isinstance(result, State):
                        results.append(result)

                    else:
                        results.extend(result)

            return results

        return cls(preparing)

    __slots__ = ("_preparing",)

    def __init__(
        self,
        preparing: DisposableStatePreparing,
        /,
    ) -> None:
        self._preparing: DisposableStatePreparing = preparing

    async def __aenter__(self) -> Iterable[State] | State:
        return await self._preparing()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass  # nothing to dispose


@final
class Disposables:
    @classmethod
    def of(
        cls,
        *disposables: Disposable | None,
    ) -> Self:
        if not disposables:
            # holding nothing it can't change - every scope without disposables shares it
            return cast(Self, _NO_DISPOSABLES)

        return cls(disposables)

    __slots__ = ("_disposables",)

    def __init__(
        self,
        disposables: Iterable[Disposable | None],
        /,
    ) -> None:
        self._disposables: Collection[Disposable] = tuple(
            disposable for disposable in disposables if disposable is not None
        )

    async def __aenter__(self) -> Iterator[State]:
        if not self._disposables:
            return iter(())  # nothing to prepare

        # preparation is atomic - either nothing starts or everything gets
        # prepared, refuse to start when cancellation is already requested
        await sleep(0)  # raise when cancelled

        # held on its own instead of being awaited inline - the shield detaches
        # the caller from the preparation, it does not stop it, and this is the
        # only reference left to reach what it produces after that
        preparing: Future[list[Iterable[State] | State | BaseException]] = gather(
            *(disposable.__aenter__() for disposable in self._disposables),
            return_exceptions=True,
        )

        results: Sequence[Iterable[State] | State | BaseException]
        try:
            results = await shield(preparing)

        except BaseException as exc:
            # cancelled midway - the preparation still completes and nothing else
            # holds what it prepares, so it is awaited and disposed here rather
            # than left to the garbage collector
            await _cleaned_up(
                self._dispose_abandoned(
                    preparing,
                    cause=exc,
                ),
                cause=exc,
            )
            raise  # reraise exception

        try:
            state: MutableSequence[State] = []
            errors: MutableSequence[BaseException] = []
            for result in results:
                if isinstance(result, BaseException):
                    errors.append(result)

                elif isinstance(result, State):
                    state.append(result)

                else:
                    state.extend(result)

            raise_collected(
                errors,
                message="Disposables preparation errors",
            )

            return iter(state)

        except BaseException as exc:
            await _cleaned_up(
                self._dispose_prepared(
                    results,
                    cause=exc,
                ),
                cause=exc,
            )
            raise  # reraise exception

    async def _dispose_abandoned(
        self,
        preparing: Future[list[Iterable[State] | State | BaseException]],
        /,
        cause: BaseException,
    ) -> None:
        await self._dispose_prepared(
            await preparing,
            cause=cause,
        )

    async def _dispose_prepared(
        self,
        prepared: Sequence[Iterable[State] | State | BaseException],
        /,
        cause: BaseException,
    ) -> None:
        await gather(
            # dispose items which succeeded, the rest was never prepared
            *(
                disposable.__aexit__(type(cause), cause, cause.__traceback__)
                for disposable, result in zip(
                    self._disposables,
                    prepared,
                    strict=True,
                )
                if not isinstance(result, BaseException)
            ),
            return_exceptions=True,  # the cause takes precedence
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # disposal is never abandoned - the shield keeps it running when the caller
        # is cancelled, and the cancellation is delivered only once it completed
        disposing: Future[list[BaseException | None]] = gather(
            *(
                disposable.__aexit__(
                    exc_type,
                    exc_val,
                    exc_tb,
                )
                for disposable in self._disposables
            ),
            return_exceptions=True,
        )

        cancellation: CancelledError | None = await _completed(disposing)
        results: Iterable[BaseException | None] = (
            () if disposing.cancelled() else disposing.result()
        )

        errors: Sequence[BaseException] = tuple(
            result for result in results if isinstance(result, BaseException)
        )
        if cancellation is None:
            raise_collected(
                errors,
                message="Disposables disposal errors",
            )
            return

        # the cancellation takes precedence, yet the failures of disposal are not lost
        for error in errors:
            ContextObservability.record_log(
                ObservabilityLevel.ERROR,
                "Disposable failed while being disposed during cancellation",
                exception=error,
            )

        raise cancellation

    def extended(
        self,
        *disposables: Disposable,
    ) -> Self:
        if not disposables:
            return self

        return self.__class__((*self._disposables, *disposables))

    def __bool__(self) -> bool:
        return len(self._disposables) > 0


async def _completed[Value](
    work: Future[Value],
    /,
) -> CancelledError | None:
    # waits for the work to complete even when cancelled, again and again - a single
    # shield detaches the caller only, the work would keep running unawaited.
    # returns the cancellation to deliver once the work completed
    cancellation: CancelledError | None = None
    while True:
        try:
            await shield(work)
            return cancellation

        except CancelledError as exc:
            if work.done():  # the work itself got cancelled, nothing is left running
                return cancellation or exc

            cancellation = cancellation or exc  # keep waiting, deliver it afterwards


async def _cleaned_up(
    cleanup: Coroutine[None, None, None],
    /,
    *,
    cause: BaseException,
) -> None:
    # completes the cleanup, a cancellation delivered meanwhile is raised only after
    # it - in place of the cause unless the cause is a cancellation already
    cancellation: CancelledError | None = await _completed(ensure_future(cleanup))
    if cancellation is not None and not isinstance(cause, CancelledError):
        raise cancellation from cause


@final
class ContextDisposables:
    @classmethod
    def of(
        cls,
        *disposables: Disposable | None,
    ) -> Self:
        return cls(disposables)

    __slots__ = (
        "_context_state",
        "_disposables",
        "_task_group",
    )

    def __init__(
        self,
        disposables: Iterable[Disposable | None],
        /,
    ) -> None:
        self._disposables: Disposables = Disposables(disposables)
        self._context_state: ContextState | None = None
        self._task_group: ScopeTaskGroup | None = None

    def __bool__(self) -> bool:
        return bool(self._disposables)

    async def __aenter__(self) -> None:
        assert self._context_state is None  # nosec: B101
        # tasks spawned while preparing would be joined only after disposal
        with ScopeTaskGroup.forbidding():
            prepared = await self._disposables.__aenter__()

        self._context_state = ContextState.updating(prepared)
        self._context_state.__enter__()

        # tasks spawned within the body are joined before the disposables are released
        self._task_group = ScopeTaskGroup()
        self._task_group.enter()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._context_state is not None  # nosec: B101
        assert self._task_group is not None  # nosec: B101

        task_group: ScopeTaskGroup = self._task_group
        self._task_group = None
        try:
            try:
                await task_group.__aexit__(
                    exc_type,
                    exc_val,
                    exc_tb,
                )

            except BaseException as exc:
                # a failed task or a cancelled join ends the body as its own error - the
                # disposables are released against it, as a scope releases them
                with ScopeTaskGroup.forbidding():
                    await self._disposables.__aexit__(
                        type(exc),
                        exc,
                        exc.__traceback__,
                    )

                raise

            with ScopeTaskGroup.forbidding():
                await self._disposables.__aexit__(
                    exc_type,
                    exc_val,
                    exc_tb,
                )

        finally:
            self._context_state.__exit__(
                exc_type,
                exc_val,
                exc_tb,
            )
            self._context_state = None


_NO_DISPOSABLES: Final[Disposables] = Disposables(())
