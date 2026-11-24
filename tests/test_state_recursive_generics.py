import json
import threading
import types
from collections.abc import Callable, Mapping, Sequence
from typing import Annotated, Any, Generic, NamedTuple, Self, TypeVar, TypeVarTuple

import pytest

from haiway import State, ValidationError
from haiway._native import nstruct


class SeqTree[V](State):
    value: V
    children: Sequence[SeqTree[V]] = ()


class SeqSelf[V](State):
    value: V
    children: Sequence[Self] = ()


class OptTree[V](State):
    value: V
    next: OptTree[V] | None = None


class OptSelf[V](State):
    value: V
    next: Self | None = None


class MapTree[K, V](State):
    value: V
    children: Mapping[K, MapTree[K, V]] = {}


class MapSelf[K, V](State):
    value: V
    children: Mapping[K, Self] = {}


class VarTree[*Ts](State):
    items: tuple[*Ts]
    children: Sequence[VarTree[*Ts]] = ()


class VarSelf[*Ts](State):
    items: tuple[*Ts]
    children: Sequence[Self] = ()


class DefaultTree[V = int](State):
    value: V
    children: Sequence[DefaultTree[V]] = ()


class BoundTree[V: int](State):
    value: V
    children: Sequence[BoundTree[V]] = ()


T = TypeVar("T")


class OldTree(State, Generic[T]):  # noqa: UP046 - the old style is what is under test
    value: T
    children: Sequence["OldTree[T]"] = ()  # noqa: UP037 - the quoting is what is under test


class MixedTree[V](State):
    value: V
    by_self: Sequence[Self] = ()
    by_name: Sequence[MixedTree[V]] = ()


class CallTree[**P](State):
    handler: Callable[P, int]
    children: Sequence[CallTree[P]] = ()


class Holder(State):
    tree: SeqTree[str]
    forest: Sequence[SeqTree[int]] = ()


class Wrapper[V](State):
    inner: SeqTree[V]


class Shape(NamedTuple):
    generic: Any
    self_based: Any
    arguments: tuple[Any, ...]
    payload: Mapping[str, Any]
    invalid: Mapping[str, Any]
    path: str
    descend: Callable[[Any], Any]


def _identity(value: int) -> int:
    return value


def _child(state: Any) -> Any:
    return state.children[0]


def _next(state: Any) -> Any:
    return state.next


def _mapped(state: Any) -> Any:
    return state.children["a"]


_SHAPES: Mapping[str, Shape] = {
    "sequence": Shape(
        generic=SeqTree,
        self_based=SeqSelf,
        arguments=(int,),
        payload={"value": 1, "children": [{"value": 2, "children": [{"value": 3}]}]},
        invalid={"value": 1, "children": [{"value": "x"}]},
        path=".children[0].value",
        descend=_child,
    ),
    "optional": Shape(
        generic=OptTree,
        self_based=OptSelf,
        arguments=(int,),
        payload={"value": 1, "next": {"value": 2, "next": {"value": 3}}},
        invalid={"value": 1, "next": {"value": "x"}},
        path=".next",
        descend=_next,
    ),
    "mapping": Shape(
        generic=MapTree,
        self_based=MapSelf,
        arguments=(str, int),
        payload={"value": 1, "children": {"a": {"value": 2, "children": {"a": {"value": 3}}}}},
        invalid={"value": 1, "children": {"a": {"value": "x"}}},
        path=".children[a].value",
        descend=_mapped,
    ),
    "variadic": Shape(
        generic=VarTree,
        self_based=VarSelf,
        arguments=(int, str),
        payload={
            "items": (1, "a"),
            "children": [{"items": (2, "b"), "children": [{"items": (3, "c")}]}],
        },
        invalid={"items": (1, "a"), "children": [{"items": ("x", "b")}]},
        path=".children[0].items[0]",
        descend=_child,
    ),
}

shapes = pytest.mark.parametrize("shape", _SHAPES.values(), ids=_SHAPES.keys())


@shapes
def test_nested_values_are_built_as_the_specialization(shape: Shape) -> None:
    specialized: Any = shape.generic[*shape.arguments]

    state: Any = specialized(**shape.payload)

    assert type(state) is specialized
    assert type(shape.descend(state)) is specialized
    assert type(shape.descend(shape.descend(state))) is specialized


