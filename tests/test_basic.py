import json
from collections.abc import Mapping, Sequence, Set
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum, IntEnum, StrEnum
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID

from pytest import raises

from haiway import MISSING, BasicObject, Map, Missing, State
from haiway.types import Sensitive
from haiway.types.basic import basic_object, basic_value


class Shade(StrEnum):
    RED = "red"


class Level(IntEnum):
    HIGH = 3


class Plain(Enum):
    A = "a"


class Leaf(State):
    label: str


class Everything(State):
    text: str
    number: int
    ratio: float
    flag: bool
    nothing: None
    identifier: UUID
    moment: datetime
    day: date
    clock: time
    location: Path
    payload: bytes
    shade: Shade
    level: Level


EVERYTHING = Everything(
    text="text",
    number=7,
    ratio=1.5,
    flag=True,
    nothing=None,
    identifier=UUID(int=1),
    moment=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    day=date(2026, 1, 2),
    clock=time(3, 4, 5),
    location=Path("/tmp/example"),
    payload=b"\x00\x01blob",
    shade=Shade.RED,
    level=Level.HIGH,
)


def test_each_supported_type_round_trips() -> None:
    assert Everything.from_mapping(EVERYTHING.to_basic_object()) == EVERYTHING


def test_supported_types_encode_to_their_decoded_spelling() -> None:
    basic: BasicObject = EVERYTHING.to_basic_object()

    assert basic["text"] == "text"
    assert basic["number"] == 7
    assert basic["ratio"] == 1.5
    assert basic["flag"] is True
    assert basic["nothing"] is None
    assert basic["identifier"] == "00000000-0000-0000-0000-000000000001"
    assert basic["moment"] == "2026-01-02T03:04:05+00:00"
    assert basic["day"] == "2026-01-02"
    assert basic["clock"] == "03:04:05"
    assert basic["location"] == "/tmp/example"
    # base64 - the only spelling json can carry for bytes
    assert basic["payload"] == "AAFibG9i"
    # enum members ride the str/int guards, so they are left as they are
    assert basic["shade"] == "red"
    assert basic["level"] == 3


def test_basic_object_normalizes_containers() -> None:
    class Container(State):
        mapping: Mapping[str, str]
        sequence: Sequence[str]
        tags: Set[str]

    basic: BasicObject = Container(
        mapping={"key": "value"},
        sequence=["a"],
        tags={"t"},
    ).to_basic_object()

    assert isinstance(basic, Map)
    assert isinstance(basic["mapping"], Map)
    assert isinstance(basic["sequence"], tuple)
    assert isinstance(basic["tags"], tuple)


class Keyed(State):
    by_uuid: Mapping[UUID, str]
    by_moment: Mapping[datetime, str]


def test_nested_non_str_keys_round_trip() -> None:
    instance = Keyed(
        by_uuid={UUID(int=2): "u"},
        by_moment={datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC): "m"},
    )

    basic: BasicObject = instance.to_basic_object()

    assert basic == {
        "by_uuid": {"00000000-0000-0000-0000-000000000002": "u"},
        # the ISO spelling, not the space separator of `str(datetime)`
        "by_moment": {"2026-01-02T03:04:05+00:00": "m"},
    }
    assert Keyed.from_mapping(basic) == instance


def test_integer_keys_round_trip() -> None:
    class Numbered(State):
        by_int: Mapping[int, str]
        by_float: Mapping[float, str]

    instance = Numbered(
        by_int={1: "i"},
        by_float={1.5: "f"},
    )

    basic: BasicObject = instance.to_basic_object()

    assert basic == {"by_int": {"1": "i"}, "by_float": {"1.5": "f"}}
    assert Numbered.from_mapping(basic) == instance


def test_key_spelling_follows_json() -> None:
    # `str()` would spell those "True"/"False"/"None"
    assert basic_object({True: "a"}) == {"true": "a"}
    assert basic_object({False: "a"}) == {"false": "a"}
    assert basic_object({None: "a"}) == {"null": "a"}
    assert basic_object({7: "a"}) == {"7": "a"}
    assert basic_object({1.5: "a"}) == {"1.5": "a"}
    assert basic_object({Shade.RED: "a"}) == {"red": "a"}
    assert basic_object({Path("/tmp/example"): "a"}) == {"/tmp/example": "a"}


def test_bytes_decode_from_base64() -> None:
    class Payload(State):
        data: bytes

    assert Payload.from_mapping({"data": "AAFibG9i"}).data == b"\x00\x01blob"

    with raises(Exception):  # noqa: B017 - reported as a validation failure
        Payload.from_mapping({"data": "not base64!"})


def test_unsupported_types_are_rejected() -> None:
    for unsupported in (Decimal("1.5"), Plain.A, MISSING, object()):
        with raises(TypeError, match="Can't convert"):
            basic_value(unsupported)


def test_unsupported_state_attribute_is_rejected() -> None:
    class WithDecimal(State):
        amount: Decimal

    with raises(TypeError, match="Can't convert 'Decimal' to a basic value"):
        WithDecimal(amount=Decimal("1.5")).to_basic_object()


def test_unsupported_key_is_reported_as_a_key() -> None:
    with raises(TypeError, match="Can't convert 'Decimal' to a basic object key"):
        basic_object({Decimal("1"): "a"})


def test_rejection_names_the_path_without_the_values() -> None:
    class Secretive(State):
        token: Annotated[str, Sensitive()]
        amounts: Mapping[str, Sequence[Decimal]]

    instance = Secretive(
        token="sk-live-value",
        amounts={"outer": (Decimal("1.5"),)},
    )

    with raises(TypeError) as exc:
        instance.to_basic_object()

    message: str = str(exc.value)
    assert message == "Can't convert 'Decimal' to a basic value at '[\"amounts\"][\"outer\"][0]'"
    assert "sk-live-value" not in message
    assert "1.5" not in message


