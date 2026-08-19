from collections.abc import Sequence
from typing import Any

from pytest import raises

from haiway import State


def test_state_typing_subclass_and_instance_checks() -> None:
    class Parent: ...

    class Child(Parent): ...

    class Unrelated: ...

    class GenericState[T](State):
        value: T

    # issubclass checks
    assert issubclass(GenericState[str], GenericState)
    assert issubclass(GenericState[Any], GenericState)
    assert issubclass(GenericState, GenericState)

    # Covariance
    assert issubclass(GenericState[Child], GenericState[Parent])
    assert not issubclass(GenericState[Parent], GenericState[Child])

    # Any
    assert issubclass(GenericState[str], GenericState[Any])
    assert issubclass(GenericState[Any], GenericState[str])

    # Unrelated
    assert not issubclass(GenericState[Unrelated], GenericState[Parent])
    assert not issubclass(Unrelated, GenericState)
    assert not issubclass(GenericState, Unrelated)

    # isinstance checks
    instance_str = GenericState[str](value="test")
    instance_child = GenericState[Child](value=Child())

    assert isinstance(instance_str, GenericState)
    assert isinstance(instance_str, GenericState[str])
    assert isinstance(instance_str, GenericState[Any])
    assert not isinstance(instance_str, GenericState[int])

    assert isinstance(instance_child, GenericState[Parent])
    assert not isinstance(instance_child, GenericState[Unrelated])

    # Check instance of unparametrized generic
    unparametrized_instance = GenericState(value="a string")
    assert isinstance(unparametrized_instance, GenericState)
    assert isinstance(unparametrized_instance, GenericState[str])
    assert not isinstance(unparametrized_instance, GenericState[int])

    unparametrized_instance_child = GenericState(value=Child())
    assert isinstance(unparametrized_instance_child, GenericState[Child])
    assert isinstance(unparametrized_instance_child, GenericState[Parent])
    assert not isinstance(unparametrized_instance_child, GenericState[str])


def test_instance_check_handles_sequence_type_parameters() -> None:
    class SequenceState[T](State):
        values: Sequence[T]

    instance = SequenceState[str](values=("a", "b"))

    assert isinstance(instance, SequenceState[str])
    assert isinstance(instance, SequenceState[Any])
    assert not isinstance(instance, SequenceState[int])


def test_instance_check_handles_nested_state_parameters() -> None:
    class InnerState[T](State):
        value: T

    class OuterState[T](State):
        inner: InnerState[T]

    instance = OuterState[str](inner=InnerState[str](value="ok"))

    assert isinstance(instance, OuterState[str])
    assert isinstance(instance, OuterState[Any])
    assert isinstance(instance, OuterState[object])
    assert not isinstance(instance, OuterState[int])


def test_instance_check_rejects_strings_for_tuple_type_parameters() -> None:
    class ValueState[T](State):
        value: T

    # str/bytes are sequences of str/int, yet they are not tuples - instance
    # checks have to agree with validation, which rejects them outright
    assert not isinstance(ValueState(value="ab"), ValueState[tuple[str, str]])
    assert not isinstance(ValueState(value=b"ab"), ValueState[tuple[int, int]])
    assert not isinstance(ValueState(value=bytearray(b"ab")), ValueState[tuple[int, int]])
    assert not isinstance(ValueState(value=memoryview(b"ab")), ValueState[tuple[int, int]])

    assert isinstance(ValueState(value=("a", "b")), ValueState[tuple[str, str]])


def test_instance_check_rejects_strings_for_sequence_type_parameters() -> None:
    class ValueState[T](State):
        value: T

    assert not isinstance(ValueState(value="ab"), ValueState[Sequence[str]])
    assert not isinstance(ValueState(value=b"ab"), ValueState[Sequence[int]])

    assert isinstance(ValueState(value=("a", "b")), ValueState[Sequence[str]])


def test_state_typing_specialization_can_not_be_inherited() -> None:
    class GenericState[T](State):
        value: T

    specialization = GenericState[str]

    # a specialization declares the attributes of its origin, so it is final the
    # same way the origin is - there is no nominal type deriving from one, and
    # no type arguments to carry over into it
    with raises(TypeError, match=r"can't inherit from GenericState\[str\]"):

        class Concrete(specialization): ...

    assert specialization.__TYPE_PARAMETERS__ == {"T": str}


def test_state_typing_checks_handle_non_class_arguments() -> None:
    class GenericState[T](State):
        value: T

    sequences = GenericState[Sequence[int]]
    strings = GenericState[Sequence[str]]
    optionals = GenericState[int | None]

    # a parameterized generic or a union has no subclass relation to resolve, so
    # it matches the equal argument and nothing else - without raising
    assert issubclass(sequences, GenericState[Sequence[int]])
    assert not issubclass(sequences, strings)
    assert isinstance(sequences(value=(1, 2)), GenericState[Sequence[int]])
    assert not isinstance(sequences(value=(1, 2)), strings)
    assert isinstance(optionals(value=None), GenericState[int | None])
    assert not isinstance(optionals(value=None), GenericState[str | None])
