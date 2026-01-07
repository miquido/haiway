from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import Enum, StrEnum
from pathlib import Path
from typing import (
    Any,
    Literal,
    NotRequired,
    Protocol,
    TypeAliasType,
    TypedDict,
    TypeVar,
    runtime_checkable,
)
from uuid import UUID, uuid4

import pytest

from haiway import MISSING, Missing, State, ValidationError


class Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class Status(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


class BasicTypes(State):
    string_val: str
    int_val: int
    float_val: float
    bool_val: bool
    bytes_val: bytes


BASIC_PAYLOAD: dict[str, object] = {
    "string_val": "text",
    "int_val": 7,
    "float_val": 1.5,
    "bool_val": True,
    "bytes_val": b"blob",
}


class SequenceState(State):
    items: Sequence[str]
    numbers: Sequence[int]


class SetState(State):
    tags: set[str]
    ids: set[int]


class MappingState(State):
    data: Mapping[str, int]


class ConcreteCollections(State):
    items: list[int]
    mapping: dict[str, int]
    tags: set[str]


class NestedCollections(State):
    matrix: list[list[int]]
    payload: dict[str, dict[str, int]]


class TupleState(State):
    fixed: tuple[str, int, bool]
    variable: tuple[int, ...]


class UnionState(State):
    value: str | int
    optional: str | None


@runtime_checkable
class Processor(Protocol):
    def __call__(self, data: str, /) -> str: ...


class CallableState(State):
    func: Callable[[], None]
    processor: Processor


class UserPayload(TypedDict):
    name: str
    age: int
    active: bool
    email: NotRequired[str]


class TypedDictState(State):
    user: UserPayload


class WrapperPayload(TypedDict):
    profile: UserPayload
    tags: list[str]


class TypedDictWrapperState(State):
    wrapper: WrapperPayload


RecursiveMap: TypeAliasType = TypeAliasType(
    "RecursiveMap",
    Mapping[str, "RecursiveMap"],
)


class RecursiveMapState(State):
    data: RecursiveMap


RecursiveJsonValue: TypeAliasType = TypeAliasType(
    "RecursiveJsonValue",
    str | int | Mapping[str, "RecursiveJsonValue"],
)


class RecursiveJsonState(State):
    value: RecursiveJsonValue


class ComplexState(State):
    uuid_val: UUID
    date_val: date
    datetime_val: datetime
    time_val: time
    timedelta_val: timedelta
    timezone_val: timezone
    path_val: Path
    pattern_val: re.Pattern[str]


class BoxState[T](State):
    item: T


class BoxContainerState(State):
    box: BoxState[int]


T = TypeVar("T")


class GenericWrapperState[T](State):
    value: T


class GenericContainerState(State):
    int_value: GenericWrapperState[int]
    str_value: GenericWrapperState[str]


class RecursiveState(State):
    name: str
    child: RecursiveState | None


class GenericState[T](State):
    value: T


class ContainerState(State):
    string_generic: GenericState[str]
    int_generic: GenericState[int]


class AnyState(State):
    anything: Any


class DefaultState(State):
    required: str
    optional: str = "default"
    optional_union: str | None = None


class NestedState(State):
    value: str


class ParentState(State):
    nested: NestedState


class DeepNestedState(State):
    items: Sequence[str]
    mapping: Mapping[str, int]
    nested: NestedState


class NoneState(State):
    value: None


class ContextStateMissingExc(State):
    value: Missing


class LiteralState(State):
    mode: Literal["read", "write", "append"]


class EnumState(State):
    color: Color
    status: Status


class ErrorState(State):
    string_val: str
    literal_val: Literal["a", "b"]
    none_val: None


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    (
        ("string_val", 42, "str"),
        ("int_val", "forty-two", "int"),
        ("float_val", "pi", "float"),
        ("bool_val", "no", "expected values"),
        ("bytes_val", "data", "bytes"),
    ),
)
def test_basic_types_validation_errors(field: str, value: object, expected: str) -> None:
    payload = dict(BASIC_PAYLOAD)
    payload[field] = value
    with pytest.raises(ValidationError) as exc:
        BasicTypes(**payload)
    assert exc.value.path == (f".{field}",)
    assert expected in str(exc.value.cause)


def test_basic_types_validation_success() -> None:
    instance = BasicTypes(**BASIC_PAYLOAD)
    assert instance.string_val == "text"
    assert instance.int_val == 7
    assert instance.float_val == 1.5
    assert instance.bool_val is True
    assert instance.bytes_val == b"blob"


def test_none_type_validation() -> None:
    assert NoneState(value=None).value is None
    with pytest.raises(ValidationError) as exc:
        NoneState(value="invalid")
    assert exc.value.path == (".value",)


