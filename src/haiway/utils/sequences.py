from collections.abc import Sequence

__all__ = [
    "as_list",
    "as_tuple",
]


def as_list[T](
    sequence: Sequence[T],
    /,
) -> list[T]:
    """
    Converts any given sequence into a list.

    Parameters
    ----------
    sequence : Sequence[T]
        The input sequence to be converted.

    Returns
    -------
    list[T]
        A new list containing all elements of the input sequence,
        or the original list if it was already one.
    """
    if isinstance(sequence, list):
        return sequence

    else:
        return list(sequence)


def as_tuple[T](
    sequence: Sequence[T],
    /,
) -> tuple[T, ...]:
    """
    Converts any given sequence into a tuple.

    Parameters
    ----------
    sequence : Sequence[T]
        The input sequence to be converted.

    Returns
    -------
    tuple[T]
        A new tuple containing all elements of the input sequence,
        or the original tuple if it was already one.
    """
    if isinstance(sequence, tuple):
        return sequence

    else:
        return tuple(sequence)
