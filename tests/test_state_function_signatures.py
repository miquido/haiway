import functools
import inspect
import itertools
import random
from collections.abc import Callable, Coroutine, Iterator, Mapping, Sequence
from functools import partial
from typing import Any, Concatenate, Protocol, runtime_checkable
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from pytest import param

from haiway import State, ValidationError, asynchronous, cache, retry, throttle, timeout
from haiway._native import nstruct
from haiway.attributes import native

# --- a protocol describing a function, checked against what holds it ------------------------


class Handler(Protocol):
    def __call__(self, request: str, /, retries: int, *, timeout: float = 1.0) -> str: ...


class Holding(State):
    handler: Handler


def _exact(request: str, /, retries: int, *, timeout: float = 1.0) -> str:
    return request


def _renamed_positional_only(other: str, /, retries: int, *, timeout: float = 1.0) -> str:
    return other


def _renamed(request: str, /, attempts: int, *, timeout: float = 1.0) -> str:
    return request


def _positional_only_retries(request: str, retries: int, /, *, timeout: float = 1.0) -> str:
    return request


def _timeout_required(request: str, /, retries: int, *, timeout: float) -> str:
    return request


def _timeout_missing(request: str, /, retries: int) -> str:
    return request


def _timeout_by_any_keyword(request: str, /, retries: int, **options: Any) -> str:
    return request


def _timeout_positional(request: str, /, retries: int, timeout: float = 1.0) -> str:
    return request


def _extra_defaulted(
    request: str, /, retries: int, *, timeout: float = 1.0, log: bool = False
) -> str:
    return request


def _extra_required(request: str, /, retries: int, *, timeout: float = 1.0, log: bool) -> str:
    return request


def _anything(*arguments: Any, **keywords: Any) -> str:
    return ""


def _positional_variadic(*arguments: Any, timeout: float = 1.0) -> str:
    return ""


def _variadic_named(*arguments: Any, retries: int, timeout: float = 1.0) -> str:
    return ""


class _Service:
    def handle(self, request: str, /, retries: int, *, timeout: float = 1.0) -> str:
        return request

    def renamed(self, request: str, /, attempts: int, *, timeout: float = 1.0) -> str:
        return request

    def __call__(self, request: str, /, retries: int, *, timeout: float = 1.0) -> str:
        return request


class _StaticService:
    @staticmethod
    def __call__(request: str, /, retries: int, *, timeout: float = 1.0) -> str:
        return request


class _Constructed:
    def __init__(self, request: str, /, retries: int, *, timeout: float = 1.0) -> None:
        pass


def _base(client: object, request: str, /, retries: int, *, timeout: float = 1.0) -> str:
    return request


def _base_named(request: str, /, retries: int, *, timeout: float = 1.0, client: object) -> str:
    return request


HANDLERS: list[tuple[str, Any, bool]] = [
    ("exact", _exact, True),
    ("positional only renamed", _renamed_positional_only, True),
    ("renamed", _renamed, False),
    ("positional only where given by name", _positional_only_retries, False),
    ("default missing", _timeout_required, False),
    ("keyword missing", _timeout_missing, False),
    ("keyword taken by any", _timeout_by_any_keyword, True),
    ("keyword taken positionally", _timeout_positional, True),
    ("extra defaulted", _extra_defaulted, True),
    ("extra required", _extra_required, False),
    ("anything", _anything, True),
    ("variadic without the name", _positional_variadic, False),
    # given by position the retries end among the variadic ones, leaving the name without one
    ("variadic with the name", _variadic_named, False),
    ("lambda", lambda request, /, retries, *, timeout=1.0: request, True),  # pyright: ignore[reportUnknownLambdaType]
    ("bound method", _Service().handle, True),
    ("bound method renamed", _Service().renamed, False),
    ("object", _Service(), True),
    ("static object", _StaticService(), True),
    ("class", _Constructed, True),
    ("partial", partial(_base, object()), True),
    ("partial by name", partial(_base_named, client=object()), True),
    ("partial given too much", partial(_exact, "request"), False),
]


