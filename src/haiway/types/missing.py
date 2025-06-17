from typing import Any, Final, TypeGuard, cast, final

__all__ = (
    "MISSING",
    "Missing",
    "is_missing",
    "not_missing",
    "when_missing",
)


class MissingType(type):
    """
    Metaclass for the Missing type implementing the singleton pattern.

    Ensures that only one instance of the Missing class ever exists,
    allowing for identity comparison using the 'is' operator.
    """

    _instance: Any = None

    def __call__(cls) -> Any:
        if cls._instance is None:
            cls._instance = super().__call__()
            return cls._instance

        else:
            return cls._instance


@final
class Missing(metaclass=MissingType):
    """
    Type representing absence of a value. Use MISSING constant for its value.

    This is a singleton class that represents the absence of a value, similar to
    None but semantically different. Where None represents "no value", MISSING
    represents "no value provided" or "value intentionally omitted".

    The MISSING constant is the only instance of this class and should be used
    for all comparisons using the 'is' operator, not equality testing.
    """

    __slots__ = ()
    __match_args__ = ()

    def __bool__(self) -> bool:
        return False

    def __hash__(self) -> int:
        return hash(self.__class__)

    def __eq__(
        self,
        value: object,
    ) -> bool:
        return value is MISSING

    def __str__(self) -> str:
        return "MISSING"

    def __repr__(self) -> str:
        return "MISSING"

    def __getattr__(
        self,
        name: str,
    ) -> Any:
        raise AttributeError("Missing has no attributes")

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


MISSING: Final[Missing] = Missing()


def is_missing(
    check: Any | Missing,
    /,
) -> TypeGuard[Missing]:
    """
    Check if a value is the MISSING sentinel.

    This function implements a TypeGuard that helps static type checkers
    understand when a value is confirmed to be the MISSING sentinel.

    Parameters
    ----------
    check : Any | Missing
        The value to check

    Returns
    -------
    TypeGuard[Missing]
        True if the value is MISSING, False otherwise

    Examples
    --------
    ```python
    if is_missing(value):
        # Here, type checkers know that value is Missing
        provide_default()
    ```
    """
    return check is MISSING


def not_missing[Value](
    check: Value | Missing,
    /,
) -> TypeGuard[Value]:
    """
    Check if a value is not the MISSING sentinel.

    This function implements a TypeGuard that helps static type checkers
    understand when a value is confirmed not to be the MISSING sentinel.

    Parameters
    ----------
    check : Value | Missing
        The value to check

    Returns
    -------
    TypeGuard[Value]
        True if the value is not MISSING, False otherwise

    Examples
    --------
    ```python
    if not_missing(value):
        # Here, type checkers know that value is of type Value
        process_value(value)
    ```
    """
    return check is not MISSING


def when_missing[Value](
    check: Value | Missing,
    /,
    *,
    value: Value,
) -> Value:
    """
    Substitute a default value when the input is MISSING.

    This function provides a convenient way to replace the MISSING
    sentinel with a default value, similar to how the or operator
    works with None but specifically for the MISSING sentinel.

    Parameters
    ----------
    check : Value | Missing
        The value to check
    value : Value
        The default value to use if check is MISSING

    Returns
    -------
    Value
        The original value if not MISSING, otherwise the provided default

    Examples
    --------
    ```python
    result = when_missing(optional_value, value=default_value)
    # result will be default_value if optional_value is MISSING
    # otherwise it will be optional_value
    ```
    """
    if check is MISSING:
        return value

    else:
        return cast(Value, check)
