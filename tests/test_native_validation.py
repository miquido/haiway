import inspect
import operator
import random
from collections.abc import Callable, Iterator, Mapping, Sequence, Set
from datetime import date, datetime, time, timedelta
from enum import IntEnum, StrEnum
from functools import partial
from pathlib import Path
from typing import Annotated, Any, Concatenate, Literal, NotRequired, TypedDict
from uuid import UUID

import pytest
from pytest import param

from haiway import Map, Meta, State, ValidationError, Validator, Verifier
from haiway._native import nstruct
from haiway.attributes.function import Function

# Every kind of annotation is validated natively - what each accepts and converts, and
# the error each raises, is what the annotations have always done in Python.


class Mode(StrEnum):
    FAST = "fast"
    SLOW = "slow"


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class Inner(State):
    x: int


class Settings(TypedDict):
    a: int
    b: NotRequired[str]


class Thing:
    pass


class Selfish(State):
    value: int

    @classmethod
    def validate(cls, value: Any) -> Selfish:
        if isinstance(value, int):
            return cls(value=value * 2)

        return super().validate(value)


def _halve(value: Any) -> Any:
    return value // 2 if isinstance(value, int) else value


def _positive(value: int) -> int:
    if value <= 0:
        raise ValueError("not positive")

    return value


type Tree = int | Sequence[Tree]


class Holder[Value](State):
    value: Value


def _holder(annotation: Any) -> type[Holder[Any]]:
    return Holder[annotation]


ACCEPTED: list[tuple[Any, Any, Any]] = [
    (int, "3", 3),
    (int, 3.0, 3),
    (float, 3, 3.0),
    (float, "1.5", 1.5),
    (bool, 1, True),
    (bool, 0, False),
    (bool, "TRUE", True),
    (bool, "false", False),
    (bytes, "aGk=", b"hi"),
    (UUID, "12345678-1234-5678-1234-567812345678", UUID("12345678-1234-5678-1234-567812345678")),
    (datetime, "2024-01-02T03:04:05", datetime(2024, 1, 2, 3, 4, 5)),
    (date, "2024-01-02", date(2024, 1, 2)),
    (time, "03:04:05", time(3, 4, 5)),
    (timedelta, "PT1H", timedelta(hours=1)),
    (Path, "a/b", Path("a/b")),
    (Mode, "fast", Mode.FAST),
    (Mode, "SLOW", Mode.SLOW),
    (Level, 1, Level.LOW),
    (Level, "HIGH", Level.HIGH),
    (Level, "1", Level.LOW),
    (Literal["a", "b"], "a", "a"),
    (Sequence[int], ["1", 2], (1, 2)),
    (Sequence[int], (1, 2), (1, 2)),
    (tuple[int, str], [1, "a"], (1, "a")),
    (Set[int], [1, 1, 2], frozenset({1, 2})),
    (Mapping[str, int], {"a": "1"}, Map({"a": 1})),
    (Mapping[str, int], Map({"a": 1}), Map({"a": 1})),
    (int | None, None, None),
    (int | None, "3", 3),
    (str | float | int, 3, 3),
    (Settings, {"a": "1"}, Map({"a": 1})),
    (Settings, {"a": 1, "b": "x"}, Map({"a": 1, "b": "x"})),
    (Inner, {"x": "1"}, Inner(x=1)),
    (Selfish, 2, Selfish(value=4)),
    (Meta, {"kind": "x"}, Meta.of(kind="x")),
    (Annotated[int, Validator(_halve)], 4, 2),
    (Annotated[int, Verifier(_positive)], 1, 1),
    (Tree, [1, [2, [3]]], (1, (2, (3,)))),
    (Holder[int], {"value": "1"}, Holder[int](value=1)),
]


@pytest.mark.parametrize(("annotation", "value", "expected"), ACCEPTED)
def test_accepted_values_are_converted_the_same(annotation: Any, value: Any, expected: Any) -> None:
    holder = _holder(annotation)
    validated = holder(value=value).value  # pyright: ignore[reportCallIssue, reportAttributeAccessIssue]
    assert validated == expected
    assert type(validated) is type(expected)


