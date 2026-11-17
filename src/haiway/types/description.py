from typing import final

__all__ = ("Description",)


@final
class Description:
    """
    Value object representing a description annotation.

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
