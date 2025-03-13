from collections.abc import Callable
from typing import Any, cast, final, overload

from haiway.types.missing import MISSING, Missing, not_missing
from haiway.utils.always import always

__all__ = [
    "Default",
    "DefaultValue",
]


@final
class DefaultValue[Value]:
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
    return cast(
        Value,
        DefaultValue(
            value,
            factory=factory,
        ),
    )