REFUSED: list[tuple[Any, Any, str]] = [
    (int, True, ".value failed: 'bool' is not matching expected type of 'int'"),
    (int, "x", ".value failed: 'str' value is not matching expected format of 'int'"),
    (int, 1.5, ".value failed: 'float' is not matching expected type of 'int'"),
    (float, "x", ".value failed: 'str' value is not matching expected format of 'float'"),
    (
        bool,
        "no",
        ".value failed: 'str' value is not matching any of expected values [True, False]",
    ),
    (bool, 2.5, ".value failed: 'float' is not matching expected type of 'bool'"),
    (str, 1, ".value failed: 'int' is not matching expected type of 'str'"),
    (
        bytes,
        "!!",
        ".value failed: 'str' value is not matching expected base64 format for 'bytes'",
    ),
    (UUID, "nope", ".value failed: 'str' value is not matching expected format of 'UUID'"),
    (
        datetime,
        "nope",
        ".value failed: 'str' value is not matching expected ISO format for 'datetime'",
    ),
    (date, "nope", ".value failed: 'str' value is not matching expected ISO format for 'date'"),
    (time, "nope", ".value failed: 'str' value is not matching expected ISO format for 'time'"),
    (
        timedelta,
        "nope",
        ".value failed: 'str' value is not matching expected ISO 8601 duration format"
        " for 'timedelta'",
    ),
    (Path, 1, ".value failed: 'int' is not matching expected type of 'Path'"),
    (
        Mode,
        "nope",
        ".value failed: 'str' is not matching any of expected Mode values [fast, slow]",
    ),
    (Mode, 1, f".value failed: 'int' is not matching expected type of '{Mode}'"),
    (
        Level,
        9,
        ".value failed: 'int' is not matching any of expected Level values [1, 2]",
    ),
    (
        Level,
        "9",
        ".value failed: 'str' is not matching any of expected Level members [LOW, HIGH]",
    ),
    (
        Literal["a", "b"],
        "c",
        ".value failed: 'str' is not matching any of expected literal values ['a', 'b']",
    ),
    (Sequence[int], "abc", ".value failed: 'str' is not matching expected type of 'Sequence'"),
    (
        Sequence[int],
        [1, "x"],
        ".value[1] failed: 'str' value is not matching expected format of 'int'",
    ),
    (
        tuple[int, str],
        [1],
        ".value failed: 'list' of length 1 does not match expected tuple length 2",
    ),
    (Set[int], 1, ".value failed: 'int' is not matching expected type of 'Set'"),
    (Mapping[str, int], 1, ".value failed: 'int' is not matching expected type of 'Mapping'"),
    (
        Mapping[str, int],
        {1: 1},
        ".value[1] failed: 'int' is not matching expected type of 'str'",
    ),
    (
        Mapping[str, int],
        {"a": "x"},
        ".value[a] failed: 'str' value is not matching expected format of 'int'",
    ),
    (Settings, {}, '.value["a"] failed: "Value for \'a\' is required"'),
    (
        Settings,
        {"a": "x"},
        ".value[\"a\"] failed: 'str' value is not matching expected format of 'int'",
    ),
    (Settings, 1, f".value failed: 'int' is not matching expected type of '{Settings}'"),
    (Inner, {"x": "y"}, ".value.x failed: 'str' value is not matching expected format of 'int'"),
    (Inner, 1, f".value failed: 'int' is not matching expected type of '{Inner}'"),
    (Meta, 1, ".value failed: 'int' is not matching expected type of 'Meta'"),
    (Callable[[], None], 1, ".value failed: 'int' is not matching expected function type"),
    (Thing, 1, f".value failed: 'int' is not matching expected type of '{Thing}'"),
    (Annotated[int, Verifier(_positive)], 0, ".value failed: not positive"),
    (
        Tree,
        [1, ["x"]],
        ".value failed: 'list' is not matching any of the allowed alternatives:"
        " int | Sequence (2 sub-exceptions)",
    ),
]


@pytest.mark.parametrize(("annotation", "value", "message"), REFUSED)
def test_refused_values_raise_the_same_error(annotation: Any, value: Any, message: str) -> None:
    holder = _holder(annotation)
    with pytest.raises(ValidationError) as exc:
        holder(value=value)  # pyright: ignore[reportCallIssue]

    assert str(exc.value) == f"Validation of {message}"


def test_union_failure_collects_every_alternative() -> None:
    holder = _holder(int | None)
    with pytest.raises(ValidationError) as exc:
        holder(value="x")  # pyright: ignore[reportCallIssue]

    cause = exc.value.cause
    assert isinstance(cause, ExceptionGroup)
    assert str(cause).startswith(
        "'str' is not matching any of the allowed alternatives: int | None"
    )
    assert len(cause.exceptions) == 2


