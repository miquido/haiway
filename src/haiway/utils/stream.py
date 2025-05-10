from asyncio import (
    AbstractEventLoop,
    CancelledError,
    Future,
    get_running_loop,
)
from collections.abc import AsyncIterator

__all__ = ("AsyncStream",)


class AsyncStream[Element](AsyncIterator[Element]):
    def __init__(
        self,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        self._loop: AbstractEventLoop = loop or get_running_loop()
        self._ready: Future[None] = self._loop.create_future()
        self._waiting: Future[Element] | None = None
        self._finish_reason: BaseException | None = None

    @property
    def finished(self) -> bool:
        return self._finish_reason is not None

    async def send(
        self,
        element: Element,
        /,
    ) -> None:
        if self._finish_reason is not None:
            return  # already finished

        # wait for readiness
        await self._ready
        # we could finish while waiting
        if self._finish_reason is not None:
            return  # already finished

        assert self._waiting is not None and not self._waiting.done()  # nosec: B101
        # send the element
        self._waiting.set_result(element)
        # and create new readiness future afterwards
        self._ready = self._loop.create_future()

    def finish(
        self,
        exception: BaseException | None = None,
    ) -> None:
        if self.finished:
            return  # already finished, ignore

        self._finish_reason = exception or StopAsyncIteration()

        if not self._ready.done():
            if get_running_loop() is not self._loop:
                self._loop.call_soon_threadsafe(
                    self._ready.set_result,
                    None,
                )

            else:
                self._ready.set_result(None)

        if self._waiting is not None and not self._waiting.done():
            if get_running_loop() is not self._loop:
                self._loop.call_soon_threadsafe(
                    self._waiting.set_exception,
                    self._finish_reason,
                )

            else:
                self._waiting.set_exception(self._finish_reason)

    def cancel(self) -> None:
        self.finish(exception=CancelledError())

    async def __anext__(self) -> Element:
        assert self._waiting is None, "AsyncStream can't be reused"  # nosec: B101

        if self._finish_reason:
            raise self._finish_reason

        try:
            assert not self._ready.done()  # nosec: B101
            # create new waiting future
            self._waiting = self._loop.create_future()
            # and notify readiness
            self._ready.set_result(None)
            # and wait for the result
            return await self._waiting

        finally:
            # cleanup waiting future
            self._waiting = None
