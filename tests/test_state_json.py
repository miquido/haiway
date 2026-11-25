import enum
import json
import math
import random
from collections.abc import Mapping, Sequence, Set
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Annotated, Any, Literal, TypedDict
from uuid import UUID

import pytest

from haiway import Alias, Default, Meta, Missing, State, Validator
from haiway.attributes.validation import ValidationError
from haiway.types import Map


class _Encoder(json.JSONEncoder):
    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return str(o)

        return super().default(o)


class Mode(enum.StrEnum):
    FAST = "fast"
    SLOW = "slow"


class Level(enum.IntEnum):
    LOW = 1
    HIGH = 2


class Inner(State):
    number: int
    text: str = "none"


class Document(State):
    identifier: UUID
    created: datetime
    day: date
    clock: time
    span: timedelta
    location: Path
    payload: bytes
    mode: Mode
    level: Level
    ratio: float
    flag: bool
    name: str
    count: int
    inner: Inner
    optional: Inner | None
    many: Sequence[Inner]
    scores: Mapping[str, int]
    tags: tuple[str, ...]
    pair: tuple[int, str]
    choice: Literal["a", "b"]
    aliased: Annotated[int, Alias("al")] = 0
    missing: int | Missing = Missing


def _document(**overrides: Any) -> Document:
    values: dict[str, Any] = {
        "identifier": UUID(int=7),
        "created": datetime(2026, 1, 2, 3, 4, 5, 678901, tzinfo=UTC),
        "day": date(2026, 1, 2),
        "clock": time(3, 4, 5),
        "span": timedelta(days=1, seconds=90),
        "location": Path("/tmp/file"),
        "payload": b"\x00\x01binary\xff",
        "mode": Mode.SLOW,
        "level": Level.HIGH,
        "ratio": 0.1,
        "flag": True,
        "name": "plain",
        "count": 10**20,
        "inner": Inner(number=1),
        "optional": None,
        "many": [Inner(number=2, text="x"), Inner(number=3)],
        "scores": {"a": 1, "b": -2},
        "tags": ("t1", "t2"),
        "pair": (1, "x"),
        "choice": "b",
        "al": 5,
    }
    values.update(overrides)
    return Document(**values)


# encoding


def test_json_is_what_json_dumps_writes_of_the_basic_object() -> None:
    document = _document()

    assert document.to_json() == json.dumps(document.to_basic_object(), separators=(",", ":"))
    assert Document.from_json(document.to_json()) == document


@pytest.mark.parametrize(
    "text",
    [
        "",
        "plain",
        'quote " and backslash \\ and slash /',
        "tab\tnewline\ncarriage\rbackspace\bformfeed\f",
        "control \x00 \x01 \x1f and delete \x7f",
        "latin é ü ñ",
        "cjk 日本語",
        "emoji 😀 and music 𝄞",
        'mixed é\n😀\\"',
        chr(0x2028) + chr(0x2029),
        "\ud800 lone surrogate",
    ],
)
def test_strings_are_spelled_as_json_dumps_spells_them(text: str) -> None:
    document = _document(name=text, scores={text: 1})

    assert document.to_json() == json.dumps(document.to_basic_object(), separators=(",", ":"))
    assert json.loads(document.to_json())["name"] == text


@pytest.mark.parametrize(
    "ratio",
    [
        0.0,
        -0.0,
        0.1,
        1e-7,
        1e22,
        1.5e300,
        5e-324,
        123456789.123456789,
    ],
)
def test_floats_are_spelled_as_json_dumps_spells_them(ratio: float) -> None:
    document = _document(ratio=ratio)

    assert document.to_json() == json.dumps(document.to_basic_object(), separators=(",", ":"))


class _Floats(State):
    value: float = 0.0
    anything: Any = None
    items: Sequence[float] = ()
    unique: Set[float] = frozenset()
    scores: Mapping[str, float] = Map()
    nested: Sequence[Mapping[str, Sequence[Any]]] = ()


