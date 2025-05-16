from collections.abc import Callable
from typing import Any, cast, final, overload

from haiway.types.missing import MISSING, Missing, not_missing
from haiway.utils.always import always

__all__ = (
    "Default",
    "DefaultValue",
)


@final
class DefaultValue[Value]:
    """
    Container for a default value or a factory function that produces a default value.

    This class stores either a direct default value or a factory function that can
    produce a default value when needed. It ensures the value or factory cannot be
    modified after initialization.

    The value can be retrieved by calling the instance like a function.
    """

    __slots__ = ("_value",)

    @overload
    def __init__(
        self,
        value: Value,
        /,
    ) -> None: ...

    @overload
    def __init__(
        self,
        /,
        *,
        factory: Callable[[], Value],
    ) -> None: ...

    @overload
    def __init__(
        self,
        value: Value | Missing,
        /,
        *,
        factory: Callable[[], Value] | Missing,
    ) -> None: ...

    def __init__(
        self,
        value: Value | Missing = MISSING,
        /,
        *,
        factory: Callable[[], Value] | Missing = MISSING,
    ) -> None:
        """
        Initialize with either a default value or a factory function.

        Parameters
        ----------
        value : Value | Missing
            The default value to store, or MISSING if using a factory
        factory : Callable[[], Value] | Missing
            A function that returns the default value when called, or MISSING if using a direct value

        Raises
        ------
        AssertionError
            If both value and factory are provided
        """  # noqa: E501
        assert (  # nosec: B101
            value is MISSING or factory is MISSING
        ), "Can't specify both default value and factory"

        self._value: Callable[[], Value | Missing]
        if not_missing(factory):
            object.__setattr__(
                self,
                "_value",
                factory,
            )

        else:
            object.__setattr__(
                self,
                "_value",
                always(value),
            )

    def __call__(self) -> Value | Missing:
        """
        Get the default value.

        Returns
        -------
        Value | Missing
            The stored default value, or the result of calling the factory function
        """
        return self._value()

    def __setattr__(
        self,
        __name: str,
        __value: Any,
    ) -> None:
        raise AttributeError("Missing can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> None:
        raise AttributeError("Missing can't be modified")


@overload
def Default[Value](
    value: Value,
    /,
) -> Value: ...


@overload
def Default[Value](
    *,
    factory: Callable[[], Value],
) -> Value: ...


def Default[Value](
    value: Value | Missing = MISSING,
    /,
    *,
    factory: Callable[[], Value] | Missing = MISSING,
) -> Value:  # it is actually a DefaultValue, but type checker has to be fooled most some cases
    """
    Create a default value container that appears as the actual value type.

    This function creates a DefaultValue instance but returns it typed as the actual
    value type it contains. This allows type checkers to treat it as if it were the
    actual value while still maintaining the lazy evaluation behavior.

    Parameters
    ----------
    value : Value | Missing
        The default value to store, or MISSING if using a factory
    factory : Callable[[], Value] | Missing
        A function that returns the default value when called, or MISSING if using a direct value

    Returns
    -------
    Value
        A DefaultValue instance that appears to be of type Value for type checking purposes

    Notes
    -----
    Only one of value or factory should be provided. If both are provided, an exception will be raised.
    """  # noqa: E501
    return cast(
        Value,
        DefaultValue(
            value,
            factory=factory,
        ),
    )
