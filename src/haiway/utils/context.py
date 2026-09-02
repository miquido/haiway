from types import TracebackType
from typing import ClassVar, Self, final

__all__ = (
    "NoopAsyncContext",
    "NoopContext",
)


@final
class NoopContext:
    """Context manager doing nothing when entered or exited.

    Stands in where a context manager has to be returned but there is nothing to
    do - ``ctx.updating()`` with no state, or ``ctx.presets()`` with no presets -
    so an empty call neither costs a context variable update nor replaces what is
    already in place. Stateless, which is why ``instance`` is shared instead of a
    fresh one being created per use.
    """

    instance: ClassVar[Self]  # defined after the class

    def __enter__(self) -> None:
        pass

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


NoopContext.instance = NoopContext()


@final
class NoopAsyncContext:
    """Async context manager doing nothing when entered or exited.

    The asynchronous counterpart of ``NoopContext``, standing in where an async
    context manager has to be returned with nothing to prepare or release -
    ``ctx.disposables()`` with no disposables. Entering it awaits nothing, so it
    does not even yield to the event loop. Stateless, which is why ``instance``
    is shared instead of a fresh one being created per use.
    """

    instance: ClassVar[Self]  # defined after the class

    async def __aenter__(self) -> None:
        pass

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        pass


NoopAsyncContext.instance = NoopAsyncContext()
