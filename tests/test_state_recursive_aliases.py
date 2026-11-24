import json
import sys
import types
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, Any, Generic, TypeAliasType, TypeVar

import pytest

from haiway import State, ValidationError
from haiway.attributes import annotations

type Forest = Sequence[ATree]


class ATree(State):
    value: int
    children: Forest = ()


type GenericForest[V] = Sequence[GTree[V]]


class GTree[V](State):
    value: V
    children: GenericForest[V] = ()


# the parameter of the alias is not the one of the class
type Renamed[X] = Sequence[RTree[X]]


class RTree[V](State):
    value: V
    children: Renamed[V] = ()


NESTED: Mapping[str, Any] = {"value": 2, "children": [{"value": 3}]}
INVALID: Mapping[str, Any] = {"value": "x"}


def test_a_class_referring_to_itself_through_an_alias() -> None:
    tree_type: Any = ATree

    tree: Any = tree_type(value=1, children=(NESTED,))

    assert type(tree.children[0]) is ATree
    assert type(tree.children[0].children[0]) is ATree
    with pytest.raises(ValidationError, match=r"children\[0\]\.value"):
        tree_type(value=1, children=(INVALID,))


def test_a_class_referring_to_itself_through_an_alias_converts() -> None:
    tree: Any = ATree.from_mapping({"value": 1, "children": [NESTED]})

    assert ATree.from_json(tree.to_json()) == tree
    assert ATree.from_mapping(tree.to_mapping()) == tree
    assert type(ATree.from_json(tree.to_json()).children[0]) is ATree
    with pytest.raises(ValueError, match="Failed to decode"):
        ATree.from_json(json.dumps({"value": 1, "children": [INVALID]}))


def test_schema_of_a_class_referring_to_itself_through_an_alias() -> None:
    schema: str = ATree.json_schema()

    assert '"$anchor": "ATree"' in schema
    assert '"$ref": "#ATree"' in schema


@pytest.mark.parametrize("generic", [GTree, RTree], ids=["same parameter", "renamed parameter"])
def test_a_generic_class_referring_to_itself_through_a_generic_alias(generic: Any) -> None:
    specialized: Any = generic[int]
    unspecialized: Any = generic

    tree: Any = specialized(value=1, children=(NESTED,))

    assert type(tree.children[0]) is specialized
    assert type(tree.children[0].children[0]) is specialized
    assert type(unspecialized(value="a", children=(NESTED,)).children[0]) is unspecialized
    with pytest.raises(ValidationError, match=r"children\[0\]\.value"):
        specialized(value=1, children=(INVALID,))


@pytest.mark.parametrize("generic", [GTree, RTree], ids=["same parameter", "renamed parameter"])
def test_a_generic_class_referring_to_itself_through_a_generic_alias_converts(
    generic: Any,
) -> None:
    specialized: Any = generic[int]
    tree: Any = specialized(value=1, children=(NESTED,))
    schema: Mapping[str, Any] = json.loads(specialized.json_schema())

    assert specialized.from_json(tree.to_json()) == tree
    assert specialized.from_mapping(tree.to_mapping()) == tree
    assert type(specialized.from_json(tree.to_json()).children[0]) is specialized
    with pytest.raises(ValueError, match="Failed to decode"):
        specialized.from_json(json.dumps({"value": 1, "children": [INVALID]}))

    assert f"#{schema['$anchor']}" in json.dumps(schema)
    assert schema["$anchor"] == f"{generic.__name__}_int_"


# the classes of these aliases are declared where they are refused or by a test - what
# the aliases refer to is not a name of this module, which only type checking needs to know
if TYPE_CHECKING:

    class Swapped[A, B](State): ...

    class PolyNode[V](State): ...

    class ClosedNode[V](State): ...

    class Repeated[V](State): ...

    class OldPair[A, B](State): ...


type Kids[X, Y] = Sequence[Swapped[X, Y]]
type Poly[X] = Sequence[PolyNode[Sequence[X]]]
type Closed[X] = Sequence[ClosedNode[int]]
type Two[X, Y] = Sequence[Repeated[X]]

GENERIC: Any = Generic

# an alias of the old style, sharing its type parameters with the class it refers to
OldA = TypeVar("OldA")
OldB = TypeVar("OldB")
OldParameters: Any = GENERIC[OldA, OldB]
OldKids = TypeAliasType(  # noqa: UP040 - the old style is what is under test
    "OldKids",
    Sequence["OldPair[OldA, OldB]"],
    type_params=(OldA, OldB),
)


