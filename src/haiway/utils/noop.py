from typing import Any

__all__ = (
    "async_noop",
    "noop",
)


def noop(
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Placeholder function that accepts any arguments and does nothing.

    This utility function is useful for cases where a callback is required
    but no action should be taken, such as in testing, as a default handler,
    or as a placeholder during development.

    Parameters
    ----------
    *args: Any
        Any positional arguments, which are ignored
    **kwargs: Any
        Any keyword arguments, which are ignored

    Returns
    -------
    None
        This function performs no operation and returns nothing
    """


async def async_noop(
    *args: Any,
    **kwargs: Any,
) -> None:
    """
    Asynchronous placeholder function that accepts any arguments and does nothing.

    This utility function is useful for cases where an async callback is required
    but no action should be taken, such as in testing, as a default async handler,
    or as a placeholder during asynchronous workflow development.

    Parameters
    ----------
    *args: Any
        Any positional arguments, which are ignored
    **kwargs: Any
        Any keyword arguments, which are ignored

    Returns
    -------
    None
        This function performs no operation and returns nothing
    """
