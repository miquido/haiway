import json
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from pytest import mark, param, raises

from haiway import (
    Alias,
    Description,
    Missing,
    Sensitive,
    Specification,
    State,
    ValidationError,
)
from haiway.types import Map


class Point(State):
    x: int
    y: int


class Plain(Enum):
    ONE = 1


class Mode(StrEnum):
    FAST = "fast"


class Level(IntEnum):
    LOW = 1


@dataclass
class Item:
    name: str
    count: int


class Rich(State):
    identifier: UUID
    created: datetime
    day: date
    moment: time
    location: Path
    data: bytes
    mode: Mode
    level: Level
    tags: tuple[str, ...]
    scores: dict[str, float]
    nested: Point


RICH = Rich(
    identifier=UUID(int=1),
    created=datetime(2026, 1, 2, 3, 4, 5),
    day=date(2026, 1, 2),
    moment=time(3, 4, 5),
    location=Path("/tmp/file"),
    data=b"hi",
    mode=Mode.FAST,
    level=Level.LOW,
    tags=("a", "b"),
    scores={"k": 1.5},
    nested=Point(x=1, y=2),
)


# --- json -------------------------------------------------------------------


def test_to_json_and_from_json_round_trip() -> None:
    assert Rich.from_json(RICH.to_json()) == RICH


def test_to_json_honors_indent() -> None:
    assert "\n  " in Point(x=1, y=2).to_json(indent=2)
    assert "\n" not in Point(x=1, y=2).to_json()


def test_from_json_accepts_bytes() -> None:
    assert Point.from_json(b'{"x": 1, "y": 2}') == Point(x=1, y=2)


def test_from_json_accepts_custom_decoder() -> None:
    class Decoder(json.JSONDecoder):
        def decode(self, s: str, *args: Any, **kwargs: Any) -> Any:
            return {"x": 7, "y": 8}

    assert Point.from_json("{}", decoder=Decoder) == Point(x=7, y=8)


@mark.parametrize(
    "payload",
    ["{", "[1]", '{"x": 1}', '{"x": "a", "y": 1}', '{"x": 1, "y": 2, "z": 3}', "3"],
    ids=["syntax", "array", "missing", "invalid-value", "unexpected", "scalar"],
)
def test_from_json_failures_are_value_errors(payload: str) -> None:
    with raises(ValueError, match="Failed to decode Point from json"):
        Point.from_json(payload)


def test_to_json_accepts_custom_encoder_for_unsupported_types() -> None:
    class Holder(State):
        value: Any

    class Encoder(json.JSONEncoder):
        def default(self, o: Any) -> Any:
            if isinstance(o, Decimal):
                return str(o)

            return super().default(o)

    assert json.loads(Holder(value=Decimal("1.5")).to_json(encoder_class=Encoder)) == {
        "value": "1.5"
    }

    with raises(ValueError, match="Failed to encode Holder to json"):
        Holder(value=Decimal("1.5")).to_json()


def test_to_json_failure_does_not_leak_values() -> None:
    class Holder(State):
        secret: Annotated[Any, Sensitive]

    with raises(ValueError) as caught:
        Holder(secret=Plain.ONE).to_json()

    assert "Plain.ONE" not in str(caught.value)
    assert "<redacted>" not in str(caught.value)


def test_from_json_array_decodes_each_element() -> None:
    assert Point.from_json_array('[{"x": 1, "y": 2}, {"x": 3, "y": 4}]') == (
        Point(x=1, y=2),
        Point(x=3, y=4),
    )
    assert Point.from_json_array(b"[]") == ()


@mark.parametrize(
    ("payload", "message"),
    [
        ("[", "Failed to decode Point from json"),
        ('{"x": 1, "y": 2}', "not an array"),
        ('[{"x": 1}]', "Failed to decode Point from json array"),
        ("[1]", "Failed to decode Point from json array"),
    ],
    ids=["syntax", "object", "invalid-element", "scalar-element"],
)
def test_from_json_array_failures_are_value_errors(payload: str, message: str) -> None:
    with raises(ValueError, match=message):
        Point.from_json_array(payload)


