import json
import random
import sys
import typing
from collections import defaultdict
from collections.abc import Callable, Iterator, Mapping, Sequence
from types import MappingProxyType
from typing import Annotated, Any, NotRequired, TypedDict, Unpack

import pytest

from haiway import Alias, State, Validator
from haiway._native import nstruct
from haiway.attributes.validation import ValidationError
from haiway.types import Map

FAILURES = (TypeError, ValueError, ValidationError)


class Leaf(State):
    value: int
    label: str = "leaf"


class Aliased(State):
    identifier: Annotated[int, Alias("id")]
    label: str = "none"


class Defaulted(State):
    first: int = 1
    second: str = "two"


class Details(TypedDict):
    code: int
    note: NotRequired[str]


class Envelope(TypedDict):
    details: Details
    leaf: Leaf


class ClosedDetails(TypedDict, closed=True):
    code: int


class OpenDetails(TypedDict, extra_items=int):
    code: int


class Holder(State):
    name: str
    leaf: Leaf
    leaves: Sequence[Leaf] = ()
    pair: tuple[Leaf, Leaf] | None = None
    by_key: Mapping[str, Leaf] = Map()
    either: Leaf | Aliased | None = None
    details: Details | None = None
    envelope: Envelope | None = None


class Box[Value](State):
    item: Value


class OpenLeading(State):
    items: tuple[Leaf, *tuple[int, ...]]


class OpenMiddle(State):
    items: tuple[str, *tuple[Leaf, ...], str]


class OpenTrailing(State):
    items: tuple[*tuple[int, ...], Leaf]


class OpenGeneric[*Rest](State):
    items: tuple[Leaf, *Rest]


class Customized(State):
    first: int
    second: int = 0

    def __init__(self, first: int, second: int = 0) -> None:
        super().__init__(first=first, second=second)


received: list[Any] = []


class Validating(State):
    value: int

    @classmethod
    def validate(cls, value: Any) -> Validating:
        received.append(value)
        return super().validate(value)


class Reading(json.JSONDecoder):
    pass


def _texts(error: BaseException) -> str:
    # the message of an error with what it is made of - an alternative of a union fails into a group
    parts: list[str] = [str(error)]
    for inner in (error.__cause__, error.__context__, *getattr(error, "exceptions", ())):
        if inner is not None:
            parts.append(_texts(inner))

    return " ".join(parts)


def _from_mapping(cls: Any, payload: Any, ignore_extra: bool) -> Any:
    return cls.from_mapping(payload, ignore_extra=ignore_extra)


def _from_map(cls: Any, payload: Any, ignore_extra: bool) -> Any:
    return cls.from_mapping(Map(payload), ignore_extra=ignore_extra)


def _from_proxy(cls: Any, payload: Any, ignore_extra: bool) -> Any:
    return cls.from_mapping(MappingProxyType(payload), ignore_extra=ignore_extra)


def _from_json(cls: Any, payload: Any, ignore_extra: bool) -> Any:
    return cls.from_json(json.dumps(payload), ignore_extra=ignore_extra)


def _from_json_bytes(cls: Any, payload: Any, ignore_extra: bool) -> Any:
    return cls.from_json(json.dumps(payload).encode(), ignore_extra=ignore_extra)


def _from_json_decoder(cls: Any, payload: Any, ignore_extra: bool) -> Any:
    return cls.from_json(json.dumps(payload), Reading, ignore_extra=ignore_extra)


def _from_json_array(cls: Any, payload: Any, ignore_extra: bool) -> Any:
    (result,) = cls.from_json_array(json.dumps([payload]), ignore_extra=ignore_extra)
    return result


def _from_json_array_decoder(cls: Any, payload: Any, ignore_extra: bool) -> Any:
    (result,) = cls.from_json_array(json.dumps([payload]), Reading, ignore_extra=ignore_extra)
    return result


ENTRY_POINTS: dict[str, Callable[[Any, Any, bool], Any]] = {
    "from_mapping": _from_mapping,
    "from_mapping_map": _from_map,
    "from_mapping_proxy": _from_proxy,
    "from_json": _from_json,
    "from_json_bytes": _from_json_bytes,
    "from_json_decoder": _from_json_decoder,
    "from_json_array": _from_json_array,
    "from_json_array_decoder": _from_json_array_decoder,
}

entry_points = pytest.mark.parametrize("decode", ENTRY_POINTS.values(), ids=ENTRY_POINTS.keys())

# a clean payload, and the same with a key which is no attribute added somewhere in it
SCENARIOS: dict[str, tuple[Any, Any, Any]] = {
    "top": (Leaf, {"value": 1}, {"value": 1, "extra": 2}),
    "top_before": (Leaf, {"value": 1, "label": "x"}, {"extra": [1], "value": 1, "label": "x"}),
    "top_between": (
        Leaf,
        {"value": 1, "label": "x"},
        {"value": 1, "extra": {"a": None}, "label": "x"},
    ),
    "several": (Leaf, {"value": 1}, {"a": 1, "value": 1, "b": 2, "c": 3, "d": 4, "e": 5}),
    "only_extras": (Defaulted, {}, {"nothing": 1, "else": [True]}),
    "alias": (Aliased, {"id": 1}, {"id": 1, "extra": "x"}),
    "alias_with_name": (
        Aliased,
        {"id": 1, "label": "x"},
        {"id": 1, "label": "x", "other": 0},
    ),
    "nested_state": (
        Holder,
        {"name": "n", "leaf": {"value": 1}},
        {"name": "n", "extra": 1, "leaf": {"value": 1, "extra": 2}},
    ),
    "sequence": (
        Holder,
        {"name": "n", "leaf": {"value": 1}, "leaves": [{"value": 1}, {"value": 2}]},
        {
            "name": "n",
            "leaf": {"value": 1},
            "leaves": [{"value": 1}, {"value": 2, "extra": 3}],
        },
    ),
    "tuple": (
        Holder,
        {"name": "n", "leaf": {"value": 1}, "pair": [{"value": 1}, {"value": 2}]},
        {
            "name": "n",
            "leaf": {"value": 1},
            "pair": [{"value": 1, "extra": 1}, {"value": 2}],
        },
    ),
    "mapping": (
        Holder,
        {"name": "n", "leaf": {"value": 1}, "by_key": {"k": {"value": 2}}},
        {"name": "n", "leaf": {"value": 1}, "by_key": {"k": {"value": 2, "extra": 3}}},
    ),
    "optional": (
        Holder,
        {"name": "n", "leaf": {"value": 1}, "either": {"value": 2}},
        {"name": "n", "leaf": {"value": 1}, "either": {"value": 2, "extra": 3}},
    ),
    "union_second": (
        Holder,
        {"name": "n", "leaf": {"value": 1}, "either": {"id": 2}},
        {"name": "n", "leaf": {"value": 1}, "either": {"id": 2, "extra": 3}},
    ),
    "typed_dict": (
        Holder,
        {"name": "n", "leaf": {"value": 1}, "details": {"code": 1, "note": "x"}},
        {"name": "n", "leaf": {"value": 1}, "details": {"code": 1, "extra": 2, "note": "x"}},
    ),
    "typed_dict_nested": (
        Holder,
        {
            "name": "n",
            "leaf": {"value": 1},
            "envelope": {"details": {"code": 1}, "leaf": {"value": 2}},
        },
        {
            "name": "n",
            "leaf": {"value": 1},
            "envelope": {"details": {"code": 1, "extra": 2}, "leaf": {"value": 2}},
        },
    ),
    "typed_dict_envelope": (
        Holder,
        {
            "name": "n",
            "leaf": {"value": 1},
            "envelope": {"details": {"code": 1}, "leaf": {"value": 2}},
        },
        {
            "name": "n",
            "leaf": {"value": 1},
            "envelope": {"details": {"code": 1}, "leaf": {"value": 2}, "extra": 2},
        },
    ),
    "state_in_typed_dict": (
        Holder,
        {
            "name": "n",
            "leaf": {"value": 1},
            "envelope": {"details": {"code": 1}, "leaf": {"value": 2}},
        },
        {
            "name": "n",
            "leaf": {"value": 1},
            "envelope": {"details": {"code": 1}, "leaf": {"value": 2, "extra": 3}},
        },
    ),
    "generic": (Box[int], {"item": 1}, {"item": 1, "extra": 2}),
    "generic_nested": (
        Box[Leaf],
        {"item": {"value": 1}},
        {"item": {"value": 1, "extra": 2}, "extra": 2},
    ),
    "open_tuple_leading": (
        OpenLeading,
        {"items": [{"value": 1}, 3, 4]},
        {"items": [{"value": 1, "extra": 2}, 3, 4]},
    ),
    "open_tuple_middle": (
        OpenMiddle,
        {"items": ["a", {"value": 1}, {"value": 2}, "b"]},
        {"items": ["a", {"value": 1, "x": 1}, {"value": 2, "y": 2}, "b"]},
    ),
    "open_tuple_trailing": (
        OpenTrailing,
        {"items": [1, 2, {"value": 3}]},
        {"items": [1, 2, {"value": 3, "extra": 4}]},
    ),
    "open_tuple_unspecialized": (
        OpenGeneric,
        {"items": [{"value": 1}, 3]},
        {"items": [{"value": 1, "extra": 2}, 3]},
    ),
    "open_tuple_specialized": (
        OpenGeneric[*tuple[int, ...]],
        {"items": [{"value": 1}, 3]},
        {"items": [{"value": 1, "extra": 2}, 3]},
    ),
    "custom_init": (Customized, {"first": 1}, {"first": 1, "extra": 2}),
    "name_of_aliased": (Aliased, {"id": 1}, {"id": 1, "extra": 2}),
}

