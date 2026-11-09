from collections.abc import Callable
from os import getenv as os_getenv
from typing import Any, NoReturn, cast, final, overload

from haiway.types.missing import Missing

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
        Combined with ``env`` it becomes the fallback used when the variable is
        not set.
    default_factory : Callable[[], Any] | Missing, optional
        Zero-argument callable invoked every time the default is resolved.
    env : str | Missing, optional
        Environment variable name read via ``os.getenv`` when resolving the
        default.
    mapping : Callable[[str], Any] | Missing, optional
        Transformation applied to the raw environment value, converting it to
        the type of the field. Requires ``env``.

    Raises
    ------
    AssertionError
        If incompatible sources are provided together.

    Examples
    --------
    >>> with_default: UUID = Default(factory=uuid4)
    """

    __slots__ = (
        "_resolve",
        "available",
        "constant",
        "env",
    )

    def __init__(
        self,
        *,
        default: Any | Missing = Missing,
        default_factory: Callable[[], Any] | Missing = Missing,
        env: str | Missing = Missing,
        mapping: Callable[[str], Any] | Missing = Missing,
    ) -> None:
        self._resolve: Callable[[], Any | Missing]
        self.available: bool
        # resolves to the very same value each time, which is the case of a plain
        # default only - a factory or an environment variable is read on every use
        self.constant: bool
        # the variable backing this default, kept to name it when it is not set -
        # an unset variable resolves to Missing, which would otherwise fail deep
        # within type validation without ever mentioning what has to be provided
        self.env: str | None
        object.__setattr__(
            self,
            "env",
            env if env is not Missing else None,
        )
        if default_factory is not Missing:
            assert default is Missing and env is Missing and mapping is Missing  # nosec: B101
            object.__setattr__(
                self,
                "_resolve",
                default_factory,
            )
            object.__setattr__(
                self,
                "available",
                True,
            )

        elif env is not Missing:
            # the variable is read on each resolution, i.e. when the owning
            # object is constructed - reading it here would freeze the value at
            # import time, before `load_env` or a test had a chance to set it
            def resolve_env() -> Any | Missing:
                value: str | None = os_getenv(env)
                if value is None:
                    return default  # Missing unless a fallback was provided

                if mapping is not Missing:
                    try:
                        return mapping(value)

                    except Exception as exc:
                        raise ValueError(f"Environment value `{env}` is not valid!") from exc

                return value

            object.__setattr__(
                self,
                "_resolve",
                resolve_env,
            )
            object.__setattr__(
                self,
                "available",
                True,
            )

        else:
            assert mapping is Missing  # nosec: B101
            object.__setattr__(
                self,
                "_resolve",
                lambda: default,
            )
            object.__setattr__(
                self,
                "available",
                default is not Missing,
            )

        object.__setattr__(
            self,
            "constant",
            default_factory is Missing and env is Missing,
        )

    def __call__(self) -> Any | Missing:
        return self._resolve()

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


@overload
def Default[Value](
    default: Value,
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


@overload
def Default[Fallback](
    *,
    env: str,
    default: Fallback,
) -> str | Fallback: ...


@overload
def Default[Value](
    *,
    env: str,
    mapping: Callable[[str], Value],
) -> Value: ...


@overload
def Default[Value, Fallback](
    *,
    env: str,
    mapping: Callable[[str], Value],
    default: Fallback,
) -> Value | Fallback: ...


def Default[Value](
    default: Value | Missing = Missing,
    *,
    factory: Callable[[], Value] | Missing = Missing,
    env: str | Missing = Missing,
    mapping: Callable[[str], Value] | Missing = Missing,
    # the overloads resolve the field type, an environment backed default with a
    # fallback resolves to the union of the two and cannot be stated here
) -> Any:
    """Create a field default resolver for ``Immutable`` and ``State`` types.

    The returned object is a ``DefaultValue`` instance disguised as ``Value`` so
    static type checkers treat the annotated field as its resolved runtime type.
    Haiway consumes it while constructing an ``Immutable`` or ``State``
    subclass; it is not a descriptor and it does not defer resolution until
    attribute access.

    Parameters
    ----------
    default : Value | Missing, optional
        Literal value used when neither ``factory`` nor ``env`` are supplied.
        Alongside ``env`` it is the fallback for an unset variable; without one
        an unset variable makes the field required.
    factory : Callable[[], Value] | Missing, optional
        Callable that is executed on demand to produce the default value.
    env : str | Missing, optional
        Name of the environment variable queried for the default value when no
        other source is set.
    mapping : Callable[[str], Value] | Missing, optional
        Transformation converting the raw environment value to the type of the
        field, such as ``int`` or ``parse_bool``. Requires ``env``.

    Returns
    -------
    Value
        A typed field marker wrapping an immutable ``DefaultValue`` resolver.

    Raises
    ------
    AssertionError
        If multiple sources are provided simultaneously.

    Notes
    -----
    An environment backed default is resolved when the owning object is
    constructed, not when the class is defined, so ``load_env`` and test
    monkeypatching both apply regardless of import order.
    """
    return cast(
        Value,
        DefaultValue(
            default=default,
            default_factory=factory,
            env=env,
            mapping=mapping,
        ),
    )
