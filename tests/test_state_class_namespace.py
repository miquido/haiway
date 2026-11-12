from collections.abc import Mapping
from typing import Any, ClassVar

from haiway.attributes.state import _attribute_annotations, _namespace_annotations  # pyright: ignore[reportPrivateUsage]

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
    bare: ClassVar = 2  # pyright: ignore[reportMissingTypeArgument]
    __special__: int
    forward: _Later | None


class _Later:
    pass


class _Bare(metaclass=_Recording):
    value = 1


def test_namespace_annotations_resolves_declared_annotations() -> None:
    annotations = _namespace_annotations(_namespaces[_Sample.__name__])

    assert annotations is not None
    assert list(annotations) == [
        "first",
        "second",
        "shared",
        "bare",
        "__special__",
        "forward",
    ]
    assert annotations["first"] is int


def test_namespace_annotations_is_none_without_annotations() -> None:
    assert _namespace_annotations({}) is None
    assert _namespace_annotations(_namespaces[_Bare.__name__]) is None


def test_attribute_annotations_skip_dunders_and_class_variables() -> None:
    annotations = _attribute_annotations(_namespaces[_Sample.__name__])

    assert list(annotations) == ["first", "second", "forward"]
    assert annotations["first"] is int


def test_attribute_annotations_without_annotations_are_empty() -> None:
    assert _attribute_annotations({}) == {}
    assert _attribute_annotations(_namespaces[_Bare.__name__]) == {}


def test_attribute_annotations_tell_class_variables_of_a_deferred_body() -> None:
    annotations = _attribute_annotations(
        {
            "__module__": __name__,
            "__annotations__": {
                "value": "int",
                "shared": "ClassVar[int]",
                "bare": "ClassVar",
                "unknown": "Undefined.ClassVar[int]",
            },
        }
    )

    assert list(annotations) == ["value", "unknown"]


def test_namespace_annotations_reads_the_stored_annotations_of_a_deferred_body() -> None:
    # `from __future__ import annotations` leaves text in `__annotations__`, no function to call
    annotations = _namespace_annotations({"__annotations__": {"value": "int"}})

    assert annotations == {"value": "int"}