def test_from_json_array_accepts_custom_decoder() -> None:
    class Decoder(json.JSONDecoder):
        def decode(self, s: str, *args: Any, **kwargs: Any) -> Any:
            return [{"x": 1, "y": 2}]

    assert Point.from_json_array("[]", decoder=Decoder) == (Point(x=1, y=2),)


# --- mappings ---------------------------------------------------------------


def test_to_mapping_keeps_live_values_and_converts_nested_states() -> None:
    mapping = RICH.to_mapping()

    assert mapping["identifier"] == UUID(int=1)
    assert mapping["nested"] == {"x": 1, "y": 2}


def test_to_mapping_non_recursive_keeps_nested_instances() -> None:
    assert RICH.to_mapping(recursive=False)["nested"] is RICH.nested


def test_to_mapping_uses_aliases_as_keys() -> None:
    class Aliased(State):
        identifier: Annotated[str, Alias("id")]

    assert Aliased(identifier="a").to_mapping() == {"id": "a"}
    assert Aliased(identifier="a").to_basic_object() == {"id": "a"}


def test_to_mapping_converts_states_inside_collections() -> None:
    class Holder(State):
        points: list[Point]
        by_name: dict[str, Point]

    holder = Holder(points=[Point(x=1, y=2)], by_name={"a": Point(x=3, y=4)})

    assert holder.to_mapping() == {
        "points": [{"x": 1, "y": 2}],
        "by_name": {"a": {"x": 3, "y": 4}},
    }


def test_to_mapping_converts_dataclasses() -> None:
    class Holder(State):
        item: Item

    holder = Holder(item=Item(name="a", count=1))

    assert holder.to_mapping() == {"item": {"name": "a", "count": 1}}
    assert holder.to_mapping(recursive=False)["item"] == Item(name="a", count=1)
    assert holder.to_basic_object() == {"item": {"name": "a", "count": 1}}
    assert json.loads(holder.to_json()) == {"item": {"name": "a", "count": 1}}


def test_to_mapping_honors_duck_typed_to_mapping() -> None:
    class Custom:
        def to_mapping(self) -> dict[str, int]:
            return {"k": 1}

    class Holder(State):
        value: Any

    assert Holder(value=Custom()).to_mapping() == {"value": {"k": 1}}
    assert json.loads(Holder(value=Custom()).to_json()) == {"value": {"k": 1}}


def test_duck_typed_to_mapping_is_not_basic() -> None:
    class Custom:
        def to_mapping(self) -> dict[str, int]:
            return {"k": 1}

    class Holder(State):
        value: Any

    with raises(TypeError, match="Can't convert 'Custom'"):
        Holder(value=Custom()).to_basic_object()


def test_to_json_rejects_duck_typed_to_mapping_returning_non_mapping() -> None:
    class Custom:
        def to_mapping(self) -> int:
            return 5

    class Holder(State):
        value: Any

    with raises(ValueError, match="Failed to encode Holder"):
        Holder(value=Custom()).to_json()


def test_to_mapping_copies_unknown_objects() -> None:
    class Marker:
        pass

    class Holder(State):
        value: Any

    marker = Marker()
    copied = Holder(value=marker).to_mapping()["value"]

    assert isinstance(copied, Marker)
    assert copied is not marker


def test_to_mapping_copies_mutable_values() -> None:
    class Holder(State):
        value: Any

    source: Any = {"a": [1]}
    mapping = Holder(value=source).to_mapping()

    assert mapping["value"] == source
    assert mapping["value"] is not source


def test_from_mapping_is_validation() -> None:
    assert Point.from_mapping({"x": "1", "y": 2}) == Point(x=1, y=2)

    with raises(TypeError):
        Point.from_mapping({"x": 1, "y": 2, "z": 3})


# --- basic objects ----------------------------------------------------------


def test_basic_object_spells_leaves_as_basic_values() -> None:
    assert RICH.to_basic_object() == {
        "identifier": "00000000-0000-0000-0000-000000000001",
        "created": "2026-01-02T03:04:05",
        "day": "2026-01-02",
        "moment": "03:04:05",
        "location": "/tmp/file",
        "data": "aGk=",
        "mode": "fast",
        "level": 1,
        "tags": ("a", "b"),
        "scores": {"k": 1.5},
        "nested": {"x": 1, "y": 2},
    }


def test_basic_object_round_trips_through_from_mapping() -> None:
    assert Rich.from_mapping(RICH.to_basic_object()) == RICH


