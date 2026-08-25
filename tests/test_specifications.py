import re
from collections.abc import Callable, Mapping, Sequence
from types import UnionType
from typing import Annotated, Any, Literal, TypedDict

from haiway import (
    Description,
    Missing,
    Specification,
    State,
    TypeSpecification,
)
from haiway.attributes.annotations import (
    BoolAttribute,
    FloatAttribute,
    IntegerAttribute,
    MappingAttribute,
    NoneAttribute,
    SequenceAttribute,
    StringAttribute,
    TupleAttribute,
    UnionAttribute,
)
from haiway.attributes.specification import type_specification

ANY_TYPES = [
    "string",
    "number",
    "integer",
    "boolean",
    "object",
    "array",
    "null",
]


def specification_of(cls: type[State]) -> Any:
    # the specification of a class, without narrowing which of its shapes it is -
    # a test asserting the whole of it has no use for the narrowing anyway
    return cls.__SPECIFICATION__


def properties_of(cls: type[State]) -> Any:
    return specification_of(cls)["properties"]


def anchor_of(type_name: str) -> str:
    # a plain name, which the qualified name of a class declared within a
    # function is not - `<locals>` has no place in an anchor
    return re.sub(r"[^-A-Za-z0-9._]", "_", type_name)


def test_specifications() -> None:
    assert type_specification(StringAttribute()) == {
        "type": "string",
    }
    assert type_specification(IntegerAttribute()) == {
        "type": "integer",
    }
    assert type_specification(FloatAttribute()) == {
        "type": "number",
    }
    assert type_specification(BoolAttribute()) == {
        "type": "boolean",
    }
    assert type_specification(NoneAttribute()) == {
        "type": "null",
    }
    assert type_specification(
        SequenceAttribute(
            base=Sequence,
            values=StringAttribute(),
        )
    ) == {"type": "array", "items": {"type": "string"}}
    assert type_specification(
        SequenceAttribute(
            base=tuple,
            values=StringAttribute(),
        )
    ) == {"type": "array", "items": {"type": "string"}}
    assert type_specification(
        TupleAttribute(
            base=tuple,
            values=(
                StringAttribute(),
                StringAttribute(),
            ),
        )
    ) == {
        "type": "array",
        "prefixItems": [{"type": "string"}, {"type": "string"}],
        "items": False,
    }
    assert type_specification(
        MappingAttribute(
            base=Mapping,
            keys=StringAttribute(),
            values=StringAttribute(),
        )
    ) == {"type": "object", "additionalProperties": {"type": "string"}}
    assert type_specification(
        UnionAttribute(
            base=UnionType,
            alternatives=(
                StringAttribute(),
                IntegerAttribute(),
            ),
        )
    ) == {"type": ["string", "integer"]}


def test_basic_specification() -> None:
    class TestModel(State):
        str_value: str
        int_value: int
        float_value: float
        bool_value: bool
        none_value: None
        list_value: Sequence[str]
        dict_value: Mapping[str, str]

    specification: TypeSpecification = {
        "type": "object",
        "properties": {
            "str_value": {"type": "string"},
            "int_value": {"type": "integer"},
            "float_value": {"type": "number"},
            "bool_value": {"type": "boolean"},
            "none_value": {"type": "null"},
            "list_value": {"type": "array", "items": {"type": "string"}},
            "dict_value": {"type": "object", "additionalProperties": {"type": "string"}},
        },
        "required": [
            "str_value",
            "int_value",
            "float_value",
            "bool_value",
            "none_value",
            "list_value",
            "dict_value",
        ],
        "additionalProperties": False,
    }
    assert TestModel.__SPECIFICATION__ == specification


def test_parametrized_specification() -> None:
    class TestModel[Param](State):
        param: Param

    assert TestModel.__SPECIFICATION__ == {
        "type": "object",
        "properties": {
            "param": {"type": ANY_TYPES},
        },
        "required": ["param"],
        "additionalProperties": False,
    }
    assert TestModel[str].__SPECIFICATION__ == {
        "type": "object",
        "properties": {
            "param": {"type": "string"},
        },
        "required": ["param"],
        "additionalProperties": False,
    }
    assert TestModel[int].__SPECIFICATION__ == {
        "type": "object",
        "properties": {
            "param": {"type": "integer"},
        },
        "required": ["param"],
        "additionalProperties": False,
    }
    assert TestModel[str] == TestModel[str]
    assert TestModel[int] == TestModel[int]
    assert TestModel[str] != TestModel[int]


