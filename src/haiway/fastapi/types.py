from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import Request, Response

__all__ = ("ExceptionHandling",)


# matches what Starlette dispatches at runtime, which FastAPI hands its handlers
# over to - a synchronous handler is called in a worker thread. FastAPI declares
# `exception_handlers` as async only, narrower than that, which is why the
# mapping is cast on the way in.
type ExceptionHandling = Callable[[Request, Any], Response | Awaitable[Response]]