scenarios = pytest.mark.parametrize(
    ("cls", "clean", "dirty"),
    SCENARIOS.values(),
    ids=SCENARIOS.keys(),
)


@scenarios
@entry_points
def test_extra_keys_are_refused_by_default(
    decode: Callable[[Any, Any, bool], Any],
    cls: Any,
    clean: Any,
    dirty: Any,
) -> None:
    assert decode(cls, clean, False) is not None

    with pytest.raises(FAILURES) as exc:
        decode(cls, dirty, False)

    assert "Unexpected" in _texts(exc.value)


@scenarios
@entry_points
def test_extra_keys_are_ignored_on_request(
    decode: Callable[[Any, Any, bool], Any],
    cls: Any,
    clean: Any,
    dirty: Any,
) -> None:
    assert decode(cls, dirty, True) == decode(cls, clean, False)
    assert decode(cls, clean, True) == decode(cls, clean, False)


def test_ignored_result_has_the_known_values() -> None:
    holder: Holder = Holder.from_json(
        json.dumps(SCENARIOS["sequence"][2]),
        ignore_extra=True,
    )
    assert holder == Holder(
        name="n",
        leaf=Leaf(value=1),
        leaves=(Leaf(value=1), Leaf(value=2)),
    )


@pytest.mark.parametrize("ignore_extra", [False, True])
@pytest.mark.parametrize(
    "decode",
    [_from_mapping, _from_json, _from_json_decoder, _from_json_array],
)
def test_missing_required_field_fails_either_way(
    decode: Callable[[Any, Any, bool], Any],
    ignore_extra: bool,
) -> None:
    with pytest.raises(FAILURES):
        decode(Leaf, {"extra": 1}, ignore_extra)

    with pytest.raises(FAILURES):
        decode(Holder, {"name": "n", "leaf": {"extra": 1}}, ignore_extra)


@pytest.mark.parametrize(
    "decode",
    [_from_mapping, _from_json, _from_json_decoder, _from_json_array],
)
@pytest.mark.parametrize(
    "payload",
    [
        {"value": "x", "extra": 1},
        {"extra": 1, "value": None},
        {"value": 1, "label": 3, "extra": 1},
    ],
)
def test_invalid_value_of_known_key_fails_with_flag(
    decode: Callable[[Any, Any, bool], Any],
    payload: Any,
) -> None:
    with pytest.raises(FAILURES) as exc:
        decode(Leaf, payload, True)

    assert "Unexpected" not in str(exc.value)


def test_invalid_nested_value_is_reported_at_its_position() -> None:
    with pytest.raises(ValidationError) as exc:
        Holder.from_mapping(
            {"name": "n", "leaf": {"value": 1}, "leaves": [{"value": 1}, {"value": "x", "q": 1}]},
            ignore_extra=True,
        )

    assert ".leaves[1].value" in str(exc.value)


@pytest.mark.parametrize("ignore_extra", [False, True])
@entry_points
def test_aliases_and_names_are_accepted(
    decode: Callable[[Any, Any, bool], Any],
    ignore_extra: bool,
) -> None:
    assert decode(Aliased, {"id": 1}, ignore_extra) == Aliased(identifier=1)
    assert decode(Aliased, {"identifier": 1}, ignore_extra) == Aliased(identifier=1)


@pytest.mark.parametrize("ignore_extra", [False, True])
def test_name_and_alias_given_together_fail_either_way(ignore_extra: bool) -> None:
    with pytest.raises(TypeError, match="Duplicate attribute"):
        Aliased.from_mapping({"id": 1, "identifier": 2}, ignore_extra=ignore_extra)

    with pytest.raises(ValueError, match="Duplicate attribute"):
        Aliased.from_json('{"id": 1, "identifier": 2}', ignore_extra=ignore_extra)


def test_extra_key_is_not_taken_for_a_neighbour() -> None:
    # the key which is not an attribute is neither the name nor the alias of one
    assert Aliased.from_mapping({"id": 1, "ident": 2, "ids": 3}, ignore_extra=True) == Aliased(
        identifier=1
    )


def test_repeated_known_key_in_json_keeps_the_last_with_extras() -> None:
    document: str = '{"extra": 1, "value": 1, "extra": 2, "value": 3}'
    assert Leaf.from_json(document, ignore_extra=True) == Leaf(value=3)
    assert Leaf.from_json(document, Reading, ignore_extra=True) == Leaf(value=3)


@pytest.mark.parametrize("ignore_extra", [False, True])
def test_non_string_keys_of_a_mapping(ignore_extra: bool) -> None:
    payload: dict[Any, Any] = {"value": 1, 3: "x", None: 1}
    if ignore_extra:
        assert Leaf.from_mapping(payload, ignore_extra=True) == Leaf(value=1)

    else:
        with pytest.raises(TypeError):
            Leaf.from_mapping(payload)


def test_default_values_are_used_when_only_extras_are_given() -> None:
    assert Defaulted.from_mapping({"x": 1}, ignore_extra=True) == Defaulted()
    assert Defaulted.from_json('{"x": {"y": [1]}}', ignore_extra=True) == Defaulted()
    assert nstruct.from_json(Defaulted, '{"x": {"y": [1]}}', False, True) == Defaulted()


# the paths which are not the validation of a mapping keep refusing


def test_other_ways_of_building_keep_refusing_extra_keys() -> None:
    leaf: Leaf = Leaf(value=1)
    refused: dict[str, Any] = {"extra": 2}
    with pytest.raises(TypeError, match="extra"):
        Leaf(value=1, **refused)

    with pytest.raises(TypeError, match="extra"):
        leaf.updating(**refused)

    with pytest.raises(TypeError, match="extra"):
        leaf.__replace__(**refused)

    with pytest.raises(TypeError, match="Unexpected attribute 'extra' for Leaf"):
        Leaf.validate({"value": 1, "extra": 2})

    with pytest.raises(ValidationError, match="Unexpected attribute 'extra' for Leaf"):
        Holder.validate({"name": "n", "leaf": {"value": 1, "extra": 2}})


def test_default_messages_are_unchanged() -> None:
    with pytest.raises(TypeError) as mapping_error:
        Leaf.from_mapping({"value": 1, "extra": 2})

    assert str(mapping_error.value) == "Unexpected attribute 'extra' for Leaf"

    with pytest.raises(ValueError) as json_error:
        Leaf.from_json('{"value": 1, "extra": 2}')

    assert str(json_error.value) == (
        "Failed to decode Leaf from json: Unexpected attribute 'extra' for Leaf"
    )

    with pytest.raises(ValidationError) as nested_error:
        Holder.from_mapping({"name": "n", "leaf": {"value": 1, "extra": 2}})

    assert str(nested_error.value) == (
        "Validation of .leaf failed: Unexpected attribute 'extra' for Leaf"
    )


# the keywords


def test_ignore_extra_is_keyword_only() -> None:
    positional: tuple[Any, ...] = (True,)
    with pytest.raises(TypeError):
        Leaf.from_mapping({"value": 1}, *positional)

    with pytest.raises(TypeError):
        Leaf.from_json("{}", json.JSONDecoder, *positional)

    with pytest.raises(TypeError):
        Leaf.from_json_array("[]", json.JSONDecoder, *positional)


# the Python fallback of the json reading


