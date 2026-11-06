import weakref
from typing import Any

import pytest

from haiway import State
from haiway._native import NATIVE

native_only = pytest.mark.skipif(not NATIVE, reason="requires the native State storage")


class Point(State):
    x: int
    y: int = 0


@native_only
def test_object_setattr_cannot_bypass_immutability() -> None:
    point = Point(x=1)

    with pytest.raises(AttributeError):
        object.__setattr__(point, "x", 2)  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError):
        object.__setattr__(point, "extra", 2)

    with pytest.raises(AttributeError):
        _ = point.__dict__  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(TypeError):
        weakref.ref(point)

    assert point.x == 1


@native_only
def test_initialized_instance_refuses_a_second_initialization() -> None:
    point = Point(x=1)

    with pytest.raises(TypeError, match="initialized already"):
        point.__init__(x=2)  # pyright: ignore[reportCallIssue]

    assert point.x == 1


@native_only
def test_custom_init_delegating_to_super_binds_the_attributes() -> None:
    class Renamed(State):
        _value: int

        def __init__(self, value: int) -> None:
            super().__init__(_value=value)

    assert Renamed(3)._value == 3  # pyright: ignore[reportPrivateUsage]


@native_only
def test_instance_which_skipped_init_is_safe_to_inspect() -> None:
    class Skipping(State):
        value: int

        def __init__(self) -> None:
            pass

    instance = Skipping()

    with pytest.raises(AttributeError):
        _ = instance.value

    assert instance == Skipping()
    assert isinstance(hash(instance), int)


@native_only
def test_hashing_a_deeply_nested_state_fails_instead_of_overflowing_the_stack() -> None:
    class Node(State):
        child: Any = None

    node = Node()
    for _ in range(300_000):
        node = Node(child=node)

    with pytest.raises(RecursionError):
        hash(node)