def test_union_attempts_each_alternative_once() -> None:
    # the first pass validates what the value already matches - the second one leaves it
    # out, so the code of the user runs once, and fails into a single error
    calls: list[int] = []

    def rejecting(value: int) -> int:
        calls.append(value)
        raise ValueError("rejected")

    holder = _holder(Annotated[int, Verifier(rejecting)] | str)
    with pytest.raises(ValidationError) as exc:
        holder(value=1)  # pyright: ignore[reportCallIssue]

    assert calls == [1]
    cause = exc.value.cause
    assert isinstance(cause, ExceptionGroup)
    assert len(cause.exceptions) == 2


class _PairsAsLists(Mapping[str, int]):
    # a mapping of the user may spell its items as anything - it is read through its keys
    def __getitem__(self, key: str) -> int:
        return {"a": 1}[key]

    def __iter__(self) -> Iterator[str]:
        return iter(("a",))

    def __len__(self) -> int:
        return 1

    def items(self) -> Any:
        return [["a", 1]]


@pytest.mark.parametrize(
    "annotation",
    [Mapping[str, int], Mapping[str, int] | int],
    ids=["mapping", "union"],
)
def test_mapping_is_read_through_its_keys_rather_than_its_items(annotation: Any) -> None:
    holder = _holder(annotation)
    assert dict(holder(value=_PairsAsLists()).value) == {"a": 1}  # pyright: ignore[reportCallIssue, reportAttributeAccessIssue]


@pytest.mark.parametrize(
    ("kind", "error"),
    [(99, ValueError), (-1, ValueError), (1 << 70, OverflowError)],
    ids=["unknown", "negative", "overflowing"],
)
def test_specs_of_kinds_the_module_does_not_have_are_refused(
    kind: int,
    error: type[Exception],
) -> None:
    with pytest.raises(error):
        nstruct.compile_spec((kind, None, None, None, None, None, ()))


def test_validator_sees_the_raw_value_before_the_annotation() -> None:
    seen: list[Any] = []

    def recording(value: Any) -> Any:
        seen.append(value)
        return value

    holder = _holder(Annotated[int, Validator(recording)])
    assert holder(value="3").value == 3  # pyright: ignore[reportCallIssue, reportAttributeAccessIssue]
    assert seen == ["3"]


def test_mapping_validation_accepts_the_mapping_it_was_given() -> None:
    holder = _holder(Mapping[str, int])
    given = Map({"a": 1})
    assert holder(value=given).value is given  # pyright: ignore[reportCallIssue, reportAttributeAccessIssue]


def test_frozenset_validation_accepts_the_set_it_was_given() -> None:
    holder = _holder(Set[int])
    given = frozenset({1, 2})
    assert holder(value=given).value is given  # pyright: ignore[reportCallIssue, reportAttributeAccessIssue]


def test_function_arguments_validate_the_same() -> None:
    def add(value: int, other: Sequence[int] = ()) -> int:
        return value + sum(other)

    function = Function(add)
    assert function("3", ["1", 2]) == 6  # pyright: ignore[reportArgumentType]

    with pytest.raises(ValidationError) as exc:
        function("x")  # pyright: ignore[reportArgumentType]

    assert str(exc.value) == (
        "Validation of .value failed: 'str' value is not matching expected format of 'int'"
    )


@pytest.mark.parametrize(
    ("annotation", "given", "expected"),
    [
        (Set[int], frozenset({"1", "2"}), frozenset({1, 2})),
        (
            Set[UUID],
            frozenset({"12345678-1234-5678-1234-567812345678"}),
            frozenset({UUID("12345678-1234-5678-1234-567812345678")}),
        ),
        (Set[Mode], frozenset({"fast"}), frozenset({Mode.FAST})),
        (Set[int], {"3"}, frozenset({3})),
    ],
)
def test_set_elements_are_converted_whatever_set_holds_them(
    annotation: Any,
    given: Any,
    expected: Any,
) -> None:
    validated = _holder(annotation)(value=given).value  # pyright: ignore[reportCallIssue, reportAttributeAccessIssue]
    assert validated == expected
    assert all(type(element) is type(next(iter(expected))) for element in validated)


def _two(first: int, second: str) -> None: ...
def _three(first: int, second: str, third: bytes) -> None: ...
def _three_defaulted(first: int, second: str, third: bytes = b"") -> None: ...
def _one(first: int) -> None: ...
def _variadic(*arguments: Any) -> None: ...
def _keyword_required(first: int, second: str, *, third: bytes) -> None: ...
def _keyword_defaulted(first: int, second: str, *, third: bytes = b"") -> None: ...