def test_missing_type_validation() -> None:
    assert ContextStateMissingExc(value=MISSING).value is MISSING
    with pytest.raises(ValidationError) as exc:
        ContextStateMissingExc(value=None)
    assert exc.value.path == (".value",)


def test_literal_validation() -> None:
    instance = LiteralState(mode="read")
    assert instance.mode == "read"

    with pytest.raises(ValidationError) as exc:
        LiteralState(mode="invalid")
    assert exc.value.path == (".mode",)


def test_literal_non_string_values() -> None:
    class NumericLiteralState(State):
        count: Literal[1, 2, 3]

    assert NumericLiteralState(count=1).count == 1

    with pytest.raises(ValidationError):
        NumericLiteralState(count=4)


def test_enum_validation() -> None:
    instance = EnumState(color=Color.RED, status=Status.ACTIVE)
    assert instance.color is Color.RED
    assert instance.status is Status.ACTIVE

    with pytest.raises(ValidationError) as exc:
        EnumState(color=123, status=Status.ACTIVE)
    assert exc.value.path == (".color",)

    with pytest.raises(ValidationError) as exc:
        EnumState(color="red", status=Status.ACTIVE)
    assert exc.value.path == (".color",)

    status_instance = EnumState(color=Color.RED, status="active")
    assert status_instance.status is Status.ACTIVE

    with pytest.raises(ValidationError) as exc:
        EnumState(color=Color.RED, status="invalid")
    assert exc.value.path == (".status",)


def test_sequence_validation() -> None:
    instance = SequenceState(items=["a", "b"], numbers=[1, 2, 3])
    assert instance.items == ("a", "b")
    assert instance.numbers == (1, 2, 3)

    with pytest.raises(ValidationError) as exc:
        SequenceState(items=["ok", 3], numbers=[1, 2, 3])
    assert exc.value.path == (".items", "[1]")

    with pytest.raises(ValidationError) as exc:
        SequenceState(items="nope", numbers=[1, 2, 3])
    assert exc.value.path == (".items",)


def test_set_validation() -> None:
    instance = SetState(tags={"x", "y"}, ids={1, 2})
    assert instance.tags == frozenset({"x", "y"})
    assert instance.ids == frozenset({1, 2})

    with pytest.raises(ValidationError) as exc:
        SetState(tags=[1], ids=[1, 2])
    assert exc.value.path == (".tags", "[0]")

    with pytest.raises(ValidationError) as exc:
        SetState(tags=["x"], ids="not-set")
    assert exc.value.path == (".ids",)


def test_mapping_validation() -> None:
    instance = MappingState(data={"a": 1, "b": 2})
    assert instance.data == {"a": 1, "b": 2}

    with pytest.raises(ValidationError) as exc:
        MappingState(data={1: 1})
    assert exc.value.path == (".data", "[1]")

    with pytest.raises(ValidationError) as exc:
        MappingState(data={"a": "x"})
    assert exc.value.path == (".data", "[a]")

    with pytest.raises(ValidationError) as exc:
        MappingState(data="not-mapping")
    assert exc.value.path == (".data",)


def test_concrete_collection_validation() -> None:
    instance = ConcreteCollections(items=[1, 2], mapping={"a": 1}, tags={"x"})
    assert instance.items == (1, 2)
    assert instance.mapping == {"a": 1}
    assert instance.tags == frozenset({"x"})

    with pytest.raises(ValidationError) as exc:
        ConcreteCollections(items=["a"], mapping={"a": 1}, tags={"x"})
    assert exc.value.path == (".items", "[0]")

    with pytest.raises(ValidationError) as exc:
        ConcreteCollections(items=[1], mapping={"a": "x"}, tags={"x"})
    assert exc.value.path == (".mapping", "[a]")

    with pytest.raises(ValidationError) as exc:
        ConcreteCollections(items=[1], mapping={"a": 1}, tags=[1])
    assert exc.value.path == (".tags", "[0]")


def test_nested_collection_validation() -> None:
    instance = NestedCollections(
        matrix=[[1, 2], [3, 4]],
        payload={"left": {"inner": 1}},
    )
    assert instance.matrix == ((1, 2), (3, 4))
    assert instance.payload == {"left": {"inner": 1}}

    with pytest.raises(ValidationError) as exc:
        NestedCollections(
            matrix=[[1, 2], [3, "bad"]],
            payload={"left": {"inner": 1}},
        )
    assert exc.value.path == (".matrix", "[1]", "[1]")

    with pytest.raises(ValidationError) as exc:
        NestedCollections(
            matrix=[[1, 2], [3, 4]],
            payload={"left": {"inner": "bad"}},
        )
    assert exc.value.path == (".payload", "[left]", "[inner]")


