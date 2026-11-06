import collections.abc
import threading
import typing
from collections.abc import Mapping, MutableMapping, Sequence
from typing import (
    Any,
    ParamSpec,
    TypeAliasType,
    TypeVar,
    TypeVarTuple,
    cast,
    get_args,
    get_origin,
)

__all__ = (
    "Variance",
    "argument_variances",
    "bind_type_arguments",
    "is_open_type_argument",
    "parameter_variances",
    "type_parameters_of",
    "unspecialized_defaults",
)

type Variance = typing.Literal["co", "contra", "inv"]


def type_parameters_of(cls: type[Any], /) -> Sequence[Any]:
    """
    The type parameters of a generic class, however they are declared.

    Parameters
    ----------
    cls : type[Any]
        Generic class, declared as ``class Box[T]`` or as ``class Box(Generic[T])``.

    Returns
    -------
    Sequence[Any]
        Its type parameters in the order they were declared, none if it is not generic.
    """
    # `class Box[T]` has them as `__type_params__`, `class Box(Generic[T])` does not
    return getattr(cls, "__type_params__", ()) or getattr(cls, "__parameters__", ())


def unspecialized_defaults(cls: type[Any], /) -> Mapping[str, Any]:
    """What the parameters of a generic class stand for when it is not specialized.

    The parameters which declare a default (PEP 696) stand for it, each in terms of
    the ones before it. A parameter without one is left out, and stands for
    its bound, or for anything.
    """
    bound: dict[str, Any] = {}
    defaults: dict[str, Any] = {}
    for parameter in type_parameters_of(cls):
        has_default: Any = getattr(parameter, "has_default", None)
        if has_default is None or not has_default():
            continue

        default: Any = parameter.__default__
        if isinstance(parameter, TypeVarTuple):
            # `*Ts = *tuple[int, str]` - the arguments it stands for, as a tuple
            elements: Sequence[Any] = get_args(default)
            if get_origin(default) is typing.Unpack:
                elements = get_args(elements[0]) if elements else ()

            if Ellipsis in elements:
                continue  # of any length - nothing to stand for

            default = tuple[*elements]

        value: Any = _substituted(_normalized(default), bound)
        bound[parameter.__name__] = value
        defaults[parameter.__name__] = value

    return defaults


def is_open_type_argument(argument: Any, /) -> bool:
    """
    Whether an argument is a parameter itself, leaving the specialization unfinished.

    Parameters
    ----------
    argument : Any
        Argument a generic class is subscripted with.

    Returns
    -------
    bool
        True for a ``TypeVar``, ``ParamSpec``, ``TypeVarTuple`` or the unpacked ``*Ts``.
    """
    if isinstance(argument, TypeVar | ParamSpec | TypeVarTuple):
        return True

    # `*Ts` - the unpacked variadic parameter
    return get_origin(argument) is typing.Unpack and isinstance(get_args(argument)[0], TypeVarTuple)


def bind_type_arguments(
    cls: type[Any],
    given: tuple[Any, ...],
    /,
) -> tuple[Sequence[tuple[Any, Any]], tuple[Any, ...]]:
    """Match the arguments of a specialization with the parameters of the class.

    Fills the parameters which declare a default, gives a variadic parameter
    whatever the others leave over, and spells the argument of a ``ParamSpec`` as
    a tuple - which, unlike the list it is written as, can be hashed.

    Returns
    -------
    tuple[Sequence[tuple[Any, Any]], tuple[Any, ...]]
        Each parameter with the argument bound to it - a tuple for a variadic one -
        and the same arguments flattened, in the order they were given.

    Raises
    ------
    TypeError
        If there are too few or too many arguments for the parameters.
    """
    parameters: Sequence[Any] = type_parameters_of(cls)
    variadic_at: int | None = next(
        (i for i, parameter in enumerate(parameters) if isinstance(parameter, TypeVarTuple)),
        None,
    )

    arguments: list[Any] = [_normalized(argument) for argument in given]
    # a lone parameter is a signature - `Handler[int, str]` is `Handler[[int, str]]`
    lone: bool = len(parameters) == 1 and isinstance(parameters[0], ParamSpec)
    if lone and not _is_signature(arguments):
        arguments = [tuple(arguments)]

    bindings: list[tuple[Any, Any]] = []
    if variadic_at is None:
        arguments.extend(_defaults(parameters, arguments))
        if len(arguments) != len(parameters):
            raise TypeError(
                f"{cls.__name__} takes {len(parameters)} type arguments, {len(given)} given"
            )

        bindings = list(zip(parameters, arguments, strict=True))

    else:
        before: Sequence[Any] = parameters[:variadic_at]
        after: Sequence[Any] = parameters[variadic_at + 1 :]
        if len(arguments) < len(before) + len(after):
            raise TypeError(
                f"{cls.__name__} takes at least {len(before) + len(after)} type arguments,"
                f" {len(given)} given"
            )

        variadic_end: int = len(arguments) - len(after)
        bindings = [
            *zip(before, arguments[: len(before)], strict=True),
            (parameters[variadic_at], tuple(arguments[len(before) : variadic_end])),
            *zip(after, arguments[variadic_end:], strict=True),
        ]

    # what was given, with the defaults - a variadic parameter takes its share of it
    return bindings, tuple(arguments)


