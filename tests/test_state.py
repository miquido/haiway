from collections.abc import Callable, Mapping, Sequence, Set
from copy import copy, deepcopy
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, NotRequired, Protocol, Required, Self, TypedDict, runtime_checkable
from uuid import UUID, uuid4

from pytest import raises

from haiway import MISSING, Default, Missing, State


def test_basic_initializes_with_arguments() -> None:
    class Selection(StrEnum):
        A = "A"
        B = "B"

    class Nes(State):
        val: str

    class TypedValues(TypedDict):
        val: str
        mis: int | Missing
        req: Required[Nes]
        nreq: NotRequired[bool]

    @runtime_checkable
    class Proto(Protocol):
        def __call__(self) -> None: ...

    class Basics(State):
        uuid: UUID
        date: date
        datetime: datetime
        string: str
        literal: Literal["A", "B"]
        sequence: Sequence[str]
        string_set: Set[str]
        frozen: tuple[int, ...]
        integer: int
        union: str | int
        optional: str | None
        none: None
        function: Callable[[], None]
        proto: Proto
        selection: Selection
        typeddict: TypedValues

    basic = Basics(
        uuid=uuid4(),
        date=date.today(),
        datetime=datetime.now(),
        string="string",
        literal="A",
        sequence=["a", "b", "c"],
        string_set={"a", "b"},
        frozen=(1, 2, 3),
        integer=0,
        union="union",
        optional="optional",
        none=None,
        function=lambda: None,
        proto=lambda: None,
        selection=Selection.A,
        typeddict={
            "val": "ok",
            "mis": 42,
            "req": Nes(val="ok"),
        },
    )
    assert basic.string == "string"
    assert basic.literal == "A"
    assert basic.sequence == ("a", "b", "c")
    assert basic.string_set == {"a", "b"}
    assert basic.frozen == (1, 2, 3)
    assert basic.integer == 0
    assert basic.union == "union"
    assert basic.optional == "optional"
    assert basic.none is None
    assert basic.selection == Selection.A
    assert basic.typeddict == TypedValues(
        val="ok",
        mis=42,
        req=Nes(val="ok"),
    )
    assert callable(basic.function)
    assert isinstance(basic.proto, Proto)


def test_basic_initializes_with_defaults() -> None:
    class Basics(State):
        string: str = ""
        integer: int = 0
        optional: str | None = None
        unique: UUID = Default(factory=uuid4)
        same: UUID = Default(uuid4())

    basic = Basics()
    assert basic.string == ""
    assert basic.integer == 0
    assert basic.optional is None
    assert basic.unique is not Basics().unique
    assert basic.same is Basics().same


def test_basic_equals_checks_properties() -> None:
    class Basics(State):
        string: str
        integer: int

    assert Basics(string="a", integer=1) == Basics(string="a", integer=1)
    assert Basics(string="a", integer=1) != Basics(string="b", integer=1)
    assert Basics(string="a", integer=1) != Basics(string="a", integer=2)
    assert Basics(string="a", integer=1) != Basics(string="b", integer=2)


def test_basic_initializes_with_arguments_and_defaults() -> None:
    class Basics(State):
        string: str
        integer: int = 0
        optional: str | None = None

    basic = Basics(
        string="string",
        integer=42,
    )
    assert basic.string == "string"
    assert basic.integer == 42
    assert basic.optional is None


def test_parametrized_initializes_with_proper_parameters() -> None:
    class Parametrized[T](State):
        value: T

    parametrized_string = Parametrized(
        value="string",
    )
    assert parametrized_string.value == "string"

    parametrized_int = Parametrized(
        value=42,
    )
    assert parametrized_int.value == 42

    assert parametrized_string != parametrized_int


def test_nested_initializes_with_proper_arguments() -> None:
    class Nested(State):
        string: str

    class Recursive(State):
        nested: Nested
        recursion: "Recursive | None"
        self_recursion: Self | None

    recursive = Recursive(
        nested=Nested(string="one"),
        recursion=Recursive(
            nested=Nested(string="two"),
            recursion=None,
            self_recursion=None,
        ),
        self_recursion=None,
    )
    assert recursive.nested == Nested(string="one")
    assert recursive.recursion == Recursive(
        nested=Nested(string="two"),
        recursion=None,
        self_recursion=None,
    )


