import inspect
import json
import math
import re
from collections.abc import Hashable, Mapping, Sequence, Set, Sized
from datetime import UTC, date, datetime, time, timedelta
from enum import Enum, IntEnum, StrEnum
from pathlib import Path
from types import GenericAlias, new_class
from typing import Annotated, Any, ForwardRef, Literal, NamedTuple, NotRequired, TypedDict, cast
from uuid import UUID

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from haiway import Alias, Default, Missing, State, Validator, Verifier
from haiway.attributes.specification import unique_anchors
from tests.strict_json import strict_loads


class Color(StrEnum):
    RED = "red"
    BLUE = "blue"


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class Aliased(StrEnum):
    RED = "red"
    CRIMSON = "red"


class AliasedLevel(IntEnum):
    LOW = 1
    MINIMUM = 1
    HIGH = 2


class Blank(StrEnum):
    EMPTY = ""


class Mode(Enum):
    EMPTY = ""
    FULL = "full"


type Maybe = int | Missing


def refuse_missing(value: Any) -> Any:
    if value is Missing:
        raise ValueError("Missing is refused")

    return value


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
        (Literal["a", "b", Missing], {"type": "string", "enum": ["a", "b"]}),
        (Literal["a", 1, Missing], {"enum": ["a", 1]}),
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
        # a verifier may refuse Missing, wherever it sits
        (Annotated[int | Missing, Verifier(refuse_missing)], NO_DEFAULT),
        (int | Annotated[Missing, Verifier(refuse_missing)], NO_DEFAULT),
        (Annotated[Maybe, Verifier(refuse_missing)], NO_DEFAULT),
        (Annotated[Annotated[int | Missing, Verifier(refuse_missing)], "layer"], NO_DEFAULT),
        (Annotated[Any, Verifier(refuse_missing)], NO_DEFAULT),
        (Annotated[int | Missing, Validator(refuse_missing)], NO_DEFAULT),
        (int | Annotated[Missing, Validator(refuse_missing)], NO_DEFAULT),
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


@pytest.mark.parametrize(
    "default",
    [
        pytest.param(Missing, id="missing"),
        pytest.param(Default(Missing), id="default of missing"),
    ],
)
def test_default_of_missing_refused_by_the_annotation_is_reported_as_such(default: Any) -> None:
    with pytest.raises(TypeError, match=r"defaults to Missing.*\| Missing"):
        state_of(int, default=default)


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
        (
            int,
            ["0", "1", "-1", "250"],
            ["01", "+1", " 1", "1_000", chr(0x661), "-0", "x", "", "a1", "-", "--1"],
        ),
        (
            float,
            ["0", "1", "-1.5", "1e5", "2.5E-3", "inf", "-inf", "nan"],
            ["NaN", "Infinity", "+inf", "+1", ".5", "1.", "01", " 1", ""],
        ),
        (bool, ["true", "false"], ["True", "TRUE", "False", "1", "yes", "", "x"]),
        (None, ["null"], ["None", "", "x"]),
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


@pytest.mark.parametrize("key", [str, Any, bytes])
def test_mapping_keys_without_a_spelling_are_not_constrained(key: Any) -> None:
    assert "propertyNames" not in property_of(mapping_of(key))


@pytest.mark.parametrize(
    ("annotation", "member", "name", "values", "keys"),
    [
        (Aliased, "red", "CRIMSON", ["red"], ["red"]),
        (AliasedLevel, 1, "MINIMUM", [1, 2], ["1", "2"]),
    ],
)
def test_names_of_enum_members_are_read_but_not_named_by_the_schema(
    annotation: Any,
    member: Any,
    name: str,
    values: Sequence[Any],
    keys: Sequence[str],
) -> None:
    assert property_of(annotation)["enum"] == values
    assert property_of(mapping_of(annotation))["propertyNames"]["enum"] == keys
    assert state_of(annotation).from_json(json.dumps({"v": name})).v == member
    assert state_of(mapping_of(annotation)).from_json(json.dumps({"v": {name: "x"}})).v == {
        member: "x"
    }


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


# 4. Anchors of recursive types name one subschema of the whole document


type _Nested = int | Sequence[_Nested]
type _NestedInObjects = int | Sequence[Mapping[str, _NestedInObjects]]


class _Tree(State):
    children: Sequence[_Tree] = ()


class _Boxed[T](State):
    value: T