def test_nested_parametrized_specification() -> None:
    class TestModelNested[Param](State):
        param: Param

    class TestModelHolder[Param: State](State):
        param: Param

    class TestModel[Param: State](State):
        param: TestModelHolder[Param]

    assert TestModel.__SPECIFICATION__ == {
        "type": "object",
        "properties": {
            "param": {
                "type": "object",
                "properties": {
                    "param": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    }
                },
                "required": ["param"],
                "additionalProperties": False,
            }
        },
        "required": ["param"],
        "additionalProperties": False,
    }
    assert TestModel[TestModelNested[str]].__SPECIFICATION__ == {
        "type": "object",
        "properties": {
            "param": {
                "type": "object",
                "properties": {
                    "param": {
                        "type": "object",
                        "properties": {
                            "param": {
                                "type": "string",
                            },
                        },
                        "required": ["param"],
                        "additionalProperties": False,
                    }
                },
                "required": ["param"],
                "additionalProperties": False,
            }
        },
        "required": ["param"],
        "additionalProperties": False,
    }
    assert TestModel[TestModelNested[int]].__SPECIFICATION__ == {
        "type": "object",
        "properties": {
            "param": {
                "type": "object",
                "properties": {
                    "param": {
                        "type": "object",
                        "properties": {
                            "param": {
                                "type": "integer",
                            },
                        },
                        "required": ["param"],
                        "additionalProperties": False,
                    }
                },
                "required": ["param"],
                "additionalProperties": False,
            }
        },
        "required": ["param"],
        "additionalProperties": False,
    }
    assert TestModel[TestModelNested[str]] == TestModel[TestModelNested[str]]
    assert TestModel[TestModelNested[int]] == TestModel[TestModelNested[int]]
    assert TestModel[TestModelNested[str]] != TestModel[TestModelNested[int]]


def test_recursive_typed_dict_specification() -> None:
    class NodeDict(TypedDict):
        value: int
        next: NodeDict | None

    class Wrapper(State):
        node: NodeDict

    anchor = anchor_of(NodeDict.__qualname__)
    assert Wrapper.__SPECIFICATION__ == {
        "type": "object",
        "properties": {
            "node": {
                "type": "object",
                "properties": {
                    "value": {"type": "integer"},
                    "next": {
                        "anyOf": [
                            {"$ref": f"#{anchor}"},
                            {"type": "null"},
                        ],
                    },
                },
                "additionalProperties": False,
                "required": ["value", "next"],
                "$anchor": anchor,
            },
        },
        "required": ["node"],
        "additionalProperties": False,
    }


def test_recursive_typed_dict_references_use_identifier() -> None:
    class NodeDict(TypedDict):
        value: int
        next: NodeDict | None
        sibling: NodeDict | None

    class Wrapper(State):
        node: NodeDict

    node_spec = Wrapper.__SPECIFICATION__["properties"]["node"]
    anchor = node_spec["$anchor"]

    assert anchor == anchor_of(NodeDict.__qualname__)
    for relation in ("next", "sibling"):
        alternatives = node_spec["properties"][relation]["anyOf"]
        assert {"$ref": f"#{anchor}"} in alternatives
        assert {"type": "null"} in alternatives


def test_non_recursive_typed_dict_has_no_identifier() -> None:
    class SimpleDict(TypedDict):
        value: int

    class Wrapper(State):
        payload: SimpleDict

    payload_spec = Wrapper.__SPECIFICATION__["properties"]["payload"]
    assert "$anchor" not in payload_spec


def test_recursive_state_specification() -> None:
    class Node(State):
        value: int = 0
        child: Node | None = None

    anchor = anchor_of(Node.__qualname__)
    assert Node.__SPECIFICATION__ == {
        "type": "object",
        "properties": {
            "value": {"type": "integer"},
            "child": {
                "anyOf": [
                    {"$ref": f"#{anchor}"},
                    {"type": "null"},
                ],
            },
        },
        "required": [],
        "additionalProperties": False,
        "$anchor": anchor,
    }


def test_recursive_state_specification_accepts_its_own_payload() -> None:
    class Node(State):
        value: int = 0
        children: Sequence[Node] = ()

    specification = Node.__SPECIFICATION__
    # the attribute referring back to the class resolves to a reference to it,
    # rather than to what the class was before it declared its own attributes -
    # an empty object closed to additional properties, refusing this very payload
    anchor = anchor_of(Node.__qualname__)
    assert specification["properties"]["children"] == {  # pyright: ignore[reportTypedDictNotRequiredAccess]
        "type": "array",
        "items": {"$ref": f"#{anchor}"},
    }
    assert specification["$anchor"] == anchor  # pyright: ignore[reportTypedDictNotRequiredAccess]


def test_recursive_state_references_use_identifier_from_every_container() -> None:
    class Node(State):
        alone: Node | None = None
        listed: Sequence[Node] = ()
        mapped: Mapping[str, Node] = {}

    specification = Node.__SPECIFICATION__
    properties = specification["properties"]  # pyright: ignore[reportTypedDictNotRequiredAccess]
    anchor = specification["$anchor"]  # pyright: ignore[reportTypedDictNotRequiredAccess]

    assert anchor == anchor_of(Node.__qualname__)
    assert {"$ref": f"#{anchor}"} in properties["alone"]["anyOf"]
    assert properties["listed"]["items"] == {"$ref": f"#{anchor}"}
    assert properties["mapped"]["additionalProperties"] == {"$ref": f"#{anchor}"}