class _PlainEncoder(json.JSONEncoder):
    pass


@pytest.mark.parametrize("number", [float("nan"), float("inf"), float("-inf")], ids=str)
@pytest.mark.parametrize(
    "build",
    [
        lambda number: _Floats(value=number),
        lambda number: _Floats(anything=number),
        lambda number: _Floats(anything=[1, {"deep": (number,)}]),
        lambda number: _Floats(items=[1.0, number]),
        lambda number: _Floats(unique={number}),
        lambda number: _Floats(scores={"a": number}),
        lambda number: _Floats(nested=[{"a": [number]}]),
    ],
    ids=["field", "any", "any-nested", "sequence", "set", "mapping-value", "nested"],
)
def test_non_finite_floats_are_refused_when_written(
    number: float,
    build: Any,
) -> None:
    instance: _Floats = build(number)
    with pytest.raises(ValueError, match="Failed to encode _Floats to json"):
        instance.to_json()

    with pytest.raises(ValueError, match="Failed to encode _Floats to json"):
        instance.to_json(indent=2)

    with pytest.raises(ValueError, match="Failed to encode _Floats to json"):
        instance.to_json(encoder_class=_PlainEncoder)


@pytest.mark.parametrize("number", [float("inf"), float("-inf")], ids=str)
def test_non_finite_floats_are_kept_by_the_mappings(number: float) -> None:
    instance = _Floats(value=number)

    assert instance.to_mapping()["value"] == number
    assert instance.to_basic_object()["value"] == number
    assert _Floats.from_mapping({"value": number}).value == number
    assert math.isnan(_Floats.from_mapping({"value": float("nan")}).value)


@pytest.mark.parametrize(
    ("key", "spelling"),
    [
        (float("nan"), "nan"),
        (float("inf"), "inf"),
        (float("-inf"), "-inf"),
        (1.5, "1.5"),
    ],
)
def test_float_keys_keep_their_spelling_in_json(key: float, spelling: str) -> None:
    class Keyed(State):
        values: Mapping[float, int]

    instance = Keyed(values={key: 1})
    for spelled in (
        instance.to_json(),
        instance.to_json(indent=2),
        instance.to_json(encoder_class=_PlainEncoder),
    ):
        assert json.loads(spelled)["values"] == {spelling: 1}
        (read,) = Keyed.from_json(spelled).values
        assert (math.isnan(read) and math.isnan(key)) or read == key


def test_empty_containers() -> None:
    document = _document(many=[], scores={}, tags=())

    assert document.to_json() == json.dumps(document.to_basic_object(), separators=(",", ":"))
    assert Document.from_json(document.to_json()) == document


def test_value_without_a_spelling_is_refused_unless_an_encoder_takes_it() -> None:
    class Holder(State):
        value: Any

    holder = Holder(value=Decimal("1.5"))

    with pytest.raises(ValueError, match="Failed to encode Holder to json"):
        holder.to_json()

    class DecimalEncoder(json.JSONEncoder):
        def default(self, o: Any) -> Any:
            return str(o) if isinstance(o, Decimal) else super().default(o)

    assert holder.to_json(encoder_class=DecimalEncoder) == '{"value":"1.5"}'


def test_indented_json_is_what_json_dumps_writes() -> None:
    document = _document()

    assert document.to_json(indent=2) == json.dumps(document.to_basic_object(), indent=2)


def test_values_of_other_kinds_are_spelled_through_the_basic_conversion() -> None:
    class Holder(State):
        values: Any

    holder = Holder(values=(b"abc", {1, 2}, timedelta(seconds=5), Path("/x"), None, (True, 1.5)))
    written = json.loads(holder.to_json())["values"]

    assert written[0] == "YWJj"
    assert sorted(written[1]) == [1, 2]
    assert written[3] == "/x"
    assert written[4:] == [None, [True, 1.5]]