@shapes
def test_nested_values_of_an_unspecialized_class_are_the_class(shape: Shape) -> None:
    state: Any = shape.generic(**shape.payload)

    assert type(state) is shape.generic
    assert type(shape.descend(state)) is shape.generic
    assert type(shape.descend(shape.descend(state))) is shape.generic


@shapes
def test_wrong_nested_value_is_reported_with_its_path(shape: Shape) -> None:
    specialized: Any = shape.generic[*shape.arguments]

    with pytest.raises(ValidationError) as raised:
        specialized(**shape.invalid)

    assert shape.path in str(raised.value)


@shapes
def test_conversions_round_trip(shape: Shape) -> None:
    specialized: Any = shape.generic[*shape.arguments]
    state: Any = specialized(**shape.payload)

    from_json: Any = specialized.from_json(state.to_json())
    from_mapping: Any = specialized.from_mapping(state.to_mapping())

    # the document is read natively - `None` is what it answers for what it leaves to Python
    assert nstruct.from_json(specialized, state.to_json(), False, False) is not None
    assert from_json == state
    assert from_mapping == state
    assert type(shape.descend(from_json)) is specialized
    assert type(shape.descend(from_mapping)) is specialized
    assert json.loads(from_json.to_json()) == json.loads(state.to_json())


@shapes
def test_wrong_nested_value_in_json_is_refused(shape: Shape) -> None:
    specialized: Any = shape.generic[*shape.arguments]

    with pytest.raises(ValueError, match="Failed to decode"):
        specialized.from_json(json.dumps(shape.invalid))

    with pytest.raises(ValidationError):
        specialized.from_mapping(shape.invalid)


@shapes
def test_schema_refers_to_the_class_by_anchor(shape: Shape) -> None:
    schema: Mapping[str, Any] = json.loads(shape.generic[*shape.arguments].json_schema())

    assert f"#{schema['$anchor']}" in json.dumps(schema)
    assert '"$ref"' in json.dumps(schema)


@shapes
def test_schema_has_the_shape_of_the_one_declared_with_self(shape: Shape) -> None:
    def named(schema: str, generic: Any) -> str:
        return schema.replace(generic.__name__, "Name")

    assert named(shape.generic[*shape.arguments].json_schema(), shape.generic) == named(
        shape.self_based[*shape.arguments].json_schema(), shape.self_based
    )
    assert named(shape.generic.json_schema(), shape.generic) == named(
        shape.self_based.json_schema(), shape.self_based
    )


@shapes
def test_equality_hash_and_identity(shape: Shape) -> None:
    specialized: Any = shape.generic[*shape.arguments]
    first: Any = specialized(**shape.payload)
    second: Any = specialized(**shape.payload)

    assert specialized is shape.generic[*shape.arguments]
    assert first == second
    assert hash(first) == hash(second)
    assert isinstance(first, shape.generic)
    assert isinstance(first, specialized)
    assert isinstance(shape.descend(first), specialized)


def test_updating_validates_nested_values_as_the_specialization() -> None:
    specialized: Any = SeqTree[int]
    updating: Mapping[str, Any] = {"children": [{"value": 5}]}
    invalid: Mapping[str, Any] = {"children": [{"value": "x"}]}
    state: Any = specialized(value=1)

    updated: Any = state.updating(**updating)

    assert type(updated.children[0]) is specialized
    with pytest.raises(ValidationError):
        state.updating(**invalid)


def test_unspecialized_class_resolves_through_its_defaults() -> None:
    nested: Mapping[str, Any] = {"children": [{"value": 2}]}
    invalid: Mapping[str, Any] = {"children": [{"value": "x"}]}
    unspecialized: Any = DefaultTree
    specialized: Any = DefaultTree[str]

    assert type(unspecialized(value=1, **nested).children[0]) is DefaultTree
    with pytest.raises(ValidationError):
        unspecialized(value=1, **invalid)

    assert type(specialized(value="a", **invalid).children[0]) is specialized
    with pytest.raises(ValidationError):
        specialized(value="a", **nested)


def test_bound_is_enforced_on_the_unspecialized_class() -> None:
    nested: Mapping[str, Any] = {"children": [{"value": 2}]}
    invalid: Mapping[str, Any] = {"children": [{"value": "x"}]}
    unspecialized: Any = BoundTree
    specialized: Any = BoundTree[bool]

    assert type(unspecialized(value=1, **nested).children[0]) is BoundTree
    with pytest.raises(ValidationError):
        unspecialized(value=1, **invalid)

    assert type(specialized(value=True, **nested).children[0]) is specialized
    with pytest.raises(ValidationError):
        specialized(value=True, **invalid)


