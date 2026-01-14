from collections.abc import Mapping, Sequence
from typing import Annotated, cast

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


class OptionalContainerState(State):
    maybe_list: Sequence[SequenceState] | None
    maybe_dict: Mapping[str, DictState] | None


class AnnotatedContainerState(State):
    annotated_answer: Annotated[str, "answer"]
    annotated_list: Annotated[Sequence[SequenceState], "list"]
    annotated_dict: Annotated[Mapping[str, DictState], "dict"]


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

optional_container_state: OptionalContainerState = OptionalContainerState(
    maybe_list=[
        SequenceState(value=1),
        SequenceState(value=2),
    ],
    maybe_dict={
        "A": DictState(key="A"),
    },
)

annotated_container_state: AnnotatedContainerState = AnnotatedContainerState(
    annotated_answer="annotated",
    annotated_list=[
        SequenceState(value=7),
    ],
    annotated_dict={
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


def test_optional_sequence_item_path_points_to_item():
    path: AttributePath[OptionalContainerState, SequenceState] = cast(
        AttributePath[OptionalContainerState, SequenceState],
        OptionalContainerState._.maybe_list[1],
    )
    assert path(optional_container_state) == optional_container_state.maybe_list[1]
    assert path.__repr__() == "OptionalContainerState.maybe_list[1]"
    assert str(path) == "maybe_list[1]"


def test_optional_sequence_item_path_points_to_nested_attribute():
    path: AttributePath[OptionalContainerState, int] = cast(
        AttributePath[OptionalContainerState, int],
        OptionalContainerState._.maybe_list[0].value,
    )
    assert path(optional_container_state) == optional_container_state.maybe_list[0].value
    assert path.__repr__() == "OptionalContainerState.maybe_list[0].value"
    assert str(path) == "maybe_list[0].value"


def test_optional_mapping_item_path_points_to_item():
    path: AttributePath[OptionalContainerState, DictState] = cast(
        AttributePath[OptionalContainerState, DictState],
        OptionalContainerState._.maybe_dict["A"],
    )
    assert path(optional_container_state) == optional_container_state.maybe_dict["A"]
    assert path.__repr__() == "OptionalContainerState.maybe_dict[A]"
    assert str(path) == "maybe_dict[A]"


def test_annotated_attribute_path_points_to_attribute():
    path: AttributePath[AnnotatedContainerState, str] = cast(
        AttributePath[AnnotatedContainerState, str],
        AnnotatedContainerState._.annotated_answer,
    )
    assert path(annotated_container_state) == annotated_container_state.annotated_answer
    assert path.__repr__() == "AnnotatedContainerState.annotated_answer"
    assert str(path) == "annotated_answer"


def test_annotated_sequence_item_path_points_to_item():
    path: AttributePath[AnnotatedContainerState, SequenceState] = cast(
        AttributePath[AnnotatedContainerState, SequenceState],
        AnnotatedContainerState._.annotated_list[0],
    )
    assert path(annotated_container_state) == annotated_container_state.annotated_list[0]
    assert path.__repr__() == "AnnotatedContainerState.annotated_list[0]"
    assert str(path) == "annotated_list[0]"


def test_annotated_mapping_item_path_points_to_item():
    path: AttributePath[AnnotatedContainerState, DictState] = cast(
        AttributePath[AnnotatedContainerState, DictState],
        AnnotatedContainerState._.annotated_dict["B"],
    )
    assert path(annotated_container_state) == annotated_container_state.annotated_dict["B"]
    assert path.__repr__() == "AnnotatedContainerState.annotated_dict[B]"
    assert str(path) == "annotated_dict[B]"


def test_id_path_set_updates_self():
    path: AttributePath[ExampleState, ExampleState] = cast(
        AttributePath[ExampleState, ExampleState],
        ExampleState._,
    )
    assert path(state, updated=state) == state
    assert path.__repr__() == "ExampleState"
    assert str(path) == ""