def test_non_string_keys_are_spelled_the_way_json_spells_them() -> None:
    class Holder(State):
        values: Any

    holder = Holder(values={1: "a", None: "c", 2.5: "d", False: "e"})

    spelled = json.loads(holder.to_json())["values"]
    assert holder.to_json() == json.dumps({"values": spelled}, separators=(",", ":"))
    assert spelled == {"1": "a", "null": "c", "2.5": "d", "false": "e"}


# decoding


def test_decoded_document_equals_the_validated_one() -> None:
    document = _document()
    text = document.to_json()

    assert Document.from_json(text) == Document.validate(json.loads(text))
    assert Document.from_json(text.encode()) == document


def test_whitespace_and_key_order_do_not_matter() -> None:
    document = _document()
    payload = json.loads(document.to_json())
    reordered = dict(reversed(list(payload.items())))
    text = json.dumps(reordered, indent=4, separators=(" , ", "  :  "))

    assert Document.from_json(text) == document
    assert Document.from_json("\n\t " + text + " \r\n") == document


def test_attributes_may_be_given_by_their_alias_or_their_name() -> None:
    document = _document()
    payload = json.loads(document.to_json())

    assert payload["al"] == 5
    without_alias = {key: value for key, value in payload.items() if key != "al"}

    assert Document.from_json(json.dumps({**without_alias, "aliased": 6})).aliased == 6


@pytest.mark.parametrize(
    "literal",
    [
        '"escaped \\u00e9 \\u65e5 \\ud83d\\ude00 \\n \\" \\\\ \\/ \\b \\f \\r \\t"',
        '"\\ud800 lone"',
        '"\\udc00 lone low"',
        '"plain é 日本 😀"',
        '""',
    ],
)
def test_strings_are_read_as_json_loads_reads_them(literal: str) -> None:
    class Holder(State):
        value: str

    text = f'{{"value": {literal}}}'

    assert Holder.from_json(text).value == json.loads(literal)


@pytest.mark.parametrize(
    "number",
    [
        "0",
        "-0",
        "7",
        "-7",
        "123456789012345678901234567890",
        "1.5",
        "-1.5e10",
        "2E-3",
        "1e308",
        "0.0",
    ],
)
def test_numbers_are_read_as_json_loads_reads_them(number: str) -> None:
    class Holder(State):
        value: Any

    decoded = Holder.from_json(f'{{"value": {number}}}').value
    expected = json.loads(number)

    assert decoded == expected
    assert type(decoded) is type(expected)


@pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity", "1e400", "-1e400"])
def test_documents_with_non_finite_numbers_are_refused(literal: str) -> None:
    class Holder(State):
        value: float = 0.0
        anything: Any = None
        nested: Sequence[Mapping[str, Any]] = ()

    class Plain(State):
        value: int = 0

    class Reading(json.JSONDecoder):
        pass

    documents: Sequence[str] = (
        f'{{"value": {literal}}}',
        f'{{"anything": {literal}}}',
        f'{{"anything": [1, {{"deep": {literal}}}]}}',
        f'{{"nested": [{{"key": {literal}}}]}}',
    )
    for document in documents:
        with pytest.raises(ValueError, match="Failed to decode Holder from json"):
            Holder.from_json(document)

        with pytest.raises(ValueError, match="Failed to decode Holder from json"):
            Holder.from_json(document, Reading)

        with pytest.raises(ValueError, match="Failed to decode Holder from json"):
            Holder.from_json_array(f"[{document}]")

        with pytest.raises(ValueError, match="Failed to decode Holder from json"):
            Holder.from_json_array(f"[{document}]", Reading)

    skipped: str = f'{{"value": 1, "extra": {literal}}}'
    for ignore_extra in (True, False):
        with pytest.raises(ValueError, match="Failed to decode Plain from json"):
            Plain.from_json(skipped, ignore_extra=ignore_extra)

        with pytest.raises(ValueError, match="Failed to decode Plain from json"):
            Plain.from_json(skipped, Reading, ignore_extra=ignore_extra)

        with pytest.raises(ValueError, match="Failed to decode Plain from json"):
            Plain.from_json_array(f"[{skipped}]", ignore_extra=ignore_extra)


