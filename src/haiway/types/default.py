from collections.abc import Callable
from functools import partial
from os import getenv as os_getenv
from typing import Any, Literal, assert_never, cast, final, overload

from haiway.types.missing import Missing

__all__ = (
    "Default",
    "DefaultEnv",
    "DefaultFactory",
    "DefaultValue",
)


@final
class _NoDefault:
    __slots__ = ()


@final
class _ConstantDefault:
    __slots__ = ("value",)

    def __init__(
        self,
        value: Any,
    ) -> None:
        self.value: Any = value


@final
class _ComputedDefault:
    __slots__ = ("resolve",)

    def __init__(
        self,
        resolve: Callable[[], Any | Missing],
    ) -> None:
        self.resolve: Callable[[], Any | Missing] = resolve


@final
class DefaultValue:
    """
    Resolver for field default values.

    ``DefaultValue`` stores a prepared default source. The owning ``State`` type
    calls the instance during object construction to resolve the effective value
    for that field. It is made by ``Default``, ``DefaultFactory`` and ``DefaultEnv``
    rather than constructed directly - what it holds is no default at all, a value which
    is the very same each time, or a callable invoked every time it is resolved.
    """

    __slots__ = ("_source",)

    def __init__(
        self,
        source: _NoDefault | _ConstantDefault | _ComputedDefault,
    ) -> None:
        self._source: _NoDefault | _ConstantDefault | _ComputedDefault = source

    @property
    def available(self) -> bool:
        """Whether a default exists, i.e. the field is not required because of it."""
        source: _NoDefault | _ConstantDefault | _ComputedDefault = self._source
        match source:
            case _NoDefault():
                return False

            case _ConstantDefault() | _ComputedDefault():
                return True

        assert_never(source)

    @property
    def constant(self) -> bool:
        """Whether resolving yields the very same value each time - true of a plain default
        and of no default at all."""
        source: _NoDefault | _ConstantDefault | _ComputedDefault = self._source
        match source:
            case _NoDefault() | _ConstantDefault():
                return True

            case _ComputedDefault():
                return False

        assert_never(source)

    def __call__(self) -> Any | Missing:
        source: _NoDefault | _ConstantDefault | _ComputedDefault = self._source
        if isinstance(source, _ConstantDefault):
            return source.value

        elif isinstance(source, _ComputedDefault):
            return source.resolve()

        elif type(source) is _NoDefault:
            return Missing

        else:
            assert_never(source)


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
    AssertionError
        If both a literal and a factory are provided.
    """
    if factory is Missing:
        return cast(
            Value,
            DefaultValue(_NoDefault() if default is Missing else _ConstantDefault(default)),
        )

    else:
        assert default is Missing  # nosec: B101

        return cast(
            Value,
            DefaultValue(_ComputedDefault(factory)),
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
            _ComputedDefault(partial(factory, *args, **kwargs) if args or kwargs else factory),
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


def DefaultEnv[Value = str, Fallback = None](
    name: str,
    /,
    *,
    fallback: Fallback | Missing = Missing,
    required: bool = False,
    mapping: Callable[[str], Any] | Missing = Missing,
) -> Value | Fallback:
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
    AssertionError
        If ``required`` is combined with a fallback.
    ValueError
        When resolving, if a required variable is not set or ``mapping`` rejects
        its value.

    Examples
    --------
    >>> host: str = DefaultEnv("SERVICE_HOST", fallback="localhost")
    >>> port: int = DefaultEnv("SERVICE_PORT", mapping=int, required=True)
    """
    assert not required or fallback is Missing  # nosec: B101

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

    return cast(
        Value | Fallback,
        DefaultValue(_ComputedDefault(resolve)),
    )