@pytest.mark.parametrize("ignore_extra", [False, True])
@pytest.mark.parametrize(
    "document",
    [
        '{"value": 1, "extra": 2, "when": NaN}',  # not read natively - a key spelled twice
        '{"value": 1, "value": 2, "extra": 3}',
        b"\xef\xbb\xbf" + b'{"value": 1, "extra": 2}',  # a mark of the encoding
        '{"value": 1, "extra": "\\ud800"}',  # a lone surrogate
    ],
)
def test_documents_read_by_the_standard_library_honor_the_flag(
    document: str | bytes,
    ignore_extra: bool,
) -> None:
    expected: Leaf | None
    try:
        expected = Leaf.from_mapping(json.loads(document), ignore_extra=ignore_extra)

    except Exception:
        expected = None

    try:
        read: Leaf | None = Leaf.from_json(document, ignore_extra=ignore_extra)

    except ValueError:
        read = None

    assert read == expected
    assert (read is not None) == ignore_extra


def test_declined_document_with_extras_falls_back() -> None:
    document: str = '{"value": 1, "label": "x", "extra": 2}'
    assert nstruct.from_json(Leaf, document, False, False) is None
    assert nstruct.from_json(Leaf, document, False, True) == Leaf(value=1, label="x")

    # an extra key makes no difference to what declines the document for another reason
    declined: str = '{"value": 1, "extra": {"a": 1}, "value": 2, "label": 3}'
    assert nstruct.from_json(Leaf, declined, False, True) is None
    with pytest.raises(ValueError):
        Leaf.from_json(declined, ignore_extra=True)


def test_native_reader_handles_the_flag_without_falling_back() -> None:
    document: str = '{"extra": 1, "value": 2, "other": [1, {"a": null}]}'
    assert nstruct.from_json(Leaf, document, False, True) == Leaf(value=2)
    assert nstruct.from_json(Leaf, document, False, False) is None  # declined
    assert nstruct.from_json(Leaf, f"[{document}, {document}]", True, True) == (
        Leaf(value=2),
        Leaf(value=2),
    )
    assert nstruct.from_json(Leaf, f"[{document}]", True, False) is None


def test_native_reader_reads_nested_extras_without_falling_back() -> None:
    document: str = (
        '{"name": "n", "x": 1, "leaf": {"value": 1, "y": [1]}, '
        '"leaves": [{"z": {}, "value": 2}], "by_key": {"k": {"value": 3, "w": "v"}}, '
        '"pair": [{"value": 1, "q": 1}, {"value": 2}], "either": {"value": 4, "r": 1}}'
    )
    read: Any = nstruct.from_json(Holder, document, False, True)
    assert read is not None
    assert read == Holder.from_mapping(json.loads(document), ignore_extra=True)
    assert read.leaves == (Leaf(value=2),)
    assert read.either == Leaf(value=4)


# skipping what is not read

EXTRA_VALUES: dict[str, str] = {
    "object": '{"a": [1, 2, {"b": null}], "c": {}}',
    "array": '[1, "two", [3, [4]], {"five": 5}]',
    "empty_object": "{}",
    "empty_array": "[]",
    "empty_object_spaced": "{ \n }",
    "empty_array_spaced": "[ \t ]",
    "string": '"plain"',
    "empty_string": '""',
    "quotes_braces": '"he said \\"hi\\" {not} [an object], \\\\"',
    "backslash_end": '"ends with a backslash \\\\"',
    "unicode_escapes": '"\\u00e9\\u0041\\ud83d\\ude00\\uD83D"',
    "unicode_text": '"é😀 ü"',
    "all_escapes": '"\\" \\\\ \\/ \\b \\f \\n \\r \\t"',
    "long_string": '"' + "x\\n" * 50_000 + '"',
    "integer": "12345678901234567890",
    "zero": "0",
    "negative": "-17",
    "fraction": "-0.5",
    "exponent": "1.5e10",
    "exponent_upper": "2E+3",
    "exponent_negative": "-1e-7",
    "true": "true",
    "false": "false",
    "null": "null",
    "nan": "NaN",
    "infinity": "Infinity",
    "negative_infinity": "-Infinity",
    "key_escapes": '{"k\\"ey": 1, "\\u00e9": 2, "é": 3}',
    "deep": "[" * 50 + "]" * 50,
    "deep_objects": '{"a":' * 50 + "1" + "}" * 50,
}

# the same document, spelled with whitespace between every token
SPACED: str = (
    ' \n\t{ \r\n"extra" \t: {extra} ,\n "value"\n: 4 ,\t"label" : "x" , "more" : {extra} } \n'
)


@pytest.mark.parametrize("extra", EXTRA_VALUES.values(), ids=EXTRA_VALUES.keys())
@pytest.mark.parametrize("layout", ["first", "middle", "last", "spaced", "only_known"])
def test_extra_values_are_skipped_natively(extra: str, layout: str) -> None:
    document: str
    match layout:
        case "first":
            document = '{"extra": ' + extra + ', "value": 4, "label": "x"}'

        case "middle":
            document = '{"value": 4, "extra": ' + extra + ', "label": "x"}'

        case "last":
            document = '{"value": 4, "label": "x", "extra": ' + extra + "}"

        case "spaced":
            document = SPACED.replace("{extra}", extra)

        case _:
            document = '{"value": 4, "label": "x"}'

    expected: Leaf = Leaf(value=4, label="x")
    assert nstruct.from_json(Leaf, document, False, True) == expected
    assert nstruct.from_json(Leaf, document.encode(), False, True) == expected
    assert Leaf.from_json(document, ignore_extra=True) == expected
    assert Leaf.from_json(document.encode(), ignore_extra=True) == expected
    assert Leaf.from_json(document, Reading, ignore_extra=True) == expected
    assert Leaf.from_json_array(f"[{document}]", ignore_extra=True) == (expected,)
    assert Leaf.from_mapping(json.loads(document), ignore_extra=True) == expected

    if layout != "only_known":
        assert nstruct.from_json(Leaf, document, False, False) is None
        with pytest.raises(ValueError, match="Unexpected attribute 'extra' for Leaf"):
            Leaf.from_json(document)


@pytest.mark.parametrize("extra", EXTRA_VALUES.values(), ids=EXTRA_VALUES.keys())
def test_extra_values_are_skipped_at_depth(extra: str) -> None:
    document: str = (
        '{"name": "n", "leaf": {"extra": '
        + extra
        + ', "value": 1}, "leaves": [{"value": 2, "extra": '
        + extra
        + '}], "details": {"code": 3, "extra": '
        + extra
        + "}}"
    )
    read: Any = nstruct.from_json(Holder, document, False, True)
    assert read == Holder(
        name="n",
        leaf=Leaf(value=1),
        leaves=(Leaf(value=2),),
        details={"code": 3},
    )
    assert read == Holder.from_json(document, Reading, ignore_extra=True)


@pytest.mark.parametrize(
    "extra",
    [
        "[1,}",
        "[1,]",
        "[1 2]",
        "[",
        "{",
        '{"a" 1}',
        '{"a": 1,}',
        '{"a": }',
        "{a: 1}",
        "{1: 1}",
        "'single'",
        '"unterminated',
        '"bad escape \\x"',
        '"bad unicode \\u12G4"',
        '"short unicode \\u12"',
        '"raw\ncontrol"',
        '{"bad\\q": 1}',
        '{"k": "bad \\q"}',
        "01",
        "1.",
        ".5",
        "1e",
        "+1",
        "--1",
        "-",
        "tru",
        "nul",
        "True",
        "Nan",
        "undefined",
        "",
        "[1] x",
        '{"a": [1, 2, {"b": [}]}',
        "[" * 5 + "]" * 4,
    ],
)
@pytest.mark.parametrize(
    "layout",
    [
        '{"value": 1, "extra": %s, "label": "x"}',
        '{"value": 1, "label": "x", "extra": %s}',
        '{"extra": %s, "value": 1}',
    ],
    ids=["between", "last", "first"],
)
def test_malformed_extra_values_are_not_skipped(extra: str, layout: str) -> None:
    document: str = layout % extra
    assert nstruct.from_json(Leaf, document, False, True) is None
    with pytest.raises(ValueError):
        Leaf.from_json(document, ignore_extra=True)

    with pytest.raises(ValueError):
        Leaf.from_json_array(f"[{document}]", ignore_extra=True)

    with pytest.raises(ValueError):
        Leaf.from_json(document, Reading, ignore_extra=True)


@pytest.mark.parametrize(
    "document",
    [
        b'{"value": 1, "extra": "\xff"}',
        b'{"value": 1, "extra": "\xc3"}',
        b'{"value": 1, "extra": "\xc0\x80"}',
        b'{"value": 1, "extra": "\xf4\x90\x80\x80"}',
        b'{"value": 1, "extra": "\xe2\x82"}',
        b'{"value": 1, "extra": ["\xf8"]}',
        b'{"value": 1, "\xff": 1}',
        b'{"value": 1, "\xc3": 1}',
    ],
)
def test_extra_values_which_are_not_utf8_are_not_skipped(document: bytes) -> None:
    assert nstruct.from_json(Leaf, document, False, True) is None
    with pytest.raises(ValueError):
        Leaf.from_json(document, ignore_extra=True)


