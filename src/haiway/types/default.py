from collections.abc import Callable
from os import getenv as os_getenv
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
        env: str | Missing = MISSING,
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
        env: str | Missing,
        factory: Callable[[], Value] | Missing,
    ) -> None: ...

    def __init__(
        self,
        value: Value | Missing = MISSING,
        /,
        *,
        env: str | Missing = MISSING,
        factory: Callable[[], Value] | Missing = MISSING,
    ) -> None:
        """
        Initialize with either a default value or a factory function.

        Parameters
        ----------
        value : Value | Missing
            The default value to be used.
        factory : Callable[[], Value] | Missing
            A function that returns the default value when called.
        env: str | Missing
            Name of environment variable to use as default, resolved when requesting a default.
        """
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

        elif not_missing(env):
            object.__setattr__(
                self,
                "_value",
                lambda: os_getenv(key=env, default=MISSING),
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
        raise AttributeError("DefaultValue can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> None:
        raise AttributeError("DefaultValue can't be modified")


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


@overload
def Default(
    *,
    env: str,
) -> str: ...


def Default[Value](
    value: Value | Missing = MISSING,
    /,
    *,
    factory: Callable[[], Value] | Missing = MISSING,
    env: str | Missing = MISSING,
) -> Value:  # it is actually a DefaultValue, but type checker has to be fooled most some cases
    """
    Create a default value container that appears as the actual value type.

    This function creates a DefaultValue instance but returns it typed as the actual
    value type it contains. This allows type checkers to treat it as if it were the
    actual value while still maintaining the lazy evaluation behavior.

    Parameters
    ----------
    value : Value | Missing
        The default value to be used.
    factory : Callable[[], Value] | Missing
        A function that returns the default value when called.
    env: str | Missing
        Name of environment variable to use as default, resolved when requesting a default.

    Returns
    -------
    Value
        A DefaultValue instance that appears to be of type Value for type checking purposes

    Notes
    -----
    Only one of value or factory or env should be provided. If more than one is provided, an exception will be raised.
    """  # noqa: E501
    return cast(
        Value,
        DefaultValue(
            value,
            env=env,
            factory=factory,
        ),
    )
