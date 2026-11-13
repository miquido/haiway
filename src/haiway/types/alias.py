from typing import final

from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = ("Alias",)


@final
class Alias:
    """Immutable annotation that records an alternate name for a bound value.

    Parameters
    ----------
    name : str
        Non-empty string identifying the exposed name that should be used
        when the annotated value is surfaced externally.

    Examples
    --------
    >>> aliased: Annotated[str, Alias("customer_id")]
    """

    __slots__ = ("name",)

    def __init__(
        self,
        name: str,
        /,
    ) -> None:
        if not name:
            raise ValueError("Alias name can't be empty")

        self.name: str
        self.name = name

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