@pytest.mark.parametrize(
    ("value", "accepted"),
    [param(value, accepted, id=name) for name, value, accepted in HANDLERS],
)
def test_protocol_functions_are_called_the_way_the_protocol_tells(
    value: Any,
    accepted: bool,
) -> None:
    for _ in range(2):  # told once, then answered from what was told for what is cached
        if accepted:
            assert Holding(handler=value).handler is value
            continue

        with pytest.raises(ValidationError) as exc:
            Holding(handler=value)

        assert str(exc.value).endswith(
            "it can't be called as Handler(request: str, /, retries: int, *, timeout: float = 1.0)"
        )


def test_protocol_functions_are_called_through_the_protocol() -> None:
    holding = Holding(handler=_exact)
    assert holding.handler("request", 1) == "request"
    assert holding.handler("request", retries=1, timeout=2.0) == "request"
    assert holding.updating(handler=_Service()).handler("x", 1) == "x"


class AsyncHandler(Protocol):
    async def __call__(self, value: int) -> int: ...


async def _async_value(value: int) -> int:
    return value


async def _async_other(other: int) -> int:
    return other


def test_async_protocol_functions_are_checked_the_same() -> None:
    class Holder(State):
        handler: AsyncHandler

    assert Holder(handler=_async_value).handler is _async_value
    with pytest.raises(ValidationError):
        Holder(handler=_async_other)  # pyright: ignore[reportArgumentType]


class Transform[T](Protocol):
    def __call__(self, value: T, /) -> T: ...


def test_generic_protocol_functions_are_checked_by_shape() -> None:
    class Holder(State):
        function: Transform[int]

    class Specialized[T](State):
        function: Transform[T]

    def identity(value: int) -> int:
        return value

    assert Holder(function=identity).function is identity
    assert Specialized[str](function=identity).function is identity  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValidationError):
        Holder(function=lambda: 0)  # pyright: ignore[reportArgumentType]


class Forwarded(Protocol):
    def __call__(self, value: DeclaredLater, /) -> DeclaredLater: ...


class DeclaredLater(State):
    value: int = 0


def test_protocol_referring_to_what_is_declared_later_is_read() -> None:
    class Holder(State):
        function: Forwarded

    def keep(value: DeclaredLater) -> DeclaredLater:
        return value

    assert Holder(function=keep).function is keep


class Inherited(Handler, Protocol):
    pass


def test_inherited_protocol_functions_are_checked_the_same() -> None:
    class Holder(State):
        handler: Inherited

    assert Holder(handler=_exact).handler is _exact
    with pytest.raises(ValidationError):
        Holder(handler=_renamed)  # pyright: ignore[reportArgumentType]


class Static(Protocol):
    @staticmethod
    def __call__(value: int, /) -> int: ...


def test_static_protocol_functions_are_checked_without_the_instance() -> None:
    class Holder(State):
        function: Static

    def single(value: int) -> int:
        return value

    assert Holder(function=single).function is single
    with pytest.raises(ValidationError):
        Holder(function=lambda: 0)  # pyright: ignore[reportArgumentType]


class Specified[**Arguments](Protocol):
    def __call__(
        self, first: int, /, *args: Arguments.args, **kwargs: Arguments.kwargs
    ) -> None: ...


def test_parameter_specification_leaves_what_follows_open() -> None:
    class Holder(State):
        function: Specified[...]

    def more(first: int, second: str, *, third: bytes) -> None: ...
    def none() -> None: ...

    assert Holder(function=more).function is more
    with pytest.raises(ValidationError):
        Holder(function=none)  # pyright: ignore[reportArgumentType]


@runtime_checkable
class Described(Protocol):
    name: str

    def __call__(self) -> None: ...


def test_protocols_with_more_than_a_call_are_not_functions() -> None:
    class Holder(State):
        value: Described

    class Value:
        name: str = "value"

        def __call__(self, extra: int) -> None: ...

    # told by its members, as a runtime checkable protocol always was - not by a signature
    assert isinstance(Holder(value=Value()).value, Value)  # pyright: ignore[reportArgumentType]


def test_union_of_protocols_picks_the_one_the_function_fits() -> None:
    class Single(Protocol):
        def __call__(self, value: int, /) -> int: ...

    class Pair(Protocol):
        def __call__(self, first: int, second: int, /) -> int: ...

    class Holder(State):
        function: Single | Pair

    def pair(first: int, second: int) -> int:
        return first + second

    assert Holder(function=pair).function is pair
    with pytest.raises(ValidationError):
        Holder(function=lambda: 0)  # pyright: ignore[reportArgumentType]