def test_integer_is_read_as_a_float_for_a_float_attribute() -> None:
    class Holder(State):
        value: float

    decoded = Holder.from_json('{"value": 3}').value

    assert decoded == 3.0
    assert type(decoded) is float


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        "{",
        "}",
        "[]",
        "null",
        "1",
        '"text"',
        '{"number": 1,}',
        '{"number": 1 "text": "x"}',
        '{"number": 01}',
        '{"number": 1.}',
        '{"number": .5}',
        '{"number": +1}',
        '{"number": 0x10}',
        '{"number": tru}',
        '{"number": nul}',
        '{"number": "unterminated}',
        '{"number": 1} trailing',
        '{"number": 1}{"number": 2}',
        "{'number': 1}",
        '{"number": "\\x"}',
        '{"number": "\\u12"}',
        '{"number": "tab\there"}',
        '{"number" 1}',
        "{1: 2}",
    ],
)
def test_malformed_documents_fail_as_they_always_did(text: str) -> None:
    with pytest.raises(ValueError, match="Failed to decode Inner from json"):
        Inner.from_json(text)


def test_documents_which_are_not_valid_for_the_class_fail_as_they_always_did() -> None:
    with pytest.raises(ValueError, match="Failed to decode Inner from json"):
        Inner.from_json('{"number": "x"}')

    with pytest.raises(ValueError, match="Unexpected attribute 'extra' for Inner"):
        Inner.from_json('{"number": 1, "extra": 2}')

    with pytest.raises(ValueError, match="Failed to decode Inner from json"):
        Inner.from_json('{"text": "x"}')


def test_repeated_key_keeps_the_last_value() -> None:
    assert Inner.from_json('{"number": 1, "number": 2}').number == 2


def test_numbers_which_need_a_conversion_are_still_converted() -> None:
    assert Inner.from_json('{"number": 7.0}').number == 7

    with pytest.raises(ValueError, match="expected type of 'int'"):
        Inner.from_json('{"number": "7"}')


def test_bytes_in_the_encodings_json_loads_detects() -> None:
    inner = Inner(number=1, text="é日")

    assert Inner.from_json(inner.to_json().encode("utf-8")) == inner
    assert Inner.from_json(inner.to_json().encode("utf-16")) == inner
    assert Inner.from_json(inner.to_json().encode("utf-32")) == inner

    # a byte order mark is the one of the encoding it spells
    assert Inner.from_json(b"\xef\xbb\xbf" + inner.to_json().encode("utf-8")) == inner

    with pytest.raises(ValueError, match="Failed to decode"):
        Inner.from_json(b'{"number": 1, "text": "\xff"}')


def test_nested_null_and_optional_attributes() -> None:
    document = _document(optional=Inner(number=9))

    assert Document.from_json(document.to_json()).optional == Inner(number=9)
    assert Document.from_json(_document().to_json()).optional is None


def test_deeply_nested_documents_are_read_or_refused_the_way_json_loads_does() -> None:
    class Node(State):
        child: Any = None

    text = '{"child": ' * 100_000 + "null" + "}" * 100_000
    refused: bool
    try:
        json.loads(text)
        refused = False

    except RecursionError:
        refused = True

    if refused:
        with pytest.raises(ValueError, match="Failed to decode"):
            Node.from_json(text)

    else:
        assert Node.from_json(text).child is not None


# arrays


def test_array_of_documents() -> None:
    inners = [Inner(number=index) for index in range(5)]
    text = json.dumps([json.loads(inner.to_json()) for inner in inners])

    assert Inner.from_json_array(text) == tuple(inners)
    assert Inner.from_json_array("[]") == ()
    assert Inner.from_json_array(text.encode()) == tuple(inners)


