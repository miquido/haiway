from typing import Any

__all__ = [
    "async_noop",
    "noop",
]


def noop(
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Placeholder function doing nothing (no operation).
    """


async def async_noop(
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Placeholder async function doing nothing (no operation).
    """
