from collections.abc import Callable
from typing import Literal, Protocol, Self, runtime_checkable

from haiway import State, frozenlist


def test_basic_initializes_with_arguments() -> None:
    @runtime_checkable
    class Proto(Protocol):
        def __call__(self) -> None: ...

    class Basics(State):
        string: str
        literal: Literal["A", "B"]
        sequence: list[str]
        frozen: frozenlist[int]
        integer: int
        union: str | int
        optional: str | None
        none: None
        function: Callable[[], None]
        proto: Proto

    basic = Basics(
        string="string",
        literal="A",
        sequence=["a", "b", "c"],
        frozen=(1, 2, 3),
        integer=0,
        union="union",
        optional="optional",
        none=None,
        function=lambda: None,
        proto=lambda: None,
    )
    assert basic.string == "string"
    assert basic.literal == "A"
    assert basic.sequence == ["a", "b", "c"]
    assert basic.frozen == (1, 2, 3)
    assert basic.integer == 0
    assert basic.union == "union"
    assert basic.optional == "optional"
    assert basic.none is None
    assert callable(basic.function)
    assert isinstance(basic.proto, Proto)


def test_basic_initializes_with_defaults() -> None:
    class Basics(State):
        string: str = ""
        integer: int = 0
        optional: str | None = None

    basic = Basics()
    assert basic.string == ""
    assert basic.integer == 0
    assert basic.optional is None


def test_basic_initializes_with_arguments_and_defaults() -> None:
    class Basics(State):
        string: str
        integer: int = 0
        optional: str | None = None

    basic = Basics(
        string="string",
        integer=42,
    )
    assert basic.string == "string"
    assert basic.integer == 42
    assert basic.optional is None


def test_parametrized_initializes_with_proper_parameters() -> None:
    class Parametrized[T](State):
        value: T

    parametrized_string = Parametrized(
        value="string",
    )
    assert parametrized_string.value == "string"

    parametrized_int = Parametrized(
        value=42,
    )
    assert parametrized_int.value == 42

    assert parametrized_string != parametrized_int


def test_nested_initializes_with_proper_arguments() -> None:
    class Nested(State):
        string: str

    class Recursive(State):
        nested: Nested
        recursion: "Recursive | None"
        self_recursion: Self | None

    recursive = Recursive(
        nested=Nested(string="one"),
        recursion=Recursive(
            nested=Nested(string="two"),
            recursion=None,
            self_recursion=None,
        ),
        self_recursion=None,
    )
    assert recursive.nested == Nested(string="one")
    assert recursive.recursion == Recursive(
        nested=Nested(string="two"),
        recursion=None,
        self_recursion=None,
    )