def _anchored(node: Any, found: dict[str, list[Any]]) -> dict[str, list[Any]]:
    if isinstance(node, dict):
        entries: dict[str, Any] = cast(dict[str, Any], node)
        if isinstance(anchor := entries.get("$anchor"), str):
            found.setdefault(anchor, []).append(entries)

        for value in entries.values():
            _anchored(value, found)

    elif isinstance(node, list):
        for element in cast(list[Any], node):
            _anchored(element, found)

    return found


def _references(node: Any, found: list[str]) -> list[str]:
    if isinstance(node, dict):
        entries: dict[str, Any] = cast(dict[str, Any], node)
        if isinstance(reference := entries.get("$ref"), str):
            found.append(reference.removeprefix("#"))

        for value in entries.values():
            _references(value, found)

    elif isinstance(node, list):
        for element in cast(list[Any], node):
            _references(element, found)

    return found


def _recursive_item(module: str, attribute: Any) -> Any:
    name: str = "Item"

    def body(namespace: dict[str, Any]) -> None:
        namespace["__module__"] = module
        # the class refers to itself by its name, as the one declared in a module would
        namespace["__annotations__"] = {"value": attribute, "parts": Sequence[ForwardRef(name)]}
        namespace["parts"] = ()

    item: Any = new_class(name, (State,), {}, body)
    return item


def test_anchors_are_unique_and_every_reference_resolves() -> None:
    first: Any = _recursive_item("first", str)
    second: Any = _recursive_item("second", int)

    class Holder(State):
        nested: _Nested
        nested_in_objects: _NestedInObjects
        tree: _Tree
        boxed: _Boxed[_Tree]
        trees: Sequence[_Tree]
        first_item: first
        second_item: second

    schema: Any = json.loads(cast(str, Holder.json_schema()))
    anchors: dict[str, list[Any]] = _anchored(schema, {})
    assert all(len(nodes) == 1 for nodes in anchors.values()), anchors.keys()
    assert set(_references(schema, [])) <= set(anchors)
    # each alias and class keeps its own subschema rather than reaching another one
    properties: Any = schema["properties"]
    assert properties["nested"]["$anchor"] != properties["nested_in_objects"]["$anchor"]
    assert properties["first_item"]["$anchor"] != properties["second_item"]["$anchor"]
    tree: dict[str, str] = {"$ref": f"#{properties['tree']['$anchor']}"}
    assert properties["boxed"]["properties"]["value"] == tree
    assert properties["trees"]["items"] == tree


def test_anchored_definitions_told_apart_by_bool_and_int_are_not_merged() -> None:
    with_true: Any = _recursive_item("first", Literal[True, "o"])
    with_one: Any = _recursive_item("second", Literal[1, "o"])

    class Holder(State):
        true_item: with_true
        one_item: with_one

    schema: Any = json.loads(cast(str, Holder.json_schema()))
    properties: Any = schema["properties"]
    assert "$ref" not in properties["one_item"]
    assert properties["true_item"]["$anchor"] != properties["one_item"]["$anchor"]


def _anchored_object(anchor: str, marker: int) -> Any:
    return {"$anchor": anchor, "const": marker, "properties": {"parts": {"$ref": f"#{anchor}"}}}


def test_generated_anchor_names_do_not_collide_with_taken_ones() -> None:
    result: Any = unique_anchors(
        cast(
            Any,
            {
                "anyOf": [
                    _anchored_object("Item", 1),
                    _anchored_object("Item", 2),
                    _anchored_object("Item_2", 3),
                ]
            },
        )
    )

    anchors: dict[str, list[Any]] = _anchored(result, {})
    # the name given once keeps it, the repeated one takes a name nothing spells
    assert sorted(anchors) == ["Item", "Item_2", "Item_3"]
    assert result["anyOf"][2]["$anchor"] == "Item_2"
    assert all(len(nodes) == 1 for nodes in anchors.values())
    # the references within each definition lead to that definition
    for node in result["anyOf"]:
        assert node["properties"]["parts"] == {"$ref": f"#{node['$anchor']}"}


def test_values_of_a_user_are_not_searched_for_anchors() -> None:
    fragment: Any = {"$anchor": "Item", "type": "string"}
    result: Any = unique_anchors(
        cast(
            Any,
            {
                "allOf": [
                    {"$anchor": "Item", "type": "integer"},
                    {"enum": [fragment], "const": fragment, "default": fragment},
                    {"examples": [fragment]},
                    {"properties": {"default": {"$anchor": "Item", "type": "null"}}},
                ]
            },
        )
    )

    assert result["allOf"][1] == {"enum": [fragment], "const": fragment, "default": fragment}
    assert result["allOf"][2] == {"examples": [fragment]}
    # a property which is named like a keyword is still a schema
    assert result["allOf"][3]["properties"]["default"]["$anchor"] == "Item_2"