class _Callee:
    def two(self, first: int, second: str) -> None: ...
    def one(self, first: int) -> None: ...
    def __call__(self, first: int, second: str) -> None: ...


class _Constructed:
    def __init__(self, first: int, second: str) -> None: ...


TAKING_TWO: list[tuple[str, Any, bool]] = [
    ("function", _two, True),
    ("lambda", lambda first, second: None, True),  # pyright: ignore[reportUnknownLambdaType]
    ("too many", _three, False),
    ("defaulted", _three_defaulted, True),
    ("too few", _one, False),
    ("variadic", _variadic, True),
    ("required keyword", _keyword_required, False),
    ("defaulted keyword", _keyword_defaulted, True),
    ("bound method", _Callee().two, True),
    ("bound method, too few", _Callee().one, False),
    ("object", _Callee(), True),
    ("class", _Constructed, True),
    ("partial", partial(_three, 1), True),
    ("partial, too few", partial(_two, 1), False),
    ("builtin without a signature", print, True),
]


@pytest.mark.parametrize(
    ("value", "accepted"),
    [param(value, accepted, id=name) for name, value, accepted in TAKING_TWO],
)
def test_function_values_have_to_take_their_arguments(value: Any, accepted: bool) -> None:
    holder = _holder(Callable[[int, str], None])
    if accepted:
        assert holder(value=value).value is value  # pyright: ignore[reportCallIssue]
        return

    with pytest.raises(ValidationError) as exc:
        holder(value=value)  # pyright: ignore[reportCallIssue]

    assert str(exc.value).endswith("it can't be called with 2 positional arguments")


@pytest.mark.parametrize(
    ("annotation", "value", "accepted"),
    [
        param(Callable[..., None], _one, True, id="anything goes"),
        param(Callable[Concatenate[int, ...], None], _three, True, id="more may follow"),
        param(Callable[Concatenate[int, ...], None], _one, True, id="first taken"),
        param(Callable[Concatenate[int, str, ...], None], _one, False, id="first only taken"),
        param(Callable[[], None], _one, False, id="nothing given"),
        param(Callable[[], None], lambda: None, True, id="nothing taken"),
    ],
)
def test_function_values_take_what_the_annotation_tells(
    annotation: Any,
    value: Any,
    accepted: bool,
) -> None:
    holder = _holder(annotation)
    if accepted:
        assert holder(value=value).value is value  # pyright: ignore[reportCallIssue]

    else:
        with pytest.raises(ValidationError):
            holder(value=value)  # pyright: ignore[reportCallIssue]


def test_union_tells_functions_apart_by_their_arguments() -> None:
    def defaulted(value: str = "") -> str:
        return value

    holder = _holder(Callable[[int, int], int] | Callable[[str], str])
    assert holder(value=_one).value is _one  # pyright: ignore[reportCallIssue]
    with pytest.raises(ValidationError):
        holder(value=_three)  # pyright: ignore[reportCallIssue]

    assert holder(value=defaulted).value is defaulted  # pyright: ignore[reportCallIssue]


def test_replaced_defaults_are_read_when_validating() -> None:
    def function(first: int, second: str, third: bytes) -> None: ...

    holder = _holder(Callable[[int, str], None])
    with pytest.raises(ValidationError):
        holder(value=function)  # pyright: ignore[reportCallIssue]

    function.__defaults__ = (b"",)
    assert holder(value=function).value is function  # pyright: ignore[reportCallIssue]


def _random_function(generator: random.Random) -> Callable[..., None]:
    # a function of a random signature - positional only, positional and keyword only
    # arguments, some defaulted, with or without the variadic ones
    names: Iterator[str] = (f"a{index}" for index in range(100))
    positional_only: list[str] = [next(names) for _ in range(generator.randint(0, 2))]
    positional: list[str] = [next(names) for _ in range(generator.randint(0, 3))]
    keyword_only: list[str] = [next(names) for _ in range(generator.randint(0, 2))]
    defaulted: int = generator.randint(0, len(positional_only) + len(positional))
    parts: list[str] = []
    for index, name in enumerate(positional_only + positional):
        default: str = "=0" if index >= len(positional_only) + len(positional) - defaulted else ""
        parts.append(name + default)
        if index == len(positional_only) - 1:
            parts.append("/")

    if generator.random() < 0.3:
        parts.append("*arguments")

    elif keyword_only:
        parts.append("*")

    parts.extend(name + ("=0" if generator.random() < 0.5 else "") for name in keyword_only)
    if generator.random() < 0.3:
        parts.append("**keywords")

    namespace: dict[str, Any] = {}
    exec(f"def function({', '.join(parts)}): pass", namespace)  # nosec: B102
    return namespace["function"]


