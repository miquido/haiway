from asyncio import CancelledError, Future, gather, shield, sleep
from collections.abc import Collection, Iterable, Iterator, MutableSequence, Sequence
from types import TracebackType
from typing import Any, NoReturn, Protocol, Self, cast, final, runtime_checkable

from haiway.attributes import State
from haiway.context.state import ContextState

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


@final  # consider immutable
class Disposables:
    @classmethod
    def of(
        cls,
        *disposables: Disposable | None,
    ) -> Self:
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
            # than left to the garbage collector. under a single shield, so a
            # cancellation delivered again does not split the cleanup in half
            await shield(
                self._dispose_abandoned(
                    preparing,
                    cause=exc,
                )
            )
            raise  # reraise exception

        try:
            return _collect_state(results)  # raises on preparation errors

        except BaseException as exc:
            await shield(
                self._dispose_prepared(
                    results,
                    cause=exc,
                )
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
        results: Iterable[BaseException | None] = await gather(
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

        _raise_collected(
            tuple(result for result in results if isinstance(result, BaseException)),
            message="Disposables disposal errors",
        )

    def extended(
        self,
        *disposables: Disposable,
    ) -> Self:
        if not disposables:
            return self

        return self.__class__((*self._disposables, *disposables))

    def __bool__(self) -> bool:
        return len(self._disposables) > 0


@final  # immutable
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
    )

    def __init__(
        self,
        disposables: Iterable[Disposable | None],
        /,
    ) -> None:
        self._disposables: Disposables
        object.__setattr__(
            self,
            "_disposables",
            Disposables(disposables),
        )
        self._context_state: ContextState | None
        object.__setattr__(
            self,
            "_context_state",
            None,
        )

    def __bool__(self) -> bool:
        return bool(self._disposables)

    async def __aenter__(self) -> None:
        assert self._context_state is None  # nosec: B101
        context_state: ContextState = ContextState.updating(await self._disposables.__aenter__())
        context_state.__enter__()
        object.__setattr__(
            self,
            "_context_state",
            context_state,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._context_state is not None  # nosec: B101

        try:
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
            object.__setattr__(
                self,
                "_context_state",
                None,
            )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be deleted"
        )


def _collect_state(
    results: Sequence[Iterable[State] | State | BaseException],
    /,
) -> Iterator[State]:
    state: MutableSequence[State] = []
    errors: MutableSequence[BaseException] = []
    for result in results:
        if isinstance(result, BaseException):
            errors.append(result)

        elif isinstance(result, State):
            state.append(result)

        else:
            state.extend(result)

    _raise_collected(
        errors,
        message="Disposables preparation errors",
    )

    return iter(state)


def _raise_collected(
    exceptions: Sequence[BaseException],
    /,
    message: str,
) -> None:
    match exceptions:
        case ():
            return  # no errors

        case (exception,):
            raise exception  # single error

        case _:
            if all(isinstance(exception, Exception) for exception in exceptions):
                raise ExceptionGroup(
                    message,
                    cast(Sequence[Exception], exceptions),
                )

            if all(isinstance(exception, CancelledError) for exception in exceptions):
                raise CancelledError()  # cancelled

            raise BaseExceptionGroup(message, exceptions)