def test_empty_tuple_is_an_array_with_no_items() -> None:
    # `prefixItems` has to name at least one schema - an empty tuple names none
    assert schema_of(state_of(tuple[()]))["properties"]["v"] == {"type": "array", "maxItems": 0}


# 8. a real validator against the State, both ways


class _Payload(TypedDict):
    name: str
    count: NotRequired[int]


class _Leaf(State):
    number: int


type _Shape = int | Sequence[_Shape]


_OMITTED: Any = object()


class _Agreement(NamedTuple):
    annotation: Any
    documents: Sequence[Any]  # the values of the attribute, as they are read from a document
    instances: Sequence[Any]  # the values the attribute holds, written and read back
    key: str = "v"


_FORMATS: FormatChecker = FormatChecker()
_STRINGS: Sequence[Any] = ("", "a", 1, 1.5, True, None, ["a"], {})
_UUID: str = "00000000-0000-0000-0000-000000000001"
_NOW: datetime = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

_AGREEMENTS: Mapping[str, _Agreement] = {
    "str": _Agreement(str, _STRINGS, ["", "text", "ü"]),
    "int": _Agreement(
        int,
        [1, 1.0, 0, -0.0, 2**53, 1e308, 1.5, "1", "", True, None, [], {}],
        [0, 1, -1, 2**53],
    ),
    "float": _Agreement(
        float,
        [1, 1.0, 1.5, -0.0, 1e308, 2**53, "1", "", True, None, []],
        [0.0, 1.5, -0.0, 1e308],
    ),
    "bool": _Agreement(
        bool,
        [True, False, 0, 1, 2, 1.0, "true", "false", "", None, []],
        [True, False],
    ),
    "none": _Agreement(None, [None, 0, "", "null", False, []], [None]),
    "uuid": _Agreement(
        UUID,
        [_UUID, _UUID.upper(), _UUID.replace("-", ""), f"{{{_UUID}}}", "", "x", 1, None],
        [UUID(_UUID), UUID(int=2**128 - 1)],
    ),
    "datetime": _Agreement(
        datetime,
        ["2026-01-02T03:04:05+00:00", "2026-01-02T03:04:05", "2026-01-02", "", "x", 1, None],
        [_NOW, datetime(2026, 1, 2, 3, 4, 5, 6)],
    ),
    "date": _Agreement(
        date,
        ["2026-01-02", "20260102", "2026-1-2", "2026-01-02T00:00:00", "", "x", 1, None],
        [date(2026, 1, 2)],
    ),
    "time": _Agreement(
        time,
        ["03:04:05", "03:04:05+00:00", "03:04", "", "x", 1, None],
        [time(3, 4, 5), time(3, 4, 5, 6, tzinfo=UTC)],
    ),
    "timedelta": _Agreement(
        timedelta,
        ["PT5S", "P1D", "-PT5S", "5", "", "x", 5, None],
        [timedelta(seconds=5), timedelta(days=1), timedelta(seconds=-5)],
    ),
    "path": _Agreement(Path, ["/a/b", "", 1, None], [Path("/a/b"), Path("a")]),
    "bytes": _Agreement(bytes, ["aGk=", "", "!", 1, None], [b"hi", b"", b"\x00\xff"]),
    "str enum": _Agreement(
        Color, ["red", "blue", "RED", "green", "", 1, None], [Color.RED, Color.BLUE]
    ),
    "int enum": _Agreement(
        Level, [1, 2, 1.0, 3, "LOW", "1", True, "", None], [Level.LOW, Level.HIGH]
    ),
    "str literal": _Agreement(Literal["a", "b"], ["a", "b", "c", "", 1, None], ["a", "b"]),
    "int literal": _Agreement(Literal[1, 2], [1, 2, 1.0, 3, "1", True, 0, None], [1, 2]),
    "enum literal": _Agreement(
        Literal[Level.LOW, Level.HIGH], [1, 2, 2.0, 3, "LOW", True, None], [Level.LOW, Level.HIGH]
    ),
    "optional": _Agreement(str | None, ["a", None, 1, "", []], ["a", None]),
    "union": _Agreement(int | str, [1, "a", 1.0, 1.5, None, True, []], [1, "a"]),
    "bool or int": _Agreement(bool | int, [True, 1, 0, 2, 1.0, "true", None], [True, 1, 0]),
    "sequence": _Agreement(
        Sequence[int], [[], [1, 2], [1.0], [1, "a"], [True], "12", 1, None, {}], [(), (1, 2)]
    ),
    "variadic tuple": _Agreement(
        tuple[int, ...], [[], [1], [1, 2], ["a"], 1, None], [(), (1,), (1, 2)]
    ),
    "tuple": _Agreement(
        tuple[int, str], [[1, "a"], [1], [1, "a", 2], ["a", 1], [], 1, None], [(1, "a")]
    ),
    "empty tuple": _Agreement(tuple[()], [[], [1], 1, None], [()]),
    "set": _Agreement(Set[str], [[], ["a"], ["a", "a"], [1], "a", None], [frozenset(), {"a", "b"}]),
    "str mapping": _Agreement(
        Mapping[str, int], [{}, {"a": 1}, {"a": "x"}, {"": 1}, [], None], [{}, {"a": 1}, {"": 2}]
    ),
    "int mapping": _Agreement(
        Mapping[int, str],
        [{}, {"1": "a"}, {"01": "a"}, {"+1": "a"}, {"-1": "a"}, {"x": "a"}, {"1": 1}],
        [{}, {1: "a", -2: "b"}],
    ),
    "float mapping": _Agreement(
        Mapping[float, str],
        [
            {},
            {"1.5": "a"},
            {"1": "a"},
            {"inf": "a"},
            {"-inf": "a"},
            {"nan": "a"},
            {"Infinity": "a"},
            {"1e400": "a"},
            {"x": "a"},
        ],
        [{}, {1.5: "a", 1.0: "b"}, {math.inf: "a"}],
    ),
    "bool mapping": _Agreement(
        Mapping[bool, str],
        [{}, {"true": "a"}, {"false": "a"}, {"True": "a"}, {"1": "a"}, {"": "a"}],
        [{}, {True: "a", False: "b"}],
    ),
    "uuid mapping": _Agreement(
        Mapping[UUID, str],
        [{}, {_UUID: "a"}, {_UUID.upper(): "a"}, {_UUID.replace("-", ""): "a"}, {"x": "a"}],
        [{}, {UUID(_UUID): "a"}],
    ),
    "typed dict": _Agreement(
        _Payload,
        [{"name": "n"}, {"name": "n", "count": 1}, {"count": 1}, {"name": 1}, {}, [], None],
        [{"name": "n"}, {"name": "n", "count": 1}],
    ),
    "state": _Agreement(
        _Leaf,
        [{"number": 1}, {"number": "x"}, {}, {"number": 1, "extra": 1}, [], None],
        [_Leaf(number=1)],
    ),
    "generic state": _Agreement(
        Box[int],
        [{"value": 1}, {"value": "x"}, {}, {"value": 1.0}, [], None],
        [Box[int](value=1)],
    ),
    "recursive state": _Agreement(
        _Tree,
        [
            {},
            {"children": []},
            {"children": [{}]},
            {"children": [{"children": [{}]}]},
            {"children": [1]},
            {"children": [{"children": 1}]},
            None,
        ],
        [_Tree(), _Tree(children=[_Tree(children=[_Tree()])])],
    ),
    "recursive alias": _Agreement(
        _Shape,
        [1, [], [1, [2, [3]]], [1, "a"], [[[["a"]]]], "a", None, 1.5],
        [1, (), (1, (2, (3,)))],
    ),
    "aliased": _Agreement(
        Annotated[int, Alias("renamed")],
        [1, "x", None],
        [1],
        key="renamed",
    ),
    "optional missing": _Agreement(int | Missing, [_OMITTED, 1, "x", None], [1, Missing]),
}


