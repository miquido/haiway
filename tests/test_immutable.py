from pytest import raises

from haiway import Immutable


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
