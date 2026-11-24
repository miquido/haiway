from typing import Any, Optional

import pytest

from haiway import State, ValidationError


class Generic[Value](State):
    value: Value


# two different classes with the same name, each given as an argument
class Integers:
    class Item(State):
        number: int


class Texts:
    class Item(State):
        number: str


ItemA = Integers.Item
ItemB = Texts.Item


# quoted, to be forward references whatever the version of Python
class Quoted(State):
    first: "Generic[ItemA]"  # noqa: UP037
    second: "Generic[ItemB]"  # noqa: UP037


class Referenced(State):
    first: Optional["Generic[ItemA]"] = None  # noqa: UP037, UP045
    second: Optional["Generic[ItemB]"] = None  # noqa: UP037, UP045


@pytest.mark.parametrize("holder", [Quoted, Referenced])
def test_forward_references_of_same_named_classes_resolve_to_their_own(holder: Any) -> None:
    value: Any = holder(first={"value": {"number": 1}}, second={"value": {"number": "a"}})

    assert type(value.first.value) is ItemA
    assert type(value.second.value) is ItemB

    with pytest.raises(ValidationError):
        holder(first={"value": {"number": 1}}, second={"value": {"number": 1}})