def test_array_failures_are_reported_as_they_always_were() -> None:
    with pytest.raises(ValueError, match="Provided json is not an array"):
        Inner.from_json_array('{"number": 1}')

    with pytest.raises(ValueError, match="Failed to decode Inner from json array"):
        Inner.from_json_array('[{"number": 1}, {"number": "x"}]')

    with pytest.raises(ValueError, match="Failed to decode Inner from json"):
        Inner.from_json_array("[{")


# what Python code is called with


_calls: list[Any] = []


class Counted(State):
    value: Annotated[int, Validator(lambda value: _calls.append(value) or value)]
    other: int = 0


def test_validators_of_python_code_see_a_valid_document_once() -> None:
    _calls.clear()

    assert Counted.from_json('{"value": 3, "other": 1}').value == 3
    assert _calls == [3]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"value": 3, "extra": 1}', 0),  # keys are checked before any value is validated
        ('{"value": 3', 0),
        ('{"value": 3, "other": "x"}', 1),  # validated in the order of the attributes, as ever
        ('{"value": 3} x', 0),
    ],
)
def test_validators_are_not_called_by_a_document_which_is_refused_before_them(
    text: str,
    expected: int,
) -> None:
    _calls.clear()

    with pytest.raises(ValueError, match="Failed to decode"):
        Counted.from_json(text)

    assert len(_calls) == expected


_Recorded = Annotated[int, Validator(lambda value: _calls.append(value) or value)]


class Leaf(State):
    x: _Recorded


class Entry(TypedDict):
    leaf: Leaf
    count: int


class ByTuple(State):
    nested: tuple[Leaf, int]
    y: int


class ByOpenTuple(State):
    nested: tuple[int, *tuple[Leaf, ...]]
    y: int


class BySet(State):
    nested: Set[Leaf]
    y: int


class ByTypedDict(State):
    nested: Entry
    y: int


class ByUnion(State):
    nested: Leaf | int
    y: int


class BySequence(State):
    nested: Sequence[Leaf]
    y: int


class ByMapping(State):
    nested: Mapping[str, Leaf]
    y: int


class Custom(State):
    a: int

    def __init__(self, **kwargs: Any) -> None:
        _calls.append(("init", kwargs))
        super().__init__(**kwargs)


class ByCustom(State):
    nested: Custom
    y: int


class ByCustomSequence(State):
    nested: Sequence[Custom]
    y: int


# the class, what it nests in a document, and how many times code of the user runs for it
_NESTED = [
    pytest.param(ByTuple, '[{"x": 1}, 2]', 1, id="tuple"),
    pytest.param(ByOpenTuple, '[2, {"x": 1}, {"x": 3}]', 2, id="open tuple"),
    pytest.param(BySet, '[{"x": 1}]', 1, id="set"),
    pytest.param(ByTypedDict, '{"leaf": {"x": 1}, "count": 2}', 1, id="typed dict"),
    pytest.param(ByUnion, '{"x": 1}', 1, id="union"),
    pytest.param(BySequence, '[{"x": 1}]', 1, id="sequence"),
    pytest.param(ByMapping, '{"a": {"x": 1}}', 1, id="mapping"),
    pytest.param(ByCustom, '{"a": 1}', 1, id="custom init"),
    pytest.param(ByCustomSequence, '[{"a": 1}]', 1, id="custom init in a sequence"),
]


@pytest.mark.parametrize(("cls", "nested", "expected"), _NESTED)
def test_code_of_nested_values_runs_once_for_a_document_which_is_read(
    cls: type[Any],
    nested: str,
    expected: int,
) -> None:
    _calls.clear()

    assert cls.from_json(f'{{"nested": {nested}, "y": 1}}').y == 1
    assert len(_calls) == expected


@pytest.mark.parametrize(("cls", "nested", "expected"), _NESTED)
def test_code_of_nested_values_is_run_by_a_refused_document_as_by_validating_it(
    cls: type[Any],
    nested: str,
    expected: int,
) -> None:
    text = f'{{"nested": {nested}, "y": "bad"}}'
    _calls.clear()
    with pytest.raises(ValueError, match="Failed to decode"):
        cls.from_json(text)

    read = len(_calls)
    _calls.clear()
    with pytest.raises(ValidationError):
        cls.validate(json.loads(text))

    # what validating the document runs - reading it adds nothing, not a second run
    assert read == len(_calls) == expected


