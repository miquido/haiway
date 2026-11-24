from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from haiway import State, ValidationError

# the annotations below are quoted on purpose - a quoted annotation is evaluated with the
# type parameters of the class in scope, as an unquoted one is


class QuotedBox[V](State):
    value: "V"  # noqa: UP037


class QuotedBounded[V: int](State):
    value: "V"  # noqa: UP037


class QuotedTree[V](State):
    value: V
    children: "Sequence[QuotedTree[V]]" = ()  # noqa: UP037


class QuotedNested[V](State):
    boxes: "Sequence[QuotedBox[V]]" = ()  # noqa: UP037


def test_quoted_annotation_sees_the_type_parameters_of_the_class() -> None:
    unspecialized: Any = QuotedBox
    specialized: Any = QuotedBox[int]

    assert specialized(value=1).value == 1
    assert unspecialized(value="anything").value == "anything"
    with pytest.raises(ValidationError, match="value"):
        specialized(value="x")


def test_quoted_annotation_keeps_the_bound_of_a_parameter() -> None:
    bounded: Any = QuotedBounded

    assert bounded(value=1).value == 1
    with pytest.raises(ValidationError, match="value"):
        bounded(value="x")


def test_quoted_reference_of_a_generic_to_itself() -> None:
    payload: Mapping[str, Any] = {"value": 2, "children": [{"value": 3}]}
    specialized: Any = QuotedTree[int]

    tree: Any = specialized(value=1, children=(payload,))

    assert type(tree.children[0]) is specialized
    assert type(tree.children[0].children[0]) is specialized
    assert specialized.from_json(tree.to_json()) == tree
    with pytest.raises(ValidationError, match=r"children\[0\]\.value"):
        specialized(value=1, children=({"value": "x"},))


def test_quoted_generic_field_is_specialized_with_the_class() -> None:
    specialized: Any = QuotedNested[int]

    nested: Any = specialized(boxes=({"value": 2},))

    assert type(nested.boxes[0]) is QuotedBox[int]
    with pytest.raises(ValidationError, match=r"boxes\[0\]\.value"):
        specialized(boxes=({"value": "x"},))


def _declare_swapping() -> type[State]:
    class Swap[A, B](State):
        first: A
        swapped: "Swap[B, A] | None" = None  # noqa: UP037

    return Swap


def test_quoted_reference_with_other_parameters_is_refused() -> None:
    with pytest.raises(TypeError, match=r"Swap can't be specialized within its own declaration"):
        _declare_swapping()
