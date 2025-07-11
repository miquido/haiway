from asyncio import (
    AbstractEventLoop,
    gather,
    get_running_loop,
    run_coroutine_threadsafe,
    wrap_future,
)
from collections.abc import Collection, Iterable
from contextlib import AbstractAsyncContextManager
from itertools import chain
from types import TracebackType

from haiway.state import Immutable, State

__all__ = (
    "Disposable",
    "Disposables",
)

type Disposable = AbstractAsyncContextManager[Iterable[State] | State | None]
"""
A type alias for asynchronous context managers that provide disposable resources.

Represents an asynchronous resource that needs proper cleanup when no longer needed.
When entered, it may return State instances that will be automatically propagated
to the current context. The resource is guaranteed to be properly disposed of
when the context exits, even if exceptions occur.

Type Details
------------
- Must be an async context manager (implements __aenter__ and __aexit__)
- Can return None, a single State instance, or multiple State instances
- State instances are automatically added to the context scope
- Cleanup is handled automatically when the context exits

Examples
--------
Creating a disposable database connection:

>>> import contextlib
>>> from haiway import State
>>>
>>> class DatabaseState(State):
...     connection: DatabaseConnection
...
>>> @contextlib.asynccontextmanager
>>> async def database_disposable():
...     connection = await create_database_connection()
...     try:
...         yield DatabaseState(connection=connection)
...     finally:
...         await connection.close()
"""


class Disposables(Immutable):
    """
    A container for multiple Disposable resources that manages their lifecycle.

    This class provides a way to handle multiple disposable resources as a single unit,
    entering all of them in parallel when the container is entered and exiting all of
    them when the container is exited. Any states returned by the disposables are
    collected and automatically propagated to the context.

    Key Features
    ------------
    - Parallel setup and cleanup of all contained disposables
    - Automatic state collection and context propagation
    - Thread-safe cross-event-loop disposal
    - Exception handling with BaseExceptionGroup for multiple failures
    - Immutable after initialization

    The class is designed to work seamlessly with ctx.scope() and ensures proper
    resource cleanup even when exceptions occur during setup or teardown.

    Examples
    --------
    Creating and using multiple disposables:

    >>> from haiway import ctx
    >>> async def main():
    ...     disposables = Disposables(
    ...         database_disposable(),
    ...         cache_disposable()
    ...     )
    ...
    ...     async with ctx.scope("app", disposables=disposables):
    ...         # Both DatabaseState and CacheState are available
    ...         db = ctx.state(DatabaseState)
    ...         cache = ctx.state(CacheState)

    Direct context manager usage:

    >>> async def process_data():
    ...     disposables = Disposables(
    ...         create_temp_file_disposable(),
    ...         create_network_connection_disposable()
    ...     )
    ...
    ...     async with disposables:
    ...         # Resources are set up in parallel
    ...         temp_file = ctx.state(TempFileState)
    ...         network = ctx.state(NetworkState)
    ...
    ...         # Process data using both resources
    ...
    ...     # All resources cleaned up automatically
    """

    _disposables: Collection[Disposable]
    _loop: AbstractEventLoop | None

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
        object.__setattr__(
            self,
            "_disposables",
            disposables,
        )
        object.__setattr__(
            self,
            "_loop",
            None,
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

    async def _setup(
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

    async def prepare(self) -> Iterable[State]:
        """
        Enter all contained disposables asynchronously.

        Enters all disposables in parallel and collects any State objects they return.
        """
        assert self._loop is None  # nosec: B101
        object.__setattr__(
            self,
            "_loop",
            get_running_loop(),
        )

        return tuple(
            chain.from_iterable(
                await gather(
                    *[self._setup(disposable) for disposable in self._disposables],
                )
            )
        )

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

    async def dispose(
        self,
        /,
        exc_type: type[BaseException] | None = None,
        exc_val: BaseException | None = None,
        exc_tb: TracebackType | None = None,
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

        match len(exceptions):
            case 0:
                return

            case 1:
                raise exceptions[0]

            case _:
                raise BaseExceptionGroup("Disposables cleanup errors", exceptions)
