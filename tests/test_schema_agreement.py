import inspect
import json
import re
from collections.abc import Hashable, Mapping, Sequence, Set, Sized
from datetime import date, datetime, time, timedelta
from enum import Enum, IntEnum, StrEnum
from pathlib import Path
from types import GenericAlias, new_class
from typing import Annotated, Any, Literal, NotRequired
from uuid import UUID

import pytest

from haiway import Missing, State


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class Blank(StrEnum):
    EMPTY = ""


class Mode(Enum):
    EMPTY = ""
    FULL = "full"


type Maybe = int | Missing


class Box[Value](State):
    value: Value


NO_DEFAULT: Any = object()


def state_of(
    annotation: Any,
    /,
    default: Any = NO_DEFAULT,
    serializable: bool = False,
) -> Any:
    def body(namespace: dict[str, Any]) -> None:
        namespace["__module__"] = __name__
        namespace["__annotations__"] = {"v": annotation}
        if default is not NO_DEFAULT:
            namespace["v"] = default

    return new_class("Subject", (State,), {"serializable": serializable}, body)


def schema_of(cls: Any, /) -> Any:
    return json.loads(cls.json_schema())


def raw_property_of(annotation: Any, /) -> Any:
    # as the class holds it, before it is written as JSON - which would turn what
    # is no JSON value into one
    return state_of(annotation).__SPECIFICATION__["properties"]["v"]


def property_of(annotation: Any, /) -> Any:
    return schema_of(state_of(annotation))["properties"]["v"]


def accepts_json(cls: Any, document: Any, /) -> bool:
    try:
        cls.from_json(json.dumps(document))

    except ValueError, TypeError:
        return False

    return True


def mapping_of(key: Any, /) -> Any:
    return GenericAlias(Mapping, (key, str))


def omittable(cls: Any, /) -> bool:
    return accepts_json(cls, {})


# 1. Missing has no JSON form


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (str | Missing, {"type": "string"}),
        (Missing | None, {"type": "null"}),
        (int | None | Missing, {"type": ["integer", "null"]}),
        (Box[int] | Missing, None),
        (Sequence[int] | Missing, {"type": "array", "items": {"type": "integer"}}),
    ],
)
def test_missing_is_not_an_alternative_of_the_schema(
    annotation: Any,
    expected: Any,
) -> None:
    result: Any = property_of(annotation)

    if expected is None:  # a single non-trivial alternative stands for the union itself
        assert "anyOf" not in result
        assert result["type"] == "object"

    else:
        assert result == expected


def test_missing_alone_has_no_specification() -> None:
    cls: Any = state_of(Missing)

    assert cls.__SERIALIZABLE__ is False
    with pytest.raises(TypeError):
        state_of(Missing, serializable=True)


def test_null_is_refused_where_missing_stood_for_it() -> None:
    cls: Any = state_of(str | Missing)

    assert not accepts_json(cls, {"v": None})
    assert property_of(str | Missing) == {"type": "string"}


# 2. required follows from omission


@pytest.mark.parametrize(
    ("annotation", "default"),
    [
        (Any, NO_DEFAULT),
        (Maybe, NO_DEFAULT),
        (Hashable, NO_DEFAULT),
        (Sized, NO_DEFAULT),
        (object, NO_DEFAULT),
        (int | Missing, NO_DEFAULT),
        (Missing | None, NO_DEFAULT),
        (Sequence[int] | Missing, NO_DEFAULT),
        (int, NO_DEFAULT),
        (int | None, NO_DEFAULT),
        (str, NO_DEFAULT),
        (Box[int], NO_DEFAULT),
        (int, 1),
        (Any, None),
        (int | None, None),
    ],
)
def test_required_attributes_are_the_ones_which_cannot_be_omitted(
    annotation: Any,
    default: Any,
) -> None:
    cls: Any = state_of(annotation, default=default)

    omitted: bool = omittable(cls)
    if cls.__SERIALIZABLE__:
        assert ("v" in schema_of(cls)["required"]) is not omitted, schema_of(cls)

    assert (cls.__signature__.parameters["v"].default is inspect.Parameter.empty) is not omitted


def test_unspecialized_generic_attribute_is_optional() -> None:
    assert omittable(Box)
    assert "value" not in schema_of(Box)["required"]
    assert Box.__signature__.parameters["value"].default is not inspect.Parameter.empty
    assert not omittable(Box[int])
    assert schema_of(Box[int])["required"] == ["value"]


# 3. NotRequired on an attribute of a State


def test_not_required_attribute_without_default_is_refused() -> None:
    with pytest.raises(TypeError, match="NotRequired"):
        state_of(NotRequired[int])


def test_not_required_attribute_with_default_is_a_default() -> None:
    cls: Any = state_of(NotRequired[int], default=1)

    assert omittable(cls)
    assert schema_of(cls)["required"] == []


# 4. tuples


def test_fixed_tuple_has_its_length() -> None:
    result: Any = property_of(tuple[int, str])

    assert result["minItems"] == 2
    assert result["maxItems"] == 2
    assert result["items"] is False
    assert not accepts_json(state_of(tuple[int, str]), {"v": [1]})


