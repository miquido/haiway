import copy
import weakref
from collections.abc import Mapping, Sequence, Set
from typing import Annotated, Any, Self, final

import pytest
from pytest import mark, raises

from haiway import Alias, Missing, State, ValidationError
from haiway.types.frozen import refuse_assignment, refuse_deletion


class Point(State):
    x: int
    y: int


class Collections(State):
    sequence: Sequence[int]
    items: Set[int]
    mapping: Mapping[str, int]


class Aliased(State):
    identifier: Annotated[str, Alias("id")]


# --- immutability -----------------------------------------------------------


def test_assignment_is_refused() -> None:
    point = Point(x=1, y=2)

    with raises(AttributeError, match="cannot be modified"):
        point.x = 5  # pyright: ignore[reportAttributeAccessIssue]

    assert point.x == 1


def test_deletion_is_refused() -> None:
    point = Point(x=1, y=2)

    with raises(AttributeError, match="cannot be deleted"):
        del point.x  # pyright: ignore[reportAttributeAccessIssue]

    assert point.x == 1


def test_new_attributes_can_not_be_added() -> None:
    point = Point(x=1, y=2)

    with raises(AttributeError):
        point.z = 3  # pyright: ignore[reportAttributeAccessIssue]


def test_instances_have_neither_dict_nor_weak_references() -> None:
    point = Point(x=1, y=2)

    with raises(AttributeError):
        _ = point.__dict__

    with raises(TypeError):
        weakref.ref(point)


def test_updating_creates_new_instance_leaving_original_intact() -> None:
    original = Point(x=1, y=2)
    updated = original.updating(x=5)

    assert updated is not original
    assert updated == Point(x=5, y=2)
    assert original == Point(x=1, y=2)


def test_copy_replace_follows_updating() -> None:
    point = Point(x=1, y=2)

    assert copy.replace(point, y=9) == Point(x=1, y=9)
    assert copy.replace(point) is point


def test_shallow_copy_returns_same_instance() -> None:
    point = Point(x=1, y=2)

    assert copy.copy(point) is point


def test_deepcopy_without_memo_creates_equal_instance() -> None:
    point = Point(x=1, y=2)
    duplicate = point.__deepcopy__(None)

    assert duplicate == point
    assert duplicate is not point
    assert type(duplicate) is Point


class _Holding(State):
    first: int
    rest: Any


def test_deepcopy_never_exposes_a_partially_copied_instance() -> None:
    # the copy is built once its values are copied - the code copying them can't get
    # hold of one with some of its attributes missing
    seen: list[Any] = []

    class Failing:
        def __deepcopy__(self, memo: dict[int, Any]) -> Any:
            seen.extend(memo.values())
            raise RuntimeError("copy failed")

    memo: dict[int, Any] = {}
    with raises(RuntimeError, match="copy failed"):
        copy.deepcopy(_Holding(first=1, rest=Failing()), memo)

    assert not any(isinstance(value, _Holding) for value in (*seen, *memo.values()))


@final
class _Record:
    value: int
    __slots__ = ("value",)

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(self, *, value: int) -> None:
        object.__setattr__(self, "value", value)

    def __deepcopy__(self, memo: dict[int, Any] | None) -> Self:
        return self  # its own copy


class _Recording(State):
    record: _Record


class _CopiedAsItself(State):
    value: int

    def __deepcopy__(self, memo: dict[int, Any] | None) -> Self:
        return self


class _Wrapping(State):
    inner: _CopiedAsItself


def test_deepcopy_uses_the_copy_of_held_instances_defining_their_own() -> None:
    # a frozen record is its own copy, and equal to itself alone - the copy holding it has to
    # hold the very record, or it would not be equal to what it was copied from
    recording = _Recording(record=_Record(value=1))
    duplicate = copy.deepcopy(recording)
    assert duplicate == recording
    assert duplicate.record is recording.record

    wrapping = _Wrapping(inner=_CopiedAsItself(value=1))
    assert copy.deepcopy(wrapping).inner is wrapping.inner


class _Hashable:
    # mutable, and hashable by identity
    def __init__(self) -> None:
        self.items: list[int] = []


def test_deepcopy_copies_a_frozenset_the_way_copy_does() -> None:
    held = _Hashable()
    duplicate = copy.deepcopy(_Holding(first=1, rest=frozenset({held})))
    assert next(iter(duplicate.rest)) is not held  # copied with what it holds

    atomic = frozenset({1, "a"})
    assert copy.deepcopy(_Holding(first=1, rest=atomic)).rest is atomic


