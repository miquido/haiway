import gc
import weakref
from collections.abc import Sequence
from types import GenericAlias
from typing import Annotated, Any, Literal, Protocol, TypeVar

from pytest import mark, raises

from haiway import State, ValidationError
from haiway.attributes.state import StateMeta


class Animal:
    pass


class Dog(Animal):
    pass


class Cat(Animal):
    pass


class Puppy(Dog):
    pass


class Box[T](State):
    value: T


class Pair[A, B](State):
    first: A
    second: B


class Holder(State):
    box: Box[Animal]


# --- identity of variants ---------------------------------------------------


def test_specialization_is_cached_per_arguments() -> None:
    assert Box[int] is Box[int]
    assert Pair[int, str] is Pair[int, str]


def test_variants_with_different_arguments_are_different_classes() -> None:
    assert Box[int] is not Box[str]
    assert Pair[int, str] is not Pair[str, int]
    assert Box[int] is not Box


def test_variant_has_descriptive_name_and_keeps_origin_details() -> None:
    assert Box[int].__name__ == "Box[int]"
    assert Pair[int, str].__name__ == "Pair[int,str]"
    assert Box[int].__origin__ is Box
    assert Box[int].__args__ == (int,)
    assert Box[int].__TYPE_PARAMETERS__ == {"T": int}
    assert Pair[int, str].__TYPE_PARAMETERS__ == {"A": int, "B": str}
    assert Box[int].__module__ == Box.__module__


def test_instances_are_of_their_exact_variant() -> None:
    assert type(Box[int](value=1)) is Box[int]
    assert type(Box(value=1)) is Box


def test_variants_share_the_attribute_names_of_their_origin() -> None:
    assert [field.name for field in Box[int].__FIELDS__] == ["value"]
    assert Box[int].__match_args__ == ("value",)


def test_specializing_with_type_variable_returns_alias() -> None:
    parameter = TypeVar("parameter")
    alias = Box[parameter]  # pyright: ignore[reportGeneralTypeIssues]

    assert isinstance(alias, GenericAlias)
    assert alias.__origin__ is Box


@mark.parametrize(
    "specialize",
    [
        lambda: Pair[int],  # pyright: ignore[reportInvalidTypeArguments]
        lambda: Box[int][str],  # pyright: ignore[reportInvalidTypeArguments]
        lambda: State[int],  # pyright: ignore[reportInvalidTypeArguments]
    ],
    ids=["wrong-arity", "re-specialization", "non-generic"],
)
def test_invalid_specialization_is_rejected(specialize: Any) -> None:
    with raises(TypeError, match=r"specialize|type arguments"):
        specialize()


def test_specialization_cache_does_not_keep_dynamic_classes_alive() -> None:
    def create() -> weakref.ReferenceType[type[Any]]:
        class Dynamic[T](State):
            value: T

        assert Dynamic[int] is Dynamic[int]
        return weakref.ref(Dynamic)

    reference = create()
    gc.collect()

    assert reference() is None


def test_specialization_with_unhashable_argument_is_not_cached() -> None:
    first = Box[Annotated[int, {"unhashable": 1}]]  # pyright: ignore[reportInvalidTypeForm]
    second = Box[Annotated[int, {"unhashable": 1}]]  # pyright: ignore[reportInvalidTypeForm]

    assert first(value="1").value == 1
    assert first is not second
    assert Box[int] is Box[int]


def test_metaclass_finality_is_not_skipped_by_empty_type_parameters() -> None:
    with raises(TypeError, match="final"):
        StateMeta(
            "Derived",
            (Pair,),
            {"__module__": __name__},
            type_parameters={},
        )


# --- equality and hashing ---------------------------------------------------


def test_instances_of_different_variants_are_not_equal() -> None:
    assert Box[int](value=1) == Box[int](value=1)
    assert Box[int](value=1) != Box[float](value=1.0)
    assert Box[int](value=1) != Box(value=1)
    assert hash(Box[int](value=1)) != hash(Box[str](value="1"))
    assert hash(Box[int](value=1)) == hash(Box[int](value=1))


