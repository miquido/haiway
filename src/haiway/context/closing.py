from asyncio import AbstractEventLoop, Future
from contextvars import ContextVar, Token
from types import TracebackType
from typing import ClassVar, Self, final
from uuid import UUID

from haiway.context.types import ContextMissing

__all__ = ("ContextClosing",)


@final  # consider immutable
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
            return cls._context.get()._future

        except LookupError:
            raise ContextMissing("Context scope closing requested out of context scope!") from None

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextClosing")

    __slots__ = (
        "_future",
        "_identifier",
        "_token",
    )

    def __init__(
        self,
        loop: AbstractEventLoop,
        identifier: UUID,
    ) -> None:
        self._future: Future[UUID] = loop.create_future()
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
            if exc_val is None:
                self._future.set_result(self._identifier)
                self._future.result()  # silence warning

            else:
                self._future.set_exception(exc_val)
                self._future.exception()  # silence warning

        finally:  # released even when completing fails - the scope is spent either way
            ContextClosing._context.reset(self._token)
            self._token = None
