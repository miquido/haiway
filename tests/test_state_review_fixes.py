import copy
import inspect
import pickle
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any

from pytest import mark, raises

from haiway import Map, Meta, Sensitive, State


class Point(State):
    x: int
    y: str = "a"


class Box[T](State):
    value: T


class Holder(State):
    mapping: Mapping[str, int]


@dataclass
class Item:
    name: str


# --- equality ---------------------------------------------------------------


def test_state_with_nan_is_equal_to_itself() -> None:
    class Measure(State):
        value: float

    measure = Measure(value=float("nan"))

    assert measure == measure  # noqa: PLR0124
    assert measure in [measure]
    assert measure != Measure(value=float("nan"))  # distinct values follow float semantics


# --- hashing ----------------------------------------------------------------


def test_map_and_meta_are_hashable_by_contents() -> None:
    assert hash(Map({"a": 1, "b": 2})) == hash(Map({"b": 2, "a": 1}))
    assert hash(Map({"a": 1})) != hash(Map({"a": 2}))
    assert hash(Meta.of({"kind": "x"})) == hash(Meta.of({"kind": "x"}))
    assert {Map({"a": 1}): "value"}[Map({"a": 1})] == "value"


def test_map_holding_unhashable_value_is_unhashable() -> None:
    with raises(TypeError, match="unhashable"):
        hash(Map({"a": [1]}))


def test_states_differing_in_a_mapping_hash_differently() -> None:
    first = Holder(mapping={"a": 1})
    second = Holder(mapping={"b": 2})

    assert hash(first) != hash(second)
    assert hash(first) == hash(Holder(mapping={"a": 1}))
    assert len({first, second, Holder(mapping={"a": 1})}) == 2


def test_nested_mappings_are_hashable() -> None:
    class Nested(State):
        mapping: Mapping[str, Mapping[str, int]]

    assert hash(Nested(mapping={"a": {"b": 1}})) == hash(Nested(mapping={"a": {"b": 1}}))
    assert hash(Nested(mapping={"a": {"b": 1}})) != hash(Nested(mapping={"a": {"b": 2}}))


# --- representation ---------------------------------------------------------


def test_repr_tells_strings_from_other_values() -> None:
    class Holder(State):
        value: Any

    assert repr(Holder(value="1")) == "Holder(value: '1')"
    assert repr(Holder(value=1)) == "Holder(value: 1)"
    assert str(Holder(value="1")) == str(Holder(value=1)) == "Holder(value: 1)"


def test_repr_keeps_redaction_and_nests() -> None:
    class Secret(State):
        token: Annotated[str, Sensitive]
        point: Point

    assert repr(Secret(token="t", point=Point(x=1))) == (
        "Secret(token: <redacted>, point: Point(x: 1, y: 'a'))"
    )


# --- signature --------------------------------------------------------------


def test_signature_lists_attributes_as_keyword_only_parameters() -> None:
    signature = inspect.signature(Point)
    parameters = signature.parameters

    assert list(parameters) == ["x", "y"]
    assert {parameter.kind for parameter in parameters.values()} == {inspect.Parameter.KEYWORD_ONLY}
    assert parameters["x"].annotation is int
    assert parameters["x"].default is inspect.Parameter.empty
    assert parameters["y"].default is not inspect.Parameter.empty


def test_signature_of_specialized_variant_and_attribute_less_state() -> None:
    assert list(inspect.signature(Box[int]).parameters) == ["value"]
    assert list(inspect.signature(State).parameters) == []


# --- pickling ---------------------------------------------------------------


@mark.parametrize("protocol", range(pickle.HIGHEST_PROTOCOL + 1))
def test_pickling_is_refused_with_a_hint(protocol: int) -> None:
    with raises(TypeError, match="to_json or to_basic_object"):
        pickle.dumps(Point(x=1), protocol=protocol)


def test_copying_still_works() -> None:
    point = Point(x=1)

    assert copy.copy(point) is point
    assert copy.deepcopy(point) == point
    assert copy.replace(point, x=2).x == 2


# --- conversion -------------------------------------------------------------


def test_dataclass_class_in_attribute_is_kept_by_to_mapping() -> None:
    class Holder(State):
        value: Any

    assert Holder(value=Item).to_mapping() == {"value": Item}
    assert Holder(value=Item(name="a")).to_mapping() == {"value": {"name": "a"}}