# --- validation against parameters ------------------------------------------


def test_variant_validates_values_against_its_arguments() -> None:
    assert Box[int](value="5").value == 5  # pyright: ignore[reportArgumentType]

    with raises(ValidationError):
        Box[int](value="x")  # pyright: ignore[reportArgumentType]

    with raises(ValidationError):
        Box[Dog](value=Animal())  # pyright: ignore[reportArgumentType]

    assert isinstance(Box[Dog](value=Puppy()).value, Puppy)  # pyright: ignore[reportArgumentType]


def test_unspecialized_generic_accepts_any_value() -> None:
    assert Box(value="text").value == "text"
    assert Box(value=3).value == 3


def test_updating_validates_against_arguments() -> None:
    box = Box[int](value=1)

    assert box.updating(value="3").value == 3  # pyright: ignore[reportArgumentType]

    with raises(ValidationError):
        box.updating(value="x")


def test_validate_converts_mapping_into_exact_variant() -> None:
    box = Box[int].validate({"value": "7"})

    assert type(box) is Box[int]
    assert box.value == 7


def test_validate_accepts_instance_of_same_or_narrower_variant() -> None:
    dog_box = Box[Dog](value=Dog())

    assert Box[Dog].validate(dog_box) is dog_box
    assert Box[Animal].validate(dog_box) is dog_box


def test_validate_rejects_instance_of_wider_or_unrelated_variant() -> None:
    with raises(TypeError):
        Box[Dog].validate(Box[Animal](value=Dog()))

    with raises(TypeError):
        Box[int].validate(Box[str](value="a"))


def test_nested_variant_attribute_keeps_the_provided_narrower_variant() -> None:
    dog_box = Box[Dog](value=Dog())

    assert Holder(box=dog_box).box is dog_box  # pyright: ignore[reportArgumentType]


def test_nested_variant_attribute_builds_declared_variant_from_mapping() -> None:
    holder = Holder(box={"value": Dog()})  # pyright: ignore[reportArgumentType]

    assert type(holder.box) is Box[Animal]


def test_nested_variant_attribute_rejects_unrelated_variant() -> None:
    with raises(ValidationError):
        Holder(box=Box[int](value=1))  # pyright: ignore[reportArgumentType]


# --- relation of parameters is preserved ------------------------------------


@mark.parametrize(
    ("variant", "other", "expected"),
    [
        (Box[int], Box[int], True),
        (Box[int], Box[str], False),
        (Box[Dog], Box[Animal], True),  # covariant
        (Box[Animal], Box[Dog], False),
        (Box[Puppy], Box[Animal], True),  # transitively
        (Box[Dog], Box[Cat], False),  # siblings
        (Box[int], Box[Any], True),
        (Box[Any], Box[int], True),
        (Box[int], Box, True),  # a variant is a kind of its origin
        (Box, Box[int], True),  # an origin matches whatever its variants match
        (Box[int], State, True),
        (Pair[Dog, int], Pair[Animal, int], True),
        (Pair[Dog, int], Pair[Animal, str], False),
        (Pair[Animal, int], Pair[Dog, int], False),
        (Pair[Dog, Puppy], Pair[Animal, Dog], True),
        (Box[int], Pair[int, int], False),  # different generics
        (Box[Sequence[int]], Box[Sequence[int]], True),
        (Box[tuple[int]], Box[Sequence[int]], False),  # non class arguments match only when equal
        (Box[int | str], Box[int | str], True),
        (Box[int], Box[int | str], True),  # a member of the union
        (Box[int | str], Box[int], False),
    ],
)
def test_subclass_relation_follows_parameters(
    variant: type[Any],
    other: type[Any],
    expected: bool,
) -> None:
    assert issubclass(variant, other) is expected


