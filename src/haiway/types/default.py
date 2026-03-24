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
    Immutable resolver for field default values.

    ``DefaultValue`` stores exactly one default source: a literal value, a factory
    callable, or an environment variable lookup. The owning ``Immutable`` or
    ``State`` type calls the instance during object construction to resolve the
    effective value for that field.

    Parameters
    ----------
    default : Any | Missing, optional
        Literal default returned unchanged when no other source is configured.
    default_factory : Callable[[], Any] | Missing, optional
        Zero-argument callable invoked every time the default is resolved.
    env : str | Missing, optional
        Environment variable name read via ``os.getenv`` when resolving the
        default.

    Raises
    ------
    AssertionError
        If incompatible sources are provided together.

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
    """Create a field default resolver for ``Immutable`` and ``State`` types.

    The returned object is a ``DefaultValue`` instance disguised as ``Value`` so
    static type checkers treat the annotated field as its resolved runtime type.
    Haiway consumes it while constructing an ``Immutable`` or ``State``
    subclass; it is not a descriptor and it does not defer resolution until
    attribute access.

    Parameters
    ----------
    default : Value | Missing, optional
        Literal value used when neither ``default_factory`` nor ``env`` are
        supplied.
    default_factory : Callable[[], Value] | Missing, optional
        Callable that is executed on demand to produce the default value.
    env : str | Missing, optional
        Name of the environment variable queried for the default value when no
        other source is set.

    Returns
    -------
    Value
        A typed field marker wrapping an immutable ``DefaultValue`` resolver.

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
