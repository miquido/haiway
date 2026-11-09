from collections.abc import Callable, Mapping, Sequence, Set
from datetime import date, datetime, time, timedelta
from enum import IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal, NotRequired, TypedDict
from uuid import UUID

import pytest

from haiway import Map, Meta, State, ValidationError, Validator, Verifier
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
