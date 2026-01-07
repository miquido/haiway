from collections.abc import Callable
from os import getenv as os_getenv
from typing import Any, NoReturn, cast, final, overload

from haiway.types.missing import MISSING, Missing, not_missing

__all__ = (
    "Default",
    "DefaultValue",
)


@final
class DefaultValue:
    """
    Container for a default value or a factory function that produces a default value.

    This class stores either a direct default value or a factory function that can
    produce a default value when needed. It ensures the value or factory cannot be
    modified after initialization.

    The value can be retrieved by calling the instance like a function.

    Examples
    --------
    >>> with_default: UUID = Default(default_factory=uuid4)
    """

    __slots__ = (
        "_value",
        "available",
    )

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
        self.available: bool
        if not_missing(default_factory):
            assert default is MISSING and env is MISSING  # nosec: B101
            object.__setattr__(
                self,
                "_value",
                default_factory,
            )
            object.__setattr__(
                self,
                "available",
                True,
            )

        elif not_missing(env):
            assert default is MISSING  # nosec: B101

            object.__setattr__(
                self,
                "_value",
                lambda: os_getenv(env, default=default),
            )
            object.__setattr__(
                self,
                "available",
                True,
            )

        else:
            object.__setattr__(
                self,
                "_value",
                lambda: default,
            )
            object.__setattr__(
                self,
                "available",
                default is not MISSING,
            )

    def __call__(self) -> Any | Missing:
        return self._value()

    def __setattr__(
        self,
        __name: str,
        __value: Any,
    ) -> NoReturn:
        raise AttributeError("DefaultValue can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> NoReturn:
        raise AttributeError("DefaultValue can't be modified")


def Default[Value](
    default: Value | Missing = MISSING,
    *,
    default_factory: Callable[[], Value] | Missing = MISSING,
    env: str | Missing = MISSING,
) -> Value:
    """Create an immutable provider for a default value.

    Exactly one source must be provided: either a literal ``default``, a
    ``default_factory`` callable, or the name of an environment variable via
    ``env``. When the returned object is invoked it yields the configured
    default or ``MISSING`` if the resolver has no value.

    Parameters
    ----------
    default
        Literal value used when neither ``default_factory`` nor ``env`` are
        supplied.
    default_factory
        Callable that is executed on demand to produce the default value.
    env
        Name of the environment variable queried for the default value when no
        other source is set.

    Returns
    -------
    Value
        An immutable ``DefaultValue`` wrapper that can be called to retrieve
        the resolved default.

    Raises
    ------
    AssertionError
        If multiple sources are provided simultaneously.
    """
    return cast(
        Value,
        DefaultValue(
            default=default,
            default_factory=default_factory,
            env=env,
        ),
    )
