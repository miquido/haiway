from typing import Any, final

__all__ = ("Description",)


@final
class Description:
    """
    Immutable value object representing a description annotation.

    Parameters
    ----------
    description : str
    A non-empty description string
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
    ) -> None:
        raise AttributeError("Description can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> None:
        raise AttributeError("Description can't be modified")