def test_basic_object_json_matches_to_json() -> None:
    assert json.loads(RICH.to_json()) == json.loads(json.dumps(RICH.to_basic_object()))


@mark.parametrize(
    "value",
    [Plain.ONE, Decimal("1.5"), object()],
    ids=["enum", "decimal", "object"],
)
def test_basic_object_refuses_unrepresentable_values_without_leaking(value: Any) -> None:
    class Holder(State):
        item: Annotated[Any, Sensitive]

    with raises(TypeError) as caught:
        Holder(item=value).to_basic_object()

    assert '["item"]' in str(caught.value)
    assert repr(value) not in str(caught.value)


def test_basic_object_names_nested_path_of_failure() -> None:
    class Holder(State):
        values: list[Any]

    with raises(TypeError, match=r'\["values"\]\[1\]'):
        Holder(values=[1, Plain.ONE]).to_basic_object()


def test_basic_object_spells_keys_the_way_json_does() -> None:
    class Holder(State):
        by_int: dict[int, str]
        by_float: dict[float, str]
        by_bool: dict[bool, str]
        by_uuid: dict[UUID, str]

    holder = Holder(
        by_int={1: "a"},
        by_float={1.5: "b"},
        by_bool={True: "c"},
        by_uuid={UUID(int=1): "d"},
    )
    basic = holder.to_basic_object()

    assert basic["by_int"] == {"1": "a"}
    assert basic["by_float"] == {"1.5": "b"}
    assert basic["by_bool"] == {"true": "c"}
    assert basic["by_uuid"] == {str(UUID(int=1)): "d"}
    assert Holder.from_mapping(basic) == holder


def test_basic_object_skips_missing_attributes() -> None:
    class Holder(State):
        value: int | Missing
        other: int = 1

    assert Holder().to_basic_object() == {"other": 1}


def test_basic_object_is_immutable() -> None:
    basic = Point(x=1, y=2).to_basic_object()

    with raises(Exception):  # noqa: B017
        basic["x"] = 2  # pyright: ignore[reportIndexIssue]


# --- redaction --------------------------------------------------------------


def test_sensitive_values_are_hidden_from_text_but_kept_in_data() -> None:
    class Credentials(State):
        user: str
        token: Annotated[str, Sensitive(redaction="<token>")]
        key: Annotated[str, Sensitive]

    credentials = Credentials(user="u", token="t0k", key="k3y")

    assert str(credentials) == "Credentials(user: u, token: <token>, key: <redacted>)"
    assert repr(credentials) == "Credentials(user: 'u', token: <token>, key: <redacted>)"
    assert credentials.to_mapping()["token"] == "t0k"
    assert json.loads(credentials.to_json())["key"] == "k3y"
    assert credentials.token == "t0k"


def test_sensitive_redaction_is_derived_from_nested_structure() -> None:
    class Holder(State):
        tokens: list[Annotated[str, Sensitive]]
        optional: Annotated[str, Sensitive] | None = None
        mapping: dict[str, Annotated[str, Sensitive]]

    holder = Holder(tokens=["a"], mapping={"k": "v"})

    assert str(holder) == ("Holder(tokens: <redacted>, optional: <redacted>, mapping: <redacted>)")


# --- json schema ------------------------------------------------------------


def test_json_schema_describes_attributes() -> None:
    schema = json.loads(Point.json_schema())

    assert schema == {
        "type": "object",
        "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
        "required": ["x", "y"],
        "additionalProperties": False,
    }


def test_json_schema_indent() -> None:
    assert "\n  " in Point.json_schema(indent=2)
    assert "\n" not in Point.json_schema()


def test_json_schema_uses_aliases_descriptions_and_defaults() -> None:
    class Described(State):
        identifier: Annotated[str, Alias("id"), "the identifier"]
        count: Annotated[int, Description("how many")] = 1

    schema = json.loads(Described.json_schema())

    assert schema["properties"] == {
        "id": {"type": "string", "description": "the identifier"},
        "count": {"type": "integer", "description": "how many"},
    }
    assert schema["required"] == ["id"]


def test_json_schema_can_be_overridden_for_a_type() -> None:
    class Opaque:
        pass

    class Holder(State):
        value: Annotated[Opaque, Specification({"type": "string"})]

    assert json.loads(Holder.json_schema())["properties"]["value"] == {"type": "string"}