def _normalized(argument: Any, /) -> Any:
    # the argument of a `ParamSpec` is written as a list, which can't be hashed
    if isinstance(argument, list):
        return tuple(cast(list[Any], argument))

    return argument


def _is_signature(arguments: Sequence[Any], /) -> bool:
    # the one argument a `ParamSpec` can be given as it is
    return len(arguments) == 1 and (
        arguments[0] is Ellipsis
        or isinstance(arguments[0], tuple | ParamSpec)
        or get_origin(arguments[0]) is typing.Concatenate
    )


def _defaults(
    parameters: Sequence[Any],
    given: Sequence[Any],
    /,
) -> Sequence[Any]:
    # a default may be written in terms of the parameters before it
    bound: dict[str, Any] = {
        parameter.__name__: argument for parameter, argument in zip(parameters, given, strict=False)
    }
    defaults: list[Any] = []
    for parameter in parameters[len(given) :]:
        if not parameter.has_default():
            break  # the rest is missing, and reported as such

        default: Any = _substituted(_normalized(parameter.__default__), bound)
        bound[parameter.__name__] = default
        defaults.append(default)

    return defaults


def _substituted(
    annotation: Any,
    bound: Mapping[str, Any],
    /,
) -> Any:
    if isinstance(annotation, TypeVar):
        return bound.get(annotation.__name__, annotation)

    parameters: Sequence[Any] = getattr(annotation, "__parameters__", ())
    names: Sequence[Any] = [getattr(parameter, "__name__", None) for parameter in parameters]
    if parameters and all(name in bound for name in names):
        try:
            return annotation[tuple(bound[name] for name in names)]

        except TypeError:
            return annotation  # not something which can be filled in

    return annotation


def argument_variances(
    cls: type[Any],
    count: int,
    /,
) -> Sequence[Variance]:
    """
    The variance of each of the arguments a specialization of the class was given.

    Parameters
    ----------
    cls : type[Any]
        Generic class which was specialized.
    count : int
        Number of arguments the specialization has, flattened - a variadic parameter
        takes the ones the others leave over.

    Returns
    -------
    Sequence[Variance]
        The variance of the parameter each argument is bound to, in the order given.
    """
    variances: Mapping[str, Variance] = parameter_variances(cls)
    parameters: Sequence[Any] = type_parameters_of(cls)
    result: list[Variance] = []
    for parameter in parameters:
        # a variadic parameter takes the arguments the others leave over
        copies: int = count - (len(parameters) - 1) if isinstance(parameter, TypeVarTuple) else 1
        variance: Variance = variances[parameter.__name__]
        result.extend(variance for _ in range(copies))

    return result


def parameter_variances(
    cls: type[Any],
    /,
) -> Mapping[str, Variance]:
    """
    The variance of each type parameter of a generic class, by the name of it.

    A declared one is honored. A parameter whose variance is inferred - the way
    ``class Box[T]`` does - is worked out from where it is used by the attributes:
    only ever read from, it is covariant, which is all an immutable ``State``
    can do with it. The parameter of an argument of a callable is where it is
    given, and it is contravariant there. A parameter declared without a variance
    keeps being covariant, as the instances can't be changed.

    Parameters
    ----------
    cls : type[Any]
        Generic class.

    Returns
    -------
    Mapping[str, Variance]
        ``"co"``, ``"contra"`` or ``"inv"`` for each parameter, by its name.
    """
    cached: Mapping[str, Variance] | None = cls.__dict__.get("__VARIANCES__")
    if cached is not None:
        return cached

    computing: set[type[Any]] = _computing_variances()
    computing.add(cls)  # a class using itself is read as it is declared, not recursed into
    try:
        return _worked_out_variances(cls)

    finally:
        computing.discard(cls)