def _text_of(agreement: _Agreement, document: Any, /) -> str:
    return json.dumps({} if document is _OMITTED else {agreement.key: document})


def _schema_accepts(cls: Any, text: str, /) -> bool:
    validator: Draft202012Validator = Draft202012Validator(schema_of(cls), format_checker=_FORMATS)
    return validator.is_valid(json.loads(text))


def _state_accepts(cls: Any, text: str, /) -> bool:
    try:
        cls.from_json(text)

    except ValueError, TypeError:
        return False

    return True


# the documents on which the schema and the class are known to differ - anything else has to
# agree. "schema only" is accepted by the schema and refused by the class, "state only" the other
# way round
_SCHEMA_ONLY: str = "schema only"
_STATE_ONLY: str = "state only"
_DISAGREEMENTS: Mapping[str, Sequence[tuple[Any, str]]] = {
    # a whole float beyond 2**53 is not read as an int, as its digits are no longer exact
    "int": [(1e308, _SCHEMA_ONLY)],
    # the schema names what is written alone, while the class reads more spellings - a bool from
    # 0, 1 and words, an enum member from its name
    "bool": [
        (0, _STATE_ONLY),
        (1, _STATE_ONLY),
        (1.0, _STATE_ONLY),
        ("true", _STATE_ONLY),
        ("false", _STATE_ONLY),
    ],
    "bool or int": [("true", _STATE_ONLY)],
    "str enum": [("RED", _STATE_ONLY)],
    "int enum": [("LOW", _STATE_ONLY)],
    # the spellings of a UUID other than the canonical one are read, not named by the schema
    "uuid": [(_UUID.replace("-", ""), _STATE_ONLY), (f"{{{_UUID}}}", _STATE_ONLY)],
    "date": [("20260102", _STATE_ONLY)],
    "uuid mapping": [({_UUID.replace("-", ""): "a"}, _STATE_ONLY)],
    # these are plain strings in the schema - it names no format the output keeps
    "datetime": [("", _SCHEMA_ONLY), ("x", _SCHEMA_ONLY)],
    "time": [("", _SCHEMA_ONLY), ("x", _SCHEMA_ONLY)],
    "timedelta": [("", _SCHEMA_ONLY), ("x", _SCHEMA_ONLY), ("5", _SCHEMA_ONLY)],
    "bytes": [("!", _SCHEMA_ONLY)],
    # a pattern cannot bound the magnitude of the number a key spells
    "float mapping": [({"1e400": "a"}, _SCHEMA_ONLY)],
}


