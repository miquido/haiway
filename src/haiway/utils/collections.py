from collections.abc import Iterable, Mapping, Set
from typing import Any, cast, overload

from haiway.types.missing import MISSING

__all__ = (
    "as_dict",
    "as_list",
    "as_set",
    "as_tuple",
    "without_missing",
)


@overload
def as_list[T](
    collection: Iterable[T],
    /,
) -> list[T]: ...


@overload
def as_list[T](
    collection: Iterable[T] | None,
    /,
) -> list[T] | None: ...


def as_list[T](
    collection: Iterable[T] | None,
    /,
) -> list[T] | None:
    """
    Converts any given Iterable into a list.

    Parameters
    ----------
    collection : Iterable[T] | None
        The input collection to be converted to a list.
        If None is provided, None is returned.

    Returns
    -------
    list[T] | None
        A new list containing all elements of the input collection,
        or the original list if it was already one.
        Returns None if None was provided.
    """

    if collection is None:
        return None

    elif isinstance(collection, list):
        return collection

    else:
        return list(collection)


@overload
def as_tuple[T](
    collection: Iterable[T],
    /,
) -> tuple[T, ...]: ...


@overload
def as_tuple[T](
    collection: Iterable[T] | None,
    /,
) -> tuple[T, ...] | None: ...


def as_tuple[T](
    collection: Iterable[T] | None,
    /,
) -> tuple[T, ...] | None:
    """
    Converts any given Iterable into a tuple.

    Parameters
    ----------
    collection : Iterable[T] | None
        The input collection to be converted to a tuple.
        If None is provided, None is returned.

    Returns
    -------
    tuple[T, ...] | None
        A new tuple containing all elements of the input collection,
        or the original tuple if it was already one.
        Returns None if None was provided.
    """

    if collection is None:
        return None

    elif isinstance(collection, tuple):
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
    collection : Set[T] | None
        The input collection to be converted to a set.
        If None is provided, None is returned.

    Returns
    -------
    set[T] | None
        A new set containing all elements of the input collection,
        or the original set if it was already one.
        Returns None if None was provided.
    """

    if collection is None:
        return None

    elif isinstance(collection, set):
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
    collection : Mapping[K, V] | None
        The input collection to be converted to a dict.
        If None is provided, None is returned.

    Returns
    -------
    dict[K, V] | None
        A new dict containing all elements of the input collection,
        or the original dict if it was already one.
        Returns None if None was provided.
    """

    if collection is None:
        return None

    elif isinstance(collection, dict):
        return collection

    else:
        return dict(collection)


@overload
def without_missing[T: Mapping[str, Any]](
    mapping: Mapping[str, Any],
    /,
) -> Mapping[str, Any]: ...


@overload
def without_missing[T: Mapping[str, Any]](
    mapping: Mapping[str, Any],
    /,
    *,
    typed: type[T],
) -> T: ...


def without_missing[T: Mapping[str, Any]](
    mapping: Mapping[str, Any],
    /,
    *,
    typed: type[T] | None = None,
) -> T | Mapping[str, Any]:
    """
    Create a new mapping without any items that have MISSING values.

    Parameters
    ----------
    mapping : Mapping[str, Any]
        The input mapping to be filtered.
    typed : type[T] | None, default=None
        Optional type to cast the result to. If provided, the result will be
        cast to this type before returning.

    Returns
    -------
    T | Mapping[str, Any]
        A new mapping containing all items of the input mapping,
        except items with MISSING values. If typed is provided,
        the result is cast to that type.
    """
    return cast(T, {key: value for key, value in mapping.items() if value is not MISSING})