def test_callable_of_coroutines_is_checked_by_its_arguments() -> None:
    class Holder(State):
        function: Callable[[int], Coroutine[Any, Any, int]]

    assert Holder(function=_async_value).function is _async_value
    with pytest.raises(ValidationError):
        Holder(function=lambda: None)  # pyright: ignore[reportArgumentType]


def test_concatenated_callable_only_checks_what_it_tells() -> None:
    class Holder(State):
        function: Callable[Concatenate[int, ...], None]

    def first(value: int, *rest: Any, flag: bool) -> None: ...

    assert Holder(function=first).function is first


# --- every call a protocol allows, made to what holds it -------------------------------------

_NAMES: Sequence[str] = ("a", "b", "c", "d")
_KINDS: Sequence[Any] = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.POSITIONAL_OR_KEYWORD,
    inspect.Parameter.KEYWORD_ONLY,
)


def _random_parameters(generator: random.Random) -> list[inspect.Parameter]:
    # a valid signature of a few parameters, sharing their names with the others generated
    names: list[str] = generator.sample(_NAMES, generator.randint(0, 3))
    parameters: list[inspect.Parameter] = []
    for name in names:
        kind: Any = generator.choice(_KINDS)
        parameters.append(
            inspect.Parameter(
                name,
                kind,
                default=0 if generator.random() < 0.4 else inspect.Parameter.empty,
            )
        )

    parameters.sort(key=lambda parameter: parameter.kind)
    if generator.random() < 0.25:
        parameters.append(inspect.Parameter("variadic", inspect.Parameter.VAR_POSITIONAL))

    if generator.random() < 0.25:
        parameters.append(inspect.Parameter("keywords", inspect.Parameter.VAR_KEYWORD))

    parameters.sort(key=lambda parameter: parameter.kind)
    # a positional parameter without a default can't follow one which has a default
    defaulted: bool = False
    for index, parameter in enumerate(parameters):
        if parameter.kind not in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD):
            continue

        if parameter.default is not parameter.empty:
            defaulted = True

        elif defaulted:
            parameters[index] = parameter.replace(default=0)

    return parameters


def _source(parameters: Sequence[inspect.Parameter], /, *, first: str = "") -> str:
    return f"({first}{str(inspect.Signature(parameters))[1:-1]})"


def _function(parameters: Sequence[inspect.Parameter], /) -> Callable[..., None]:
    namespace: dict[str, Any] = {}
    exec(f"def function{_source(parameters)}: pass", namespace)  # nosec: B102
    return namespace["function"]


def _variants(
    generator: random.Random,
    parameters: list[inspect.Parameter],
) -> Iterator[tuple[str, Any]]:
    # the same parameters taken by each kind of callable, and a partial of extra ones
    namespace: dict[str, Any] = {}
    after_self: str = "self, " if parameters else "self"
    exec(  # nosec: B102
        f"class Method:\n    def method{_source(parameters, first=after_self)}: pass\n"
        f"class Called:\n    def __call__{_source(parameters, first=after_self)}: pass\n"
        f"class Static:\n    @staticmethod\n    def __call__{_source(parameters)}: pass\n"
        f"class Constructed:\n    def __init__{_source(parameters, first=after_self)}: pass\n",
        namespace,
    )
    yield ("function", _function(parameters))
    yield ("bound method", namespace["Method"]().method)
    yield ("object", namespace["Called"]())
    yield ("static object", namespace["Static"]())
    yield ("class", namespace["Constructed"])

    extra: list[inspect.Parameter] = [
        inspect.Parameter("given", inspect.Parameter.POSITIONAL_ONLY),
        *parameters,
    ]
    yield ("partial", partial(_function(extra), 0))
    named: list[str] = [
        parameter.name
        for parameter in parameters
        if parameter.kind in (parameter.POSITIONAL_OR_KEYWORD, parameter.KEYWORD_ONLY)
    ]
    if named:
        keywords: dict[str, Any] = dict.fromkeys(generator.sample(named, 1), 0)
        yield ("partial by name", partial(_function(parameters), **keywords))


