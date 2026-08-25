from collections.abc import Mapping, Sequence, Set
from typing import Annotated, cast

from pytest import raises

from haiway import AttributePath, State


class SequenceState(State):
    value: int


class DictState(State):
    key: str


class NestedState(State):
    value: float


class RecursiveState(State):
    more: RecursiveState | None


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


class TwoAttributeState(State):
    text: str
    number: int


two_attribute_state: TwoAttributeState = TwoAttributeState(
    text="text",
    number=7,
)


class QuotedRecursiveState(State):
    value: int = 0
    child: QuotedRecursiveState | None = None


quoted_recursive_state: QuotedRecursiveState = QuotedRecursiveState(
    value=1,
    child=QuotedRecursiveState(value=2),
)


class MutableContainerState(State):
    items: list[int]
    labels: set[str]
    mapping: dict[str, int]


mutable_container_state: MutableContainerState = MutableContainerState(
    items=[1, 2, 3],
    labels={"a"},
    mapping={"key": 1},
)


def test_attribute_path_updates_attribute():
    path: AttributePath[TwoAttributeState, str] = cast(
        AttributePath[TwoAttributeState, str],
        TwoAttributeState._.text,
    )
    assert path(two_attribute_state, updated="replaced") == TwoAttributeState(
        text="replaced",
        number=7,
    )


def test_quoted_recursive_attribute_path_points_to_attribute():
    path: AttributePath[QuotedRecursiveState, QuotedRecursiveState | None] = cast(
        AttributePath[QuotedRecursiveState, QuotedRecursiveState | None],
        QuotedRecursiveState._.child,
    )
    assert path(quoted_recursive_state) == QuotedRecursiveState(value=2)
    assert path.__repr__() == "QuotedRecursiveState.child"


def test_quoted_recursive_nested_attribute_path_points_to_attribute():
    path: AttributePath[QuotedRecursiveState, int] = cast(
        AttributePath[QuotedRecursiveState, int],
        QuotedRecursiveState._.child.value,
    )
    assert path(quoted_recursive_state) == 2
    assert str(path) == "child.value"


def test_mutable_sequence_attribute_path_points_to_coerced_value():
    path: AttributePath[MutableContainerState, Sequence[int]] = cast(
        AttributePath[MutableContainerState, Sequence[int]],
        MutableContainerState._.items,
    )
    assert path(mutable_container_state) == (1, 2, 3)


def test_mutable_sequence_item_path_points_to_item():
    path: AttributePath[MutableContainerState, int] = cast(
        AttributePath[MutableContainerState, int],
        MutableContainerState._.items[1],
    )
    assert path(mutable_container_state) == 2
    assert path(mutable_container_state, updated=9) == MutableContainerState(
        items=[1, 9, 3],
        labels={"a"},
        mapping={"key": 1},
    )


def test_mutable_set_attribute_path_points_to_coerced_value():
    path: AttributePath[MutableContainerState, Set[str]] = cast(
        AttributePath[MutableContainerState, Set[str]],
        MutableContainerState._.labels,
    )
    assert path(mutable_container_state) == frozenset(("a",))


def test_mutable_mapping_item_path_points_to_item():
    path: AttributePath[MutableContainerState, int] = cast(
        AttributePath[MutableContainerState, int],
        MutableContainerState._.mapping["key"],
    )
    assert path(mutable_container_state) == 1


def test_missing_attribute_path_raises():
    with raises(AttributeError):
        _ = MutableContainerState._.missing  # pyright: ignore[reportAttributeAccessIssue]