def test_encoded_surrogate_is_left_to_the_standard_library() -> None:
    # json.loads reads bytes allowing surrogates - the reader declines, it does not refuse
    document: bytes = b'{"value": 1, "extra": "\xed\xa0\x80"}'
    assert nstruct.from_json(Leaf, document, False, True) is None
    assert Leaf.from_json(document, ignore_extra=True) == Leaf(value=1)


@pytest.mark.parametrize(
    "document",
    [
        b'{"value": 1, "extra": "\xc3\xa9\xe2\x82\xac\xf0\x9f\x98\x80"}',
        b'{"value": 1, "\xc3\xa9\xf0\x9f\x98\x80": 1}',
        b'{"value": 1, "\xed\x9f\xbf\xee\x80\x80\xf4\x8f\xbf\xbf": 1}',
    ],
)
def test_extra_values_which_are_utf8_are_skipped(document: bytes) -> None:
    assert nstruct.from_json(Leaf, document, False, True) == Leaf(value=1)


def _from_json_text(text: str) -> Any:
    return Leaf.from_json(text, ignore_extra=True)


def _from_json_text_decoder(text: str) -> Any:
    return Leaf.from_json(text, Reading, ignore_extra=True)


def _from_json_array_text(text: str) -> Any:
    return Leaf.from_json_array("[" + text, ignore_extra=True)


@pytest.mark.parametrize(
    "decode",
    [_from_json_text, _from_json_text_decoder, _from_json_array_text],
    ids=["native", "decoder", "array"],
)
@pytest.mark.parametrize("cut", range(1, 60))
def test_truncated_documents_are_never_read(cut: int, decode: Callable[[str], Any]) -> None:
    document: str = '{"value": 1, "extra": {"a": [1, "x\\"y", {"b": null}]}, "label": "x"}'
    truncated: str = document[:cut]
    with pytest.raises(ValueError):
        decode(truncated)

    assert nstruct.from_json(Leaf, truncated, False, True) is None


@pytest.mark.parametrize("key", ["\\q", "\\u12", "\\ud800x\\"])
def test_extra_key_with_a_bad_spelling_is_not_skipped(key: str) -> None:
    document: str = '{"value": 1, "' + key + '": 1}'
    assert nstruct.from_json(Leaf, document, False, True) is None
    with pytest.raises(ValueError):
        Leaf.from_json(document, ignore_extra=True)


def test_extra_key_spelled_with_escapes_is_still_extra_or_known() -> None:
    assert Leaf.from_json('{"val\\u0075e": 1, "e\\u0078tra": 2}', ignore_extra=True) == Leaf(
        value=1
    )
    assert nstruct.from_json(Leaf, '{"val\\u0075e": 1, "e\\u0078tra": 2}', False, True) == Leaf(
        value=1
    )
    with pytest.raises(ValueError, match="Unexpected attribute 'extra' for Leaf"):
        Leaf.from_json('{"value": 1, "e\\u0078tra": 2}')


def _nested(depth: int) -> str:
    return "[" * depth + "]" * depth


@pytest.mark.parametrize("depth", [10, 100, 400, 800, 950, 990, 1500, 5000, 100_000])
def test_deep_extra_values_are_read_as_the_standard_library_reads_them(depth: int) -> None:
    document: str = '{"value": 1, "extra": ' + _nested(depth) + "}"
    expected: Leaf | None
    try:
        expected = Leaf.from_mapping(json.loads(document), ignore_extra=True)

    except Exception:
        expected = None

    native: Any
    try:
        native = nstruct.from_json(Leaf, document, False, True)

    except RecursionError:
        native = None

    if native is not None:
        assert native == expected

    try:
        read: Leaf | None = Leaf.from_json(document, ignore_extra=True)

    except ValueError:
        read = None

    assert read == expected