@pytest.mark.parametrize(("cls", "nested", "expected"), _NESTED)
def test_code_of_nested_values_is_not_run_by_a_document_which_is_not_json(
    cls: type[Any],
    nested: str,
    expected: int,
) -> None:
    _calls.clear()

    with pytest.raises(ValueError, match="Failed to decode"):
        cls.from_json(f'{{"nested": {nested}, "y": 1')

    assert _calls == []


class Ordered(State):
    first: _Recorded
    second: _Recorded
    third: int = 0


@pytest.mark.parametrize(
    ("text", "calls"),
    [
        ('{"first": 1, "second": 2}', [1, 2]),
        ('{"second": 2, "first": 1}', [1, 2]),  # the order of the attributes
        ('{"third": 3, "second": 2, "first": 1}', [1, 2]),
    ],
)
def test_code_runs_in_the_order_of_the_attributes(text: str, calls: list[int]) -> None:
    _calls.clear()
    Ordered.from_json(text)

    assert _calls == calls


class _Decoder(json.JSONDecoder):
    pass


@pytest.mark.parametrize(
    "text",
    [
        '{"first": 1, "second": "x"}',  # refused by what comes after the code has run
        '{"second": "x", "first": 1}',
        '{"first": 1}',  # one is missing
        '{"first": "a", "second": 2}',
        '{"first": 1, "second": 2, "third": "x"}',
        '{"first": 1, "second": [1]}',
        '{"first": 1, "second": 2, "extra": 1}',
    ],
)
def test_document_refused_after_code_has_run_is_reported_as_it_always_was(text: str) -> None:
    _calls.clear()
    with pytest.raises(ValueError) as native:
        Ordered.from_json(text)

    native_calls = list(_calls)
    _calls.clear()
    with pytest.raises(ValueError) as plain:
        Ordered.from_json(text, decoder=_Decoder)

    assert str(native.value) == str(plain.value)
    assert type(native.value.__cause__) is type(plain.value.__cause__)
    assert native_calls == _calls


def test_array_refused_after_code_has_run_is_reported_as_it_always_was() -> None:
    text = '[{"first": 1, "second": 2}, {"first": 1, "second": "x"}]'
    _calls.clear()
    with pytest.raises(ValueError) as native:
        Ordered.from_json_array(text)

    native_calls = list(_calls)
    _calls.clear()
    with pytest.raises(ValueError) as plain:
        Ordered.from_json_array(text, decoder=_Decoder)

    assert str(native.value) == str(plain.value)
    assert native_calls == _calls


class Raising(State):
    a: int

    def __init__(self, **kwargs: Any) -> None:
        _calls.append(("init", kwargs))
        raise ValueError("refused by the class")


class ByRaising(State):
    nested: Raising
    y: int


# the nested value of each, with LEAF to be replaced by what is read for the class within it
_SHAPES = [
    pytest.param(ByTuple, "[LEAF, 2]", id="tuple"),
    pytest.param(ByOpenTuple, '[2, {"x": 1}, LEAF]', id="open tuple"),
    pytest.param(BySet, "[LEAF]", id="set"),
    pytest.param(ByTypedDict, '{"leaf": LEAF, "count": 2}', id="typed dict"),
    pytest.param(ByUnion, "LEAF", id="union"),
    pytest.param(BySequence, '[{"x": 1}, LEAF]', id="sequence"),
    pytest.param(ByMapping, '{"a": LEAF}', id="mapping"),
]
_LEAVES = [
    pytest.param('{"x": 1}', id="valid"),
    pytest.param('{"x": "bad"}', id="invalid"),
    pytest.param('{"x": 1, "extra": 1}', id="extra key"),
    pytest.param('{"y": 1}', id="missing"),
]


