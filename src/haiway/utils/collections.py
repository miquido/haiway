from collections.abc import Mapping, Sequence, Set
from typing import overload

__all__ = [
    "as_dict",
    "as_list",
    "as_set",
    "as_tuple",
]


@overload
def as_list[T](
    collection: Sequence[T],
    /,
) -> list[T]: ...


@overload
def as_list[T](
    collection: Sequence[T] | None,
    /,
) -> list[T] | None: ...


def as_list[T](
    collection: Sequence[T] | None,
    /,
) -> list[T] | None:
    """
    Converts any given Sequence into a list.

    Parameters
    ----------
    collection : Sequence[T] | None
        The input collection to be converted.

    Returns
    -------
    list[T] | None
        A new list containing all elements of the input collection,\
         or the original list if it was already one.
        None if no value was provided.
    """

    if collection is None:
        return None

    if isinstance(collection, list):
        return collection

    else:
        return list(collection)


@overload
def as_tuple[T](
    collection: Sequence[T],
    /,
) -> tuple[T, ...]: ...


@overload
def as_tuple[T](
    collection: Sequence[T] | None,
    /,
) -> tuple[T, ...] | None: ...


def as_tuple[T](
    collection: Sequence[T] | None,
    /,
) -> tuple[T, ...] | None:
    """
    Converts any given Sequence into a tuple.

    Parameters
    ----------
    collection : Sequence[T] | None
        The input collection to be converted.

    Returns
    -------
    tuple[T] | None
        A new tuple containing all elements of the input collection,\
         or the original tuple if it was already one.
        None if no value was provided.
    """

    if collection is None:
        return None

    if isinstance(collection, tuple):
        return collection

    else:
        return tuple(collection)


@overload
def as_set[T](
    collection: Set[T],
    /,
) -> set[T]: ...


@overload
def as_set[T](
    collection: Set[T] | None,
    /,
) -> set[T] | None: ...


def as_set[T](
    collection: Set[T] | None,
    /,
) -> set[T] | None:
    """
    Converts any given Set into a set.

    Parameters
    ----------
    collection : Set[T]
        The input collection to be converted.

    Returns
    -------
    set[T]
        A new set containing all elements of the input collection,\
         or the original set if it was already one.
        None if no value was provided.
    """

    if collection is None:
        return None

    if isinstance(collection, set):
        return collection

    else:
        return set(collection)


@overload
def as_dict[K, V](
    collection: Mapping[K, V],
    /,
) -> dict[K, V]: ...


@overload
def as_dict[K, V](
    collection: Mapping[K, V] | None,
    /,
) -> dict[K, V] | None: ...


def as_dict[K, V](
    collection: Mapping[K, V] | None,
    /,
) -> dict[K, V] | None:
    """
    Converts any given Mapping into a dict.

    Parameters
    ----------
    collection : Mapping[K, V]
        The input collection to be converted.

    Returns
    -------
    dict[K, V]
        A new dict containing all elements of the input collection,\
         or the original dict if it was already one.
        None if no value was provided.
    """

    if collection is None:
        return None

    if isinstance(collection, dict):
        return collection

    else:
        return dict(collection)
