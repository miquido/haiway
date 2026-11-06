import enum
from collections.abc import Set
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

import pytest

from haiway import Missing, State, Validator
from haiway.attributes.validation import ValidationError


class Mode(enum.StrEnum):
    FAST = "fast"
    SLOW = "slow"


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


class Raw(State):
    identifier: UUID
    created: datetime
    day: date
    clock: time
    location: Path
    mode: Mode
    level: Level


RAW: dict[str, Any] = {
    "identifier": "00000000-0000-0000-0000-000000000001",
    "created": "2026-01-02T03:04:05+00:00",
    "day": "2026-01-02",
    "clock": "03:04:05",
    "location": "/tmp/file",
    "mode": "slow",
    "level": 2,
}


def test_raw_spellings_are_converted_to_the_types() -> None:
    raw = Raw.validate(RAW)

    assert raw.identifier == UUID(int=1)
    assert raw.created == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert raw.day == date(2026, 1, 2)
    assert raw.clock == time(3, 4, 5)
    assert raw.location == Path("/tmp/file")
    assert raw.mode is Mode.SLOW
    assert raw.level is Level.HIGH


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("identifier", "not-a-uuid"),
        ("created", "yesterday"),
        ("day", "02.01.2026"),
        ("clock", "late"),
        ("mode", "medium"),
        ("level", 3),
    ],
)
def test_raw_spellings_which_do_not_convert_are_reported_by_the_annotation(
    key: str,
    value: Any,
) -> None:
    with pytest.raises(ValidationError, match=key):
        Raw.validate({**RAW, key: value})


def test_integer_enum_is_also_read_by_name() -> None:
    assert Raw.validate({**RAW, "level": "HIGH"}).level is Level.HIGH


def test_optional_raw_spelling_is_converted_and_none_is_kept() -> None:
    class Optional(State):
        created: datetime | None = None

    assert Optional.validate({"created": "2026-01-02T03:04:05"}).created == datetime(
        2026, 1, 2, 3, 4, 5
    )
    assert Optional.validate({"created": None}).created is None
    assert Optional().created is None


# literals


class Choice(State):
    mode: Literal["fast", "slow"]
    number: Literal[1, 2] = 1


def test_literal_accepts_its_values_only() -> None:
    assert Choice(mode="fast").mode == "fast"
    assert Choice(mode="slow", number=2).number == 2

    with pytest.raises(ValidationError):
        Choice(mode="medium")  # pyright: ignore[reportArgumentType]

    with pytest.raises(ValidationError):
        Choice(mode="fast", number=3)  # pyright: ignore[reportArgumentType]


# sets


class Tagged(State):
    tags: Set[str]
    levels: frozenset[int] = frozenset()


def test_set_is_coerced_to_a_frozenset() -> None:
    tagged = Tagged(tags=["a", "b", "a"], levels={1, 2})  # pyright: ignore[reportArgumentType]

    assert tagged.tags == frozenset({"a", "b"})
    assert isinstance(tagged.tags, frozenset)
    assert tagged.levels == frozenset({1, 2})

    assert Tagged(tags=frozenset({"x"})).tags == frozenset({"x"})


def test_set_elements_are_validated() -> None:
    with pytest.raises(ValidationError):
        Tagged(tags=["a", 1])  # pyright: ignore[reportArgumentType]


# unions of what is accepted as it is


class Mixed(State):
    value: int | str
    number: float | int = 0
    flag: bool | int = False
    stamp: datetime | str | None = None


def test_union_keeps_the_type_a_value_already_has() -> None:
    mixed = Mixed(value="7", number=3, flag=1, stamp="2026-01-02")

    assert mixed.value == "7"
    assert type(mixed.number) is int
    assert type(mixed.flag) is int
    assert mixed.stamp == "2026-01-02"  # a str is a str, it is not read as a datetime


def test_union_tells_a_bool_from_an_int() -> None:
    assert Mixed(value=1, flag=True).flag is True
    assert type(Mixed(value=1, flag=0).flag) is int


def test_union_takes_what_one_of_its_alternatives_accepts() -> None:
    assert Mixed(value=1, number=2.5).number == 2.5
    assert Mixed(value=1, stamp=datetime(2026, 1, 2)).stamp == datetime(2026, 1, 2)
    assert Mixed(value=1, stamp=None).stamp is None

    with pytest.raises(ValidationError):
        Mixed(value=object())  # pyright: ignore[reportArgumentType]


def test_union_with_missing() -> None:
    class Maybe(State):
        value: int | str | Missing

    assert Maybe(value=1).value == 1
    assert Maybe(value="x").value == "x"


def test_constant_default_does_not_run_user_callables_when_the_class_is_defined() -> None:
    calls: list[Any] = []

    def validate(value: Any) -> Any:
        calls.append(value)
        return value

    class Defaulted(State):
        value: Annotated[int, Validator(validate)] = 1

    assert calls == []
    assert Defaulted().value == 1
    assert calls == [1]


def test_optional_attribute_accepts_none_and_missing_by_identity() -> None:
    class Optional(State):
        a: int | None
        b: str | Missing

    value = Optional(a=None, b=Missing())  # pyright: ignore[reportArgumentType]
    assert value.a is None
    assert value.to_mapping() == {"a": None}


def test_optional_sequence_with_a_cached_none_default_still_coerces_input() -> None:
    class Shared(State):
        items: list[int] | None = None

    assert Shared().items is None
    assert Shared(items=[1, 2]).items == (1, 2)  # coerced to an immutable tuple, as in Python