def test_tuple_validation() -> None:
    instance = TupleState(fixed=["ok", 3, True], variable=[1, 2, 3])
    assert instance.fixed == ("ok", 3, True)
    assert instance.variable == (1, 2, 3)

    with pytest.raises(ValidationError) as exc:
        TupleState(fixed=["ok", "no", True], variable=[1])
    assert exc.value.path == (".fixed", "[1]")

    with pytest.raises(ValidationError) as exc:
        TupleState(fixed=["ok", 3, True], variable=[1, "bad"])
    assert exc.value.path == (".variable", "[1]")


def test_union_validation() -> None:
    instance = UnionState(value="hello", optional="x")
    assert instance.value == "hello"
    assert instance.optional == "x"

    instance = UnionState(value=3, optional=None)
    assert instance.value == 3
    assert instance.optional is None

    with pytest.raises(ValidationError) as exc:
        UnionState(value=["bad"], optional="x")
    assert exc.value.path == (".value",)


def test_callable_validation() -> None:
    def noop() -> None:
        return None

    def upper(data: str, /) -> str:
        return data.upper()

    instance = CallableState(func=noop, processor=upper)
    assert instance.processor("ok") == "OK"

    with pytest.raises(ValidationError) as exc:
        CallableState(func="not callable", processor=upper)
    assert exc.value.path == (".func",)

    with pytest.raises(ValidationError) as exc:
        CallableState(func=noop, processor="not callable")
    assert exc.value.path == (".processor",)


def test_typed_dict_validation() -> None:
    instance = TypedDictState(
        user={
            "name": "Jane",
            "age": 30,
            "active": True,
            "email": "jane@example.com",
        },
    )
    assert instance.user["name"] == "Jane"
    assert instance.user["active"] is True

    instance = TypedDictState(user={"name": "Alice", "age": 25, "active": True})
    assert instance.user["name"] == "Alice"
    assert "email" not in instance.user


def test_typed_dict_preserves_false_values() -> None:
    instance = TypedDictState(user={"name": "Bob", "age": 40, "active": False})
    assert instance.user["active"] is False


def test_typed_dict_requires_required_fields() -> None:
    with pytest.raises(ValidationError):
        TypedDictState(user={"name": "Eve", "age": 30})


def test_typed_dict_nested_validation() -> None:
    instance = TypedDictWrapperState(
        wrapper={
            "profile": {"name": "Ana", "age": 20, "active": True},
            "tags": ["one", "two"],
        },
    )
    assert instance.wrapper["tags"] == ("one", "two")

    with pytest.raises(ValidationError) as exc:
        TypedDictWrapperState(
            wrapper={
                "profile": {"name": "Ana", "age": 20, "active": True},
                "tags": ["one", 2],
            },
        )
    assert exc.value.path == (".wrapper", '["tags"]', "[1]")

    with pytest.raises(ValidationError) as exc:
        TypedDictWrapperState(
            wrapper={
                "profile": {
                    "name": "Ana",
                    "age": 20,
                    "active": True,
                    "email": 123,
                },
                "tags": ["ok"],
            },
        )
    assert exc.value.path == (".wrapper", '["profile"]', '["email"]')


def test_recursive_type_alias_validation() -> None:
    instance = RecursiveMapState(data={"child": {}})
    assert instance.data["child"] == {}

    with pytest.raises(ValidationError) as exc:
        RecursiveMapState(data={"child": 1})
    assert exc.value.path == (".data", "[child]")


def test_recursive_union_alias_validation() -> None:
    instance = RecursiveJsonState(value="leaf")
    assert instance.value == "leaf"

    instance = RecursiveJsonState(value={"child": {"grand": "leaf"}})
    assert instance.value["child"]["grand"] == "leaf"

    with pytest.raises(ValidationError) as exc:
        RecursiveJsonState(value={"child": {"grand": ["bad"]}})
    assert exc.value.path == (".value",)


def test_state_attribute_validation() -> None:
    instance = ParentState(nested=NestedState(value="child"))
    assert instance.nested.value == "child"

    with pytest.raises(ValidationError) as exc:
        ParentState(nested={"value": 123})
    assert exc.value.path == (".nested", ".value")

    with pytest.raises(ValidationError) as exc:
        ParentState(nested="not-a-state")
    assert exc.value.path == (".nested",)