def test_old_style_generic_with_a_quoted_reference() -> None:
    nested: Mapping[str, Any] = {"value": 1, "children": [{"value": 2}]}
    invalid: Mapping[str, Any] = {"value": 1, "children": [{"value": "x"}]}
    specialized: Any = OldTree[int]
    state: Any = specialized(**nested)

    assert type(state.children[0]) is specialized
    assert specialized.from_json(state.to_json()) == state
    with pytest.raises(ValidationError):
        specialized(**invalid)


def test_self_and_the_class_with_its_parameters_are_one_attribute() -> None:
    payload: Mapping[str, Any] = {
        "value": 1,
        "by_self": [{"value": 2}],
        "by_name": [{"value": 3}],
    }
    specialized: Any = MixedTree[int]
    state: Any = specialized(**payload)
    schema: str = specialized.json_schema()

    assert type(state.by_self[0]) is specialized
    assert type(state.by_name[0]) is specialized
    assert schema.count('"$anchor"') == 1
    assert schema.count('"$ref": "#MixedTree_int_"') == 2
    assert specialized.from_json(state.to_json()) == state


def test_parameter_specification() -> None:
    specialized: Any = CallTree[[int]]
    unspecialized: Any = CallTree

    state: Any = specialized(
        handler=_identity,
        children=({"handler": _identity},),
    )
    bare: Any = unspecialized(handler=lambda: 1, children=({"handler": lambda: 2},))

    assert type(state.children[0]) is specialized
    assert type(bare.children[0]) is unspecialized


def test_specialized_with_itself() -> None:
    specialized: Any = SeqTree[SeqTree[int]]
    payload: Mapping[str, Any] = {
        "value": {"value": 1, "children": [{"value": 2}]},
        "children": [{"value": {"value": 3}}],
    }

    nested: Any = specialized(**payload)

    assert type(nested.value) is SeqTree[int]
    assert type(nested.value.children[0]) is SeqTree[int]
    assert type(nested.children[0]) is specialized
    assert type(nested.children[0].value) is SeqTree[int]
    assert specialized.from_json(nested.to_json()) == nested


def test_embedded_in_other_states() -> None:
    holder_payload: Mapping[str, Any] = {
        "tree": {"value": "a", "children": [{"value": "b"}]},
        "forest": [{"value": 1, "children": [{"value": 2}]}],
    }
    holder_invalid: Mapping[str, Any] = {"tree": {"value": "a", "children": [{"value": 1}]}}
    wrapper_payload: Mapping[str, Any] = {"inner": {"value": 1, "children": [{"value": 2}]}}
    wrapper_invalid: Mapping[str, Any] = {"inner": {"value": 1, "children": [{"value": "x"}]}}
    holder_type: Any = Holder
    wrapper_type: Any = Wrapper
    wrapper_int: Any = Wrapper[int]

    holder: Any = holder_type(**holder_payload)
    wrapped: Any = wrapper_int(**wrapper_payload)

    assert type(holder.tree.children[0]) is SeqTree[str]
    assert type(holder.forest[0].children[0]) is SeqTree[int]
    assert Holder.from_json(holder.to_json()) == holder
    with pytest.raises(ValidationError):
        holder_type(**holder_invalid)

    assert type(wrapped.inner) is SeqTree[int]
    assert type(wrapped.inner.children[0]) is SeqTree[int]
    assert type(wrapper_type(**wrapper_payload).inner.children[0]) is SeqTree[Any]
    with pytest.raises(ValidationError):
        wrapper_int(**wrapper_invalid)


def test_concurrent_specialization_yields_one_class() -> None:
    class Racing[V](State):
        value: V
        children: Sequence[Racing[V]] = ()

    created: list[Any] = []
    barrier = threading.Barrier(8)

    def specialize() -> None:
        barrier.wait()
        created.append(Racing[int])

    threads = [threading.Thread(target=specialize) for _ in range(8)]
    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert len(created) == 8
    assert len({id(variant) for variant in created}) == 1


def test_other_specializations_within_the_declaration_are_refused() -> None:
    with pytest.raises(
        TypeError,
        match=r"Pair can't be specialized within its own declaration - only Pair\[A, B\], its own",
    ):

        class Pair[A, B](State):
            first: A
            swapped: Pair[B, A] | None = None