def _random_partial(generator: random.Random, function: Callable[..., None]) -> Any:
    names: list[str] = [*inspect.signature(function).parameters, "unknown"]
    keywords: dict[str, Any] = {
        name: 0 for name in names if not name.startswith("*") and generator.random() < 0.25
    }
    arguments: tuple[int, ...] = (0,) * generator.randint(0, 2)
    return partial(function, *arguments, **keywords)


def _calls(value: Any, positional: int, extensible: bool) -> bool:
    # what the call itself tells, as the functions do nothing - inspect reads partials and
    # positional only arguments given by name its own way. Arguments which may follow are
    # tried as more positional ones, and as every argument left by name
    function: Any = value
    given: int = 0
    keywords: Mapping[str, Any] = {}
    if isinstance(value, partial):
        function, given, keywords = value.func, len(value.args), value.keywords

    parameters: list[inspect.Parameter] = list(inspect.signature(function).parameters.values())
    for extra in range(8) if extensible else (0,):
        named: dict[str, Any] = {
            parameter.name: 0
            for index, parameter in enumerate(parameters)
            if extensible
            and parameter.name not in keywords
            and (
                parameter.kind is parameter.KEYWORD_ONLY
                or (
                    parameter.kind is parameter.POSITIONAL_OR_KEYWORD
                    and index >= given + positional + extra
                )
            )
        }
        try:
            value(*((None,) * (positional + extra)), **named)

        except TypeError:
            continue

        return True

    return False


def test_functions_are_accepted_exactly_when_their_signature_binds() -> None:
    generator = random.Random(29)
    holders: Mapping[tuple[int, bool], type[Holder[Any]]] = {
        (0, False): _holder(Callable[[], None]),
        (1, False): _holder(Callable[[int], None]),
        (2, False): _holder(Callable[[int, int], None]),
        (3, False): _holder(Callable[[int, int, int], None]),
        (1, True): _holder(Callable[Concatenate[int, ...], None]),
        (2, True): _holder(Callable[Concatenate[int, int, ...], None]),
    }
    for _ in range(3000):
        function: Callable[..., None] = _random_function(generator)
        value: Any = function if generator.random() < 0.5 else _random_partial(generator, function)
        for (positional, extensible), holder in holders.items():
            expected: bool = _calls(value, positional, extensible)
            try:
                holder(value=value)  # pyright: ignore[reportCallIssue]
                accepted = True

            except ValidationError:
                accepted = False

            assert accepted == expected, (
                inspect.signature(function),
                value,
                positional,
                extensible,
            )


class _Unpacking[*Arguments](State):
    function: Callable[[int, *Arguments], None]


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        param(_one, True, id="none unpacked"),
        param(_two, True, id="one unpacked"),
        param(_three, True, id="two unpacked"),
        param(lambda: None, False, id="the one told missing"),
    ],
)
def test_unpacked_arguments_stand_for_any_number_of_them(value: Any, accepted: bool) -> None:
    if accepted:
        assert _Unpacking(function=value).function is value

    else:
        with pytest.raises(ValidationError):
            _Unpacking(function=value)


class _StaticCallee:
    @staticmethod
    def __call__(first: int, second: str) -> None: ...


class _StaticCalleeOfOne:
    @staticmethod
    def __call__(first: int) -> None: ...


class _InheritedCallee(_Callee):
    pass


@pytest.mark.parametrize(
    ("value", "accepted"),
    [
        param(_StaticCallee(), True, id="static"),
        param(_StaticCalleeOfOne(), False, id="static, too few"),
        param(_InheritedCallee(), True, id="inherited"),
        param({}.get, True, id="bound builtin"),
        param(str.replace, False, id="method descriptor"),
        param(operator.itemgetter(0), False, id="object of a builtin class, taking one"),
    ],
)
def test_objects_are_called_the_way_their_class_tells(value: Any, accepted: bool) -> None:
    holder = _holder(Callable[[int, str], None])
    for _ in range(2):  # told once, then answered from what was told
        if accepted:
            assert holder(value=value).value is value  # pyright: ignore[reportCallIssue]

        else:
            with pytest.raises(ValidationError):
                holder(value=value)  # pyright: ignore[reportCallIssue]