def test_non_strict_leaves_unsupported_values_unchanged() -> None:
    amount = Decimal("1.5")

    assert basic_object({"amount": amount}, strict=False) == {"amount": amount}
    assert basic_object({amount: "a"}, strict=False) == {amount: "a"}
    # the supported ones are still converted around it
    assert basic_object(
        {"identifier": UUID(int=1), "amount": amount},
        strict=False,
    ) == {"identifier": "00000000-0000-0000-0000-000000000001", "amount": amount}


class Branch(State):
    leaves: Sequence[Leaf]


class Tree(State):
    branches: Sequence[Branch]
    grouped: Mapping[str, Sequence[Leaf]]


def test_nesting_is_converted_to_any_depth() -> None:
    instance = Tree(
        branches=(Branch(leaves=(Leaf(label="a"), Leaf(label="b"))),),
        grouped={"group": (Leaf(label="c"),)},
    )

    basic: BasicObject = instance.to_basic_object()

    assert basic == {
        "branches": ({"leaves": ({"label": "a"}, {"label": "b"})},),
        "grouped": {"group": ({"label": "c"},)},
    }
    assert Tree.from_mapping(basic) == instance


def test_dataclass_attribute_is_converted() -> None:
    @dataclass
    class Point:
        identifier: UUID
        label: str

    class Located(State):
        point: Point

    assert Located(point=Point(identifier=UUID(int=4), label="p")).to_basic_object() == {
        "point": {"identifier": "00000000-0000-0000-0000-000000000004", "label": "p"}
    }


def test_state_is_converted_by_state_rather_than_by_the_value_table() -> None:
    # `types` can't reach `State` from within `basic`, the structured branches
    # live in `State` itself - a `State` handed to the value table is rejected
    with raises(TypeError, match="Can't convert 'Leaf' to a basic value"):
        basic_value(Leaf(label="a"))

    # reached through `State`, the same value converts at any nesting depth
    class Holder(State):
        leaf: Leaf
        leaves: Mapping[str, Sequence[Leaf]]

    assert Holder(leaf=Leaf(label="a"), leaves={"g": (Leaf(label="b"),)}).to_basic_object() == {
        "leaf": {"label": "a"},
        "leaves": {"g": ({"label": "b"},)},
    }


def test_to_mapping_keeps_the_live_values() -> None:
    mapping: Mapping[str, Any] = EVERYTHING.to_mapping(recursive=True)

    assert mapping["identifier"] == UUID(int=1)
    assert mapping["moment"] == datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
    assert mapping["day"] == date(2026, 1, 2)
    assert mapping["location"] == Path("/tmp/example")
    assert mapping["payload"] == b"\x00\x01blob"


def test_to_json_encodes_non_str_keys() -> None:
    instance = Keyed(
        by_uuid={UUID(int=2): "u"},
        by_moment={datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC): "m"},
    )

    assert json.loads(instance.to_json()) == {
        "by_uuid": {"00000000-0000-0000-0000-000000000002": "u"},
        "by_moment": {"2026-01-02T03:04:05+00:00": "m"},
    }


def test_to_json_output_is_unchanged_for_str_keys() -> None:
    assert EVERYTHING.to_json() == (
        '{"text": "text", "number": 7, "ratio": 1.5, "flag": true, "nothing": null,'
        ' "identifier": "00000000-0000-0000-0000-000000000001",'
        ' "moment": "2026-01-02T03:04:05+00:00", "day": "2026-01-02", "clock": "03:04:05",'
        ' "location": "/tmp/example", "payload": "AAFibG9i", "shade": "red", "level": 3}'
    )


def test_to_json_still_reaches_a_custom_encoder() -> None:
    class WithDecimal(State):
        amount: Decimal

    class DecimalEncoder(json.JSONEncoder):
        def default(self, o: Any) -> Any:
            if isinstance(o, Decimal):
                return float(o)

            return super().default(o)

    # non-strict conversion leaves `Decimal` for the encoder to handle
    assert (
        WithDecimal(amount=Decimal("1.5")).to_json(encoder_class=DecimalEncoder)
        == '{"amount": 1.5}'
    )


def test_missing_reachable_within_a_mapping_is_rejected() -> None:
    class Holder(State):
        values: Mapping[str, Any]

    with raises(TypeError, match="Can't convert 'Missing' to a basic value"):
        Holder(values={"absent": MISSING}).to_basic_object()


def test_missing_attribute_is_omitted_rather_than_converted() -> None:
    class Optional(State):
        value: str | Missing = MISSING

    assert Optional().to_basic_object() == {}


def test_to_json_converts_a_leaf_through_its_to_mapping() -> None:
    class Custom:
        def to_mapping(self) -> Mapping[str, Any]:
            return {"identifier": UUID(int=5), "nested": {"day": date(2026, 1, 2)}}

    class Holder(State):
        value: Any

    instance = Holder(value=Custom())

    # the non-strict conversion honors the duck-typed `to_mapping` the same way
    # `to_mapping(recursive=True)` does, and converts the mapping it returns
    assert json.loads(instance.to_json()) == {
        "value": {
            "identifier": "00000000-0000-0000-0000-000000000005",
            "nested": {"day": "2026-01-02"},
        }
    }

    # strict conversion keeps rejecting it - the mapping is not a spelling the
    # validation of the annotation would read back
    with raises(TypeError, match="Can't convert 'Custom' to a basic value"):
        instance.to_basic_object()
