import re
from collections.abc import Callable, Mapping, Sequence, Set
from datetime import UTC, date, datetime, time, timedelta, timezone
from enum import Enum, StrEnum
from pathlib import Path
from typing import Any, Literal, NotRequired, Protocol, Required, TypedDict, runtime_checkable
from uuid import UUID, uuid4

import pytest

from haiway import MISSING, Missing, State
from haiway.state.attributes import AttributeAnnotation
from haiway.state.validation import AttributeValidator


class Color(Enum):
    RED = "red"
    GREEN = "green"
    BLUE = "blue"


class Status(StrEnum):
    ACTIVE = "active"
    INACTIVE = "inactive"


@runtime_checkable
class Processor(Protocol):
    def __call__(self, data: str) -> str: ...


class UserDict(TypedDict):
    name: str
    age: int
    email: NotRequired[str]
    active: Required[bool]


class NestedState(State):
    value: str


class SimpleState(State):
    name: str


def test_validator_basic_types() -> None:
    class BasicTypes(State):
        string_val: str
        int_val: int
        float_val: float
        bool_val: bool
        bytes_val: bytes

    # Valid values
    instance = BasicTypes(
        string_val="test",
        int_val=42,
        float_val=3.14,
        bool_val=True,
        bytes_val=b"data",
    )
    assert instance.string_val == "test"
    assert instance.int_val == 42
    assert instance.float_val == 3.14
    assert instance.bool_val is True
    assert instance.bytes_val == b"data"

    # Invalid types should raise TypeError
    with pytest.raises(TypeError, match="is not matching expected type"):
        BasicTypes(
            string_val=42,
            int_val=42,
            float_val=3.14,
            bool_val=True,
            bytes_val=b"data",
        )

    with pytest.raises(TypeError, match="is not matching expected type"):
        BasicTypes(
            string_val="test",
            int_val="not_int",
            float_val=3.14,
            bool_val=True,
            bytes_val=b"data",
        )


def test_validator_none_type() -> None:
    class NoneTest(State):
        none_val: None

    # Valid None
    instance = NoneTest(none_val=None)
    assert instance.none_val is None

    # Invalid non-None
    with pytest.raises(TypeError, match="is not matching expected type of 'None'"):
        NoneTest(none_val="not_none")


def test_validator_missing_type() -> None:
    class MissingTest(State):
        missing_val: Missing

    # Valid Missing
    instance = MissingTest(missing_val=MISSING)
    assert instance.missing_val is MISSING

    # Invalid non-Missing
    with pytest.raises(TypeError, match="is not matching expected type of 'Missing'"):
        MissingTest(missing_val="not_missing")


def test_validator_literal_type() -> None:
    class LiteralTest(State):
        mode: Literal["read", "write", "append"]
        count: Literal[1, 2, 3]

    # Valid literals
    instance = LiteralTest(mode="read", count=2)
    assert instance.mode == "read"
    assert instance.count == 2

    # Invalid literals
    with pytest.raises(ValueError, match="is not matching expected values"):
        LiteralTest(mode="invalid", count=2)

    with pytest.raises(ValueError, match="is not matching expected values"):
        LiteralTest(mode="read", count=5)


def test_validator_enum_type() -> None:
    class EnumTest(State):
        color: Color
        status: Status

    # Valid enums
    instance = EnumTest(color=Color.RED, status=Status.ACTIVE)
    assert instance.color == Color.RED
    assert instance.status == Status.ACTIVE

    # Invalid enum values
    with pytest.raises(TypeError, match="is not matching expected type"):
        EnumTest(color="red", status=Status.ACTIVE)

    with pytest.raises(TypeError, match="is not matching expected type"):
        EnumTest(color=Color.RED, status="active")


def test_validator_sequence_type() -> None:
    class SequenceTest(State):
        items: Sequence[str]
        numbers: Sequence[int]

    # Valid sequences (lists converted to tuples)
    instance = SequenceTest(items=["a", "b", "c"], numbers=[1, 2, 3])
    assert instance.items == ("a", "b", "c")
    assert instance.numbers == (1, 2, 3)

    # Valid empty sequence
    instance_empty = SequenceTest(items=[], numbers=[])
    assert instance_empty.items == ()
    assert instance_empty.numbers == ()

    # Invalid element types
    with pytest.raises(TypeError):
        SequenceTest(items=[1, 2, 3], numbers=[1, 2, 3])

    # Invalid non-sequence type
    with pytest.raises(TypeError, match="is not matching expected type"):
        SequenceTest(items="not_a_sequence", numbers=[1, 2, 3])