def test_non_recursive_state_has_no_identifier() -> None:
    class Inner(State):
        value: int = 0

    class Outer(State):
        inner: Inner = Inner()

    assert "$anchor" not in Outer.__SPECIFICATION__
    assert "$anchor" not in Outer.__SPECIFICATION__["properties"]["inner"]  # pyright: ignore[reportTypedDictNotRequiredAccess]


def test_recursive_state_with_unrepresentable_attribute_is_not_serializable() -> None:
    class Node(State):
        handler: Callable[[], None]
        child: Node | None = None

    assert Node.__SERIALIZABLE__ is False


def test_any_specification_accepts_every_json_value() -> None:
    class Example(State):
        value: Any

    assert properties_of(Example)["value"] == {"type": ANY_TYPES}  # pyright: ignore[reportTypedDictNotRequiredAccess]


def test_literal_of_one_type_names_it() -> None:
    class Textual(State):
        value: Literal["one", "other"]

    class Numeric(State):
        value: Literal[1, 2]

    class Boolean(State):
        value: Literal[True]

    assert properties_of(Textual)["value"] == {  # pyright: ignore[reportTypedDictNotRequiredAccess]
        "type": "string",
        "enum": ["one", "other"],
    }
    assert properties_of(Numeric)["value"] == {  # pyright: ignore[reportTypedDictNotRequiredAccess]
        "type": "integer",
        "enum": [1, 2],
    }
    # a JSON boolean is not a JSON integer, no matter that `bool` is an `int`
    assert properties_of(Boolean)["value"] == {  # pyright: ignore[reportTypedDictNotRequiredAccess]
        "type": "boolean",
        "enum": [True],
    }


def test_literal_of_mixed_types_names_only_the_values() -> None:
    class Example(State):
        value: Literal["one", 2, None]

    # naming a single type would refuse the values of the other ones - the enum
    # is what makes the type unnecessary here
    assert Example.__SERIALIZABLE__ is True
    assert properties_of(Example)["value"] == {"enum": ["one", 2, None]}  # pyright: ignore[reportTypedDictNotRequiredAccess]


def test_union_of_the_same_type_names_it_once() -> None:
    class Example(State):
        value: Missing | None = None

    # a type can be named only once within a list of types
    assert properties_of(Example)["value"] == {"type": "null"}  # pyright: ignore[reportTypedDictNotRequiredAccess]


def test_union_of_overlapping_alternatives_accepts_any_of_them() -> None:
    class Example(State):
        value: Sequence[int] | Sequence[str]

    # both alternatives accept an empty array, which the annotation validates -
    # requiring exactly one of them to match would refuse it
    assert properties_of(Example)["value"] == {  # pyright: ignore[reportTypedDictNotRequiredAccess]
        "anyOf": [
            {"type": "array", "items": {"type": "integer"}},
            {"type": "array", "items": {"type": "string"}},
        ],
    }


def test_recursive_anchor_is_a_plain_name() -> None:
    class Node(State):
        child: Node | None = None

    anchor = specification_of(Node)["$anchor"]

    # the class is declared within this function, which its qualified name spells
    # out with characters an anchor can't hold
    assert "<locals>" in Node.__qualname__
    assert re.fullmatch(r"[A-Za-z_][-A-Za-z0-9._]*", anchor)


def test_declared_specification_is_used_at_every_depth() -> None:
    positive: Specification = Specification({"type": "integer", "minimum": 0})

    class Example(State):
        value: Annotated[int, positive]
        values: Sequence[Annotated[int, positive]]
        mapped: Mapping[str, Annotated[int, positive]]

    properties = properties_of(Example)
    assert properties["value"] == {"type": "integer", "minimum": 0}
    assert properties["values"] == {
        "type": "array",
        "items": {"type": "integer", "minimum": 0},
    }
    assert properties["mapped"] == {
        "type": "object",
        "additionalProperties": {"type": "integer", "minimum": 0},
    }


def test_declared_specification_keeps_its_own_description() -> None:
    class Example(State):
        described: Annotated[
            int,
            Description("of the attribute"),
            Specification({"type": "integer"}),
        ]
        self_described: Annotated[
            int,
            Description("of the attribute"),
            Specification({"type": "integer", "description": "of the specification"}),
        ]

    properties = properties_of(Example)
    assert properties["described"] == {
        "type": "integer",
        "description": "of the attribute",
    }
    assert properties["self_described"] == {
        "type": "integer",
        "description": "of the specification",
    }
