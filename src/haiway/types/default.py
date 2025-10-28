from collections.abc import Callable
from os import getenv as os_getenv
from typing import Any, cast, final, overload

from haiway.types.missing import MISSING, Missing, not_missing

__all__ = ("Default", "DefaultValue")


@final
class DefaultValue:
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
        /,
        *,
        default: Any | Missing,
    ) -> None: ...

    @overload
    def __init__(
        self,
        /,
        *,
        default_factory: Callable[[], Any] | Missing,
    ) -> None: ...

    @overload
    def __init__(
        self,
        /,
        *,
        env: str | Missing,
    ) -> None: ...

    @overload
    def __init__(
        self,
        /,
        *,
        default: Any | Missing,
        default_factory: Callable[[], Any] | Missing,
        env: str | Missing,
    ) -> None: ...

    def __init__(
        self,
        *,
        default: Any | Missing = MISSING,
        default_factory: Callable[[], Any] | Missing = MISSING,
        env: str | Missing = MISSING,
    ) -> None:
        self._value: Callable[[], Any | Missing]
        if not_missing(default_factory):
            assert default is MISSING and env is MISSING  # nosec: B101
            object.__setattr__(
                self,
                "_value",
                default_factory,
            )

        elif not_missing(env):
            assert default is MISSING  # nosec: B101
            object.__setattr__(
                self,
                "_value",
                lambda: os_getenv(key=env, default=MISSING),
            )

        else:
            object.__setattr__(
                self,
                "_value",
                lambda: default,
            )

    def __call__(self) -> Any | Missing:
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


def Default[Value](
    default: Value | Missing = MISSING,
    *,
    default_factory: Callable[[], Value] | Missing = MISSING,
    env: str | Missing = MISSING,
) -> Value:
    return cast(
        Value,
        DefaultValue(
            default=default,
            default_factory=default_factory,
            env=env,
        ),
    )