def test_depth_of_skipped_values_leaves_nothing_behind() -> None:
    limit: int = sys.getrecursionlimit()
    assert Leaf.from_json(
        '{"value": 1, "extra": ' + _nested(limit // 4) + "}", ignore_extra=True
    ) == Leaf(value=1)

    for depth in (limit * 3, 100_000):
        try:
            Leaf.from_json('{"value": 1, "extra": ' + _nested(depth) + "}", ignore_extra=True)

        except ValueError:
            pass

    # the recursion which was given up is not held against the next decode
    assert Leaf.from_json('{"value": 1, "extra": [[]]}', ignore_extra=True) == Leaf(value=1)
    assert Leaf.from_json('{"extra": {"a": [{}]}, "value": 2}', ignore_extra=True) == Leaf(value=2)


# which alternative of a union wins when the extra keys are all that tells them apart


class First(State):
    kind: str = "first"


class Second(State):
    kind: str = "second"
    other: int = 0


class Either(State):
    pick: First | Second


@pytest.mark.parametrize(
    "decode",
    [_from_mapping, _from_json, _from_json_decoder, _from_json_array],
)
def test_first_matching_alternative_wins_when_extras_are_ignored(
    decode: Callable[[Any, Any, bool], Any],
) -> None:
    # `other` is no attribute of First - ignored, it fits, so it wins over Second
    picked: Either = decode(Either, {"pick": {"kind": "second", "other": 3}}, True)
    assert type(picked.pick) is First
    assert picked.pick.kind == "second"

    # refused by First without the flag - Second is the one which fits
    picked = decode(Either, {"pick": {"kind": "second", "other": 3}}, False)
    assert type(picked.pick) is Second
    assert picked.pick.other == 3


class AlternativeA(State):
    a: int


class AlternativeB(State):
    b: int


class Choice(State):
    pick: AlternativeA | AlternativeB


@pytest.mark.parametrize("ignore_extra", [False, True])
@pytest.mark.parametrize(
    "document",
    [
        '{"pick": {"a": "bad", "a": 1, "b": 2}}',  # the repeat makes the first alternative fit
        '{"pick": {"a": 1, "a": "bad", "b": 2}}',  # the repeat makes the first one fail
        '{"pick": {"b": 2, "a": "bad", "a": "worse"}}',  # a later alternative has to match
        '{"pick": {"a": "bad", "b": 1, "b": 2}}',  # the later alternative has its own repeat
        '{"pick": {"a": "bad", "a": 1}}',
        '{"pick": {"a": "bad", "x": 1, "x": 2, "b": 3}}',
        '{"pick": {"a": "bad", "\\u0061": 1, "b": 2}}',  # a repeat spelled with an escape
    ],
)
def test_repeated_key_in_a_union_is_read_as_the_standard_library_reads_it(
    document: str,
    ignore_extra: bool,
) -> None:
    def outcome(decoder: Any) -> Any:
        try:
            return Choice.from_json(document, *decoder, ignore_extra=ignore_extra)

        except ValueError as exc:
            return type(exc.__cause__)

    assert outcome(()) == outcome((Reading,))


def test_repeated_key_making_the_first_alternative_fit_picks_it() -> None:
    document: str = '{"pick": {"a": "bad", "a": 1, "b": 2}}'
    assert Choice.from_json(document, ignore_extra=True).pick == AlternativeA(a=1)
    assert Choice.from_json(document, Reading, ignore_extra=True).pick == AlternativeA(a=1)
    assert nstruct.from_json(Choice, document, False, True) is None  # left to the standard library


def test_union_object_with_many_keys_after_a_failed_alternative_is_declined() -> None:
    extras: str = ", ".join(f'"k{index}": {index}' for index in range(40))
    document: str = '{"pick": {"a": "bad", ' + extras + ', "b": 2}}'
    assert nstruct.from_json(Choice, document, False, True) is None
    assert Choice.from_json(document, ignore_extra=True) == Choice.from_json(
        document,
        Reading,
        ignore_extra=True,
    )


def test_later_alternative_is_still_read_natively_without_a_repeated_key() -> None:
    document: str = '{"pick": {"a": "bad", "b": 2}}'
    assert nstruct.from_json(Choice, document, False, True) == Choice(pick=AlternativeB(b=2))


# the keys of a TypedDict


class Wrapper(State):
    details: Details


class Enveloping(State):
    envelope: Envelope


@pytest.mark.parametrize(
    "decode",
    [_from_mapping, _from_json, _from_json_decoder, _from_json_array],
)
def test_typed_dict_refuses_undeclared_keys_by_default(
    decode: Callable[[Any, Any, bool], Any],
) -> None:
    assert decode(Wrapper, {"details": {"code": 1}}, False).details == {"code": 1}
    assert decode(Wrapper, {"details": {"code": 1, "note": "n"}}, False).details == {
        "code": 1,
        "note": "n",
    }
    with pytest.raises(FAILURES) as exc:
        decode(Wrapper, {"details": {"code": 1, "extra": 2}}, False)

    assert "Unexpected key 'extra' for Details" in str(exc.value)


@pytest.mark.parametrize(
    "decode",
    [_from_mapping, _from_json, _from_json_decoder, _from_json_array],
)
def test_typed_dict_drops_undeclared_keys_on_request(
    decode: Callable[[Any, Any, bool], Any],
) -> None:
    assert decode(Wrapper, {"details": {"code": 1, "extra": 2, "z": [1]}}, True).details == {
        "code": 1
    }


def test_typed_dict_validate_refuses_undeclared_keys() -> None:
    undeclared: Any = {"code": 1, "extra": 2}
    with pytest.raises(ValidationError, match="Unexpected key 'extra' for Details"):
        Wrapper.validate({"details": {"code": 1, "extra": 2}})

    with pytest.raises(ValidationError, match="Unexpected key 'extra' for Details"):
        Wrapper(details=undeclared)


def test_typed_dict_refusal_is_reported_at_its_position() -> None:
    with pytest.raises(ValidationError) as exc:
        Enveloping.from_mapping(
            {"envelope": {"details": {"code": 1, "extra": 2}, "leaf": {"value": 1}}}
        )

    assert str(exc.value) == (
        "Validation of .envelope[\"details\"] failed: Unexpected key 'extra' for Details"
    )

    with pytest.raises(ValidationError) as top:
        Wrapper.from_mapping({"details": {"code": 1, "extra": 2}})

    assert str(top.value) == "Validation of .details failed: Unexpected key 'extra' for Details"


def test_typed_dict_refusal_comes_before_the_values_are_validated() -> None:
    with pytest.raises(ValidationError, match="Unexpected key 'extra' for Details"):
        Wrapper.from_mapping({"details": {"code": "not a number", "extra": 2}})

    with pytest.raises(ValidationError) as exc:
        Wrapper.from_mapping({"details": {"code": "not a number", "extra": 2}}, ignore_extra=True)

    assert "Unexpected" not in str(exc.value)


def test_typed_dict_still_requires_declared_keys() -> None:
    for ignore_extra in (False, True):
        with pytest.raises(ValidationError, match="Value for 'code' is required"):
            Wrapper.from_mapping({"details": {}}, ignore_extra=ignore_extra)

    with pytest.raises(ValidationError, match="Value for 'code' is required"):
        Wrapper.from_mapping({"details": {"extra": 2}}, ignore_extra=True)


def test_typed_dict_in_an_attribute_of_another_kind() -> None:
    class Many(State):
        items: Sequence[Details]
        lookup: Mapping[str, Details]
        maybe: Details

    clean: dict[str, Any] = {
        "items": [{"code": 1}],
        "lookup": {"a": {"code": 2}},
        "maybe": {"code": 3},
    }
    dirty: dict[str, Any] = {
        "items": [{"code": 1, "x": 1}],
        "lookup": {"a": {"code": 2, "y": 2}},
        "maybe": {"code": 3, "z": 3},
    }
    assert Many.from_mapping(dirty, ignore_extra=True) == Many.from_mapping(clean)
    assert Many.from_json(json.dumps(dirty), ignore_extra=True) == Many.from_mapping(clean)
    for path in ("items", "lookup", "maybe"):
        broken: dict[str, Any] = {**clean, path: dirty[path]}
        with pytest.raises(ValidationError, match="Unexpected key"):
            Many.from_mapping(broken)

        with pytest.raises(ValueError, match="Unexpected key"):
            Many.from_json(json.dumps(broken))

        assert Many.from_mapping(broken, ignore_extra=True) == Many.from_mapping(clean)


def test_typed_dict_reads_a_mapping_which_is_not_a_dict() -> None:
    class Own(Mapping[str, Any]):
        def __init__(self, entries: dict[str, Any]) -> None:
            self.entries = entries

        def __getitem__(self, key: str) -> Any:
            return self.entries[key]

        def __iter__(self) -> Iterator[str]:
            return iter(self.entries)

        def __len__(self) -> int:
            return len(self.entries)

    assert Wrapper.from_mapping({"details": Own({"code": 1})}).details == {"code": 1}
    assert Wrapper.from_mapping({"details": Map({"code": 1})}).details == {"code": 1}
    with pytest.raises(ValidationError, match="Unexpected key 'extra' for Details"):
        Wrapper.from_mapping({"details": Own({"code": 1, "extra": 2})})

    with pytest.raises(ValidationError, match="Unexpected key 'extra' for Details"):
        Wrapper.from_mapping({"details": Map({"code": 1, "extra": 2})})

    assert Wrapper.from_mapping(
        {"details": Own({"code": 1, "extra": 2})}, ignore_extra=True
    ).details == {"code": 1}
    with pytest.raises(ValidationError):
        Wrapper.from_mapping({"details": [("code", 1)]})


def test_typed_dict_with_non_string_keys_is_refused() -> None:
    with pytest.raises(ValidationError, match="Unexpected key '3' for Details"):
        Wrapper.from_mapping({"details": {"code": 1, 3: "x"}})

    assert Wrapper.from_mapping({"details": {"code": 1, 3: "x"}}, ignore_extra=True).details == {
        "code": 1
    }


class ClosedWrapper(State):
    details: ClosedDetails


class OpenWrapper(State):
    details: OpenDetails


@pytest.mark.parametrize("cls", [ClosedWrapper, OpenWrapper])
def test_pep_728_typed_dicts_refuse_undeclared_keys_too(cls: Any) -> None:
    # `extra_items` is not validated - what is undeclared is refused, or dropped on request
    assert cls.from_mapping({"details": {"code": 1}}).details == {"code": 1}
    with pytest.raises(ValidationError, match="Unexpected key 'extra' for"):
        cls.from_mapping({"details": {"code": 1, "extra": 2}})

    with pytest.raises(ValueError, match="Unexpected key 'extra' for"):
        cls.from_json('{"details": {"code": 1, "extra": 2}}')

    assert cls.from_mapping({"details": {"code": 1, "extra": 2}}, ignore_extra=True).details == {
        "code": 1
    }
    assert cls.from_json('{"details": {"code": 1, "extra": 2}}', ignore_extra=True).details == {
        "code": 1
    }


# a class constructing or validating itself


def test_custom_init_is_not_given_extra_keys() -> None:
    assert Customized.from_mapping({"first": 1, "extra": 2}, ignore_extra=True) == Customized(1)
    assert Customized.from_json('{"extra": 2, "first": 1}', ignore_extra=True) == Customized(1)
    assert Customized.from_json_array('[{"extra": 2, "first": 1}]', ignore_extra=True) == (
        Customized(1),
    )
    with pytest.raises(TypeError, match="Unexpected attribute 'extra' for Customized"):
        Customized.from_mapping({"first": 1, "extra": 2})


def test_custom_init_nested_with_a_mapping_which_is_not_a_dict() -> None:
    class Parent(State):
        child: Customized

    payload: Any = {"child": MappingProxyType({"first": 1, "extra": 2})}
    assert Parent.from_mapping(payload, ignore_extra=True) == Parent(child=Customized(1))
    with pytest.raises(ValidationError):
        Parent.from_mapping(payload)


def test_class_validating_itself_is_handed_the_value_unchanged() -> None:
    received.clear()
    with pytest.raises(TypeError, match="Unexpected attribute 'extra' for Validating"):
        Validating.from_mapping({"value": 1, "extra": 2}, ignore_extra=True)

    assert received == [{"value": 1, "extra": 2}]

    class Parent(State):
        child: Validating

    received.clear()
    with pytest.raises(ValidationError, match="Unexpected attribute 'extra' for Validating"):
        Parent.from_mapping({"child": {"value": 1, "extra": 2}}, ignore_extra=True)

    assert received == [{"value": 1, "extra": 2}]

    received.clear()
    assert Parent.from_json('{"child": {"value": 1}}', ignore_extra=True).child.value == 1
    assert received == [{"value": 1}]


# the flag is what one decode was asked for, nothing else


def _decode_inner(value: Any) -> Leaf:
    return Leaf.from_mapping(value)


class Guarded(State):
    leaf: Annotated[Leaf, Validator(_decode_inner)]


@pytest.mark.parametrize(
    "decode",
    [_from_mapping, _from_json, _from_json_decoder, _from_json_array],
)
def test_flag_is_not_inherited_by_validators_decoding_on_their_own(
    decode: Callable[[Any, Any, bool], Any],
) -> None:
    assert decode(Guarded, {"leaf": {"value": 1}}, True) == Guarded(leaf=Leaf(value=1))
    with pytest.raises(FAILURES, match="Unexpected attribute 'extra' for Leaf"):
        decode(Guarded, {"leaf": {"value": 1, "extra": 2}}, True)

    # known keys of the validated value are not affected
    with pytest.raises(FAILURES, match="Unexpected attribute 'extra' for Guarded"):
        decode(Guarded, {"leaf": {"value": 1}, "extra": 2}, False)

    assert decode(Guarded, {"leaf": {"value": 1}, "extra": 2}, True) == Guarded(leaf=Leaf(value=1))


def test_flag_is_not_carried_over_between_decodes() -> None:
    dirty: dict[str, Any] = {"value": 1, "extra": 2}
    assert Leaf.from_mapping(dirty, ignore_extra=True) == Leaf(value=1)
    with pytest.raises(TypeError):
        Leaf.from_mapping(dirty)

    with pytest.raises(TypeError):
        Leaf.validate(dirty)

    assert Leaf.from_json(json.dumps(dirty), ignore_extra=True) == Leaf(value=1)
    with pytest.raises(ValueError):
        Leaf.from_json(json.dumps(dirty))

    # after a flagged decode which failed
    with pytest.raises(ValidationError):
        Leaf.from_mapping({"value": "x", "extra": 2}, ignore_extra=True)

    with pytest.raises(TypeError):
        Leaf.from_mapping(dirty)

    with pytest.raises(ValueError):
        Leaf.from_json('{"value": 1, "extra": [1,}', ignore_extra=True)

    with pytest.raises(ValueError):
        Leaf.from_json(json.dumps(dirty))

    with pytest.raises(ValueError):
        Leaf.from_json_array(json.dumps([dirty]))

    assert Leaf.from_json_array(json.dumps([dirty]), ignore_extra=True) == (Leaf(value=1),)


def test_validating_with_a_flag_does_not_change_the_class() -> None:
    Holder.from_mapping(SCENARIOS["nested_state"][2], ignore_extra=True)
    with pytest.raises(ValidationError):
        Holder.from_mapping(SCENARIOS["nested_state"][1] | {"leaf": {"value": 1, "x": 1}})


# a mapping given in a form validation reads differently


@pytest.mark.parametrize("ignore_extra", [False, True])
def test_instance_given_for_a_state_is_kept(ignore_extra: bool) -> None:
    leaf: Leaf = Leaf(value=1)
    holder: Holder = Holder.from_mapping({"name": "n", "leaf": leaf}, ignore_extra=ignore_extra)
    assert holder.leaf is leaf
    instance: Any = leaf
    assert Leaf.from_mapping(instance, ignore_extra=ignore_extra) is leaf


def test_ignoring_extra_with_more_attributes_than_the_fast_path_holds() -> None:
    class Wide(State):
        a0: int = 0
        a1: int = 0
        a2: int = 0
        a3: int = 0
        a4: int = 0
        a5: int = 0
        a6: int = 0
        a7: int = 0
        a8: int = 0
        a9: int = 0
        a10: int = 0
        a11: int = 0
        a12: int = 0
        a13: int = 0
        a14: int = 0
        a15: int = 0
        a16: int = 0
        a17: int = 0
        a18: int = 0
        a19: int = 0
        a20: int = 0
        a21: int = 0
        a22: int = 0
        a23: int = 0
        a24: int = 0
        a25: int = 0
        a26: int = 0
        a27: int = 0
        a28: int = 0
        a29: int = 0
        a30: int = 0
        a31: int = 0
        a32: int = 0
        a33: int = 0
        a34: int = 0
        a35: int = 0
        a36: int = 0
        a37: int = 0
        a38: int = 0
        a39: int = 0

    known: dict[str, Any] = {f"a{index}": index for index in range(40)}
    extras: dict[str, Any] = {f"x{index}": index for index in range(100)}
    assert Wide.from_mapping({**extras, **known}, ignore_extra=True) == Wide(**known)
    assert Wide.from_mapping({**known, **extras}, ignore_extra=True) == Wide(**known)
    assert Wide.from_json(json.dumps({**extras, **known}), ignore_extra=True) == Wide(**known)
    with pytest.raises(TypeError, match="Unexpected attribute 'x0' for Wide"):
        Wide.from_mapping({**known, **extras})

    # more entries than attributes, and a small class
    assert Leaf.from_mapping(
        {**extras, "value": 1} | {f"y{index}": 0 for index in range(100)}, ignore_extra=True
    ) == Leaf(value=1)


# parity with the standard library, over documents carrying values which are not read

_FRAGMENTS: Sequence[str] = (
    "{}",
    "[]",
    "[1, 2.5e3, -0, true, false, null]",
    '{"a": {"b": [{"c": "d"}]}}',
    '"a \\"quoted\\" \\\\ \\u00e9 \\ud83d\\ude00 string"',
    '"é😀"',
    "NaN",
    "-Infinity",
    "12345678901234567890123",
    "1e5",
    '[[[[{"deep": [[]]}]]]]',
)
_NOISE: str = '{}[]",:0123456789-+.eEtrufalsnNIy\\u \n' + "abcxyzé😀"


def _noisy(rng: random.Random, document: str) -> str:
    characters: list[str] = list(document)
    for _ in range(rng.randint(1, 3)):
        at: int = rng.randrange(len(characters) + 1)
        if rng.random() < 0.4 and characters:
            del characters[min(at, len(characters) - 1)]

        else:
            characters.insert(at, rng.choice(_NOISE))

    return "".join(characters)


@pytest.mark.parametrize("seed", range(4))
def test_skipping_reads_what_the_standard_library_reads(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(600):
        extras: str = ", ".join(
            f'"extra{index}": {rng.choice(_FRAGMENTS)}' for index in range(rng.randint(1, 3))
        )
        document: str = (
            '{"name": "n", '
            + extras
            + ', "leaf": {"value": 1, '
            + extras.replace("extra", "other")
            + '}, "leaves": [{"value": 2, '
            + extras
            + "}]}"
        )
        if rng.random() < 0.7:
            document = _noisy(rng, document)

        expected: Holder | None
        try:
            expected = Holder.from_mapping(json.loads(document), ignore_extra=True)

        except Exception:
            expected = None

        native: Any = nstruct.from_json(Holder, document, False, True)
        if native is not None:
            assert expected is not None, document
            assert native.to_json() == expected.to_json(), document

        try:
            read: Holder | None = Holder.from_json(document, ignore_extra=True)

        except ValueError:
            read = None

        assert (read is None) == (expected is None), document
        if read is not None and expected is not None:
            assert read.to_json() == expected.to_json(), document


# what fails while the keys are read is not turned into a refusal


def test_typed_dict_keys_which_fail_to_be_read_raise_their_own_error() -> None:
    class Unreadable(Mapping[str, Any]):
        def __getitem__(self, key: str) -> Any:
            raise KeyError(key)

        def __iter__(self) -> Iterator[str]:
            raise RuntimeError("no keys")

        def __len__(self) -> int:
            return 1

    class Incomparable:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("no comparison")

        def __hash__(self) -> int:
            return 1

    with pytest.raises(ValidationError, match="no keys"):
        Wrapper.from_mapping({"details": Unreadable()})

    with pytest.raises(ValidationError, match="no comparison"):
        Wrapper.from_mapping({"details": {"code": 1, Incomparable(): 2}})

    assert Wrapper.from_mapping(
        {"details": {"code": 1, Incomparable(): 2}}, ignore_extra=True
    ).details == {"code": 1}


class Unique(State):
    leaves: frozenset[Leaf]


type Tree = Leaf | Sequence[Tree]


class Rooted(State):
    tree: Tree


def test_extras_are_ignored_in_sets_and_recursive_aliases() -> None:
    assert Unique.from_mapping(
        {"leaves": [{"value": 1, "x": 1}, {"value": 2}]}, ignore_extra=True
    ) == Unique(leaves=frozenset({Leaf(value=1), Leaf(value=2)}))
    with pytest.raises(ValidationError, match="Unexpected attribute 'x' for Leaf"):
        Unique.from_mapping({"leaves": [{"value": 1, "x": 1}]})

    payload: dict[str, Any] = {"tree": [{"value": 1, "x": 1}, [{"value": 2, "y": 2}]]}
    expected: Rooted = Rooted.from_mapping({"tree": [{"value": 1}, [{"value": 2}]]})
    assert Rooted.from_mapping(payload, ignore_extra=True) == expected
    assert Rooted.from_json(json.dumps(payload), ignore_extra=True) == expected
    with pytest.raises(ValidationError):
        Rooted.from_mapping(payload)


# the keys of a TypedDict are read from a snapshot, flag or not


class Multiplying(dict[str, Any]):
    def __getitem__(self, key: str) -> Any:
        return super().__getitem__(key) * 2


@pytest.mark.parametrize("ignore_extra", [False, True])
def test_typed_dict_reads_the_same_values_with_and_without_the_flag(ignore_extra: bool) -> None:
    read: Wrapper = Wrapper.from_mapping(
        {"details": Multiplying(code=3)},
        ignore_extra=ignore_extra,
    )
    unflagged: Wrapper = Wrapper.from_mapping({"details": Multiplying(code=3)})
    assert read.details == {"code": 3}
    assert unflagged.details == {"code": 3}


def test_typed_dict_is_read_whole_before_the_values_are_validated() -> None:
    changed: list[str] = []

    def change(value: Any) -> Any:
        details["note"] = "late"
        changed.append("done")
        return value

    class Validated(TypedDict):
        code: Annotated[int, Validator(change)]
        note: NotRequired[str]

    class Reading(State):
        details: Validated

    details: dict[str, Any] = {"code": 1}
    for ignore_extra in (False, True):
        details.clear()
        details["code"] = 1
        assert Reading.from_mapping({"details": details}, ignore_extra=ignore_extra).details == {
            "code": 1
        }

    assert changed == ["done", "done"]


# the digits of a skipped integer are limited as the ones of a read one


@pytest.mark.parametrize(
    "decode",
    [_from_json_text, _from_json_text_decoder],
    ids=["native", "decoder"],
)
@pytest.mark.parametrize(
    ("limit", "digits", "accepted"),
    [
        (4300, 5000, False),
        (640, 641, False),
        (0, 5000, True),
        (6000, 5000, True),
        (4300, 4300, True),
        (4300, 600, True),
    ],
    ids=["above", "least-limit", "none", "raised", "at-limit", "short"],
)
def test_skipped_integers_are_limited_as_the_standard_library_limits_them(
    decode: Callable[[str], Any],
    limit: int,
    digits: int,
    accepted: bool,
) -> None:
    documents: tuple[str, str] = (
        '{"value": 1, "extra": ' + "9" * digits + "}",
        '{"value": 1, "extra": [' + "-" + "9" * digits + "]}",
    )
    incoming: int = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(limit)
    try:
        for text in documents:
            if accepted:
                assert decode(text) == Leaf(value=1)

            else:
                with pytest.raises(ValueError):
                    decode(text)

    finally:
        sys.set_int_max_str_digits(incoming)


@pytest.mark.parametrize(
    ("digits", "read"),
    [(600, True), (640, True), (641, False), (5000, False)],
)
def test_skipped_integers_over_the_least_limit_are_left_to_the_standard_library(
    digits: int,
    read: bool,
) -> None:
    # the limit is not asked for, only the least one there can be is told apart
    document: str = '{"value": 1, "extra": ' + "9" * digits + "}"
    expected: Leaf | None = Leaf(value=1) if read else None
    assert nstruct.from_json(Leaf, document, False, True) == expected


def test_skipped_long_integer_with_an_invalid_known_value_is_an_error() -> None:
    document: str = '{"value": "bad", "x": ' + "9" * 5000 + "}"
    with pytest.raises(ValueError) as native:
        Leaf.from_json(document, ignore_extra=True)

    with pytest.raises(ValueError) as decoded:
        Leaf.from_json(document, Reading, ignore_extra=True)

    assert type(native.value.__cause__) is type(decoded.value.__cause__)
    assert nstruct.from_json(Leaf, document, False, True) is None


def test_skipped_floats_and_short_integers_have_no_such_limit() -> None:
    document: str = '{"value": 1, "extra": ' + "9" * 600 + ', "more": 1.' + "9" * 5000 + "}"
    assert nstruct.from_json(Leaf, document, False, True) == Leaf(value=1)


# a class validating itself is validated by it - whichever way it is read


class Owning(State):
    value: int

    @classmethod
    def validate(cls, value: Any) -> Owning:
        received.append(value)
        return super().validate(value)


@pytest.mark.parametrize("ignore_extra", [False, True])
def test_json_is_validated_by_a_class_validating_itself(ignore_extra: bool) -> None:
    document: str = '{"value": 1}'
    results: list[Any] = []
    for decode in (
        lambda: Owning.from_json(document, ignore_extra=ignore_extra),
        lambda: Owning.from_json(document, Reading, ignore_extra=ignore_extra),
        lambda: Owning.from_mapping(json.loads(document), ignore_extra=ignore_extra),
        lambda: Owning.from_json_array("[" + document + "]", ignore_extra=ignore_extra)[0],
        lambda: Owning.from_json_array("[" + document + "]", Reading, ignore_extra=ignore_extra)[0],
    ):
        received.clear()
        results.append(decode())
        assert received == [{"value": 1}]

    assert results == [Owning(value=1)] * 5


@pytest.mark.parametrize("ignore_extra", [False, True])
def test_json_with_extras_is_given_to_a_class_validating_itself_as_it_is(
    ignore_extra: bool,
) -> None:
    document: str = '{"value": 1, "extra": 2}'
    for decode in (
        lambda: Owning.from_json(document, ignore_extra=ignore_extra),
        lambda: Owning.from_json(document, Reading, ignore_extra=ignore_extra),
        lambda: Owning.from_mapping(json.loads(document), ignore_extra=ignore_extra),
        lambda: Owning.from_json_array("[" + document + "]", ignore_extra=ignore_extra),
    ):
        received.clear()
        with pytest.raises(FAILURES, match="Unexpected attribute 'extra' for Owning"):
            decode()

        assert received == [{"value": 1, "extra": 2}]


def test_class_validating_itself_is_validated_by_it_when_nested_in_json() -> None:
    class Parent(State):
        child: Owning
        children: Sequence[Owning] = ()

    received.clear()
    parent: Parent = Parent.from_json(
        '{"child": {"value": 1}, "children": [{"value": 2}]}', ignore_extra=True
    )
    assert parent.children == (Owning(value=2),)
    assert received == [{"value": 1}, {"value": 2}]


# a TypedDict which is not accepted is not an instance


def test_check_refuses_undeclared_keys_of_a_typed_dict() -> None:
    spec: Any = Wrapper.__FIELDS__[0].spec
    assert nstruct.check(spec, {"code": 1})
    assert nstruct.check(spec, Map({"code": 1, "note": "n"}))
    assert not nstruct.check(spec, {"code": 1, "extra": 2})
    assert not nstruct.check(spec, Map({"code": 1, "extra": 2}))
    assert not nstruct.check(spec, {"code": 1, 3: 2})
    assert not nstruct.check(spec, {"note": "n"})
    assert not nstruct.check(spec, 3)


def test_specialization_check_goes_through_the_typed_dict_check() -> None:
    details_box: Any = Box[Details]
    leaf_box: Any = Box[Leaf]
    assert isinstance(Box[Details](item={"code": 1}), details_box)
    assert not isinstance(Box[Details](item={"code": 1}), leaf_box)


# nothing under a class deciding for itself is given the flag


class Inner(State):
    value: int


class InitOwner(State):
    inner: Inner

    def __init__(self, inner: Inner) -> None:
        super().__init__(inner=inner)


class ValidateOwner(State):
    inner: Inner

    @classmethod
    def validate(cls, value: Any) -> ValidateOwner:
        return super().validate(value)


class Outside(State):
    init_owner: InitOwner | None = None
    validate_owner: ValidateOwner | None = None
    owners: Sequence[InitOwner | ValidateOwner] = ()


BOUNDARIES: dict[str, tuple[Any, Any]] = {
    "init_top": (InitOwner, {"inner": {"value": 1, "x": 1}}),
    "validate_top": (ValidateOwner, {"inner": {"value": 1, "x": 1}}),
    "init_nested": (Outside, {"init_owner": {"inner": {"value": 1, "x": 1}}}),
    "validate_nested": (Outside, {"validate_owner": {"inner": {"value": 1, "x": 1}}}),
    "sequence": (Outside, {"owners": [{"inner": {"value": 1, "x": 1}}]}),
}


@pytest.mark.parametrize(("cls", "payload"), BOUNDARIES.values(), ids=BOUNDARIES.keys())
@entry_points
def test_extras_below_a_class_deciding_for_itself_are_refused(
    decode: Callable[[Any, Any, bool], Any],
    cls: Any,
    payload: Any,
) -> None:
    for ignore_extra in (False, True):
        with pytest.raises(FAILURES) as exc:
            decode(cls, payload, ignore_extra)

        assert "Unexpected attribute 'x' for Inner" in _texts(exc.value)


@pytest.mark.parametrize("decode", [_from_mapping, _from_json, _from_json_decoder])
def test_extras_beside_a_class_deciding_for_itself_are_still_ignored(
    decode: Callable[[Any, Any, bool], Any],
) -> None:
    assert decode(Outside, {"extra": 1, "init_owner": {"inner": {"value": 1}}}, True) == Outside(
        init_owner=InitOwner(inner=Inner(value=1))
    )
    assert decode(InitOwner, {"inner": {"value": 1}, "extra": 1}, True) == InitOwner(
        inner=Inner(value=1)
    )


class ValidatingMixin(State):
    @classmethod
    def validate(cls, value: Any) -> Any:
        received.append(value)
        return super().validate(value)


class Mixed(ValidatingMixin):
    value: int


class Nothing(ValidatingMixin):
    pass


class GenericValidating[Value](State):
    value: Value

    @classmethod
    def validate(cls, value: Any) -> Any:
        received.append(value)
        return super().validate(value)


@pytest.mark.parametrize(
    ("cls", "document", "expected"),
    [
        (Mixed, '{"value": 1}', Mixed(value=1)),
        (Nothing, "{}", Nothing()),
        (GenericValidating[int], '{"value": 1}', GenericValidating[int](value=1)),
    ],
    ids=["inherited_validate", "no_attributes", "specialization"],
)
@pytest.mark.parametrize("ignore_extra", [False, True])
def test_a_validate_which_is_inherited_or_has_no_attributes_is_called_for_json(
    cls: Any,
    document: str,
    expected: Any,
    ignore_extra: bool,
) -> None:
    received.clear()
    assert cls.from_json(document, ignore_extra=ignore_extra) == expected
    assert len(received) == 1
    received.clear()
    assert cls.from_json_array(f"[{document}]", ignore_extra=ignore_extra) == (expected,)
    assert len(received) == 1


# a TypedDict is read once, from a copy


class Counting(Mapping[str, Any]):
    def __init__(self, entries: dict[str, Any]) -> None:
        self.entries = entries
        self.reads: list[str] = []

    def __getitem__(self, key: str) -> Any:
        self.reads.append(key)
        return self.entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.entries)

    def __len__(self) -> int:
        return len(self.entries)


class Choosing(State):
    pick: Details | Mapping[str, Any]


class Maybe(State):
    pick: Details | None = None


def test_typed_dict_check_does_not_change_or_reread_what_it_is_given() -> None:
    given: defaultdict[str, Any] = defaultdict(list, {"code": 1})
    Choosing.from_mapping({"pick": given})
    assert dict(given) == {"code": 1}

    with pytest.raises(ValidationError):
        Maybe.from_mapping({"pick": defaultdict(list, {"note": "n"})})

    missing: defaultdict[str, Any] = defaultdict(int)
    with pytest.raises(ValidationError, match="Value for 'code' is required"):
        Wrapper.from_mapping({"details": missing})

    assert dict(missing) == {}

    counting: Counting = Counting({"code": 1, "note": "n"})
    assert Wrapper.from_mapping({"details": counting}).details == {"code": 1, "note": "n"}
    assert sorted(counting.reads) == ["code", "note"]

    checked: Counting = Counting({"code": 1, "note": "n"})
    assert nstruct.check(Wrapper.__FIELDS__[0].spec, checked)
    assert sorted(checked.reads) == ["code", "note"]


# the first alternative which takes the value as it is wins, with or without the flag


@pytest.mark.parametrize("ignore_extra", [False, True])
@pytest.mark.parametrize(
    "decode",
    [_from_mapping, _from_json, _from_json_decoder],
)
def test_alternative_accepting_the_value_as_it_is_wins_over_a_typed_dict(
    decode: Callable[[Any, Any, bool], Any],
    ignore_extra: bool,
) -> None:
    assert decode(Choosing, {"pick": {"code": 1, "x": 2}}, ignore_extra).pick == {"code": 1, "x": 2}
    assert decode(Choosing, {"pick": {"code": 1}}, ignore_extra).pick == {"code": 1}


# the elements of an open tuple are validated natively, with the flag


OPEN_TUPLES: tuple[str, ...] = (
    "open_tuple_leading",
    "open_tuple_middle",
    "open_tuple_trailing",
    "open_tuple_unspecialized",
)


@pytest.mark.parametrize(
    ("cls", "clean", "dirty"),
    [SCENARIOS[name] for name in OPEN_TUPLES],
    ids=OPEN_TUPLES,
)
def test_open_tuple_is_read_natively_with_the_flag(
    cls: Any,
    clean: Any,
    dirty: Any,
) -> None:
    read: Any = nstruct.from_json(cls, json.dumps(dirty), False, True)
    assert read is not None
    assert read == cls.from_mapping(clean)
    assert nstruct.from_json(cls, json.dumps(dirty), False, False) is None  # declined

    # a position which is not an object is still refused
    with pytest.raises(FAILURES):
        cls.from_mapping({"items": [1, 2]}, ignore_extra=True)


def test_typed_dict_check_raises_what_comparing_a_key_raises() -> None:
    class Incomparable:
        def __eq__(self, other: object) -> bool:
            raise RuntimeError("no comparison")

        def __hash__(self) -> int:
            return 1

    with pytest.raises(RuntimeError, match="no comparison"):
        nstruct.check(Wrapper.__FIELDS__[0].spec, {"code": 1, Incomparable(): 2})


# a bare tuple unpacked is any number of anything


_BARE_TUPLE: Any = tuple
_BARE_TYPING_TUPLE: Any = typing.Tuple  # noqa: UP006

# declared by their annotations as values - a bare tuple is not a type argument for pyright
BareMiddle: Any = type(
    "BareMiddle",
    (State,),
    {"__annotations__": {"items": tuple[int, Unpack[_BARE_TUPLE], str]}},  # noqa: UP044
)
BareTyping: Any = type(
    "BareTyping",
    (State,),
    {"__annotations__": {"items": tuple[int, Unpack[_BARE_TYPING_TUPLE], str]}},  # noqa: UP044
)


class EmptyMiddle(State):
    items: tuple[int, Unpack[tuple[()]], str]  # noqa: UP044 - the spelling under test


@pytest.mark.parametrize("cls", [BareMiddle, BareTyping], ids=["tuple", "typing.Tuple"])
@pytest.mark.parametrize(
    "items",
    [(1, "x"), (1, 2, "x"), (1, None, 2.5, "y", "x")],
)
def test_bare_unpacked_tuple_stands_for_any_number_of_anything(
    cls: Any,
    items: tuple[Any, ...],
) -> None:
    assert cls(items=items).items == items
    assert cls.from_mapping({"items": list(items)}).items == items
    assert cls.from_json(json.dumps({"items": list(items)})).items == items


@pytest.mark.parametrize("cls", [BareMiddle, BareTyping], ids=["tuple", "typing.Tuple"])
@pytest.mark.parametrize("items", [(1,), ("x", 2, "x"), (1, 2, 3)])
def test_bare_unpacked_tuple_keeps_the_elements_around_it(
    cls: Any,
    items: tuple[Any, ...],
) -> None:
    with pytest.raises(FAILURES):
        cls(items=items)


@pytest.mark.parametrize(
    ("items", "valid"),
    [((1, "x"), True), ((1, 2, "x"), False), ((1,), False)],
)
def test_unpacked_empty_tuple_stays_an_empty_fixed_part(
    items: tuple[Any, ...],
    valid: bool,
) -> None:
    if valid:
        assert EmptyMiddle(items=items).items == items

    else:
        with pytest.raises(FAILURES):
            EmptyMiddle(items=items)


# a tuple of the alias it is within


type UnpackedTree = int | tuple[*tuple[UnpackedTree, ...]]
type PlainTree = int | tuple[PlainTree, ...]


class UnpackedTreeHolder(State):
    tree: UnpackedTree


class PlainTreeHolder(State):
    tree: PlainTree


@pytest.mark.parametrize("cls", [UnpackedTreeHolder, PlainTreeHolder], ids=["unpacked", "plain"])
@pytest.mark.parametrize("tree", [1, (), (1, (2,)), (1, ((2, (3,)),))])
def test_tuple_of_a_recursive_alias_is_resolved(cls: Any, tree: Any) -> None:
    assert cls(tree=tree).tree == tree
    assert cls.from_json(json.dumps({"tree": tree})).tree == tree


@pytest.mark.parametrize("cls", [UnpackedTreeHolder, PlainTreeHolder], ids=["unpacked", "plain"])
@pytest.mark.parametrize("tree", ["x", (1, "x"), (1, (2, ("x",)))])
def test_tuple_of_a_recursive_alias_validates_its_elements(cls: Any, tree: Any) -> None:
    with pytest.raises(FAILURES):
        cls(tree=tree)
