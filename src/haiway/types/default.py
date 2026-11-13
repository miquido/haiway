from collections.abc import Callable
from functools import partial
from os import getenv as os_getenv
from typing import Any, Literal, cast, final, overload

from haiway.types.frozen import same_copy, same_deepcopy
from haiway.types.missing import Missing

__all__ = (
    "Default",
    "DefaultEnv",
    "DefaultFactory",
    "DefaultValue",
)


@final
class DefaultValue:
    """
    Immutable resolver for field default values.

    ``DefaultValue`` stores a prepared default resolver. The owning ``State`` type
    calls the instance during object construction to resolve the effective value
    for that field. Use ``Default``, ``DefaultFactory`` or ``DefaultEnv`` to create one.

    Parameters
    ----------
    resolve : Callable[[], Any | Missing]
        Callable invoked every time the default is resolved.
    available : bool
        Whether a default exists, i.e. the field is not required because of it.
    constant : bool
        Whether ``resolve`` yields the very same value each time, which is the
        case of a plain default only.
    """

    __slots__ = (
        "_resolve",
        "available",
        "constant",
    )

    def __init__(
        self,
        resolve: Callable[[], Any | Missing],
        available: bool,
        constant: bool,
    ) -> None:
        self._resolve: Callable[[], Any | Missing] = resolve
        self.available: bool = available
        self.constant: bool = constant

    def __call__(self) -> Any | Missing:
        return self._resolve()

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy


@overload
def Default[Value](
    default: Value,
) -> Value: ...


@overload
def Default[Value](
    *,
    factory: Callable[[], Value],
) -> Value: ...


def Default[Value](
    default: Value | Missing = Missing,
    *,
    factory: Callable[[], Value] | Missing = Missing,
) -> Any:
    """Create a field default resolver for ``State`` types.

    The returned object is a ``DefaultValue`` instance disguised as ``Value`` so
    static type checkers treat the annotated field as its resolved runtime type.
    Haiway consumes it while constructing a ``State``
    subclass; it is not a descriptor and it does not defer resolution until
    attribute access. See ``DefaultFactory`` for a factory called with arguments
    and ``DefaultEnv`` for a value read from an environment variable.

    Parameters
    ----------
    default : Value | Missing, optional
        Literal value used when ``factory`` is not supplied.
    factory : Callable[[], Value] | Missing, optional
        Callable that is executed on demand to produce the default value.

    Returns
    -------
    Value
        A typed field marker wrapping an immutable ``DefaultValue`` resolver.

    Raises
    ------
    ValueError
        If both a literal and a factory are provided.
    """
    if factory is not Missing:
        if default is not Missing:
            raise ValueError("A default factory can't be combined with a literal default")

        return cast(Value, DefaultValue(factory, available=True, constant=False))

    return cast(
        Value,
        DefaultValue(lambda: default, available=default is not Missing, constant=True),
    )


def DefaultFactory[**Args, Value](
    factory: Callable[Args, Value],
    /,
    *args: Args.args,
    **kwargs: Args.kwargs,
) -> Value:
    """Create a default produced by calling ``factory`` with the given arguments.

    Equivalent to ``Default(factory=lambda: factory(*args, **kwargs))``, without
    the lambda. The call is made every time the default is resolved, the
    arguments are evaluated once, when the class is defined.

    Parameters
    ----------
    factory : Callable[Args, Value]
        Callable producing the default value.
    *args : Args.args
        Positional arguments passed to ``factory``.
    **kwargs : Args.kwargs
        Keyword arguments passed to ``factory``.

    Returns
    -------
    Value
        A typed field marker wrapping an immutable ``DefaultValue`` resolver.

    Examples
    --------
    >>> tags: Sequence[str] = DefaultFactory(tuple, ("a", "b"))
    """
    return cast(
        Value,
        DefaultValue(
            partial(factory, *args, **kwargs) if args or kwargs else factory,
            available=True,
            constant=False,
        ),
    )


@overload
def DefaultEnv(
    name: str,
    /,
    *,
    required: Literal[True],
) -> str: ...


@overload
def DefaultEnv[Value](
    name: str,
    /,
    *,
    required: Literal[True],
    mapping: Callable[[str], Value],
) -> Value: ...


@overload
def DefaultEnv[Fallback](
    name: str,
    /,
    *,
    fallback: Fallback,
) -> str | Fallback: ...


@overload
def DefaultEnv[Value, Fallback](
    name: str,
    /,
    *,
    fallback: Fallback,
    mapping: Callable[[str], Value],
) -> Value | Fallback: ...


def DefaultEnv(
    name: str,
    /,
    *,
    fallback: Any | Missing = Missing,
    required: bool = False,
    mapping: Callable[[str], Any] | Missing = Missing,
    # the overloads resolve the field type, a fallback resolves to the union of
    # the two and cannot be stated here
) -> Any:
    """Create a default read from an environment variable.

    The variable is read when the owning object is constructed, not when the
    class is defined, so ``load_env`` and test monkeypatching both apply
    regardless of import order. What happens when it is not set has to be stated
    explicitly, either with a fallback or by marking it as required.

    Parameters
    ----------
    name : str
        Name of the environment variable.
    fallback : Any | Missing, optional
        Fallback used when the variable is not set. ``Missing`` makes the value
        optional, which requires the field to accept ``Missing``. It is not
        passed through ``mapping``.
    required : bool, default=False
        Raise when the variable is not set instead of using a fallback.
    mapping : Callable[[str], Any] | Missing, optional
        Transformation converting the raw value to the type of the field, such
        as ``int`` or ``parse_bool``.

    Returns
    -------
    Any
        A typed field marker wrapping an immutable ``DefaultValue`` resolver.

    Raises
    ------
    ValueError
        If ``required`` is combined with a fallback. When resolving, if a
        required variable is not set or ``mapping`` rejects its value.

    Examples
    --------
    >>> host: str = DefaultEnv("SERVICE_HOST", fallback="localhost")
    >>> port: int = DefaultEnv("SERVICE_PORT", mapping=int, required=True)
    """
    if required and fallback is not Missing:
        raise ValueError("A required environment default can't have a fallback")

    def resolve() -> Any | Missing:
        value: str | None = os_getenv(name)
        if value is None:
            if required:
                raise ValueError(f"Required environment value `{name}` is missing!")

            return fallback

        if mapping is not Missing:
            try:
                return mapping(value)

            except Exception as exc:
                raise ValueError(f"Environment value `{name}` is not valid!") from exc

        return value

    return DefaultValue(
        resolve,
        available=True,
        constant=False,
    )