def _declare_kids_then_swapped() -> Any:
    class Swapped[A, B](State):
        a: A
        b: B
        kids: Kids[A, B] = ()
        swapped: Kids[B, A] = ()

    return Swapped


def _declare_swapped_then_kids() -> Any:
    class Swapped[A, B](State):
        a: A
        b: B
        swapped: Kids[B, A] = ()
        kids: Kids[A, B] = ()

    return Swapped


def _declare_swapped_alone() -> Any:
    class Swapped[A, B](State):
        a: A
        b: B
        swapped: Kids[B, A] = ()

    return Swapped


def _declare_polymorphic() -> Any:
    class PolyNode[V](State):
        value: V
        kids: Poly[V] = ()

    return PolyNode


def _declare_closed() -> Any:
    class ClosedNode[V](State):
        value: V
        kids: Closed[V] = ()

    return ClosedNode


@pytest.mark.parametrize(
    "declare",
    [
        _declare_kids_then_swapped,
        _declare_swapped_then_kids,
        _declare_swapped_alone,
        _declare_polymorphic,
        _declare_closed,
    ],
)
def test_other_specializations_through_an_alias_are_refused(declare: Callable[[], Any]) -> None:
    with pytest.raises(TypeError, match="can't be specialized within its own declaration"):
        declare()


def test_an_alias_given_the_own_parameters_is_not_confused_with_one_given_them_swapped() -> None:
    class Swapped[A, B](State):
        a: A
        b: B
        kids: Kids[A, B] = ()

    payload: Mapping[str, Any] = {"a": 2, "b": "y"}
    specialized: Any = Swapped[int, str]

    state: Any = specialized(a=1, b="x", kids=(payload,))

    assert type(state.kids[0]) is specialized


def test_a_parameter_of_the_class_given_to_several_parameters_of_an_alias() -> None:
    class Repeated[V](State):
        value: V
        kids: Two[V, V] = ()

    specialized: Any = Repeated[int]
    tree: Any = specialized(value=1, kids=({"value": 2},))

    assert type(tree.kids[0]) is specialized
    with pytest.raises(ValidationError, match=r"kids\[0\]\.value"):
        specialized(value=1, kids=(INVALID,))


type NestedFor[X] = Sequence[Nesting[X]]
type NestedAlias[Y] = NestedFor[Y]


class Nesting[V](State):
    value: V
    children: NestedAlias[V] = ()


def test_an_alias_of_an_alias_of_the_class() -> None:
    specialized: Any = Nesting[int]
    tree: Any = specialized(value=1, children=(NESTED,))

    assert type(tree.children[0].children[0]) is specialized
    with pytest.raises(ValidationError, match=r"children\[0\]\.value"):
        specialized(value=1, children=(INVALID,))


type Missing = Sequence[Vanishing]


class Vanishing:
    pass


def _declare_broken() -> Any:
    class Broken(State):
        children: Missing = ()

    return Broken


def test_an_undefined_name_within_an_alias_is_reported_with_the_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(sys.modules[__name__], "Vanishing")

    with pytest.raises(NameError, match=r"Vanishing.*Missing"):
        _declare_broken()


def _declare_old_pair(rest: str) -> Any:
    # declared dynamically, the annotation is what a type checker has no form for
    return types.new_class(
        "OldPair",
        (State, OldParameters),
        {},
        lambda namespace: namespace.update(
            __module__=__name__,
            __annotations__={"a": "OldA", "b": "OldB", "rest": rest},
            rest=(),
        ),
    )


def test_an_alias_sharing_the_type_parameters_of_the_class_is_not_given_them_swapped() -> None:
    with pytest.raises(TypeError, match="can't be specialized within its own declaration"):
        _declare_old_pair("OldKids[OldB, OldA]")


def test_an_alias_sharing_the_type_parameters_of_the_class_works_given_them_in_order() -> None:
    specialized: Any = _declare_old_pair("OldKids[OldA, OldB]")[int, str]
    payload: Mapping[str, Any] = {"a": 2, "b": "t"}

    state: Any = specialized(a=1, b="s", rest=(payload,))

    assert type(state.rest[0]) is specialized
    with pytest.raises(ValidationError):
        specialized(a=1, b="s", rest=({"a": "x", "b": "t"},))


