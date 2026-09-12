from typing import Any, NoReturn, final

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
        assert name  # nosec: B101

        self.name: str
        object.__setattr__(
            self,
            "name",
            name,
        )

    def __setattr__(
        self,
        __name: str,
        __value: Any,
    ) -> NoReturn:
        raise AttributeError("Alias can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> NoReturn:
        raise AttributeError("Alias can't be modified")