def test_relation_is_preserved_through_nested_parameters() -> None:
    assert issubclass(Box[Box[Dog]], Box[Box[Animal]])
    assert not issubclass(Box[Box[Animal]], Box[Box[Dog]])
    assert not issubclass(Box[Box[Dog]], Box[Box[Cat]])
    assert issubclass(Box[Pair[Dog, int]], Box[Pair[Animal, int]])
    assert not issubclass(Box[Pair[Dog, int]], Box[Pair[Animal, str]])
    assert Box[Box[Dog]] is not Box[Box[Animal]]

    inner = Box[Dog](value=Dog())
    nested = Box[Box[Dog]](value=inner)

    assert isinstance(nested, Box[Box[Animal]])  # pyright: ignore[reportArgumentType]
    assert not isinstance(nested, Box[Box[Cat]])  # pyright: ignore[reportArgumentType]


def test_collections_of_variants_accept_narrower_variants() -> None:
    class Shelf(State):
        boxes: Sequence[Box[Animal]]

    shelf = Shelf(boxes=[Box[Dog](value=Dog()), Box[Cat](value=Cat())])

    assert [type(box) for box in shelf.boxes] == [Box[Dog], Box[Cat]]

    with raises(ValidationError, match=r"\.boxes"):
        Shelf(boxes=[Box[int](value=1)])  # pyright: ignore[reportArgumentType]


def test_instance_relation_follows_parameters() -> None:
    dog_box = Box[Dog](value=Dog())

    assert isinstance(dog_box, Box[Dog])  # pyright: ignore[reportArgumentType]
    assert isinstance(dog_box, Box[Animal])  # pyright: ignore[reportArgumentType]
    assert isinstance(dog_box, Box)
    assert not isinstance(dog_box, Box[Cat])  # pyright: ignore[reportArgumentType]
    assert not isinstance(Box[Animal](value=Animal()), Box[Dog])  # pyright: ignore[reportArgumentType]


def test_instance_check_inspects_values_of_unspecialized_instances() -> None:
    assert isinstance(Box(value=1), Box[int])  # pyright: ignore[reportArgumentType]
    assert isinstance(Box(value=Dog()), Box[Animal])  # pyright: ignore[reportArgumentType]
    assert not isinstance(Box(value="s"), Box[int])  # pyright: ignore[reportArgumentType]
    assert not isinstance(Box(value=Animal()), Box[Dog])  # pyright: ignore[reportArgumentType]


def test_instance_check_of_non_specialized_state_is_nominal() -> None:
    assert isinstance(Box[int](value=1), State)  # pyright: ignore[reportArgumentType]
    assert not isinstance(object(), Box[int])  # pyright: ignore[reportArgumentType]
    assert not isinstance(Holder(box=Box[Animal](value=Animal())), Box[int])  # pyright: ignore[reportArgumentType]


@mark.parametrize(
    ("argument", "good", "bad"),
    [
        (int, 1, "1"),
        (float, 1.5, 1),
        (bool, True, 1),
        (str, "a", 1),
        (bytes, b"a", "a"),
        (list[int], [1], 5),
        (Sequence[int], (1,), "text"),
        (dict[str, int], {"a": 1}, [("a", 1)]),
        (Any, object(), None),
    ],
    ids=lambda value: getattr(value, "__name__", None) or repr(value)[:12],
)
def test_instance_check_of_unspecialized_instance_by_argument(
    argument: Any,
    good: Any,
    bad: Any,
) -> None:
    variant: type[Any] = Box[argument]

    assert isinstance(Box(value=good), variant)
    if argument is not Any:
        assert not isinstance(Box(value=bad), variant)


def test_variant_inheriting_class_relation_is_rejected() -> None:
    with raises(TypeError, match="final"):

        class Narrowed(Box[int]):  # pyright: ignore[reportGeneralTypeIssues]
            pass


# --- schema of variants -----------------------------------------------------


def test_json_schema_is_resolved_per_variant() -> None:
    assert Box[int].json_schema() != Box[str].json_schema()
    assert '"value": {"type": "integer"}' in Box[int].json_schema()
    assert '"value": {"type": "string"}' in Box[str].json_schema()


def test_variant_of_unserializable_argument_has_no_schema() -> None:
    class Opaque:
        pass

    assert Box[Opaque].json_schema(required=False) is None
    with raises(TypeError):
        Box[Opaque].json_schema()


