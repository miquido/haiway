from __future__ import annotations

import typing
from typing import ClassVar

from haiway import State


class _Later(State):
    value: int
    label: str = "x"
    child: _Later | None = None


def test_a_state_declared_with_deferred_annotations_keeps_its_defaults() -> None:
    state = _Later(value=1, child=_Later(value=2))

    assert state.label == "x"
    assert state.child is not None
    assert state.child.value == 2


class _Shared(State):
    shared: ClassVar[int] = 1
    qualified: typing.ClassVar[str] = "x"
    value: int


def test_class_variables_of_a_deferred_body_are_not_attributes() -> None:
    assert _Shared.__match_args__ == ("value",)
    assert _Shared.shared == 1
    assert _Shared.qualified == "x"
    assert _Shared(value=2).to_mapping() == {"value": 2}