def test_deepcopy_keeps_a_value_referring_back_to_the_instance() -> None:
    loop: list[Any] = []
    original = _Holding(first=1, rest=loop)
    loop.append(original)

    duplicate = copy.deepcopy(original)

    assert duplicate is not original
    assert duplicate.rest is not loop
    assert duplicate.rest[0] is duplicate


# --- sources are not shared -------------------------------------------------


def test_mutating_source_collections_does_not_affect_state() -> None:
    sequence = [1, 2]
    items = {1, 2}
    mapping = {"a": 1}
    state = Collections(sequence=sequence, items=items, mapping=mapping)

    sequence.append(3)
    items.add(3)
    mapping["b"] = 2

    assert state.sequence == (1, 2)
    assert state.items == frozenset({1, 2})
    assert state.mapping == {"a": 1}


def test_sequences_and_sets_are_converted_to_immutable_containers() -> None:
    state = Collections(sequence=[1, 2], items={1, 2}, mapping={})

    assert isinstance(state.sequence, tuple)
    assert isinstance(state.items, frozenset)


def test_updating_converts_collections_as_well() -> None:
    state = Collections(sequence=[1], items={1}, mapping={}).updating(
        sequence=[5, 6],
        items={7},
    )

    assert state.sequence == (5, 6)
    assert state.items == frozenset({7})


def test_held_mappings_can_not_be_mutated() -> None:
    class Held(State):
        mapping: Mapping[str, int]
        concrete: dict[str, int]
        nested: Mapping[str, Sequence[int]]

    held = Held(mapping={"a": 1}, concrete={"a": 1}, nested={"k": [1]})

    with raises(AttributeError):
        held.mapping["b"] = 2  # pyright: ignore[reportIndexIssue]

    with raises(AttributeError):
        held.concrete["b"] = 2  # pyright: ignore[reportIndexIssue]

    assert held.nested["k"] == (1,)
    assert held.mapping == {"a": 1}
    assert copy.deepcopy(held) == held


# --- construction -----------------------------------------------------------


def test_construction_refuses_positional_arguments() -> None:
    with raises(TypeError):
        Point(1, 2)  # pyright: ignore[reportCallIssue]


def test_construction_refuses_missing_required_attribute() -> None:
    with raises(ValidationError) as caught:
        Point(x=1)  # pyright: ignore[reportCallIssue]

    assert ".y" in str(caught.value)


def test_validation_error_names_the_failing_attribute() -> None:
    with raises(ValidationError) as caught:
        Point(x="nope", y=1)  # pyright: ignore[reportArgumentType]

    assert ".x" in str(caught.value)


def test_missing_marks_optional_attribute() -> None:
    class Optional(State):
        value: int | Missing

    assert Optional().value is Missing  # pyright: ignore[reportCallIssue]
    assert Optional(value=Missing).value is Missing
    assert Optional(value=1).updating(value=Missing).value is Missing


# --- pattern matching -------------------------------------------------------


def test_positional_pattern_matching_uses_declaration_order() -> None:
    match Point(x=1, y=2):
        case Point(first, second):  # pyright: ignore[reportGeneralTypeIssues]
            assert (first, second) == (1, 2)

        case _:
            raise AssertionError("should match")


def test_keyword_pattern_matching() -> None:
    match Point(x=1, y=2):
        case Point(y=2, x=value):
            assert value == 1

        case _:
            raise AssertionError("should match")


def test_pattern_matching_works_for_specialized_generic() -> None:
    class Box[T](State):
        value: T
        label: str = "l"

    match Box[int](value=3):
        case Box(value, label):  # pyright: ignore[reportGeneralTypeIssues]
            assert (value, label) == (3, "l")

        case _:
            raise AssertionError("should match")


# --- alias handling ---------------------------------------------------------


def test_alias_and_name_are_both_accepted_everywhere() -> None:
    assert Aliased(id="a").identifier == "a"  # pyright: ignore[reportCallIssue]
    assert Aliased(identifier="a").identifier == "a"
    assert Aliased.validate({"id": "a"}).identifier == "a"
    assert Aliased.validate({"identifier": "a"}).identifier == "a"
    assert Aliased(id="a").updating(id="b").identifier == "b"  # pyright: ignore[reportCallIssue]


def test_validate_refuses_alias_and_name_together() -> None:
    with raises(TypeError, match="Duplicate attribute"):
        Aliased.validate({"id": "a", "identifier": "b"})


def test_validate_refuses_non_mapping_and_unknown_keys() -> None:
    with raises(TypeError, match="not matching expected type"):
        Point.validate(3)

    with raises(TypeError, match="Unexpected attribute 'z'"):
        Point.validate({"x": 1, "y": 2, "z": 3})