def _disagreements(name: str, /) -> Mapping[str, str]:
    agreement: _Agreement = _AGREEMENTS[name]
    cls: Any = state_of(agreement.annotation)
    found: dict[str, str] = {}
    for document in agreement.documents:
        text: str = _text_of(agreement, document)
        by_schema: bool = _schema_accepts(cls, text)
        by_state: bool = _state_accepts(cls, text)
        if by_schema != by_state:
            found[text] = "schema only" if by_schema else "state only"

    return found


@pytest.mark.parametrize("name", list(_AGREEMENTS))
def test_schema_is_a_valid_json_schema(name: str) -> None:
    Draft202012Validator.check_schema(schema_of(state_of(_AGREEMENTS[name].annotation)))


@pytest.mark.parametrize("name", list(_AGREEMENTS))
def test_schema_and_state_accept_the_same_documents(name: str) -> None:
    agreement: _Agreement = _AGREEMENTS[name]
    expected: Mapping[str, str] = {
        _text_of(agreement, document): kind for document, kind in _DISAGREEMENTS.get(name, ())
    }
    assert _disagreements(name) == expected


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param(name, value, id=f"{name}-{index}")
        for name, agreement in _AGREEMENTS.items()
        for index, value in enumerate(agreement.instances)
    ],
)
def test_written_instances_conform_to_the_schema_and_read_back(name: str, value: Any) -> None:
    cls: Any = state_of(_AGREEMENTS[name].annotation)
    validator: Draft202012Validator = Draft202012Validator(schema_of(cls), format_checker=_FORMATS)
    instance: Any = cls(**{"v": value})
    text: str = instance.to_json()

    validator.validate(strict_loads(text))
    assert cls.from_json(text) == instance


def test_python_names_of_aliased_attributes_are_read_but_not_named_by_the_schema() -> None:
    cls: Any = state_of(Annotated[int, Alias("renamed")])
    text: str = json.dumps({"v": 1})

    assert _state_accepts(cls, text)
    assert not _schema_accepts(cls, text)


type _Same = int | Sequence[_Same]
_FirstItem: Any = _recursive_item("first", str)
_SecondItem: Any = _recursive_item("second", int)


class _Multi(State):
    first_alias: _Nested
    second_alias: _Same
    first_item: _FirstItem
    second_item: _SecondItem
    tree: _Tree
    boxed: _Boxed[_Tree]
    trees: Sequence[_Tree]


