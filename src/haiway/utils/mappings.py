from collections.abc import Mapping

__all__ = [
    "as_dict",
]


def as_dict[K, V](
    mapping: Mapping[K, V],
    /,
) -> dict[K, V]:
    """
    Converts any given mapping into a dict.

    Parameters
    ----------
    mapping : Mapping[K, V]
        The input mapping to be converted.

    Returns
    -------
    dict[K, V]
        A new dict containing all elements of the input mapping,
        or the original dict if it was already one.
    """
    if isinstance(mapping, dict):
        return mapping

    else:
        return dict(mapping)
