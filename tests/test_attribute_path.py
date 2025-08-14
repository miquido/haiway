from collections.abc import Mapping, Sequence
from typing import cast

from haiway import AttributePath, State


class SequenceState(State):
    value: int


class DictState(State):
    key: str


class NestedState(State):
    value: float


class RecursiveState(State):
    more: "RecursiveState | None"


class ExampleState(State):
    answer: str
    nested: NestedState
    recursive: RecursiveState
    list_models: Sequence[SequenceState]
    tuple_models: tuple[SequenceState, ...]
    tuple_mixed_models: tuple[SequenceState, DictState, NestedState]
    dict_models: Mapping[str, DictState]


state: ExampleState = ExampleState(
    answer="testing",
    nested=NestedState(
        value=3.14,
    ),
    recursive=RecursiveState(
        more=RecursiveState(
            more=None,
        ),
    ),
    list_models=[
        SequenceState(value=65),
        SequenceState(value=66),
    ],
    tuple_models=(
        SequenceState(value=42),
        SequenceState(value=21),
    ),
    tuple_mixed_models=(
        SequenceState(value=42),
        DictState(key="C"),
        NestedState(value=3.33),
    ),
    dict_models={
        "A": DictState(key="A"),
        "B": DictState(key="B"),
    },
)


def test_id_path_points_to_self():
    path: AttributePath[ExampleState, ExampleState] = cast(
        AttributePath[ExampleState, ExampleState],
        ExampleState._,
    )
    assert path(state) == state
    assert path.__repr__() == "ExampleState"
    assert str(path) == ""


def test_attribute_path_points_to_attribute():
    path: AttributePath[ExampleState, str] = cast(
        AttributePath[ExampleState, str],
        ExampleState._.answer,
    )
    assert path(state) == state.answer
    assert path.__repr__() == "ExampleState.answer"
    assert str(path) == "answer"


def test_nested_attribute_path_points_to_nested_attribute():
    path: AttributePath[ExampleState, float] = cast(
        AttributePath[ExampleState, float],
        ExampleState._.nested.value,
    )
    assert path(state) == state.nested.value
    assert path.__repr__() == "ExampleState.nested.value"
    assert str(path) == "nested.value"


def test_recursive_attribute_path_points_to_attribute():
    path: AttributePath[ExampleState, RecursiveState] = cast(
        AttributePath[ExampleState, RecursiveState],
        ExampleState._.recursive,
    )
    assert path(state) == state.recursive
    assert path.__repr__() == "ExampleState.recursive"
    assert str(path) == "recursive"


def test_list_item_path_points_to_item():
    path: AttributePath[ExampleState, SequenceState] = cast(
        AttributePath[ExampleState, SequenceState],
        ExampleState._.list_models[1],
    )
    assert path(state) == state.list_models[1]
    assert path.__repr__() == "ExampleState.list_models[1]"
    assert str(path) == "list_models[1]"


def test_tuple_item_path_points_to_item():
    path: AttributePath[ExampleState, SequenceState] = cast(
        AttributePath[ExampleState, SequenceState],
        ExampleState._.tuple_models[1],
    )
    assert path(state) == state.tuple_models[1]
    assert path.__repr__() == "ExampleState.tuple_models[1]"
    assert str(path) == "tuple_models[1]"


def test_mixed_tuple_item_path_points_to_item():
    path: AttributePath[ExampleState, DictState] = cast(
        AttributePath[ExampleState, DictState], ExampleState._.tuple_mixed_models[1]
    )
    assert path(state) == state.tuple_mixed_models[1]
    assert path.__repr__() == "ExampleState.tuple_mixed_models[1]"
    assert str(path) == "tuple_mixed_models[1]"


def test_dict_item_path_points_to_item():
    path: AttributePath[ExampleState, DictState] = cast(
        AttributePath[ExampleState, DictState],
        ExampleState._.dict_models["B"],
    )
    assert path(state) == state.dict_models["B"]
    assert path.__repr__() == "ExampleState.dict_models[B]"
    assert str(path) == "dict_models[B]"


def test_id_path_set_updates_self():
    path: AttributePath[ExampleState, ExampleState] = cast(
        AttributePath[ExampleState, ExampleState],
        ExampleState._,
    )
    assert path(state, updated=state) == state
    assert path.__repr__() == "ExampleState"
    assert str(path) == ""
