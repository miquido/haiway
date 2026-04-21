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
