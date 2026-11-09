import copy
from collections.abc import Mapping
from types import new_class
from typing import Any, ClassVar

from pytest import raises

from haiway import Default, Immutable
from haiway.types.immutable import attribute_names, namespace_annotations


class RecursiveImmutable(Immutable):
    child: RecursiveImmutable | None


def test_recursive_immutable_annotations_do_not_fail_during_class_creation() -> None:
    instance = RecursiveImmutable(
        child=RecursiveImmutable(
            child=None,
        ),
    )

    assert instance.child is not None
    assert instance.child.child is None


class ParentImmutable(Immutable):
    a: int


def test_immutable_subclasses_cannot_be_inherited() -> None:
    with raises(
        TypeError,
        match=r"Immutable subclasses cannot be inherited",
    ):

        class ChildImmutable(ParentImmutable):
            b: int


class _SlottedMixin:
    __slots__ = ("extra",)


class _PlainMixin:
    pass


def test_immutable_can_not_be_combined_with_another_base() -> None:
    for other in (_SlottedMixin, _PlainMixin, int):
        with raises(TypeError, match="inherits from Immutable alone"):
            new_class("Combined", (Immutable, other))


class SlottedImmutable(Immutable):
    required: int
    defaulted: str = "default"


def test_immutable_attributes_are_stored_in_slots() -> None:
    instance = SlottedImmutable(required=1)

    assert not hasattr(instance, "__dict__")
    assert SlottedImmutable.__slots__ == ("required", "defaulted")
    assert SlottedImmutable.__match_args__ == ("required", "defaulted")
    assert instance.required == 1
    # the declared default is resolved into the attributes, not left on the class
    assert instance.defaulted == "default"

    with raises(AttributeError):
        object.__setattr__(instance, "undeclared", 42)


def test_immutable_attribute_named_after_its_type_resolves_to_the_type() -> None:
    class Shadowing(Immutable):
        str: str = "text"

    assert Shadowing().str == "text"
    assert Shadowing.__ATTRIBUTES__.keys() == {"str"}


class Probed(Immutable):
    required: int
    defaulted: str = "x"


def test_missing_required_attribute_is_reported_by_name() -> None:
    with raises(AttributeError, match=r"Missing required attribute: required@Probed"):
        Probed()  # pyright: ignore[reportCallIssue]


def test_immutable_rejects_mutation_and_deletion() -> None:
    instance = Probed(required=1)

    with raises(AttributeError, match="cannot be modified"):
        instance.required = 2  # pyright: ignore[reportAttributeAccessIssue]

    with raises(AttributeError, match="cannot be deleted"):
        del instance.required  # pyright: ignore[reportAttributeAccessIssue]

    assert instance.required == 1


def test_immutable_str_repr_and_copies() -> None:
    instance = Probed(required=1)

    assert str(instance) == "Probed(required: 1, defaulted: x)"
    assert repr(instance) == str(instance)
    assert copy.copy(instance) is instance
    assert copy.deepcopy(instance) is instance


def test_immutable_default_values_are_resolved_per_instance() -> None:
    class Defaulting(Immutable):
        items: tuple[int, ...] = ()
        factory: list[int] = Default(factory=list[int])

    first = Defaulting()
    second = Defaulting()

    assert first.items == ()
    assert first.factory == []
    assert first.factory is not second.factory


_namespaces: dict[str, Mapping[str, Any]] = {}


class _Recording(type):
    def __new__(
        mcs,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
    ) -> type:
        _namespaces[name] = dict(namespace)
        return super().__new__(mcs, name, bases, namespace)


class _Sample(metaclass=_Recording):
    first: int
    second: str = "x"
    shared: ClassVar[int] = 1
    __special__: int
    forward: _Later | None


class _Later:
    pass


class _Bare(metaclass=_Recording):
    value = 1


def test_namespace_annotations_resolves_declared_annotations() -> None:
    annotations = namespace_annotations(_namespaces[_Sample.__name__])

    assert annotations is not None
    assert list(annotations) == ["first", "second", "shared", "__special__", "forward"]
    assert annotations["first"] is int


def test_namespace_annotations_is_none_without_annotations() -> None:
    assert namespace_annotations({}) is None
    assert namespace_annotations(_namespaces[_Bare.__name__]) is None


def test_attribute_names_skips_dunders_and_class_variables() -> None:
    annotations = namespace_annotations(_namespaces[_Sample.__name__])

    assert attribute_names(annotations) == ("first", "second", "forward")


def test_attribute_names_without_annotations_is_empty() -> None:
    assert attribute_names(None) == ()
    assert attribute_names({}) == ()


def test_immutable_equality_and_inequality_both_compare_identity() -> None:
    first = Probed(required=1)
    same = first
    second = Probed(required=1)

    assert first == same
    assert (first != same) is False
    assert first != second
    assert (first == second) is False
