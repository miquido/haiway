import asyncio
from collections.abc import Callable, Mapping, Sequence, Set
from copy import copy, deepcopy
from datetime import date, datetime
from enum import StrEnum
from typing import (
    Annotated,
    Any,
    Literal,
    NotRequired,
    Protocol,
    Required,
    Self,
    TypedDict,
    runtime_checkable,
)
from uuid import UUID, uuid4

from pytest import mark, raises

from haiway import MISSING, Alias, Default, Missing, State, ValidationError, ctx


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
        unique: UUID = Default(default_factory=uuid4)
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


def test_to_mapping_uses_alias_for_nested_states() -> None:
    class Child(State):
        value: Annotated[str, Alias("child_value")]

    class Container(State):
        child: Annotated[Child, Alias("child_alias")]
        metadata: Annotated[str, Alias("meta_alias")]

    container = Container(
        child=Child(value="ok"),
        metadata="info",
    )
    assert container.to_mapping() == {
        "child_alias": {"child_value": "ok"},
        "meta_alias": "info",
    }


def test_from_mapping_accepts_nested_aliases() -> None:
    class Child(State):
        value: Annotated[str, Alias("child_value")]

    class Container(State):
        child: Annotated[Child, Alias("child_alias")]

    container = Container.from_mapping(
        {
            "child_alias": {
                "child_value": "ok",
            },
        }
    )
    assert container.child == Child(value="ok")
    assert container.to_mapping() == {
        "child_alias": {"child_value": "ok"},
    }


def test_updating_honors_aliases() -> None:
    class Example(State):
        value: Annotated[int, Alias("external")]

    example = Example(value=1)
    updated = example.updating(external=2)

    assert updated.value == 2
    assert updated != example


def test_initialization_handles_conflicting_alias_and_field() -> None:
    class Example(State):
        value: Annotated[int, Alias("external")]

    assert Example(value=1, external=2).value == 2
    assert Example(external=2, value=1).value == 2


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

    with raises(ValidationError):
        _ = Generic[int](nested=NestedGeneric[str](value="ok"))

    with raises(ValidationError):
        _ = Container(generic=Generic(nested=NestedGeneric(value=42)))

    with raises(ValidationError):
        _ = Container(generic=Generic[int](nested=NestedGeneric[str](value="ok")))

    # not raises
    _ = Container(generic=Generic(nested=NestedGeneric(value="ok")))


def test_copying_leaves_same_object() -> None:
    class Nested(State):
        string: str
        payload: Any

    class Copied(State):
        string: str
        nested: Nested
        items: Sequence[Nested]
        payload: Any

    nested_payload: dict[str, list[str]] = {"inner": ["x"]}
    payload: dict[str, list[int]] = {"numbers": [1, 2]}
    nested = Nested(string="answer", payload=nested_payload)
    origin = Copied(
        string="42",
        nested=nested,
        items=[nested],
        payload=payload,
    )
    assert copy(origin) is origin
    deep_copied = deepcopy(origin)
    assert deep_copied is not origin
    assert deep_copied == origin
    assert deep_copied.nested is not origin.nested
    assert deep_copied.items is not origin.items
    assert deep_copied.items[0] is not origin.items[0]
    assert deep_copied.payload is not origin.payload
    assert deep_copied.payload["numbers"] is not origin.payload["numbers"]
    assert deep_copied.nested.payload is not origin.nested.payload
    assert deep_copied.nested.payload["inner"] is not origin.nested.payload["inner"]


def test_deepcopy_preserves_aliasing_and_cycles() -> None:
    class Shared(State):
        value: int

    class Cyclic(State):
        payload: Any

    class Box:
        def __init__(self) -> None:
            self.ref: Any | None = None

    shared = Shared(value=1)
    result = deepcopy([shared, shared])
    assert result[0] is result[1]
    assert result[0] is not shared

    box = Box()
    cyclic = Cyclic(payload=box)
    box.ref = cyclic
    cyclic_copy = deepcopy(cyclic)
    assert cyclic_copy is not cyclic
    assert cyclic_copy.payload.ref is cyclic_copy


def test_updating_returns_self_when_no_changes() -> None:
    class Example(State):
        value: int
        text: str

    instance = Example(value=1, text="a")
    assert instance.updating() is instance


def test_updating_only_validates_provided_attributes() -> None:
    counter: dict[str, int] = {"calls": 0}

    class Tracked(State):
        value: int

        @classmethod
        def validate(
            cls,
            value: Any,
        ) -> Self:
            counter["calls"] += 1
            return super().validate(value)

    class Container(State):
        first: int
        tracked: Tracked

    instance = Container(first=1, tracked=Tracked(value=1))
    counter["calls"] = 0

    updated_first = instance.updating(first=2)
    assert updated_first is not instance
    assert updated_first.tracked is instance.tracked
    assert counter["calls"] == 0

    counter["calls"] = 0
    replacement = instance.updating(tracked=Tracked(value=2))
    assert replacement is not instance
    assert counter["calls"] == 1


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


@mark.asyncio
async def test_concurrent_state_initialization() -> None:
    """Verify concurrent state requests don't double-initialize."""
    init_count = 0

    class CountingState(State):
        def __init__(self) -> None:
            nonlocal init_count
            init_count += 1

    async with ctx.scope("concurrent"):
        # Request state concurrently from multiple coroutines
        async def request_state() -> CountingState:
            return ctx.state(CountingState)

        results = await asyncio.gather(
            request_state(),
            request_state(),
            request_state(),
        )

        # All should reference the same instance
        assert results[0] is results[1] is results[2]
        # Initialization should happen exactly once
        assert init_count == 1


@mark.asyncio
async def test_no_deadlock_on_recursive_state_access() -> None:
    """Verify recursive state access during init doesn't cause deadlock."""
    init_count = 0

    class DependentState(State):
        value: str = "default"

    class RecursiveState(State):
        value: str = "recursive"

        def __init__(self) -> None:
            nonlocal init_count
            init_count += 1
            # Access another state during initialization
            # This would deadlock with a non-reentrant lock
            ctx.state(DependentState)

    async with ctx.scope("recursive"):
        # This should not deadlock
        recursive = ctx.state(RecursiveState)
        assert recursive.value == "recursive"
        assert ctx.state(RecursiveState) is recursive
        # Initialization happened exactly once (despite recursive access)
        assert init_count == 1


def test_serializable_required_rejects_missing_spec() -> None:
    with raises(TypeError, match="requires serialization"):

        class Unserializable(State, serializable=True):
            func: Callable[[], None]


def test_serializable_required_accepts_schema() -> None:
    class Serializable(State, serializable=True):
        value: int

    assert Serializable.json_schema(required=True) is not None
