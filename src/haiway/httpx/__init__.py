try:
    import httpx2  # pyright: ignore[reportUnusedImport]

except ImportError as exc:  # pragma: no cover - covered via guard tests
    raise ImportError(
        "haiway.httpx requires the 'httpx2' extra. Install via `pip install haiway[httpx]`."
    ) from exc

from haiway.httpx.client import HTTPXClient

__all__ = ("HTTPXClient",)