@pytest.mark.parametrize(
    ("annotation", "minimum"),
    [
        (tuple[int, *tuple[str, ...]], 1),
        (tuple[int, *tuple[str, ...], bool], 2),
        (tuple[*tuple[str, ...], bool], 1),
    ],
)
def test_open_tuple_has_its_minimum_length(
    annotation: Any,
    minimum: int,
) -> None:
    result: Any = property_of(annotation)

    assert result["minItems"] == minimum
    assert "maxItems" not in result


def test_open_tuple_without_fixed_elements_has_no_minimum() -> None:
    result: Any = property_of(tuple[int, ...])

    assert "minItems" not in result


# 5. literals


@pytest.mark.parametrize(
    ("annotation", "expected"),
    [
        (Literal[Color.RED], {"type": "string", "enum": ["red"]}),
        (Literal[Level.HIGH], {"type": "integer", "enum": [2]}),
        (Literal[Color.RED, "other"], {"type": "string", "enum": ["red", "other"]}),
        (Literal[Color.RED, 1], {"enum": ["red", 1]}),
    ],
)
def test_literal_of_enum_members_names_their_values(
    annotation: Any,
    expected: Any,
) -> None:
    result: Any = raw_property_of(annotation)

    assert result == expected
    assert [type(element) for element in result["enum"]] == [
        type(element) for element in expected["enum"]
    ]
    assert all(type(element) in (str, int) for element in result["enum"])


@pytest.mark.parametrize(
    "annotation",
    [
        Literal[Mode.EMPTY],
        Literal[b"x"],
        Literal[Mode.FULL, "a"],
    ],
)
def test_literal_without_json_form_has_no_specification(annotation: Any) -> None:
    cls: Any = state_of(annotation)

    assert cls.__SERIALIZABLE__ is False
    with pytest.raises(TypeError):
        cls.json_schema()

    with pytest.raises(TypeError, match="json schema"):
        state_of(annotation, serializable=True)


# 6. sets


@pytest.mark.parametrize("annotation", [Set[int], frozenset[str], Set[Color], Set[bytes | Blank]])
def test_set_does_not_claim_unique_items(annotation: Any) -> None:
    result: Any = property_of(annotation)

    assert "uniqueItems" not in result
    assert result["type"] == "array"


def test_set_accepts_what_its_schema_describes() -> None:
    cls: Any = state_of(Set[int])

    assert accepts_json(cls, {"v": [1, 1]})


# 7. mapping keys


@pytest.mark.parametrize(
    ("key", "accepted", "refused"),
    [
        (int, ["1", "-1", "01", "+1", " 1", "1_000", chr(0x661)], ["x", "", "a1", "-", "--1"]),
        (bool, ["true", "false", "True", "TRUE", "False"], ["1", "yes", "", "x"]),
        (Color, ["red", "blue"], ["x", "green", ""]),
        (Literal["a", "b"], ["a", "b"], ["c", ""]),
    ],
)
def test_mapping_keys_are_constrained_to_their_spelling(
    key: Any,
    accepted: Sequence[str],
    refused: Sequence[str],
) -> None:
    names: Any = property_of(mapping_of(key))["propertyNames"]

    def matches(text: str) -> bool:
        if "enum" in names:
            return text in names["enum"]

        return re.search(names["pattern"], text) is not None

    assert all(matches(text) for text in accepted), names
    assert not any(matches(text) for text in refused), names


@pytest.mark.parametrize("key", [str, Any, float, None, bytes])
def test_mapping_keys_without_a_spelling_are_not_constrained(key: Any) -> None:
    assert "propertyNames" not in property_of(mapping_of(key))


def test_mapping_of_uuid_keys_names_the_format() -> None:
    assert property_of(mapping_of(UUID))["propertyNames"] == {
        "type": "string",
        "format": "uuid",
    }


# 8. formats


@pytest.mark.parametrize(
    ("annotation", "formatted"),
    [
        (datetime, False),
        (time, False),
        (timedelta, False),
        (Path, False),
        (date, True),
        (UUID, True),
    ],
)
def test_format_is_claimed_only_where_the_output_keeps_it(
    annotation: Any,
    formatted: bool,
) -> None:
    result: Any = property_of(annotation)

    assert result["type"] == "string"
    assert ("format" in result) is formatted


def test_naive_datetime_and_negative_duration_conform() -> None:
    class Moments(State):
        at: datetime
        clock: time
        span: timedelta

    document: Any = json.loads(
        Moments(
            at=datetime(2024, 1, 1),
            clock=time(1, 2),
            span=timedelta(seconds=-1.5),
        ).to_json()
    )

    assert document == {"at": "2024-01-01T00:00:00", "clock": "01:02:00", "span": "-PT1.5S"}
    assert all("format" not in value for value in schema_of(Moments)["properties"].values())


def test_annotated_description_is_kept() -> None:
    assert property_of(Annotated[datetime, "moment"]) == {
        "type": "string",
        "description": "moment",
    }
