from typing import Any, final

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
    >>> alias = Alias("customer_id")
    >>> alias.alias
    'customer_id'
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
    ) -> None:
        raise AttributeError("Alias can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> None:
        raise AttributeError("Alias can't be modified")