def test_nested_variant_schema_embeds_the_declared_arguments() -> None:
    class IntHolder(State):
        box: Box[int]

    schema = IntHolder.json_schema()

    assert '"box": {"type": "object", "properties": {"value": {"type": "integer"}}' in schema


# --- bounds and defaults ----------------------------------------------------


def test_bounded_parameter_validates_values_against_the_argument() -> None:
    class Bounded[T: Animal](State):
        value: T

    assert isinstance(Bounded[Dog](value=Puppy()).value, Puppy)

    with raises(ValidationError):
        Bounded[Dog](value=Animal())


class Bounded[T: Animal](State):
    value: T


class Constrained[T: (int, str)](State):
    value: T


class UnionBounded[T: int | str](State):
    value: T


@mark.parametrize(
    "argument",
    [Animal, Dog, Puppy, Any, Dog | Cat, Dog | Puppy, "Animal"],
    ids=repr,
)
def test_bound_admits_matching_arguments(argument: Any) -> None:
    assert Bounded[argument].__args__ == (argument,)


@mark.parametrize(
    "argument",
    [int, object, Animal | int, None, Literal["a"]],
    ids=repr,
)
def test_bound_refuses_arguments_outside_of_it(argument: Any) -> None:
    with raises(TypeError, match="is bound to 'Animal'"):
        Bounded[argument]


@mark.parametrize("argument", [int, str, bool, Any, Literal["a"], Literal[1]])
def test_constraints_admit_matching_arguments(argument: Any) -> None:
    assert Constrained[argument].__args__ == (argument,)


@mark.parametrize("argument", [float, bytes, int | str, int | float, None, Literal[1.5]])
def test_constraints_refuse_other_arguments(argument: Any) -> None:
    with raises(TypeError, match="is constrained to 'int', 'str'"):
        Constrained[argument]


def test_union_bound_admits_members_and_refuses_others() -> None:
    assert UnionBounded[int].__args__ == (int,)
    assert UnionBounded[int | str].__args__ == (int | str,)

    with raises(TypeError, match="is bound to"):
        UnionBounded[float]


def test_bound_of_generic_alias_argument_follows_its_origin() -> None:
    class SequenceBounded[T: Sequence[Any]](State):
        value: T

    assert SequenceBounded[tuple[int, ...]].__args__ == (tuple[int, ...],)
    assert SequenceBounded[list[int]].__args__ == (list[int],)

    with raises(TypeError, match="is bound to"):
        SequenceBounded[dict[str, int]]


def test_bound_which_can_not_be_decided_on_classes_admits_the_argument() -> None:
    class Shape(Protocol):  # not runtime checkable - no class relation to check
        def area(self) -> int: ...

    class Shaped[T: Shape](State):
        value: T

    assert Shaped[Dog].__args__ == (Dog,)


def test_refused_specialization_is_not_cached() -> None:
    with raises(TypeError):
        Bounded[int]

    with raises(TypeError):
        Bounded[int]


def test_multiple_parameters_are_each_checked() -> None:
    class Mixed[A: Animal, B: (int, str)](State):
        first: A
        second: B

    assert Mixed[Dog, int].__args__ == (Dog, int)

    with raises(TypeError, match="'A' is bound"):
        Mixed[int, int]

    with raises(TypeError, match="'B' is constrained"):
        Mixed[Dog, float]


def test_unbounded_parameters_accept_anything() -> None:
    assert Box[object].__args__ == (object,)
    assert Box[None].__args__ == (None,)
    assert Box[int | None].__args__ == (int | None,)


def test_defaults_are_carried_over_to_variants() -> None:
    class Defaulted[T](State):
        value: T
        label: str = "default"

    assert Defaulted[int](value=1).label == "default"
    assert Defaulted[int](value=1, label="x").label == "x"


def test_specialization_serializes_and_round_trips() -> None:
    box = Box[int](value=3)

    assert Box[int].from_json(box.to_json()) == box
    assert Box[int].from_mapping(box.to_mapping()) == box
    assert Box[int].from_mapping(box.to_basic_object()) == box
