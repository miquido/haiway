from typing import Any, NoReturn, final

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
        object.__setattr__(
            self,
            "description",
            description,
        )

    def __setattr__(
        self,
        __name: str,
        __value: Any,
    ) -> NoReturn:
        raise AttributeError("Description can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> NoReturn:
        raise AttributeError("Description can't be modified")