def _multi_document(**changes: Any) -> Any:
    document: dict[str, Any] = {
        "first_alias": [1, [2]],
        "second_alias": [3, [4]],
        "first_item": {"value": "a", "parts": [{"value": "b"}]},
        "second_item": {"value": 1, "parts": [{"value": 2}]},
        "tree": {"children": [{}]},
        "boxed": {"value": {"children": [{}]}},
        "trees": [{}, {"children": [{}]}],
    }
    return {**document, **changes}


_MULTI_DOCUMENTS: Sequence[Any] = (
    _multi_document(),
    _multi_document(first_alias=["a"]),
    _multi_document(second_alias=[1, ["a"]]),
    _multi_document(first_item={"value": 1}),
    _multi_document(second_item={"value": "a"}),
    _multi_document(first_item={"value": "a", "parts": [{"value": 1}]}),
    _multi_document(second_item={"value": 1, "parts": [{"value": "a"}]}),
    _multi_document(tree={"children": [{"children": 1}]}),
    _multi_document(boxed={"value": {"children": [1]}}),
    _multi_document(trees=[{}, {"children": [{"children": [1]}]}]),
    _multi_document(trees=[{"children": [{"children": [{}]}]}]),
)


@pytest.mark.parametrize("document", _MULTI_DOCUMENTS, ids=range(len(_MULTI_DOCUMENTS)))
def test_recursive_definitions_sharing_names_agree_with_the_schema(document: Any) -> None:
    text: str = json.dumps(document)

    assert _schema_accepts(_Multi, text) == _state_accepts(_Multi, text)


def test_recursive_definitions_sharing_names_keep_their_own_schemas() -> None:
    assert _schema_accepts(_Multi, json.dumps(_multi_document()))
    assert not _schema_accepts(_Multi, json.dumps(_multi_document(first_item={"value": 1})))
    assert not _schema_accepts(_Multi, json.dumps(_multi_document(second_item={"value": "a"})))
    anchors: dict[str, list[Any]] = _anchored(schema_of(_Multi), {})
    assert all(len(nodes) == 1 for nodes in anchors.values())


def test_recursive_definitions_sharing_names_are_written_to_the_schema() -> None:
    instance: _Multi = _Multi.from_json(json.dumps(_multi_document()))
    text: str = instance.to_json()

    Draft202012Validator(schema_of(_Multi), format_checker=_FORMATS).validate(strict_loads(text))
    assert _Multi.from_json(text) == instance


def _anchored_definition(anchor: str, kind: str) -> dict[str, Any]:
    return {
        "$anchor": anchor,
        "type": "array",
        "items": {"anyOf": [{"type": kind}, {"$ref": f"#{anchor}"}]},
    }


def test_a_name_given_once_keeps_reaching_its_definition() -> None:
    # a specification of the user may refer to an anchor from outside of it - renaming a
    # repeated name takes no name the document spells
    schema: Any = unique_anchors(
        cast(
            Any,
            {
                "type": "object",
                "properties": {
                    "a": _anchored_definition("Item", "integer"),
                    "b": _anchored_definition("Item", "boolean"),
                    "c": _anchored_definition("Item_2", "string"),
                    "d": {"$ref": "#Item_2"},
                },
            },
        )
    )
    properties: Any = schema["properties"]
    assert properties["c"]["$anchor"] == "Item_2"
    assert properties["b"]["$anchor"] not in {"Item", "Item_2"}
    assert properties["d"] == {"$ref": "#Item_2"}
    assert properties["b"]["items"]["anyOf"][1] == {"$ref": f"#{properties['b']['$anchor']}"}


def test_equal_definitions_bound_to_other_definitions_are_kept_apart() -> None:
    # the same text within definitions renamed apart refers to each of them, so it is two
    def holding(kind: str) -> dict[str, Any]:
        return {
            "$anchor": "Item",
            "type": "object",
            "properties": {
                "value": {"type": kind},
                "parts": {"$anchor": "Nest", "anyOf": [{"$ref": "#Item"}, {"$ref": "#Nest"}]},
            },
        }

    schema: Any = unique_anchors(
        cast(
            Any,
            {"type": "object", "properties": {"a": holding("string"), "b": holding("integer")}},
        )
    )
    second: Any = schema["properties"]["b"]
    assert second["$anchor"] != "Item"
    assert "$anchor" in second["properties"]["parts"]
    assert second["properties"]["parts"]["anyOf"][0] == {"$ref": f"#{second['$anchor']}"}
