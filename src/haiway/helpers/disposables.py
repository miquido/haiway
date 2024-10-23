from asyncio import gather, shield
from collections.abc import Iterable
from types import TracebackType
from typing import Protocol, final, runtime_checkable

from haiway.state import State

__all__ = [
    "Disposable",
    "Disposables",
]


@runtime_checkable
class Disposable(Protocol):
    async def initialize(self) -> State | None: ...
    async def dispose(self) -> None: ...


@final
class Disposables:
    def __init__(
        self,
        *disposables: Disposable,
    ) -> None:
        self._disposables: tuple[Disposable, ...] = disposables

    async def initialize(self) -> Iterable[State]:
        return [
            state
            for state in await gather(
                *[disposable.initialize() for disposable in self._disposables],
            )
            if state is not None
        ]

    async def dispose(self) -> None:
        results: list[BaseException | None] = await shield(
            gather(
                *[disposable.dispose() for disposable in self._disposables],
                return_exceptions=True,
            ),
        )

        self._disposables = ()
        exceptions: list[BaseException] = [
            exception for exception in results if exception is not None
        ]

        if len(exceptions) > 1:
            raise BaseExceptionGroup("Disposing errors", exceptions)

        elif exceptions:
            raise exceptions[0]

    async def __aenter__(self) -> Iterable[State]:
        return await self.initialize()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self.dispose()