def test_complex_type_validation() -> None:
    instance = ComplexState(
        uuid_val=uuid4(),
        date_val=date.today(),
        datetime_val=datetime.now(tz=UTC),
        time_val=time(12, 30, 45),
        timedelta_val=timedelta(days=1),
        timezone_val=UTC,
        path_val=Path("/tmp/test"),
        pattern_val=re.compile(r"test.*"),
    )
    assert isinstance(instance.uuid_val, UUID)
    assert instance.timezone_val is UTC

    with pytest.raises(ValidationError) as exc:
        ComplexState(
            uuid_val="not-uuid",
            date_val=date.today(),
            datetime_val=datetime.now(tz=UTC),
            time_val=time(12),
            timedelta_val=timedelta(days=1),
            timezone_val=UTC,
            path_val=Path("/tmp/test"),
            pattern_val=re.compile(r"test.*"),
        )
    assert exc.value.path == (".uuid_val",)


def test_recursive_state_validation() -> None:
    child = RecursiveState(name="child", child=None)
    parent = RecursiveState(name="parent", child=child)
    assert parent.child is child
    assert parent.child.child is None


def test_generic_state_validation() -> None:
    container = ContainerState(
        string_generic=GenericState[str](value="text"),
        int_generic=GenericState[int](value=10),
    )
    assert container.string_generic.value == "text"
    assert container.int_generic.value == 10

    with pytest.raises(ValidationError) as exc:
        GenericState[str](value=1)
    assert exc.value.path == (".value",)

    with pytest.raises(ValidationError) as exc:
        ContainerState(string_generic="bad", int_generic=GenericState[int](value=10))
    assert exc.value.path == (".string_generic",)


def test_generic_state_specialization_validation() -> None:
    specialized_instance = BoxContainerState(box=BoxState(item=1))
    assert specialized_instance.box.item == 1

    with pytest.raises(ValidationError) as exc:
        BoxContainerState(box={"item": "oops"})
    assert exc.value.path == (".box", ".item")

    with pytest.raises(ValidationError) as exc:
        GenericWrapperState[int](value="nope")
    assert exc.value.path == (".value",)

    generic_container = GenericContainerState(
        int_value=GenericWrapperState[int](value=5),
        str_value=GenericWrapperState[str](value="ok"),
    )
    assert generic_container.int_value.value == 5
    assert generic_container.str_value.value == "ok"

    with pytest.raises(ValidationError) as exc:
        GenericContainerState(
            int_value={"value": "wrong"},
            str_value=GenericWrapperState[str](value="ok"),
        )
    assert exc.value.path == (".int_value", ".value")

    with pytest.raises(ValidationError) as exc:
        GenericContainerState(int_value=GenericWrapperState[int](value=5), str_value={"value": 7})
    assert exc.value.path == (".str_value", ".value")


def test_validation_error_messages() -> None:
    with pytest.raises(ValidationError) as exc:
        ErrorState(string_val=42, literal_val="a", none_val=None)
    assert exc.value.path == (".string_val",)
    assert "str" in str(exc.value.cause)

    with pytest.raises(ValidationError) as exc:
        ErrorState(string_val="ok", literal_val="z", none_val=None)
    assert exc.value.path == (".literal_val",)
    assert "literal" in str(exc.value.cause)

    with pytest.raises(ValidationError) as exc:
        ErrorState(string_val="ok", literal_val="a", none_val="nope")
    assert exc.value.path == (".none_val",)
    assert "None" in str(exc.value.cause)


def test_any_type_accepts_all_values() -> None:
    instance = AnyState(anything="string")
    assert instance.anything == "string"

    instance = AnyState(anything=42)
    assert instance.anything == 42

    instance = AnyState(anything=None)
    assert instance.anything is None


def test_default_value_handling() -> None:
    instance = DefaultState(required="value")
    assert instance.optional == "default"
    assert instance.optional_union is None

    override = DefaultState(
        required="value",
        optional="custom",
        optional_union="present",
    )
    assert override.optional == "custom"
    assert override.optional_union == "present"

    with pytest.raises(ValidationError) as exc:
        DefaultState(required="value", optional=123)
    assert exc.value.path == (".optional",)


def test_validation_error_paths() -> None:
    with pytest.raises(ValidationError) as exc:
        DeepNestedState(
            items=["ok", 2],
            mapping={"key": 1},
            nested=NestedState(value="child"),
        )
    assert exc.value.path == (".items", "[1]")

    with pytest.raises(ValidationError) as exc:
        DeepNestedState(
            items=["ok"],
            mapping={1: 1},
            nested=NestedState(value="child"),
        )
    assert exc.value.path == (".mapping", "[1]")

    with pytest.raises(ValidationError) as exc:
        DeepNestedState(
            items=["ok"],
            mapping={"key": 1},
            nested={"value": 123},
        )
    assert exc.value.path == (".nested", ".value")

    class Wrapped(State):
        level1: DeepNestedState

    with pytest.raises(ValidationError) as exc:
        Wrapped(
            level1={
                "items": ["ok"],
                "mapping": {"key": 1},
                "nested": {"value": 123},
            },
        )
    assert exc.value.path == (".level1", ".nested", ".value")