def test_polymorphic_recursion_is_refused() -> None:
    with pytest.raises(
        TypeError,
        match=r"Nest can't be specialized within its own declaration - only Nest\[V\]",
    ):

        class Nest[V](State):
            value: V
            inner: Nest[Sequence[V]] | None = None


def test_closed_self_specialization_is_refused() -> None:
    with pytest.raises(
        TypeError,
        match=r"C can't be specialized within its own declaration - only C\[V\]",
    ):

        class C[V](State):
            value: V
            ints: Sequence[C[int]] = ()


def test_the_same_bounds_in_another_order_are_refused() -> None:
    with pytest.raises(TypeError, match=r"Ordered can't be specialized within its own"):

        class Ordered[A: int, B: int](State):
            first: A
            swapped: Ordered[B, A] | None = None


class HeadAndRest[T, *Ts](State):
    head: T
    rest: tuple[*Ts]
    children: Sequence[HeadAndRest[T, *Ts]] = ()


class HeadAndCall[T, **P](State):
    head: T
    handler: Callable[P, int]
    children: Sequence[HeadAndCall[T, P]] = ()


class Described[V](State):
    value: V
    children: Sequence[Annotated[Described[V], "a child"]] = ()


def test_own_parameters_of_a_variadic_generic_with_a_leading_parameter() -> None:
    specialized: Any = HeadAndRest[int, str, bool]
    payload: Mapping[str, Any] = {
        "head": 1,
        "rest": ("a", True),
        "children": [{"head": 2, "rest": ("b", False)}],
    }
    invalid: Mapping[str, Any] = {"head": 1, "rest": ("a", True), "children": [{"head": "x"}]}

    state: Any = specialized(**payload)

    assert type(state.children[0]) is specialized
    assert specialized.from_json(state.to_json()) == state
    with pytest.raises(ValidationError):
        specialized(**invalid)

    assert type(HeadAndRest(**payload).children[0]) is HeadAndRest


def test_own_parameters_of_a_parameter_specification_with_a_leading_parameter() -> None:
    specialized: Any = HeadAndCall[int, [int]]

    state: Any = specialized(
        head=1,
        handler=_identity,
        children=({"head": 2, "handler": _identity},),
    )

    assert type(state.children[0]) is specialized
    with pytest.raises(ValidationError):
        specialized(head=1, handler=_identity, children=({"head": "x", "handler": _identity},))


def test_own_parameters_within_annotated() -> None:
    specialized: Any = Described[int]

    state: Any = specialized(value=1, children=({"value": 2},))

    assert type(state.children[0]) is specialized
    with pytest.raises(ValidationError):
        specialized(value=1, children=({"value": "x"},))


def test_the_refusal_spells_variadic_parameters_and_parameter_specifications_as_declared() -> None:
    with pytest.raises(TypeError, match=r"only Row\[\*Ts\], its own"):

        class Row[*Ts](State):
            items: tuple[*Ts]
            inner: Sequence[Row[int]] = ()

    with pytest.raises(TypeError, match=r"only Handler\[T, P\], its own"):

        class Handler[T, **P](State):
            handler: Callable[P, T]
            inner: Sequence[Handler[int, [str]]] = ()


GENERIC: Any = Generic
Head = TypeVar("Head")
Rest = TypeVarTuple("Rest")


def _declare_variadic(name: str, parameters: Any, annotation: str) -> Any:
    # declared dynamically, the self-references are what a type checker has no form for
    return types.new_class(
        name,
        (State, parameters),
        {},
        lambda namespace: namespace.update(
            __module__=__name__,
            __annotations__={"rest": annotation},
            rest=(),
        ),
    )


@pytest.mark.parametrize(
    ("parameters", "reference"),
    [
        (GENERIC[Head, *Rest], "Sequence[Open[Head, *Rest, int]]"),
        (GENERIC[Head, *Rest], "Sequence[Open[int, *Rest]]"),
        (GENERIC[Head, *Rest], "Sequence[Open[*Rest]]"),
        (GENERIC[*Rest], "Sequence[Open[*Rest, int]]"),
    ],
    ids=["trailing argument", "leading argument", "leading parameter dropped", "variadic only"],
)
def test_open_specializations_of_a_variadic_generic_are_refused(
    parameters: Any,
    reference: str,
) -> None:
    with pytest.raises(TypeError, match=r"Open can't be specialized within its own declaration"):
        _declare_variadic("Open", parameters, reference)
