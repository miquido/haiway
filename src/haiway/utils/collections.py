from collections.abc import Mapping, Sequence, Set

__all__ = [
    "as_dict",
    "as_list",
    "as_set",
    "as_tuple",
]


def as_list[T](
    collection: Sequence[T],
    /,
) -> list[T]:
    """
    Converts any given Sequence into a list.

    Parameters
    ----------
    collection : Sequence[T]
        The input collection to be converted.

    Returns
    -------
    list[T]
        A new list containing all elements of the input collection,
        or the original list if it was already one.
    """
    if isinstance(collection, list):
        return collection

    else:
        return list(collection)


def as_tuple[T](
    collection: Sequence[T],
    /,
) -> tuple[T, ...]:
    """
    Converts any given Sequence into a tuple.

    Parameters
    ----------
    collection : Sequence[T]
        The input collection to be converted.

    Returns
    -------
    tuple[T]
        A new tuple containing all elements of the input collection,
        or the original tuple if it was already one.
    """
    if isinstance(collection, tuple):
        return collection

    else:
        return tuple(collection)


def as_set[T](
    collection: Set[T],
    /,
) -> set[T]:
    """
    Converts any given Set into a set.

    Parameters
    ----------
    collection : Set[T]
        The input collection to be converted.

    Returns
    -------
    set[T]
        A new set containing all elements of the input collection,
        or the original set if it was already one.
    """
    if isinstance(collection, set):
        return collection

    else:
        return set(collection)


def as_dict[K, V](
    collection: Mapping[K, V],
    /,
) -> dict[K, V]:
    """
    Converts any given Mapping into a dict.

    Parameters
    ----------
    collection : Mapping[K, V]
        The input collection to be converted.

    Returns
    -------
    dict[K, V]
        A new dict containing all elements of the input collection,
        or the original dict if it was already one.
    """
    if isinstance(collection, dict):
        return collection

    else:
        return dict(collection)