def test_json_schema_formats_of_standard_types() -> None:
    class Formatted(State):
        identifier: UUID
        created: datetime
        day: date
        moment: time
        location: Path
        nested: Point

    properties = json.loads(Formatted.json_schema())["properties"]

    assert properties["identifier"]["format"] == "uuid"
    assert properties["created"]["format"] == "date-time"
    assert properties["day"]["format"] == "date"
    assert properties["moment"]["format"] == "time"
    assert properties["location"]["format"] == "path"
    assert properties["nested"]["properties"]["x"] == {"type": "integer"}


def test_json_schema_of_bytes_is_a_base64_string() -> None:
    class Holder(State, serializable=True):
        data: bytes

    assert json.loads(Holder.json_schema())["properties"]["data"] == {
        "type": "string",
        "contentEncoding": "base64",
    }
    assert Holder.from_json(Holder(data=b"hi").to_json()) == Holder(data=b"hi")
    assert json.loads(Holder(data=b"hi").to_json()) == {"data": "aGk="}


@mark.parametrize("annotation", [Set[int], frozenset[int], set[int]])
def test_json_schema_of_sets_is_an_array_of_unique_items(annotation: Any) -> None:
    class Holder(State, serializable=True):
        items: annotation  # pyright: ignore[reportInvalidTypeForm]

    assert json.loads(Holder.json_schema())["properties"]["items"] == {
        "type": "array",
        "items": {"type": "integer"},
        "uniqueItems": True,
    }

    holder = Holder(items={1, 2})  # pyright: ignore[reportCallIssue]
    assert Holder.from_json(holder.to_json()) == holder
    assert sorted(json.loads(holder.to_json())["items"]) == [1, 2]


def test_set_of_unserializable_items_has_no_schema() -> None:
    class Opaque:
        pass

    class Holder(State):
        items: Set[Opaque]

    assert Holder.json_schema(required=False) is None


def test_rich_state_with_bytes_is_serializable() -> None:
    assert Rich.__SERIALIZABLE__ is True
    assert json.loads(Rich.json_schema())["properties"]["data"]["contentEncoding"] == "base64"


DURATIONS: Sequence[Any] = (
    param(timedelta(0), "PT0S", id="zero"),
    param(timedelta(seconds=5), "PT5S", id="seconds"),
    param(timedelta(minutes=2), "PT2M", id="minutes"),
    param(timedelta(hours=3), "PT3H", id="hours"),
    param(timedelta(days=4), "P4D", id="days"),
    param(timedelta(days=1, hours=2, minutes=3, seconds=4), "P1DT2H3M4S", id="mixed"),
    param(timedelta(seconds=1.5), "PT1.5S", id="fraction"),
    param(timedelta(microseconds=1), "PT0.000001S", id="microsecond"),
    param(timedelta(milliseconds=250), "PT0.25S", id="trimmed-fraction"),
    param(timedelta(hours=100), "P4DT4H", id="beyond-a-day"),
    param(timedelta(days=-1), "-P1D", id="negative"),
    param(timedelta(seconds=-1.5), "-PT1.5S", id="negative-fraction"),
)


@mark.parametrize(("value", "spelled"), DURATIONS)
def test_timedelta_is_spelled_as_iso_duration_and_read_back(
    value: timedelta,
    spelled: str,
) -> None:
    class Holder(State, serializable=True):
        span: timedelta

    holder = Holder(span=value)

    assert holder.to_basic_object() == {"span": spelled}
    assert json.loads(holder.to_json()) == {"span": spelled}
    assert Holder(span=spelled).span == value  # pyright: ignore[reportArgumentType]
    assert Holder.from_json(holder.to_json()) == holder
    assert Holder.from_mapping(holder.to_basic_object()) == holder


@mark.parametrize(
    ("spelled", "expected"),
    [
        ("P1W", timedelta(weeks=1)),
        ("P1W2D", timedelta(days=9)),
        ("PT36H", timedelta(hours=36)),
        ("PT90M", timedelta(minutes=90)),
        ("PT0,5S", timedelta(milliseconds=500)),
        ("+PT5S", timedelta(seconds=5)),
        ("P0D", timedelta(0)),
        ("PT1.000001S", timedelta(seconds=1, microseconds=1)),
    ],
)
def test_timedelta_reads_other_valid_iso_spellings(spelled: str, expected: timedelta) -> None:
    class Holder(State):
        span: timedelta

    assert Holder(span=spelled).span == expected  # pyright: ignore[reportArgumentType]


