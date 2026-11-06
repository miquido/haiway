import copy
import enum
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Any
from uuid import UUID, uuid4

import pytest

from haiway import Alias, State
from haiway.attributes.validation import ValidationError
from haiway.types import Map


class Mode(enum.StrEnum):
    FAST = "fast"
    SLOW = "slow"


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


class Aliased(State):
    identifier: Annotated[int, Alias("id")]
    label: str = "none"


class Point(State):
    x: int
    y: int = 0


# the ways of calling a class, which the native code tells apart


def test_class_is_called_with_keywords_or_with_an_unpacked_mapping() -> None:
    values: dict[str, Any] = {"x": 1, "y": 2}

    assert Point(x=1, y=2) == Point(**values)
    assert Point(x=1).y == 0


def test_class_is_called_with_a_keyword_which_is_not_the_interned_name() -> None:
    values: dict[str, Any] = {"".join(("x", "")): 1, "".join(["y", ""]): 2}

    assert Point(**values) == Point(x=1, y=2)
    assert Point(**{str(chr(120)): 5}).x == 5


def test_alias_and_name_are_both_accepted_but_not_together() -> None:
    assert Aliased(id=1) == Aliased(identifier=1)  # pyright: ignore[reportCallIssue]

    with pytest.raises(TypeError, match="Duplicate attribute"):
        Aliased(id=1, identifier=2)  # pyright: ignore[reportCallIssue]


def test_unexpected_keywords_and_positionals_are_refused() -> None:
    with pytest.raises(TypeError, match="no attribute 'z'"):
        Point(x=1, z=2)  # pyright: ignore[reportCallIssue]

    with pytest.raises(TypeError):
        Point(1)  # pyright: ignore[reportCallIssue]

    with pytest.raises(ValidationError, match="Missing"):
        Point()  # pyright: ignore[reportCallIssue]


def test_more_keywords_than_the_fast_path_takes_are_still_accepted() -> None:
    namespace: dict[str, Any] = {"State": State}
    names = [f"field_{index}" for index in range(40)]
    exec(
        "class Wide(State):\n" + "".join(f"    {name}: int = 0\n" for name in names),
        namespace,
    )
    wide: Any = namespace["Wide"]

    instance = wide(**{name: index for index, name in enumerate(names)})

    assert instance.field_39 == 39
    assert wide.validate(dict.fromkeys(names, 1)).field_0 == 1


def test_class_with_many_attributes_is_built_and_updated() -> None:
    namespace: dict[str, Any] = {"State": State}
    names = [f"field_{index}" for index in range(70)]
    exec(
        "class Huge(State):\n" + "".join(f"    {name}: int = 0\n" for name in names),
        namespace,
    )
    huge: Any = namespace["Huge"]

    instance = huge(field_69=9)

    assert instance.field_69 == 9
    assert instance.updating(field_0=1, field_69=2).field_69 == 2
    assert instance.updating(field_0=1).field_0 == 1


# updating


def test_updating_by_keywords_by_alias_and_by_unpacking() -> None:
    point = Point(x=1, y=2)
    updates: dict[str, Any] = {"y": 5}

    assert point.updating(x=3) == Point(x=3, y=2)
    assert point.updating(**updates) == Point(x=1, y=5)
    assert point.updating() is point
    assert Aliased(id=1).updating(id=2) == Aliased(id=2)  # pyright: ignore[reportCallIssue]
    assert Aliased(id=1).updating(identifier=2) == Aliased(id=2)  # pyright: ignore[reportCallIssue]


def test_updating_refuses_what_it_can_not_apply() -> None:
    point = Point(x=1)

    with pytest.raises(TypeError, match="no attribute 'z'"):
        point.updating(z=1)  # pyright: ignore[reportCallIssue]

    with pytest.raises(TypeError, match="Duplicate attribute"):
        Aliased(id=1).updating(id=2, identifier=3)  # pyright: ignore[reportCallIssue]

    with pytest.raises(TypeError):
        point.updating(1)  # pyright: ignore[reportCallIssue]

    with pytest.raises(ValidationError):
        point.updating(x="x")  # pyright: ignore[reportArgumentType]


# validating a mapping


def test_validate_takes_mappings_of_any_kind() -> None:
    expected = Point(x=1, y=2)

    assert Point.validate({"x": 1, "y": 2}) == expected
    assert Point.validate(Map({"x": 1, "y": 2})) == expected
    assert Point.validate(MappingProxyType({"x": 1, "y": 2})) == expected
    assert Point.validate(expected) is expected


def test_validate_names_a_key_which_is_not_an_attribute_before_any_value() -> None:
    with pytest.raises(TypeError, match="Unexpected attribute 'z' for Point"):
        Point.validate({"x": "not an int", "z": 1})


def test_validate_refuses_what_is_not_a_mapping() -> None:
    with pytest.raises(TypeError, match="'int' is not matching expected type"):
        Point.validate(1)


