from collections.abc import Sequence
from typing import cast

import pytest

from haiway import AttributePath, AttributeRequirement, State


class Example(State):
    x: int
    ys: Sequence[int]


example = Example(x=1, ys=[1, 2, 3])


def test_equal_requirement() -> None:
    path = Example._.x
    req = AttributeRequirement.equal(1, path)
    assert req.lhs is path
    assert req.operator == "equal"
    assert req.rhs == 1
    assert req.check(example)
    assert req.check(example, raise_exception=False)
    with pytest.raises(ValueError):
        req.check(Example(x=2, ys=[1, 2, 3]))
    assert not req.check(Example(x=2, ys=[1, 2, 3]), raise_exception=False)


def test_not_equal_requirement() -> None:
    path = Example._.x
    req = AttributeRequirement.not_equal(1, path)
    assert req.lhs is path
    assert req.operator == "not_equal"
    assert req.rhs == 1
    assert req.check(Example(x=2, ys=[1, 2, 3]))
    assert req.check(Example(x=2, ys=[1, 2, 3]), raise_exception=False)
    with pytest.raises(ValueError):
        req.check(example)
    assert not req.check(example, raise_exception=False)


def test_contains_requirement() -> None:
    path = Example._.ys
    req = AttributeRequirement.contains(2, path)
    assert req.lhs is path
    assert req.operator == "contains"
    assert req.rhs == 2
    assert req.check(example)
    assert req.check(example, raise_exception=False)
    with pytest.raises(ValueError):
        req.check(Example(x=1, ys=[3, 4]))
    assert not req.check(Example(x=1, ys=[3, 4]), raise_exception=False)


def test_contains_any_requirement() -> None:
    path = Example._.ys
    req = AttributeRequirement[Example].contains_any([2, 4], path)
    assert req.lhs is path
    assert req.operator == "contains_any"
    assert req.rhs == [2, 4]
    assert req.check(example)
    assert req.check(example, raise_exception=False)
    with pytest.raises(ValueError):
        req.check(Example(x=1, ys=[5, 6]))
    assert not req.check(Example(x=1, ys=[5, 6]), raise_exception=False)


def test_contained_in_requirement() -> None:
    path = Example._.x
    req = AttributeRequirement.contained_in([1, 2], path)
    assert req.lhs == [1, 2]
    assert req.operator == "contained_in"
    assert req.rhs is path
    assert req.check(example)
    assert req.check(example, raise_exception=False)
    with pytest.raises(ValueError):
        req.check(Example(x=3, ys=[1, 2, 3]))
    assert not req.check(Example(x=3, ys=[1, 2, 3]), raise_exception=False)


def test_logical_and_or_requirements() -> None:
    path_x = Example._.x
    path_ys = Example._.ys
    req1 = AttributeRequirement.equal(1, path_x)
    req2 = AttributeRequirement.contains(2, path_ys)
    and_req = req1 & req2
    assert and_req.lhs is req1
    assert and_req.operator == "and"
    assert and_req.rhs is req2
    assert and_req.check(example)
    with pytest.raises(ValueError):
        and_req.check(Example(x=1, ys=[3, 4]))
    or_req = req1 | req2
    assert or_req.lhs is req1
    assert or_req.operator == "or"
    assert or_req.rhs is req2
    assert or_req.check(Example(x=1, ys=[3, 4]))
    assert or_req.check(Example(x=2, ys=[1, 2]))
    with pytest.raises(ValueError):
        or_req.check(Example(x=2, ys=[3, 4]))


def test_filter() -> None:
    path_x = Example._.x
    req = AttributeRequirement.equal(1, path_x)
    values = [
        example,
        Example(x=2, ys=[1, 2, 3]),
        Example(x=1, ys=[4, 5]),
    ]
    assert req.filter(values) == [example, Example(x=1, ys=[4, 5])]


def test_immutability() -> None:
    path = cast(AttributePath[Example, int], Example._.x)
    req = AttributeRequirement.equal(1, path)
    with pytest.raises(AttributeError):
        req.lhs = path
    with pytest.raises(AttributeError):
        del req.rhs
