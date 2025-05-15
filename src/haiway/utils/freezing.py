from typing import Any

__all__ = ("freeze",)


def freeze(
    instance: object,
    /,
) -> None:
    """
    Make an object instance immutable by preventing attribute modification.

    This function modifies the given object to prevent further changes to its attributes.
    It replaces the object's __setattr__ and __delattr__ methods with ones that raise
    exceptions, effectively making the object immutable after this function is called.

    Parameters
    ----------
    instance : object
        The object instance to make immutable

    Returns
    -------
    None
        The object is modified in-place

    Notes
    -----
    - This only affects direct attribute assignments and deletions
    - Mutable objects contained within the instance can still be modified internally
    - The object's class remains unchanged, only the specific instance is affected
    """

    def frozen_set(
        __name: str,
        __value: Any,
    ) -> None:
        raise RuntimeError(f"{instance.__class__.__qualname__} is frozen and can't be modified")

    def frozen_del(
        __name: str,
    ) -> None:
        raise RuntimeError(f"{instance.__class__.__qualname__} is frozen and can't be modified")

    instance.__delattr__ = frozen_del
    instance.__setattr__ = frozen_set
