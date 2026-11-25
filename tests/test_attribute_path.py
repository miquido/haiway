import threading
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass
from typing import Annotated, Any, Literal, TypedDict, cast

import pytest
from pytest import raises

from haiway import Alias, AttributePath, Map, Missing, State, ValidationError, Verifier


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


class _SerializedLeaf(State):
    field: Annotated[str, Alias("f")]
    plain: int = 0


class _SerializedTyped(TypedDict):
    value: int


type _SerializedLeaves = Sequence[_SerializedLeaf]


class _SerializedRoot(State):
    leaf: Annotated[_SerializedLeaf, Alias("l")]
    optional: _SerializedLeaf | None = None
    items: Annotated[Sequence[_SerializedLeaf], Alias("its")] = ()
    pair: tuple[_SerializedLeaf, ...] = ()
    mapping: Mapping[str, _SerializedLeaf] = Map()
    typed: _SerializedTyped | None = None
    aliased: _SerializedLeaves = ()


@pytest.mark.parametrize(
    ("path", "expected"),
    (
        (_SerializedRoot._.leaf, ("l",)),
        (_SerializedRoot._.leaf.field, ("l", "f")),
        (_SerializedRoot._.leaf.plain, ("l", "plain")),
        (_SerializedRoot._.optional.field, ("optional", "f")),
        (_SerializedRoot._.items[0].field, ("its", 0, "f")),
        (_SerializedRoot._.pair[2].field, ("pair", 2, "f")),
        (_SerializedRoot._.mapping["key"].field, ("mapping", "key", "f")),
        (_SerializedRoot._.typed.value, ("typed", "value")),
        (_SerializedRoot._.aliased[1].field, ("aliased", 1, "f")),
    ),
    ids=(
        "alias",
        "nested-alias",
        "no-alias",
        "optional",
        "sequence-index",
        "tuple-index",
        "mapping-key",
        "typed-dict",
        "type-alias",
    ),
)
def test_serialized_components_resolve_aliases(
    path: AttributePath[Any, Any],
    expected: tuple[str | int, ...],
) -> None:
    assert path.serialized_components == expected


def test_serialized_components_keep_python_components_untouched() -> None:
    path = _SerializedRoot._.items[0].field
    assert path.components == ("items", "[0]", "field")
    assert str(path) == "items[0].field"
    assert path.serialized_components == ("its", 0, "f")


class _SerializedFixedTuple(State):
    pair: tuple[_SerializedLeaf, Annotated[_SerializedLeaf, Alias("second")]]
    by_int: Mapping[int, _SerializedLeaf] = Map()


def _resolved_in_mapping(
    mapping: Any,
    components: Sequence[str | int],
) -> Any:
    for component in components:
        mapping = mapping[component]

    return mapping


def test_serialized_components_locate_values_in_to_mapping() -> None:
    value = _SerializedRoot(
        leaf=_SerializedLeaf(field="a", plain=1),
        optional=_SerializedLeaf(field="b"),
        items=(_SerializedLeaf(field="c"), _SerializedLeaf(field="d", plain=7)),
        pair=(_SerializedLeaf(field="e"),),
        mapping={"key": _SerializedLeaf(field="g")},
        typed={"value": 5},
        aliased=(_SerializedLeaf(field="h"), _SerializedLeaf(field="i")),
    )
    mapping = value.to_mapping()

    for path in (
        _SerializedRoot._.leaf.field,
        _SerializedRoot._.leaf.plain,
        _SerializedRoot._.optional.field,
        _SerializedRoot._.items[1].field,
        _SerializedRoot._.items[1].plain,
        _SerializedRoot._.pair[0].field,
        _SerializedRoot._.mapping["key"].field,
        _SerializedRoot._.aliased[1].field,
    ):
        assert _resolved_in_mapping(mapping, path.serialized_components) == path(value), (
            f"{path!r} not found under {path.serialized_components}"
        )

    # reading a value through a TypedDict is not supported by paths, locating it is
    assert _resolved_in_mapping(mapping, _SerializedRoot._.typed.value.serialized_components) == 5


def test_serialized_components_use_alias_not_python_name() -> None:
    mapping = _SerializedRoot(leaf=_SerializedLeaf(field="a")).to_mapping()
    assert "l" in mapping
    assert "leaf" not in mapping
    assert "leaf" not in _SerializedRoot._.leaf.serialized_components


def test_serialized_components_of_fixed_tuple_element_alias() -> None:
    path = _SerializedFixedTuple._.pair[1].field
    assert path.serialized_components == ("pair", 1, "f")


def test_serialized_components_stringify_mapping_keys() -> None:
    path = _SerializedFixedTuple._.by_int[3].field
    assert path.serialized_components == ("by_int", "3", "f")


def test_serialized_components_are_stable_across_repeated_access() -> None:
    first = _SerializedRoot._.items[0].field
    second = _SerializedRoot._.items[0].field
    assert first is second
    assert first.serialized_components == second.serialized_components
    # a different index of the same path does not leak into the cached one
    assert _SerializedRoot._.items[5].field.serialized_components == ("its", 5, "f")
    assert first.serialized_components == ("its", 0, "f")


def test_serialized_components_of_root_path_are_empty() -> None:
    assert _SerializedRoot._.serialized_components == ()


def test_serialized_components_return_immutable_sequence() -> None:
    assert isinstance(_SerializedRoot._.leaf.serialized_components, tuple)


