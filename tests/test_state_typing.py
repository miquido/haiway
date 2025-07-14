from typing import Any

from haiway import State


def test_state_typing_subclass_and_instance_checks() -> None:
    class Parent: ...

    class Child(Parent): ...

    class Unrelated: ...

    class GenericState[T](State):
        value: T

    class ConcreteState(GenericState[str]): ...

    # issubclass checks
    assert issubclass(GenericState[str], GenericState)
    assert issubclass(GenericState[Any], GenericState)
    assert issubclass(GenericState, GenericState)
    assert issubclass(ConcreteState, GenericState)
    assert issubclass(ConcreteState, GenericState[str])
    assert not issubclass(ConcreteState, GenericState[int])

    # Covariance
    assert issubclass(GenericState[Child], GenericState[Parent])
    assert not issubclass(GenericState[Parent], GenericState[Child])

    # Any
    assert issubclass(GenericState[str], GenericState[Any])
    assert not issubclass(GenericState[Any], GenericState[str])

    # Unrelated
    assert not issubclass(GenericState[Unrelated], GenericState[Parent])
    assert not issubclass(Unrelated, GenericState)
    assert not issubclass(GenericState, Unrelated)

    # isinstance checks
    instance_str = GenericState[str](value="test")
    instance_child = GenericState[Child](value=Child())
    instance_concrete = ConcreteState(value="test")

    assert isinstance(instance_str, GenericState)
    assert isinstance(instance_str, GenericState[str])
    assert isinstance(instance_str, GenericState[Any])
    assert not isinstance(instance_str, GenericState[int])

    assert isinstance(instance_child, GenericState[Parent])
    assert not isinstance(instance_child, GenericState[Unrelated])

    assert isinstance(instance_concrete, ConcreteState)
    assert isinstance(instance_concrete, GenericState[str])
    assert isinstance(instance_concrete, GenericState)
    assert not isinstance(instance_concrete, GenericState[int])

    # Check instance of unparametrized generic
    unparametrized_instance = GenericState(value="a string")
    assert isinstance(unparametrized_instance, GenericState)
    assert isinstance(unparametrized_instance, GenericState[str])
    assert not isinstance(unparametrized_instance, GenericState[int])

    unparametrized_instance_child = GenericState(value=Child())
    assert isinstance(unparametrized_instance_child, GenericState[Child])
    assert isinstance(unparametrized_instance_child, GenericState[Parent])
    assert not isinstance(unparametrized_instance_child, GenericState[str])
