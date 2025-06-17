from asyncio import (
    AbstractEventLoop,
    gather,
    get_running_loop,
    run_coroutine_threadsafe,
    wrap_future,
)
from collections.abc import Iterable
from contextlib import AbstractAsyncContextManager
from itertools import chain
from types import TracebackType
from typing import Any, final

from haiway.state import State

__all__ = (
    "Disposable",
    "Disposables",
)

type Disposable = AbstractAsyncContextManager[Iterable[State] | State | None]
"""
A type alias for asynchronous context managers that can be disposed.

Represents an asynchronous resource that needs proper cleanup when no longer needed.
When entered, it may return State instances that will be propagated to the context.
"""


@final
class Disposables:
    """
    A container for multiple Disposable resources that manages their lifecycle.

    This class provides a way to handle multiple disposable resources as a single unit,
    entering all of them when the container is entered and exiting all of them when
    the container is exited. Any states returned by the disposables are collected
    and returned as a unified collection.

    The class is immutable after initialization.
    """

    __slots__ = ("_disposables", "_loop")

    def __init__(
        self,
        *disposables: Disposable,
    ) -> None:
        """
        Initialize a collection of disposable resources.

        Parameters
        ----------
        *disposables: Disposable
            Variable number of disposable resources to be managed together.
        """
        self._disposables: tuple[Disposable, ...]
        object.__setattr__(
            self,
            "_disposables",
            disposables,
        )
        self._loop: AbstractEventLoop | None
        object.__setattr__(
            self,
            "_loop",
            None,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    def __bool__(self) -> bool:
        """
        Check if this container has any disposables.

        Returns
        -------
        bool
            True if there are disposables, False otherwise.
        """
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
        """
        Enter all contained disposables asynchronously.

        Enters all disposables in parallel and collects any State objects they return.

        Returns
        -------
        Iterable[State]
            Collection of State objects from all disposables.
        """
        assert self._loop is None  # nosec: B101
        object.__setattr__(
            self,
            "_loop",
            get_running_loop(),
        )
        return [
            *chain.from_iterable(
                state
                for state in await gather(
                    *[self._initialize(disposable) for disposable in self._disposables],
                )
            )
        ]

    async def _cleanup(
        self,
        /,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> list[bool | BaseException | None]:
        return await gather(
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

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Exit all contained disposables asynchronously.

        Properly disposes of all resources by calling their __aexit__ methods in parallel.
        If multiple disposables raise exceptions, they are collected into a BaseExceptionGroup.

        Parameters
        ----------
        exc_type: type[BaseException] | None
            The type of exception that caused the context to be exited
        exc_val: BaseException | None
            The exception that caused the context to be exited
        exc_tb: TracebackType | None
            The traceback for the exception that caused the context to be exited

        Raises
        ------
        BaseExceptionGroup
            If multiple disposables raise exceptions during exit
        """

        assert self._loop is not None  # nosec: B101
        results: list[bool | BaseException | None]

        try:
            current_loop: AbstractEventLoop = get_running_loop()
            if self._loop != current_loop:
                results = await wrap_future(
                    run_coroutine_threadsafe(
                        self._cleanup(
                            exc_type,
                            exc_val,
                            exc_tb,
                        ),
                        loop=self._loop,
                    )
                )

            else:
                results = await self._cleanup(
                    exc_type,
                    exc_val,
                    exc_tb,
                )

        finally:
            object.__setattr__(
                self,
                "_loop",
                None,
            )

        exceptions: list[BaseException] = [exc for exc in results if isinstance(exc, BaseException)]

        if len(exceptions) > 1:
            raise BaseExceptionGroup("Disposables cleanup errors", exceptions)
