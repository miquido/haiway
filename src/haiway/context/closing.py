from asyncio import AbstractEventLoop, Future
from contextvars import ContextVar, Token
from types import TracebackType
from typing import ClassVar, Self, final
from uuid import UUID

from haiway.context.types import ContextMissing

__all__ = ("ContextClosing",)


@final
class ContextClosing:
    """
    Future completed when the context scope owning it begins closing.

    Kept apart from the scope itself, so scope elements which have to notice
    their scope ending - like event subscriptions - can wait for it without
    depending on the scope which owns them.
    """

    @classmethod
    def current(
        cls,
        /,
    ) -> Future[UUID]:
        try:
            closing: ContextClosing = cls._context.get()

        except LookupError:
            raise ContextMissing("Context scope closing requested out of context scope!") from None

        # made when it is first waited for - most scopes end with nothing waiting
        future: Future[UUID] | None = closing._future
        if future is None:
            future = closing._loop.create_future()
            closing._future = future

        return future

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextClosing")

    __slots__ = (
        "_future",
        "_identifier",
        "_loop",
        "_token",
    )

    def __init__(
        self,
        loop: AbstractEventLoop,
        identifier: UUID,
    ) -> None:
        self._loop: AbstractEventLoop = loop
        self._future: Future[UUID] | None = None
        self._token: Token[ContextClosing] | None = None
        self._identifier: UUID = identifier

    def __enter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        self._token = ContextClosing._context.set(self)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        try:
            # complete before releasing - everything waiting for the scope to end is
            # unblocked while the scope is still unwinding, before its tasks are joined
            # always with the identifier - the way a scope ends is not a result of
            # waiting for it, a waiter must not receive the error of the scope body
            future: Future[UUID] | None = self._future
            if future is not None:
                future.set_result(self._identifier)
                future.result()  # silence warning

        finally:  # released even when completing fails - the scope is spent either way
            ContextClosing._context.reset(self._token)
            self._token = None