def _worked_out_variances(
    cls: type[Any],
    /,
) -> Mapping[str, Variance]:

    variances: MutableMapping[str, Variance] = {}
    annotations: Mapping[str, Any] = getattr(cls, "__ANNOTATED__", {})
    for parameter in type_parameters_of(cls):
        if getattr(parameter, "__contravariant__", False):
            variances[parameter.__name__] = "contra"

        elif getattr(parameter, "__covariant__", False) or not getattr(
            parameter, "__infer_variance__", False
        ):
            variances[parameter.__name__] = "co"

        else:
            variances[parameter.__name__] = _inferred_variance(parameter, annotations)

    # a class which is not declared yet has no annotations to read the use of a
    # parameter from - what is worked out then is not kept
    if hasattr(cls, "__ANNOTATED__"):
        cls.__VARIANCES__ = variances  # pyright: ignore[reportAttributeAccessIssue]

    return variances


def _inferred_variance(
    parameter: Any,
    annotations: Mapping[str, Any],
    /,
) -> Variance:
    seen: set[bool] = set()  # True - where it is read from, False - where it is given
    for annotation in annotations.values():
        _collect_positions(annotation, parameter.__name__, True, seen, 0)

    if seen == {False}:
        return "contra"

    if seen == {True, False}:
        return "inv"

    return "co"


_MAX_DEPTH: int = 16
_state = threading.local()


def _computing_variances() -> set[type[Any]]:
    # the classes being worked out by this thread - another one has its own
    computing: set[type[Any]] | None = getattr(_state, "computing", None)
    if computing is None:
        computing = set()
        _state.computing = computing

    return computing


def _collect_positions(
    annotation: Any,
    name: str,
    positive: bool,
    seen: set[bool],
    depth: int,
    /,
) -> None:
    if depth > _MAX_DEPTH:
        return  # a recursive annotation - what was found so far decides

    if isinstance(annotation, TypeVar | TypeVarTuple | ParamSpec):
        if annotation.__name__ == name:
            seen.add(positive)

        return

    origin: Any = get_origin(annotation)
    if origin is None:
        return

    arguments: Sequence[Any] = get_args(annotation)
    if origin in (collections.abc.Callable, typing.Callable):  # pyright: ignore[reportUnknownMemberType]
        _collect_callable_positions(arguments, name, positive, seen, depth)

    elif isinstance(origin, TypeAliasType):
        _collect_alias_positions(origin, arguments, name, positive, seen, depth)

    elif (
        getattr(origin, "__ANNOTATED__", None) is not None and origin not in _computing_variances()
    ):
        _collect_state_positions(origin, arguments, name, positive, seen, depth)

    else:
        # `Annotated` carries its metadata along as arguments, which are not types
        for argument in arguments[:1] if origin is typing.Annotated else arguments:
            _collect_positions(argument, name, positive, seen, depth + 1)


def _collect_alias_positions(
    alias: TypeAliasType,
    arguments: Sequence[Any],
    name: str,
    positive: bool,
    seen: set[bool],
    depth: int,
    /,
) -> None:
    # `type Callback[T] = Callable[[T], None]` - an argument is used the way the alias
    # uses its parameter, which is what its value says
    for parameter, argument in zip(alias.__type_params__, arguments, strict=False):
        used: set[bool] = set()
        _collect_positions(alias.__value__, parameter.__name__, True, used, depth + 1)
        for read in used:
            _collect_positions(argument, name, positive if read else not positive, seen, depth + 1)


def _collect_callable_positions(
    arguments: Sequence[Any],
    name: str,
    positive: bool,
    seen: set[bool],
    depth: int,
    /,
) -> None:
    # the arguments of a callable are given to it - the other way around
    given: Sequence[Any] = ()
    if arguments and isinstance(arguments[0], list | tuple):
        given = cast(Sequence[Any], arguments[0])

    elif arguments and isinstance(arguments[0], ParamSpec):
        given = (arguments[0],)  # `Callable[P, R]` - all of its arguments are given to it

    elif arguments and get_origin(arguments[0]) is typing.Concatenate:
        given = get_args(arguments[0])  # the types before the signature, and the signature

    for argument in given:
        _collect_positions(argument, name, not positive, seen, depth + 1)

    for argument in arguments[1:]:
        _collect_positions(argument, name, positive, seen, depth + 1)


def _collect_state_positions(
    origin: Any,
    arguments: Sequence[Any],
    name: str,
    positive: bool,
    seen: set[bool],
    depth: int,
    /,
) -> None:
    # another generic State - an argument is used the way that State uses its parameter
    variances: Mapping[str, Variance] = parameter_variances(origin)
    for parameter, argument in zip(type_parameters_of(origin), arguments, strict=False):
        match variances[parameter.__name__]:
            case "co":
                _collect_positions(argument, name, positive, seen, depth + 1)

            case "contra":
                _collect_positions(argument, name, not positive, seen, depth + 1)

            case "inv":
                _collect_positions(argument, name, True, seen, depth + 1)
                _collect_positions(argument, name, False, seen, depth + 1)