# annotations the native code accepts as they are


class Rich(State):
    identifier: UUID
    created: datetime
    day: date
    clock: time
    span: timedelta
    location: Path
    payload: bytes
    mode: Mode
    level: Level
    scores: Mapping[str, int]
    pair: tuple[int, str]
    values: Sequence[int]


def _rich(**overrides: Any) -> Rich:
    values: dict[str, Any] = {
        "identifier": uuid4(),
        "created": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        "day": date(2026, 1, 2),
        "clock": time(3, 4),
        "span": timedelta(seconds=90),
        "location": Path("/tmp/file"),
        "payload": b"abc",
        "mode": Mode.FAST,
        "level": Level.HIGH,
        "scores": {"a": 1, "b": 2},
        "pair": (1, "x"),
        "values": (1, 2, 3),
    }
    values.update(overrides)
    return Rich(**values)


def test_instances_of_the_types_are_kept_as_they_are() -> None:
    identifier = uuid4()
    created = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    rich = _rich(identifier=identifier, created=created)

    assert rich.identifier is identifier
    assert rich.created is created
    assert rich.mode is Mode.FAST
    assert rich.level is Level.HIGH
    assert isinstance(rich.location, Path)


def test_other_spellings_of_the_types_are_converted() -> None:
    rich = _rich(
        identifier="00000000-0000-0000-0000-000000000001",
        created="2026-01-02T03:04:05+00:00",
        mode="slow",
        level=1,
    )

    assert rich.identifier == UUID(int=1)
    assert rich.created == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert rich.mode is Mode.SLOW
    assert rich.level is Level.LOW


def test_mapping_is_validated_into_an_immutable_map() -> None:
    source: dict[str, int] = {"a": 1}
    rich = _rich(scores=source)

    assert isinstance(rich.scores, Map)
    assert rich.scores == {"a": 1}

    source["b"] = 2  # the instance does not follow the mapping it was built from
    assert rich.scores == {"a": 1}


def test_mapping_failures_name_the_key() -> None:
    with pytest.raises(ValidationError, match=r"\[b\]"):
        _rich(scores={"a": 1, "b": "x"})


def test_fixed_tuple_takes_a_list_and_checks_its_length() -> None:
    assert _rich(pair=[1, "x"]).pair == (1, "x")

    with pytest.raises(ValidationError, match="length"):
        _rich(pair=(1,))

    with pytest.raises(ValidationError):
        _rich(pair=("x", 1))


def test_sequence_is_coerced_to_a_tuple() -> None:
    assert _rich(values=[1, 2]).values == (1, 2)
    assert isinstance(_rich(values=[1, 2]).values, tuple)


# the conversions, which spell what they know natively


def test_basic_object_spells_the_leaves() -> None:
    identifier = uuid4()
    rich = _rich(identifier=identifier)
    basic = rich.to_basic_object()

    assert basic["identifier"] == str(identifier)
    assert basic["created"] == "2026-01-02T03:04:05+00:00"
    assert basic["day"] == "2026-01-02"
    assert basic["clock"] == "03:04:00"
    assert basic["span"] == "PT1M30S" or isinstance(basic["span"], str)
    assert basic["location"] == "/tmp/file"
    assert basic["payload"] == "YWJj"
    assert basic["mode"] == "fast"
    assert basic["level"] == 2
    assert basic["scores"] == {"a": 1, "b": 2}
    assert basic["pair"] == (1, "x")


def test_mapping_keeps_the_values_as_they_are() -> None:
    identifier = uuid4()
    rich = _rich(identifier=identifier)
    mapping = rich.to_mapping()

    assert mapping["identifier"] == identifier
    assert mapping["created"] == rich.created
    assert mapping["location"] == Path("/tmp/file")
    assert mapping["payload"] == b"abc"
    assert mapping["scores"] == {"a": 1, "b": 2}
    assert mapping["values"] == [1, 2, 3]


def test_conversion_round_trips_through_json() -> None:
    rich = _rich()

    assert Rich.from_json(rich.to_json()) == rich


def test_deep_copy_is_equal_and_independent_of_the_original() -> None:
    rich = _rich()
    duplicate = copy.deepcopy(rich)

    assert duplicate == rich
    assert duplicate is not rich


# a class which constructs itself


class Renamed(State):
    _value: int

    def __init__(self, value: int) -> None:
        super().__init__(_value=value)


def test_validate_calls_a_class_which_constructs_itself() -> None:
    assert Renamed(3)._value == 3  # pyright: ignore[reportPrivateUsage]
    assert Renamed.validate(Renamed(4))._value == 4  # pyright: ignore[reportPrivateUsage]

    # the keys are given to its own constructor, which does not know them by these names
    with pytest.raises(TypeError):
        Renamed.validate({"_value": 3})