type Level0[W] = W | None
type Level1[W] = tuple[Level0[W], Level0[W]]
type Level2[W] = tuple[Level1[W], Level1[W]]
type Level3[W] = tuple[Level2[W], Level2[W]]
type Level4[W] = tuple[Level3[W], Level3[W]]
type Level5[W] = tuple[Level4[W], Level4[W]]
type Level6[W] = tuple[Level5[W], Level5[W]]
type Level7[W] = tuple[Level6[W], Level6[W]]
type Level8[W] = tuple[Level7[W], Level7[W]]
type Level9[W] = tuple[Level8[W], Level8[W]]
type Level10[W] = tuple[Level9[W], Level9[W]]
type Level11[W] = tuple[Level10[W], Level10[W]]


def test_an_alias_used_twice_by_another_is_resolved_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resolutions: list[Any] = []
    resolve: Any = vars(annotations)["_resolve_type_alias"]

    def counting(alias: Any, **kwargs: Any) -> Any:
        resolutions.append(alias)
        return resolve(alias, **kwargs)

    monkeypatch.setattr(annotations, "_resolve_type_alias", counting, raising=True)

    class Chain[V](State):
        chained: Level11[V]

    assert Chain is not None
    assert len(resolutions) <= 2 * 12


class Entry(State):
    n: int


type Entries = Sequence["Entry"]


def test_type_parameters_of_the_class_do_not_shadow_names_within_an_alias() -> None:
    class Shadowing[Entry](State):  # the parameter is named as a class of the module
        first: Entry
        entries: Entries = ()

    specialized: Any = Shadowing[str]

    state: Any = specialized(first="a", entries=({"n": 1},))

    assert type(state.entries[0]) is Entry


class Namespace:
    attribute = int


type Nonexistent = Sequence[Namespace.attribute]


def _declare_nonexistent() -> Any:
    class Broken(State):
        children: Nonexistent = ()

    return Broken


def test_a_missing_attribute_within_an_alias_is_reported_with_the_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(Namespace, "attribute")

    with pytest.raises(AttributeError, match=r"attribute.*Nonexistent"):
        _declare_nonexistent()


def _declare_old_pair_after(direct: tuple[str, Any], rest: str) -> Any:
    # a reference to the class made in order, then the alias which is the one under test
    return types.new_class(
        "OldPair",
        (State, OldParameters),
        {},
        lambda namespace: namespace.update(
            __module__=__name__,
            __annotations__={"a": "OldA", "b": "OldB", "direct": direct[0], "rest": rest},
            direct=direct[1],
            rest=(),
        ),
    )


@pytest.mark.parametrize(
    "direct",
    [("OldPair[OldA, OldB]", None), ("Sequence[OldPair[OldA, OldB]]", ())],
)
def test_an_alias_given_the_own_parameters_swapped_is_refused_after_a_reference_in_order(
    direct: tuple[str, Any],
) -> None:
    with pytest.raises(TypeError, match="can't be specialized within its own declaration"):
        _declare_old_pair_after(direct, "OldKids[OldB, OldA]")


def test_an_alias_given_the_own_parameters_in_order_works_after_a_reference_in_order() -> None:
    specialized: Any = _declare_old_pair_after(
        ("Sequence[OldPair[OldA, OldB]]", ()),
        "OldKids[OldA, OldB]",
    )[int, str]

    state: Any = specialized(a=1, b="s", rest=({"a": 2, "b": "t"},))

    assert type(state.rest[0]) is specialized
    with pytest.raises(ValidationError):
        specialized(a=1, b="s", rest=({"a": "x", "b": "t"},))


def _declare_wide() -> Any:
    # twenty parameters, each of them given to an alias twice
    count: int = 20
    parameters: str = ", ".join(f"T{index}" for index in range(count))
    alias_parameters: str = ", ".join(f"X{index}, Y{index}" for index in range(count))
    arguments: str = ", ".join(f"T{index}, T{index}" for index in range(count))
    referring: str = ", ".join(f"{'X' if index % 2 else 'Y'}{index}" for index in range(count))
    namespace: dict[str, Any] = {"__name__": __name__, "State": State, "Sequence": Sequence}
    exec(
        f"type WideKids[{alias_parameters}] = Sequence[Wide[{referring}]]\n"
        f"class Wide[{parameters}](State):\n"
        "    value: T0\n"
        f"    kids: WideKids[{arguments}] = ()\n",
        namespace,
    )
    return namespace["Wide"]


def test_a_class_with_many_parameters_each_given_to_an_alias_twice() -> None:
    specialized: Any = _declare_wide()[*(int,) * 20]

    tree: Any = specialized(value=1, kids=({"value": 2, "kids": ({"value": 3},)},))

    assert type(tree.kids[0].kids[0]) is specialized
    with pytest.raises(ValidationError, match=r"kids\[0\]\.value"):
        specialized(value=1, kids=(INVALID,))