def test_validator_set_type() -> None:
    class SetTest(State):
        tags: Set[str]
        numbers: Set[int]

    # Valid sets (converted to frozenset)
    instance = SetTest(tags={"a", "b", "c"}, numbers={1, 2, 3})
    assert instance.tags == frozenset({"a", "b", "c"})
    assert instance.numbers == frozenset({1, 2, 3})

    # Invalid element types
    with pytest.raises(TypeError):
        SetTest(tags={1, 2, 3}, numbers={1, 2, 3})

    # Invalid non-set type
    with pytest.raises(TypeError, match="is not matching expected type"):
        SetTest(tags="not_a_set", numbers={1, 2, 3})


def test_validator_mapping_type() -> None:
    class MappingTest(State):
        data: Mapping[str, int]

    # Valid mapping
    instance = MappingTest(data={"a": 1, "b": 2})
    assert instance.data == {"a": 1, "b": 2}

    # Invalid key types
    with pytest.raises(TypeError):
        MappingTest(data={1: 1, 2: 2})

    # Invalid value types
    with pytest.raises(TypeError):
        MappingTest(data={"a": "not_int", "b": "also_not_int"})

    # Invalid non-mapping type
    with pytest.raises(TypeError, match="is not matching expected type"):
        MappingTest(data="not_a_mapping")


def test_validator_tuple_type() -> None:
    class TupleTest(State):
        fixed: tuple[str, int, bool]
        variable: tuple[str, ...]

    # Valid fixed tuple
    instance = TupleTest(fixed=["hello", 42, True], variable=["a", "b", "c"])
    assert instance.fixed == ("hello", 42, True)
    assert instance.variable == ("a", "b", "c")

    # Invalid fixed tuple length
    with pytest.raises(ValueError, match="is not matching expected type"):
        TupleTest(fixed=["hello", 42], variable=["a", "b"])

    # Invalid fixed tuple types
    with pytest.raises(TypeError):
        TupleTest(fixed=["hello", "not_int", True], variable=["a", "b"])

    # Invalid variable tuple element types
    with pytest.raises(TypeError):
        TupleTest(fixed=["hello", 42, True], variable=["a", 1, "c"])


def test_validator_union_type() -> None:
    class UnionTest(State):
        value: str | int
        optional: str | None

    # Valid union values
    instance1 = UnionTest(value="string", optional="text")
    assert instance1.value == "string"
    assert instance1.optional == "text"

    instance2 = UnionTest(value=42, optional=None)
    assert instance2.value == 42
    assert instance2.optional is None

    # Invalid union type
    with pytest.raises(ExceptionGroup, match="is not matching expected type"):
        UnionTest(value=[], optional="text")


def test_validator_callable_type() -> None:
    class CallableTest(State):
        func: Callable[[], None]
        processor: Processor

    def test_func() -> None:
        pass

    def test_processor(data: str) -> str:
        return data.upper()

    # Valid callables
    instance = CallableTest(func=test_func, processor=test_processor)
    assert callable(instance.func)
    assert isinstance(instance.processor, Processor)

    # Invalid non-callable
    with pytest.raises(TypeError, match="is not matching expected type"):
        CallableTest(func="not_callable", processor=test_processor)


def test_validator_typed_dict() -> None:
    class TypedDictTest(State):
        user: UserDict

    # Valid TypedDict with all fields
    instance = TypedDictTest(
        user={"name": "John", "age": 30, "email": "john@example.com", "active": True}
    )
    assert instance.user["name"] == "John"
    assert instance.user["age"] == 30
    assert instance.user["email"] == "john@example.com"
    assert instance.user["active"] is True

    # Valid TypedDict with missing NotRequired field
    instance2 = TypedDictTest(user={"name": "Jane", "age": 25, "active": False})
    assert instance2.user["name"] == "Jane"
    assert "email" not in instance2.user

    # Invalid missing Required field
    with pytest.raises((TypeError, ValueError)):  # Should fail validation due to missing 'active'
        TypedDictTest(user={"name": "Bob", "age": 35, "email": "bob@example.com"})


def test_validator_state_type() -> None:
    class StateTest(State):
        nested: NestedState
        simple: SimpleState

    # Valid State instances
    instance = StateTest(nested=NestedState(value="test"), simple=SimpleState(name="example"))
    assert instance.nested.value == "test"
    assert instance.simple.name == "example"

    # Invalid State type - dict conversion not supported for State types in validation
    with pytest.raises(TypeError):
        StateTest(nested="not_a_state", simple=SimpleState(name="valid"))

    # Another invalid type test
    with pytest.raises(TypeError):
        StateTest(nested=NestedState(value="valid"), simple="not_a_state")


