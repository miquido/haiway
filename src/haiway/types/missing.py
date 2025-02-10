from typing import Any, Final, TypeGuard, cast, final

__all__ = [
    "MISSING",
    "Missing",
    "is_missing",
    "not_missing",
    "when_missing",
]


class MissingType(type):
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
    """

    __slots__ = ()
    __match_args__ = ()

    def __bool__(self) -> bool:
        return False

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
    return check is MISSING


def not_missing[Value](
    check: Value | Missing,
    /,
) -> TypeGuard[Value]:
    return check is not MISSING


def when_missing[Value](
    check: Value | Missing,
    /,
    *,
    value: Value,
) -> Value:
    if check is MISSING:
        return value

    else:
        return cast(Value, check)