def test_dict_skips_missing_properties() -> None:
    class Basics(State):
        string: str
        integer: int | Missing | None

    assert Basics(string="a", integer=1).to_mapping() == {"string": "a", "integer": 1}
    assert Basics(string="a", integer=MISSING).to_mapping() == {"string": "a"}
    assert Basics(string="a", integer=None).to_mapping() == {"string": "a", "integer": None}


def test_initialization_allows_missing_properties() -> None:
    class Basics(State):
        string: str
        integer: int | Missing | None

    assert Basics(**{"string": "a", "integer": 1}) == Basics(string="a", integer=1)
    assert Basics(**{"string": "a", "integer": None}) == Basics(string="a", integer=None)
    assert Basics(**{"string": "a"}) == Basics(string="a", integer=MISSING)


def test_generic_subtypes_validation() -> None:
    class NestedGeneric[T](State):
        value: T

    class Generic[T](State):
        nested: NestedGeneric[T]

    class Container(State):
        generic: Generic[str]

    assert isinstance(Generic[str](nested=NestedGeneric[str](value="ok")), Generic)
    assert isinstance(Generic(nested=NestedGeneric[str](value="ok")), Generic)
    assert isinstance(Generic(nested=NestedGeneric(value="ok")), Generic)

    assert isinstance(Generic[str](nested=NestedGeneric[str](value="ok")), Generic[Any])
    assert isinstance(Generic(nested=NestedGeneric[str](value="ok")), Generic[Any])
    assert isinstance(Generic[str](nested=NestedGeneric(value="ok")), Generic[Any])
    assert isinstance(Generic(nested=NestedGeneric(value="ok")), Generic[Any])

    assert isinstance(Generic[str](nested=NestedGeneric[str](value="ok")), Generic[str])
    assert isinstance(Generic(nested=NestedGeneric[str](value="ok")), Generic[str])
    assert isinstance(Generic[str](nested=NestedGeneric(value="ok")), Generic[str])
    assert isinstance(Generic(nested=NestedGeneric(value="ok")), Generic[str])

    with raises(TypeError):
        _ = Generic[int](nested=NestedGeneric[str](value="ok"))

    with raises(TypeError):
        _ = Container(generic=Generic(nested=NestedGeneric(value=42)))

    with raises(TypeError):
        _ = Container(generic=Generic[int](nested=NestedGeneric[str](value="ok")))

    # not raises
    _ = Container(generic=Generic(nested=NestedGeneric(value="ok")))


def test_copying_leaves_same_object() -> None:
    class Nested(State):
        string: str

    class Copied(State):
        string: str
        nested: Nested

    origin = Copied(string="42", nested=Nested(string="answer"))
    assert copy(origin) is origin
    assert deepcopy(origin) is origin


def test_hash_consistency_with_missing_values() -> None:
    class HashTest(State):
        required: str
        optional: int | Missing
        nullable: str | None

    # Test that hash is consistent regardless of how MISSING values are specified
    obj1 = HashTest(required="test", optional=MISSING, nullable=None)
    obj2 = HashTest(required="test", nullable=None)  # optional omitted
    obj3 = HashTest(**{"required": "test", "nullable": None})  # optional missing from dict

    assert hash(obj1) == hash(obj2) == hash(obj3)
    assert obj1 == obj2 == obj3


def test_hash_with_unhashable_attributes() -> None:
    class UnhashableTest(State):
        name: str
        data_list: Sequence[int]
        data_dict: Mapping[str, int]
        data_set: Set[str]
        function: Callable[..., Any] | None

    obj1 = UnhashableTest(
        name="test",
        data_list=[1, 2, 3],
        data_dict={"a": 1, "b": 2},
        data_set={"x", "y"},
        function=None,
    )

    # Should not raise TypeError
    hash_value = hash(obj1)
    assert isinstance(hash_value, int)

    # Objects with same data should have same hash
    obj2 = UnhashableTest(
        name="test",
        data_list=[1, 2, 3],
        data_dict={"a": 1, "b": 2},
        data_set={"x", "y"},
        function=None,
    )
    assert hash(obj1) == hash(obj2)

    # Objects with different data should have different hashes (usually)
    obj3 = UnhashableTest(
        name="test",
        data_list=[1, 2, 3],
        data_dict={"a": 1, "b": 2},
        data_set={"x", "y"},
        function=lambda: ...,  # Different function
    )
    obj4 = UnhashableTest(
        name="test",
        data_list=[1, 2, 3],
        data_dict={"a": 1, "b": 2},
        data_set={"x", "y"},
        function=lambda: ...,  # Different function
    )
    # Note: Hash collisions are possible but unlikely
    assert hash(obj3) != hash(obj4)