def test_validator_complex_types() -> None:
    class ComplexTest(State):
        uuid_val: UUID
        date_val: date
        datetime_val: datetime
        time_val: time
        timedelta_val: timedelta
        timezone_val: timezone
        path_val: Path
        pattern_val: re.Pattern[str]

    # Valid complex types
    uuid_val = uuid4()
    date_val = date.today()
    datetime_val = datetime.now()
    time_val = time(12, 30, 45)
    timedelta_val = timedelta(days=1, hours=2)
    timezone_val = UTC
    path_val = Path("/tmp/test")
    pattern_val = re.compile(r"test.*")

    instance = ComplexTest(
        uuid_val=uuid_val,
        date_val=date_val,
        datetime_val=datetime_val,
        time_val=time_val,
        timedelta_val=timedelta_val,
        timezone_val=timezone_val,
        path_val=path_val,
        pattern_val=pattern_val,
    )

    assert instance.uuid_val == uuid_val
    assert instance.date_val == date_val
    assert instance.datetime_val == datetime_val
    assert instance.time_val == time_val
    assert instance.timedelta_val == timedelta_val
    assert instance.timezone_val == timezone_val
    assert instance.path_val == path_val
    assert instance.pattern_val == pattern_val

    # Invalid types
    with pytest.raises(TypeError, match="is not matching expected type"):
        ComplexTest(
            uuid_val="not_uuid",
            date_val=date_val,
            datetime_val=datetime_val,
            time_val=time_val,
            timedelta_val=timedelta_val,
            timezone_val=timezone_val,
            path_val=path_val,
            pattern_val=pattern_val,
        )


def test_validator_recursive_state() -> None:
    class RecursiveState(State):
        name: str
        child: "RecursiveState | None"

    # Valid recursive structure
    instance = RecursiveState(name="parent", child=RecursiveState(name="child", child=None))

    assert instance.name == "parent"
    assert instance.child is not None
    assert instance.child.name == "child"
    assert instance.child.child is None

    # Valid None child
    instance2 = RecursiveState(name="single", child=None)
    assert instance2.name == "single"
    assert instance2.child is None


def test_validator_generic_state() -> None:
    class GenericState[T](State):
        value: T

    class ContainerState(State):
        string_generic: GenericState[str]
        int_generic: GenericState[int]

    # Valid generic instances
    instance = ContainerState(
        string_generic=GenericState[str](value="test"), int_generic=GenericState[int](value=42)
    )

    assert instance.string_generic.value == "test"
    assert instance.int_generic.value == 42

    # Invalid type for generic state
    with pytest.raises(TypeError):
        ContainerState(string_generic="not_a_state", int_generic=GenericState[int](value=42))


def test_validation_error_messages() -> None:
    class ErrorTest(State):
        string_val: str
        literal_val: Literal["a", "b"]
        none_val: None

    # String type error
    with pytest.raises(TypeError) as exc_info:
        ErrorTest(string_val=42, literal_val="a", none_val=None)
    assert "is not matching expected type" in str(exc_info.value)

    # Literal value error
    with pytest.raises(ValueError) as exc_info:
        ErrorTest(string_val="test", literal_val="invalid", none_val=None)
    assert "is not matching expected values" in str(exc_info.value)

    # None type error
    with pytest.raises(TypeError) as exc_info:
        ErrorTest(string_val="test", literal_val="a", none_val="not_none")
    assert "is not matching expected type of 'None'" in str(exc_info.value)


def test_validation_any_type() -> None:
    class AnyTest(State):
        anything: Any

    # Any accepts all types
    instance1 = AnyTest(anything="string")
    assert instance1.anything == "string"

    instance2 = AnyTest(anything=42)
    assert instance2.anything == 42

    instance3 = AnyTest(anything=[1, 2, 3])
    assert instance3.anything == [1, 2, 3]

    instance4 = AnyTest(anything=None)
    assert instance4.anything is None


def test_validator_with_defaults() -> None:
    class DefaultTest(State):
        required: str
        optional: str = "default"
        optional_union: str | None = None

    # Using defaults
    instance = DefaultTest(required="test")
    assert instance.required == "test"
    assert instance.optional == "default"
    assert instance.optional_union is None

    # Overriding defaults
    instance2 = DefaultTest(required="test", optional="custom", optional_union="not_none")
    assert instance2.required == "test"
    assert instance2.optional == "custom"
    assert instance2.optional_union == "not_none"

    # Invalid override of default
    with pytest.raises(TypeError):
        DefaultTest(required="test", optional=42)


def test_attribute_validator_direct_usage() -> None:
    # Create validator for str type
    str_annotation = AttributeAnnotation(origin=str, arguments=())
    validator = AttributeValidator.of(str_annotation, recursion_guard={})

    # Valid string
    result = validator("test")
    assert result == "test"

    # Invalid type
    with pytest.raises(TypeError):
        validator(42)


def test_unsupported_type_annotation() -> None:
    # Create annotation for arbitrary type
    class ArbitraryType:
        pass

    annotation = AttributeAnnotation(origin=ArbitraryType, arguments=())

    validator = AttributeValidator.of(annotation, recursion_guard={})
    assert validator is not None
