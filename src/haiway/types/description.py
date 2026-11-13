from typing import final

from haiway.types.frozen import same_copy, same_deepcopy

__all__ = ("Description",)


@final
class Description:
    """
    Immutable value object representing a description annotation.

    Parameters
    ----------
    description : str
    A description string

    Examples
    --------
    >>> described: Annotated[str, Description("Lorem ipsum...")]
    """

    __slots__ = ("description",)

    def __init__(
        self,
        description: str,
        /,
    ) -> None:
        self.description: str
        self.description = description

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
