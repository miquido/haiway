from asyncio import gather
from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager
from itertools import chain
from types import TracebackType
from typing import final

from haiway.state import State
from haiway.utils import freeze

__all__ = [
    "Disposable",
    "Disposables",
]

type Disposable = AbstractAsyncContextManager[Iterable[State] | State | None]


@final
class Disposables:
    def __init__(
        self,
        *disposables: Disposable,
    ) -> None:
        self._disposables: tuple[Disposable, ...] = disposables

        freeze(self)

    def __bool__(self) -> bool:
        return len(self._disposables) > 0

    async def _initialize(
        self,
        disposable: Disposable,
        /,
    ) -> Iterable[State]:
        match await disposable.__aenter__():
            case None:
                return ()

            case State() as single:
                return (single,)

            case multiple:
                return multiple

    async def __aenter__(self) -> Iterable[State]:
        return [
            *chain.from_iterable(
                state
                for state in await gather(
                    *[self._initialize(disposable) for disposable in self._disposables],
                )
            )
        ]

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        results: list[bool | BaseException | None] = await gather(
            *[
                disposable.__aexit__(
                    exc_type,
                    exc_val,
                    exc_tb,
                )
                for disposable in self._disposables
            ],
            return_exceptions=True,
        )

        exceptions: list[BaseException] = [exc for exc in results if isinstance(exc, BaseException)]

        if len(exceptions) > 1:
            raise BaseExceptionGroup("Disposing errors", exceptions)
