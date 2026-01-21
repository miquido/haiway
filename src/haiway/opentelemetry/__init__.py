try:
    import opentelemetry  # pyright: ignore[reportUnusedImport]
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "haiway.opentelemetry requires the 'opentelemetry' extra. "
        "Install via `pip install haiway[opentelemetry]`."
    ) from exc

from haiway.opentelemetry.observability import OpenTelemetry

__all__ = ("OpenTelemetry",)