def test_serialized_components_align_with_components() -> None:
    path = _SerializedRoot._.items[0].field
    assert len(path.serialized_components) == len(path.components)


# the first use of a path from several threads at once gives every one of them the same path

_THREADS = 16
_ROUNDS = 100


def _paths_found_by_threads(
    root: Any,
    path_of: Callable[[Any], Any],
) -> set[int]:
    barrier: threading.Barrier = threading.Barrier(_THREADS)
    found: list[Any] = [None] * _THREADS

    def work(index: int) -> None:
        barrier.wait()
        found[index] = path_of(root)

    threads: list[threading.Thread] = [
        threading.Thread(target=work, args=(index,)) for index in range(_THREADS)
    ]
    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    return {id(path) for path in found}


def _same_path_for_all_threads(
    make_root: Callable[[], Any],
    path_of: Callable[[Any], Any],
) -> bool:
    # paths are kept on the class, so each round starts with the first use of a new one
    return all(len(_paths_found_by_threads(make_root(), path_of)) == 1 for _ in range(_ROUNDS))


def _new_state_class() -> Any:
    class Created(State):
        value: int
        items: Sequence[int] = ()

    return Created


def test_first_use_of_an_attribute_path_from_threads_gives_one_path() -> None:
    assert _same_path_for_all_threads(_new_state_class, lambda root: root._.value)


def test_first_use_of_an_item_path_from_threads_gives_one_path() -> None:
    assert _same_path_for_all_threads(_new_state_class, lambda root: root._.items[0])


def test_item_paths_kept_are_still_bounded() -> None:
    root: Any = _new_state_class()
    paths: Any = root._.items
    for index in range(1000):
        _ = paths[index]

    assert len(paths.__children__) <= 256


class _Shaped(State):
    literal: Literal["a", "b"] = "a"
    anything: Any = None
    optional: int | Missing = Missing
    real: float = 0.0
    flag: bool = False


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param("literal", "b", id="literal"),
        pytest.param("anything", [1], id="any"),
        pytest.param("optional", 1, id="missing union"),
    ],
)
def test_paths_read_and_update_attributes_of_any_annotation(name: str, value: Any) -> None:
    path: AttributePath[_Shaped, Any] = getattr(_Shaped._, name)
    initial = _Shaped()
    assert path(initial) is getattr(initial, name)

    updated: _Shaped = path(initial, value)
    assert path(updated) == getattr(_Shaped(**{name: value}), name)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        pytest.param("real", 1, id="int as float"),
        pytest.param("flag", "true", id="str as bool"),
        pytest.param("literal", "b", id="literal"),
    ],
)
def test_path_updates_validate_as_construction_does(name: str, value: Any) -> None:
    path: AttributePath[_Shaped, Any] = getattr(_Shaped._, name)
    expected: Any = getattr(_Shaped(**{name: value}), name)
    updated: Any = getattr(path(_Shaped(), value), name)
    assert updated == expected
    assert type(updated) is type(expected)


def test_path_updates_refuse_what_construction_refuses() -> None:
    with raises(ValidationError) as constructed:
        _Shaped(**{"literal": "c"})

    with raises(ValidationError) as updated:
        _Shaped._.literal(_Shaped(), cast(Any, "c"))

    assert updated.value.path == constructed.value.path


def _refusing_five(value: int) -> None:
    if value == 5:
        raise ValueError("five refused")


class _Inner(State):
    number: Annotated[int, Verifier(_refusing_five)] = 0
    items: Sequence[int] = ()


class _Outer(State):
    inner: _Inner = _Inner()
    many: Sequence[_Inner] = ()
    named: Mapping[str, _Inner] = Map()


@pytest.mark.parametrize(
    ("update", "construct"),
    [
        pytest.param(
            lambda outer: _Outer._.inner.items(outer, cast(Any, [1, "x"])),
            lambda: _Outer(inner=cast(Any, {"items": [1, "x"]})),
            id="attribute",
        ),
        pytest.param(
            lambda outer: _Outer._.inner.number(outer, 5),
            lambda: _Outer(inner=cast(Any, {"number": 5})),
            id="verified",
        ),
        pytest.param(
            lambda outer: _Outer._.many[0].number(outer, 5),
            lambda: _Outer(many=cast(Any, [{"number": 5}])),
            id="sequence item",
        ),
        pytest.param(
            lambda outer: _Outer._.named["a"].number(outer, 5),
            lambda: _Outer(named=cast(Any, {"a": {"number": 5}})),
            id="mapping item",
        ),
    ],
)
def test_path_update_failures_report_the_whole_path(
    update: Callable[[_Outer], _Outer],
    construct: Callable[[], _Outer],
) -> None:
    outer = _Outer(many=[_Inner()], named={"a": _Inner()})
    with raises(ValidationError) as constructed:
        construct()

    with raises(ValidationError) as updated:
        update(outer)

    assert tuple(updated.value.path) == tuple(constructed.value.path)
    assert type(updated.value.cause) is type(constructed.value.cause)


@dataclass(frozen=True)
class _Plain:
    number: int = 0


class _HoldingPlain(State):
    plain: _Plain = _Plain()


def test_path_updates_of_other_objects_take_values_as_they_do() -> None:
    # a dataclass validates nothing - a path updating it neither, as on construction
    holding = _HoldingPlain()
    updated = _HoldingPlain._.plain.number(holding, cast(Any, "text"))
    assert updated.plain == _Plain(number=cast(Any, "text"))