def _read_as_text(
    cls: type[Any],
    text: str,
    decoder: type[json.JSONDecoder],
    ignore_extra: bool,
    array: bool = False,
) -> tuple[Any, ...]:
    _calls.clear()
    try:
        if array:
            read: Any = cls.from_json_array(text, decoder=decoder, ignore_extra=ignore_extra)

        else:
            read = cls.from_json(text, decoder=decoder, ignore_extra=ignore_extra)

    except ValueError as exc:
        return ("refused", str(exc), type(exc.__cause__), list(_calls))

    return ("read", read, list(_calls))


@pytest.mark.parametrize("ignore_extra", [False, True])
@pytest.mark.parametrize("leaf", _LEAVES)
@pytest.mark.parametrize(("cls", "shape"), _SHAPES)
def test_document_refused_within_a_nested_value_is_reported_as_it_always_was(
    cls: type[Any],
    shape: str,
    leaf: str,
    ignore_extra: bool,
) -> None:
    text = '{"nested": ' + shape.replace("LEAF", leaf) + ', "y": 1}'

    assert _read_as_text(cls, text, json.JSONDecoder, ignore_extra) == _read_as_text(
        cls, text, _Decoder, ignore_extra
    )


@pytest.mark.parametrize("ignore_extra", [False, True])
@pytest.mark.parametrize(
    ("cls", "nested"),
    [
        pytest.param(ByCustom, '{"a": "bad"}', id="custom init refused by its value"),
        pytest.param(ByCustom, '{"a": 1, "extra": 1}', id="custom init with an extra key"),
        pytest.param(ByRaising, '{"a": 1}', id="custom init raising"),
        pytest.param(ByCustomSequence, '[{"a": 1}, {"a": "bad"}]', id="custom init in a sequence"),
    ],
)
def test_document_refused_by_a_class_constructing_itself_is_reported_as_it_always_was(
    cls: type[Any],
    nested: str,
    ignore_extra: bool,
) -> None:
    text = '{"nested": ' + nested + ', "y": 1}'

    assert _read_as_text(cls, text, json.JSONDecoder, ignore_extra) == _read_as_text(
        cls, text, _Decoder, ignore_extra
    )


@pytest.mark.parametrize("ignore_extra", [False, True])
@pytest.mark.parametrize("leaf", _LEAVES)
@pytest.mark.parametrize(("cls", "shape"), _SHAPES)
def test_array_refused_within_a_nested_value_is_reported_as_it_always_was(
    cls: type[Any],
    shape: str,
    leaf: str,
    ignore_extra: bool,
) -> None:
    good = '{"nested": ' + shape.replace("LEAF", '{"x": 1}') + ', "y": 1}'
    other = '{"nested": ' + shape.replace("LEAF", leaf) + ', "y": 1}'
    text = f"[{good}, {other}]"

    assert _read_as_text(cls, text, json.JSONDecoder, ignore_extra, array=True) == _read_as_text(
        cls, text, _Decoder, ignore_extra, array=True
    )


def test_default_of_a_factory_is_called_for_each_document() -> None:
    created: list[int] = []

    def factory() -> int:
        created.append(1)
        return len(created)

    class WithFactory(State):
        value: int = Default(factory=factory)

    first = WithFactory.from_json("{}")
    second = WithFactory.from_json("{}")

    assert (first.value, second.value) == (1, 2)


# the same documents, written and read back by both implementations


def _random_inner(generator: random.Random) -> dict[str, Any]:
    return {"number": generator.randint(-(10**12), 10**12), "text": _random_text(generator)}


def _random_text(generator: random.Random) -> str:
    alphabet = ["a", "Z", " ", '"', "\\", "\n", "é", "日", "😀", "\x01", "/", chr(0x2028)]
    return "".join(generator.choice(alphabet) for _ in range(generator.randint(0, 12)))