@mark.parametrize(
    "spelled",
    [
        *("", "P", "PT", "P1Y", "P1M", "P1Y2M3D", "1D", "PT5", "PT1H5", "P1DT"),
        *("PT-5S", "PT1.S", "PT1.1234567S", "pt5s", " PT5S", "PT5S "),
    ],
)
def test_timedelta_refuses_invalid_or_ambiguous_durations(spelled: str) -> None:
    class Holder(State):
        span: timedelta

    with raises(ValidationError, match="ISO 8601 duration"):
        Holder(span=spelled)  # pyright: ignore[reportArgumentType]


@mark.parametrize("value", [5, 1.5, None, b"PT5S"])
def test_timedelta_refuses_other_types(value: Any) -> None:
    class Holder(State):
        span: timedelta

    with raises(ValidationError):
        Holder(span=value)  # pyright: ignore[reportArgumentType]


def test_json_schema_of_timedelta_is_a_duration_string() -> None:
    class Holder(State, serializable=True):
        span: timedelta

    assert json.loads(Holder.json_schema())["properties"]["span"] == {
        "type": "string",
        "format": "duration",
    }


def test_timedelta_is_spelled_inside_collections_and_mappings() -> None:
    class Holder(State):
        spans: tuple[timedelta, ...]
        by_name: dict[str, timedelta]

    holder = Holder(
        spans=(timedelta(seconds=1), timedelta(days=1)),
        by_name={"a": timedelta(minutes=1)},
    )

    assert holder.to_basic_object() == {
        "spans": ("PT1S", "P1D"),
        "by_name": {"a": "PT1M"},
    }
    assert Holder.from_mapping(holder.to_basic_object()) == holder


def test_json_schema_without_specification_is_reported() -> None:
    class Opaque:
        pass

    class Holder(State):
        value: Opaque
        optional: Opaque | None = None

    assert Holder.__SERIALIZABLE__ is False
    assert Holder.json_schema(required=False) is None

    with raises(TypeError, match="cannot be represented using json schema"):
        Holder.json_schema()

    with raises(TypeError, match="cannot be represented using json schema"):
        Holder.__SPECIFICATION__  # noqa: B018


def test_serializable_flag_requires_schema() -> None:
    class Opaque:
        pass

    with raises(TypeError, match="requires serialization"):

        class Holder(State, serializable=True):
            value: Opaque

    class Fine(State, serializable=True):
        value: int

    assert Fine.__SERIALIZABLE__ is True
    assert Fine(value=1).to_json() == '{"value": 1}'


def test_recursive_state_has_schema_with_reference() -> None:
    class Node(State):
        value: int
        children: tuple[Node, ...] = ()

    node = Node(value=1, children=(Node(value=2),))

    assert Node.__SERIALIZABLE__ is True
    assert Node.from_json(node.to_json()) == node


def test_serialization_walks_nested_states_sequences_and_maps() -> None:
    class Inner(State):
        number: int

    class Outer(State):
        inner: Inner
        many: Sequence[Inner]
        named: Mapping[str, Inner]

    outer = Outer(
        inner=Inner(number=1),
        many=[Inner(number=2)],
        named={"k": Inner(number=3)},
    )
    expected: dict[str, Any] = {
        "inner": {"number": 1},
        "many": ({"number": 2},),
        "named": {"k": {"number": 3}},
    }

    assert outer.to_basic_object() == expected
    assert outer.to_mapping() == {
        "inner": {"number": 1},
        "many": [{"number": 2}],
        "named": {"k": {"number": 3}},
    }
    assert isinstance(outer.to_basic_object(), Map)


def test_strict_conversion_names_the_path_of_the_failure() -> None:
    class Holder(State):
        values: Any

    with raises(TypeError, match=r"\[\"values\"\]\[1\]\[\"k\"\]"):
        Holder(values=(1, {"k": Decimal(1)})).to_basic_object()
