try:
    import starlette  # pyright: ignore[reportUnusedImport]

except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "haiway.starlette requires the 'starlette' extra. "
        "Install via `pip install haiway[starlette]`."
    ) from exc

from haiway.starlette.application import application
from haiway.starlette.context import ServerContext
from haiway.starlette.middleware import ContextMiddleware
from haiway.starlette.streaming import StreamResponse
from haiway.starlette.trace import request_trace_context
from haiway.starlette.types import ObservabilityPreparing

__all__ = (
    "ContextMiddleware",
    "ObservabilityPreparing",
    "ServerContext",
    "StreamResponse",
    "application",
    "request_trace_context",
)