@pytest.mark.parametrize("seed", range(40))
def test_random_documents_agree_between_reading_and_validating(seed: int) -> None:
    generator = random.Random(seed)
    payload: dict[str, Any] = {
        "identifier": str(UUID(int=generator.getrandbits(128))),
        "created": datetime(2000 + seed, 1 + seed % 12, 1 + seed % 28, seed % 24).isoformat(),
        "day": date(2000 + seed, 1 + seed % 12, 1 + seed % 28).isoformat(),
        "clock": time(seed % 24, seed % 60).isoformat(),
        "span": "P1DT1H",
        "location": f"/tmp/{_random_text(generator).replace('/', '_')}",
        "payload": "AAEC",
        "mode": generator.choice(["fast", "slow"]),
        "level": generator.choice([1, 2]),
        "ratio": generator.choice(
            [generator.random(), generator.randint(0, 9), 1e10 * generator.random()]
        ),
        "flag": generator.choice([True, False]),
        "name": _random_text(generator),
        "count": generator.randint(-(10**30), 10**30),
        "inner": _random_inner(generator),
        "optional": generator.choice([None, _random_inner(generator)]),
        "many": [_random_inner(generator) for _ in range(generator.randint(0, 4))],
        "scores": {
            _random_text(generator): generator.randint(0, 99)
            for _ in range(generator.randint(0, 4))
        },
        "tags": [_random_text(generator) for _ in range(generator.randint(0, 4))],
        "pair": [generator.randint(0, 9), _random_text(generator)],
        "choice": generator.choice(["a", "b"]),
    }
    text = json.dumps(payload, ensure_ascii=generator.choice([True, False]))

    decoded = Document.from_json(text)

    assert decoded == Document.validate(json.loads(text))
    assert Document.from_json(decoded.to_json()) == decoded
    assert decoded.to_json() == json.dumps(decoded.to_basic_object(), separators=(",", ":"))


def test_validation_error_of_a_nested_attribute_names_its_position() -> None:
    document = json.loads(_document().to_json())
    document["many"][1]["number"] = "x"

    with pytest.raises(ValueError, match=r"many\[1\]\.number|\.many\[1\]"):
        Document.from_json(json.dumps(document))

    with pytest.raises(ValidationError):
        Document.validate(document)


@pytest.mark.parametrize("container", [Map, Meta])
def test_json_dumps_handles_the_immutable_mappings(container: Any) -> None:
    mapping: Any = container({"b": 2, "a": 1})

    assert json.loads(json.dumps(mapping)) == {"a": 1, "b": 2}
    assert json.dumps(mapping, indent=2) == json.dumps({"b": 2, "a": 1}, indent=2)
    assert json.dumps(mapping, sort_keys=True) == '{"a": 1, "b": 2}'
    assert json.dumps({"k": [mapping]}) == '{"k": [{"b": 2, "a": 1}]}'
    assert json.dumps(container({"d": Decimal(1)}), cls=_Encoder) == '{"d": "1"}'


def test_state_to_json_uses_the_encoder_options() -> None:
    class Holder(State):
        values: Mapping[str, int]
        amount: Any

    holder = Holder(values={"b": 2, "a": 1}, amount=Decimal(3))

    assert json.loads(holder.to_json(encoder_class=_Encoder)) == {
        "values": {"b": 2, "a": 1},
        "amount": "3",
    }
    assert "\n" in holder.to_json(encoder_class=_Encoder, indent=2)
    assert Holder.from_json(json.dumps({"values": {"a": 1}, "amount": "x"})).values == {"a": 1}


class _IntKeyedJSON(State):
    values: dict[int, int]


@pytest.mark.parametrize("key", (0, 1, -1, 2**63 - 1, -(2**63), 2**64, -(2**100)))
def test_int_keys_are_written_as_json_writes_them(key: int) -> None:
    keyed = _IntKeyedJSON(values={key: 1, key + 1: 2})
    assert json.loads(keyed.to_json()) == json.loads(json.dumps({"values": {key: 1, key + 1: 2}}))
    assert _IntKeyedJSON.from_json(keyed.to_json()) == keyed