def test_hash_with_dict_key_order_independence() -> None:
    class DictTest(State):
        data: Mapping[str, int]

    # Dictionaries with same content but different creation order should hash the same
    obj1 = DictTest(data={"a": 1, "b": 2, "c": 3})
    obj2 = DictTest(data={"c": 3, "a": 1, "b": 2})

    assert hash(obj1) == hash(obj2)
    assert obj1 == obj2


def test_hash_with_custom_objects() -> None:
    # Test with a simple custom object that can go through Any validation
    class CustomTest(State):
        name: str
        custom: Any  # Use Any to allow custom objects

    custom_obj1 = {"value": "test"}  # Use dict as custom object
    custom_obj2 = {"value": "test"}  # Same content

    obj1 = CustomTest(name="test", custom=custom_obj1)
    obj2 = CustomTest(name="test", custom=custom_obj2)

    # Should not raise TypeError
    hash_value1 = hash(obj1)
    hash_value2 = hash(obj2)

    assert isinstance(hash_value1, int)
    assert isinstance(hash_value2, int)
    # Objects with same content should have same hash
    assert hash_value1 == hash_value2


def test_hash_performance_with_many_attributes() -> None:
    # Test that the optimized hash function performs well with many attributes
    class ManyAttrs(State):
        attr_0: int = 0
        attr_1: int = 1
        attr_2: int = 2
        attr_3: int = 3
        attr_4: int = 4
        attr_5: int = 5
        attr_6: int = 6
        attr_7: int = 7
        attr_8: int = 8
        attr_9: int = 9

    obj = ManyAttrs()

    # Should compute hash without issues
    hash_value = hash(obj)
    assert isinstance(hash_value, int)


def test_hash_with_nested_unhashable_collections() -> None:
    class NestedTest(State):
        nested_list: Sequence[Sequence[int]]
        nested_dict: Mapping[str, Mapping[str, int]]

    obj = NestedTest(nested_list=[[1, 2], [3, 4]], nested_dict={"outer": {"inner": 42}})

    # Should handle nested unhashable collections
    hash_value = hash(obj)
    assert isinstance(hash_value, int)


def test_hash_stability_across_instances() -> None:
    class StabilityTest(State):
        value: str
        number: int

    # Same data should always produce same hash
    obj1 = StabilityTest(value="test", number=42)
    obj2 = StabilityTest(value="test", number=42)

    assert hash(obj1) == hash(obj2)

    # Hash should be stable across multiple calls
    hash1 = hash(obj1)
    hash2 = hash(obj1)
    hash3 = hash(obj1)

    assert hash1 == hash2 == hash3


def test_hash_excludes_missing_values() -> None:
    class MissingTest(State):
        always_present: str
        sometimes_missing: int | Missing
        sometimes_none: str | None

    # Test various combinations of missing values
    obj1 = MissingTest(always_present="test", sometimes_missing=42, sometimes_none="value")
    obj2 = MissingTest(always_present="test", sometimes_missing=MISSING, sometimes_none="value")
    obj3 = MissingTest(always_present="test", sometimes_missing=MISSING, sometimes_none=None)

    # All should be hashable
    hash1 = hash(obj1)
    hash2 = hash(obj2)
    hash3 = hash(obj3)

    assert isinstance(hash1, int)
    assert isinstance(hash2, int)
    assert isinstance(hash3, int)

    # obj2 should have different hash from obj1 since it's missing a value
    assert hash1 != hash2

    # obj3 should have different hash from obj2 since None != MISSING
    assert hash2 != hash3
