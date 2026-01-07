from typing import Any, NoReturn, final

__all__ = ("Alias",)


@final
class Alias:
    """Immutable annotation that records an alternate name for a bound value.

    Parameters
    ----------
    alias : str
        Non-empty string identifying the exposed name that should be used
        when the annotated value is surfaced externally.

    Examples
    --------
    >>> aliased: Annotated[str, Alias("customer_id")]
    """

    __slots__ = ("alias",)

    def __init__(
        self,
        alias: str,
        /,
    ) -> None:
        assert alias  # nosec: B101

        self.alias: str
        object.__setattr__(
            self,
            "alias",
            alias,
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
