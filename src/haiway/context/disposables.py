from asyncio import CancelledError, gather
from collections.abc import Collection, Generator, Iterable, Iterator, MutableSequence, Sequence
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
        # TODO: consider making transformations before actual preparation
        # to have better performance when preparing state
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

    __slots__ = (
        "_disposables",
        "_status",
    )

    def __init__(
        self,
        disposables: Iterable[Disposable | None],
        /,
    ) -> None:
        self._disposables: Collection[Disposable] = tuple(
            disposable for disposable in disposables if disposable is not None
        )
        self._status: bool = False  # TODO: to be converted to status tracking of partial success

    async def __aenter__(self) -> Iterator[State]:
        assert self._status is False  # nosec: B101
        try:
            return _collect_state(
                await gather(
                    *(disposable.__aenter__() for disposable in self._disposables),
                    return_exceptions=True,
                )
            )

        except BaseException:
            # TODO: FIXME: dispose only those which succeeded

            raise  # reraise exception

        finally:
            self._status = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._status is True  # nosec: B101
        try:
            results: Iterable[BaseException | None] = await gather(
                *(
                    disposable.__aexit__(exc_type, exc_val, exc_tb)
                    for disposable in self._disposables
                ),
                return_exceptions=True,
            )

            exceptions: MutableSequence[BaseException] = []
            for result in results:
                if isinstance(result, BaseException):
                    exceptions.append(result)

            if not exceptions:
                return  # no errors

            if len(exceptions) == 1:
                raise exceptions[0]  # single error

            if all(isinstance(exception, Exception) for exception in exceptions):
                raise ExceptionGroup(
                    "Disposables disposal errors",
                    cast(Sequence[Exception], exceptions),
                )

            if all(isinstance(exception, CancelledError) for exception in exceptions):
                raise CancelledError()  # cancelled

            raise BaseExceptionGroup("Disposables disposal errors", exceptions)

        finally:
            self._status = False

    def extended(
        self,
        *disposables: Disposable,
    ) -> Self:
        assert self._status is False  # nosec: B101

        if not disposables:
            return self

        return self.__class__((*self._disposables, *disposables))

    def __bool__(self) -> bool:
        return len(self._disposables) > 0


def _collect_state(
    results: Sequence[BaseException | Iterable[State] | State],
) -> Generator[State]:
    exceptions: MutableSequence[BaseException] = []
    for result in results:
        if isinstance(result, BaseException):
            exceptions.append(result)

        elif isinstance(result, State):
            yield result

        else:
            yield from result

    if not exceptions:
        return  # no errors

    if len(exceptions) == 1:
        raise exceptions[0]  # single error

    if all(isinstance(exception, Exception) for exception in exceptions):
        raise ExceptionGroup(
            "Disposables preparation errors",
            cast(Sequence[Exception], exceptions),
        )

    if all(isinstance(exception, CancelledError) for exception in exceptions):
        raise CancelledError()  # cancelled

    raise BaseExceptionGroup("Disposables preparation errors", exceptions)


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
