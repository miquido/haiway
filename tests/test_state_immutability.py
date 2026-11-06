import copy
import weakref
from collections.abc import Mapping, Sequence, Set
from typing import Annotated, Any

from pytest import mark, raises

from haiway import MISSING, Alias, Missing, State, ValidationError


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

    assert Optional().value is MISSING  # pyright: ignore[reportCallIssue]
    assert Optional(value=MISSING).value is MISSING
    assert Optional(value=1).updating(value=MISSING).value is MISSING


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
