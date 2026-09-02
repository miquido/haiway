try:
    import fastapi  # pyright: ignore[reportUnusedImport]

except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "haiway.fastapi requires the 'fastapi' extra. Install via `pip install haiway[fastapi]`."
    ) from exc

from haiway.fastapi.application import application
from haiway.fastapi.types import ExceptionHandling

# the request context declaration and the middleware are shared with the
# Starlette integration - FastAPI applications are Starlette applications
from haiway.starlette import (
    ContextMiddleware,
    ObservabilityPreparing,
    ServerContext,
    StreamResponse,
    request_trace_context,
)

__all__ = (
    "ContextMiddleware",
    "ExceptionHandling",
    "ObservabilityPreparing",
    "ServerContext",
    "StreamResponse",
    "application",
    "request_trace_context",
)