def test_validate_returns_instances_unchanged() -> None:
    point = Point(x=1, y=2)

    assert Point.validate(point) is point


def test_class_path_resolves_attribute_paths() -> None:
    assert Point._.x.__class__.__name__ == "AttributePath"  # pyright: ignore[reportAttributeAccessIssue]
    assert Point._.x(Point(x=4, y=5)) == 4  # pyright: ignore[reportAttributeAccessIssue, reportCallIssue]


@mark.parametrize("recursive", [True, False])
def test_to_mapping_omits_missing_values(recursive: bool) -> None:
    class Optional(State):
        value: int | Missing
        other: str = "x"

    assert Optional().to_mapping(recursive=recursive) == {"other": "x"}


def test_str_and_repr_and_to_str_agree() -> None:
    point: Any = Point(x=1, y=2)

    assert str(point) == repr(point) == point.to_str() == "Point(x: 1, y: 2)"


class Origin(State):
    x: int
    y: int = 0


def test_object_setattr_cannot_bypass_immutability() -> None:
    point = Origin(x=1)

    with pytest.raises(AttributeError):
        object.__setattr__(point, "x", 2)  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError):
        object.__setattr__(point, "extra", 2)

    with pytest.raises(AttributeError):
        _ = point.__dict__  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(TypeError):
        weakref.ref(point)

    assert point.x == 1


def test_initialized_instance_refuses_a_second_initialization() -> None:
    point = Origin(x=1)

    with pytest.raises(TypeError, match="initialized already"):
        point.__init__(x=2)  # pyright: ignore[reportCallIssue]

    assert point.x == 1


def test_custom_init_delegating_to_super_binds_the_attributes() -> None:
    class Renamed(State):
        _value: int

        def __init__(self, value: int) -> None:
            super().__init__(_value=value)

    assert Renamed(3)._value == 3  # pyright: ignore[reportPrivateUsage]


def test_instance_which_skipped_init_is_safe_to_inspect() -> None:
    class Skipping(State):
        value: int

        def __init__(self) -> None:
            pass

    instance = Skipping()

    with pytest.raises(AttributeError):
        _ = instance.value

    assert instance == Skipping()
    assert isinstance(hash(instance), int)


def test_hashing_a_deeply_nested_state_does_not_overflow_the_stack() -> None:
    class Node(State):
        child: Any = None

    node = Node()
    for _ in range(300_000):
        node = Node(child=node)

    # how deep it goes before it is refused depends on the stack of the platform - what
    # it must never do is crash, which a hash of the whole chain used to
    try:
        assert isinstance(hash(node), int)

    except RecursionError:
        pass


def test_updating_an_instance_which_was_never_initialized_is_refused() -> None:
    class Pair(State):
        first: int
        second: int = 0

    blank: Pair = Pair.__new__(Pair)
    # carrying over a value which is not there would make an instance missing it
    with pytest.raises(TypeError, match="not initialized"):
        blank.updating(first=1)

    with pytest.raises(TypeError, match="not initialized"):
        blank.updating(**{"first": 1, "unknown": 2})

    assert blank.updating(first=1, second=2) == Pair(first=1, second=2)


def test_class_can_not_be_changed_once_it_is_created() -> None:
    class Counter(State):
        value: int = 0

        def doubled(self) -> int:
            return self.value * 2

    def initialize(self: Counter, **values: Any) -> None: ...

    with pytest.raises(TypeError, match="immutable type"):
        Counter.__init__ = initialize  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(TypeError, match="immutable type"):
        Counter.value = 1  # a descriptor replaced would hide the value of every instance

    with pytest.raises(TypeError, match="immutable type"):
        del Counter.value

    with pytest.raises(TypeError, match="immutable type"):
        Counter.doubled = lambda self: 0  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(TypeError, match="immutable type"):
        type.__setattr__(Counter, "extra", 1)

    with pytest.raises(TypeError, match="immutable type"):
        State.extra = 1  # pyright: ignore[reportAttributeAccessIssue]

    counter = Counter(value=2)
    assert counter.value == 2
    assert counter.doubled() == 4


def test_generic_class_can_not_be_changed_and_is_still_specialized() -> None:
    class Box[T](State):
        value: T

    with pytest.raises(TypeError, match="immutable type"):
        Box.value = 1  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(TypeError, match="immutable type"):
        Box[int].value = 1  # pyright: ignore[reportAttributeAccessIssue]

    assert Box[int] is Box[int]
    assert isinstance(Box[bool](value=True), Box[int])