def _calls(told: Sequence[inspect.Parameter]) -> Iterator[tuple[tuple[Any, ...], dict[str, Any]]]:
    # every way of calling a function of these parameters - by position or by name, each
    # default given or left out, with the variadic arguments some more
    positional: list[inspect.Parameter] = [
        parameter
        for parameter in told
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    keyword_only: list[inspect.Parameter] = [
        parameter for parameter in told if parameter.kind is parameter.KEYWORD_ONLY
    ]
    variadic: bool = any(parameter.kind is parameter.VAR_POSITIONAL for parameter in told)
    keywords: bool = any(parameter.kind is parameter.VAR_KEYWORD for parameter in told)
    for given in range(len(positional) + 1):
        rest: list[inspect.Parameter] = positional[given:]
        # what is not given by position is given by name, or left out for its default
        if any(
            parameter.kind is parameter.POSITIONAL_ONLY and parameter.default is parameter.empty
            for parameter in rest
        ):
            continue

        choices: list[list[bool]] = [
            [True] if parameter.default is parameter.empty else [True, False]
            for parameter in [*rest, *keyword_only]
            if parameter.kind is not parameter.POSITIONAL_ONLY
        ]
        named: list[inspect.Parameter] = [
            parameter
            for parameter in [*rest, *keyword_only]
            if parameter.kind is not parameter.POSITIONAL_ONLY
        ]
        for chosen in itertools.product(*choices):
            arguments: dict[str, Any] = {
                parameter.name: 0 for parameter, used in zip(named, chosen, strict=True) if used
            }
            # some more, and more than any generated function takes by position
            more: Sequence[int] = (0, 1, 6) if variadic and given == len(positional) else (0,)
            for extra in more:
                for unknown in (False, True) if keywords else (False,):
                    yield (
                        (0,) * (given + extra),
                        {**arguments, **({"unknown": 0} if unknown else {})},
                    )


def _takes_every_call(value: Any, told: Sequence[inspect.Parameter]) -> bool:
    for arguments, keywords in _calls(told):
        try:
            value(*arguments, **keywords)

        except TypeError:
            return False

    return True


def _protocol(told: Sequence[inspect.Parameter]) -> type[State]:
    namespace: dict[str, Any] = {"Protocol": Protocol, "State": State}
    after_self: str = "self, " if told else "self"
    exec(  # nosec: B102
        "class Told(Protocol):\n"
        f"    def __call__{_source(told, first=after_self)}: ...\n"
        "class Holder(State):\n"
        "    value: Told\n",
        namespace,
    )
    return namespace["Holder"]


def test_functions_are_accepted_exactly_when_they_take_every_call_of_the_protocol() -> None:
    generator = random.Random(31)
    checked: dict[bool, int] = {True: 0, False: 0}
    for _ in range(150):
        told: list[inspect.Parameter] = _random_parameters(generator)
        holder: type[State] = _protocol(told)
        for _ in range(12):
            taking: list[inspect.Parameter] = _random_parameters(generator)
            for kind, value in _variants(generator, taking):
                expected: bool = _takes_every_call(value, told)
                try:
                    holder(value=value)  # pyright: ignore[reportCallIssue]
                    accepted = True

                except ValidationError:
                    accepted = False

                checked[expected] += 1
                assert accepted == expected, (
                    kind,
                    inspect.Signature(told),
                    inspect.Signature(taking),
                    value,
                )

    # both answers were checked, plenty of times
    assert checked[True] > 500
    assert checked[False] > 500


def test_signatures_read_by_python_are_compared_the_same() -> None:
    # the fallback compares what inspect reads - for a plain function the same as its code
    generator = random.Random(37)
    for _ in range(2000):
        told: list[inspect.Parameter] = _random_parameters(generator)
        taking: list[inspect.Parameter] = _random_parameters(generator)
        function: Callable[..., None] = _function(taking)
        assert nstruct.signature_fits(
            native._parameters(tuple(inspect.signature(function).parameters.values())),  # pyright: ignore[reportPrivateUsage]
            native._parameters(told),  # pyright: ignore[reportPrivateUsage]
            False,
        ) == _takes_every_call(function, told), (inspect.Signature(told), inspect.Signature(taking))


@pytest.mark.parametrize(
    "described",
    [
        param("not a tuple", id="not a tuple"),
        param((("a", 9, False),), id="unknown kind"),
        param((("a", 1),), id="too short"),
        param(((1, 1, False),), id="name of a number"),
        param((("a", 1, 1),), id="default of a number"),
    ],
)
def test_malformed_parameters_are_refused(described: Any) -> None:
    with pytest.raises((TypeError, ValueError)):
        nstruct.signature_fits(described, (), False)

    with pytest.raises((TypeError, ValueError)):
        nstruct.signature_fits((), described, False)


def test_functions_of_many_parameters_are_read() -> None:
    names: list[str] = [f"a{index}" for index in range(80)]
    told: list[inspect.Parameter] = [
        inspect.Parameter(name, inspect.Parameter.POSITIONAL_OR_KEYWORD) for name in names
    ]
    holder: type[State] = _protocol(told)
    function: Callable[..., None] = _function(told)
    assert holder(value=function).value is function  # pyright: ignore[reportCallIssue, reportAttributeAccessIssue]
    # given by name, the last can't be given by position any more
    with pytest.raises(ValidationError):
        holder(value=partial(function, a79=0))  # pyright: ignore[reportCallIssue]

    taking_more: Callable[..., None] = _function(
        [*told, inspect.Parameter("more", inspect.Parameter.KEYWORD_ONLY)]
    )
    assert holder(value=partial(taking_more, more=0)) is not None  # pyright: ignore[reportCallIssue]
    renamed: Callable[..., None] = _function([*told[:-1], told[-1].replace(name="other")])
    with pytest.raises(ValidationError):
        holder(value=renamed)  # pyright: ignore[reportCallIssue]


def test_replaced_code_is_read_when_validating() -> None:
    def function(request: str, /, retries: int, *, timeout: float = 1.0) -> str:
        return request

    assert Holding(handler=function).handler is function
    function.__code__ = _renamed.__code__
    with pytest.raises(ValidationError):
        Holding(handler=function)


# --- what is told of callables by their kind, without telling them apart wrongly ------------


@functools.cache
def _cached_one(first: int, /) -> int:
    return first


@functools.cache
def _cached_two(first: int, second: int, /) -> int:
    return first + second


class _TakingTwo(State):
    function: Callable[[int, int], int]


@pytest.mark.parametrize("first", [_cached_one, _cached_two])
def test_wrappers_of_one_kind_are_told_by_what_they_wrap(first: Any) -> None:
    # whichever is told first, the other is told on its own - not by the answer of its kind
    for value in (first, _cached_two if first is _cached_one else _cached_one):
        if value is _cached_two:
            assert _TakingTwo(function=value).function is value

        else:
            with pytest.raises(ValidationError):
                _TakingTwo(function=value)


def test_static_methods_held_are_told_by_what_they_wrap() -> None:
    def two(first: int, second: int) -> int:
        return first + second

    def one(first: int) -> int:
        return first

    assert _TakingTwo(function=staticmethod(two)).function is not None  # pyright: ignore[reportArgumentType]
    with pytest.raises(ValidationError):
        _TakingTwo(function=staticmethod(one))  # pyright: ignore[reportArgumentType]


def test_partials_giving_names_twice_are_told_each_on_its_own() -> None:
    def three(first: int, second: int, *, third: int, fourth: int) -> int:
        return first

    def two(first: int, *, third: int, fourth: int) -> int:
        return first

    # a partial of a partial, both giving names - read by Python, never by the kind
    taking = partial(partial(three, third=1), fourth=1)
    refused = partial(partial(two, third=1), fourth=1)
    assert _TakingTwo(function=taking).function is taking
    with pytest.raises(ValidationError):
        _TakingTwo(function=refused)

    assert _TakingTwo(function=taking).function is taking


class _Signed:
    def __init__(self, signature: inspect.Signature) -> None:
        self.__signature__ = signature

    def __call__(self, *arguments: Any) -> int:
        return 0


def test_objects_with_signatures_of_their_own_are_told_each_on_its_own() -> None:
    two = inspect.Signature(
        [inspect.Parameter(name, inspect.Parameter.POSITIONAL_ONLY) for name in ("a", "b")]
    )
    one = inspect.Signature([inspect.Parameter("a", inspect.Parameter.POSITIONAL_ONLY)])
    assert _TakingTwo(function=_Signed(two)) is not None  # pyright: ignore[reportArgumentType]
    assert _TakingTwo(function=_Signed(one)) is not None  # pyright: ignore[reportArgumentType]


@pytest.mark.parametrize("mock", [Mock, MagicMock, AsyncMock], ids=["mock", "magic", "async"])
def test_mocks_are_taken_for_any_function(mock: type[Any]) -> None:
    value: Any = mock()
    assert Holding(handler=value).handler is value
    assert _TakingTwo(function=value).function is value


async def _fetch(key: str, /, *, fresh: bool = False) -> str:
    return key


def _fetch_blocking(key: str, /, *, fresh: bool = False) -> str:
    return key


class Fetching(Protocol):
    async def __call__(self, key: str, /, *, fresh: bool = False) -> str: ...


@pytest.mark.parametrize(
    "decorated",
    [
        param(lambda: cache(_fetch), id="cache"),
        param(lambda: cache(limit=2)(_fetch), id="cache with a limit"),
        param(lambda: retry(_fetch), id="retry"),
        param(lambda: retry(limit=2)(_fetch), id="retry with a limit"),
        param(lambda: asynchronous()(_fetch_blocking), id="asynchronous"),
        param(lambda: throttle(limit=2)(_fetch), id="throttle"),
        param(lambda: timeout(1)(_fetch), id="timeout"),
    ],
)
def test_decorated_functions_are_taken_as_what_they_decorate(
    decorated: Callable[[], Any],
) -> None:
    class Holder(State):
        fetching: Fetching

    class Plain(State):
        fetching: Callable[[str], Coroutine[Any, Any, str]]

    value: Any = decorated()
    assert Holder(fetching=value).fetching is value
    assert Plain(fetching=value).fetching is value


# --- every call of told parameters which more may follow, completed somehow -----------------


def _completes_every_call(
    value: Any,
    told: Sequence[inspect.Parameter],
    taking: Sequence[inspect.Parameter],
) -> bool:
    # each call of the told parameters has to be made to work by giving more - positional
    # arguments, or the names of what is left - as a parameter specification may give anything
    # a partial given its first argument takes the rest at the positions of the parameters
    keywords: Mapping[str, Any] = value.keywords if isinstance(value, partial) else {}
    positional: list[inspect.Parameter] = [
        parameter
        for parameter in taking
        if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
    ]
    for arguments, named in _calls(told):
        for extra in range(6):
            given: int = len(arguments) + extra
            completed: dict[str, Any] = {
                parameter.name: 0
                for index, parameter in enumerate(taking)
                if parameter.name not in keywords
                and parameter.name not in named
                and (
                    parameter.kind is parameter.KEYWORD_ONLY
                    or (
                        parameter.kind is parameter.POSITIONAL_OR_KEYWORD
                        and positional.index(parameter) >= given
                    )
                )
            }
            try:
                value(*arguments, *((0,) * extra), **named, **completed)

            except TypeError:
                continue

            break

        else:
            return False

    return True


def _open_protocol(told: Sequence[inspect.Parameter]) -> type[State]:
    namespace: dict[str, Any] = {"Protocol": Protocol, "State": State}
    signature: str = str(inspect.Signature(told))[1:-1]
    exec(  # nosec: B102
        "class Told[**P](Protocol):\n"
        f"    def __call__(self, {signature + ', ' if signature else ''}"
        "*args: P.args, **kwargs: P.kwargs): ...\n"
        "class Holder(State):\n"
        "    value: Told[...]\n",
        namespace,
    )
    return namespace["Holder"]


def test_functions_are_accepted_exactly_when_they_complete_every_call_of_an_open_protocol() -> None:
    generator = random.Random(41)
    checked: dict[bool, int] = {True: 0, False: 0}
    for _ in range(150):
        told: list[inspect.Parameter] = [
            parameter
            for parameter in _random_parameters(generator)
            if parameter.kind in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
        ]
        holder: type[State] = _open_protocol(told)
        for _ in range(12):
            taking: list[inspect.Parameter] = _random_parameters(generator)
            for kind, value in _variants(generator, taking):
                expected: bool = _completes_every_call(value, told, taking)
                try:
                    holder(value=value)  # pyright: ignore[reportCallIssue]
                    accepted = True

                except ValidationError:
                    accepted = False

                checked[expected] += 1
                assert accepted == expected, (
                    kind,
                    inspect.Signature(told),
                    inspect.Signature(taking),
                    value,
                )

    assert checked[True] > 500
    assert checked[False] > 100
