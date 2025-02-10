from collections.abc import Callable, Sequence, Set
from copy import copy, deepcopy
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal, NotRequired, Protocol, Required, Self, TypedDict, runtime_checkable
from uuid import UUID, uuid4

from pytest import raises

from haiway import MISSING, Default, Missing, State, frozenlist


def test_basic_initializes_with_arguments() -> None:
    class Selection(StrEnum):
        A = "A"
        B = "B"

    class Nes(State):
        val: str

    class TypedValues(TypedDict):
        val: str
        mis: int | Missing
        req: Required[Nes]
        nreq: NotRequired[bool]

    @runtime_checkable
    class Proto(Protocol):
        def __call__(self) -> None: ...

    class Basics(State):
        uuid: UUID
        date: date
        datetime: datetime
        string: str
        literal: Literal["A", "B"]
        sequence: Sequence[str]
        string_set: Set[str]
        frozen: frozenlist[int]
        integer: int
        union: str | int
        optional: str | None
        none: None
        function: Callable[[], None]
        proto: Proto
        selection: Selection
        typeddict: TypedValues

    basic = Basics(
        uuid=uuid4(),
        date=date.today(),
        datetime=datetime.now(),
        string="string",
        literal="A",
        sequence=["a", "b", "c"],
        string_set={"a", "b"},
        frozen=(1, 2, 3),
        integer=0,
        union="union",
        optional="optional",
        none=None,
        function=lambda: None,
        proto=lambda: None,
        selection=Selection.A,
        typeddict={
            "val": "ok",
            "mis": 42,
            "req": Nes(val="ok"),
        },
    )
    assert basic.string == "string"
    assert basic.literal == "A"
    assert basic.sequence == ("a", "b", "c")
    assert basic.string_set == {"a", "b"}
    assert basic.frozen == (1, 2, 3)
    assert basic.integer == 0
    assert basic.union == "union"
    assert basic.optional == "optional"
    assert basic.none is None
    assert basic.selection == Selection.A
    assert basic.typeddict == TypedValues(
        val="ok",
        mis=42,
        req=Nes(val="ok"),
    )
    assert callable(basic.function)
    assert isinstance(basic.proto, Proto)


def test_basic_initializes_with_defaults() -> None:
    class Basics(State):
        string: str = ""
        integer: int = 0
        optional: str | None = None
        unique: UUID = Default(factory=uuid4)
        same: UUID = Default(uuid4())

    basic = Basics()
    assert basic.string == ""
    assert basic.integer == 0
    assert basic.optional is None
    assert basic.unique is not Basics().unique
    assert basic.same is Basics().same


def test_basic_equals_checks_properties() -> None:
    class Basics(State):
        string: str
        integer: int

    assert Basics(string="a", integer=1) == Basics(string="a", integer=1)
    assert Basics(string="a", integer=1) != Basics(string="b", integer=1)
    assert Basics(string="a", integer=1) != Basics(string="a", integer=2)
    assert Basics(string="a", integer=1) != Basics(string="b", integer=2)


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


def test_dict_skips_missing_properties() -> None:
    class Basics(State):
        string: str
        integer: int | Missing | None

    assert Basics(string="a", integer=1).as_dict() == {"string": "a", "integer": 1}
    assert Basics(string="a", integer=MISSING).as_dict() == {"string": "a"}
    assert Basics(string="a", integer=None).as_dict() == {"string": "a", "integer": None}


def test_initialization_allows_missing_properties() -> None:
    class Basics(State):
        string: str
        integer: int | Missing | None

    assert Basics(**{"string": "a", "integer": 1}) == Basics(string="a", integer=1)
    assert Basics(**{"string": "a", "integer": None}) == Basics(string="a", integer=None)
    assert Basics(**{"string": "a"}) == Basics(string="a", integer=MISSING)


def test_generic_subtypes_validation() -> None:
    class NestedGeneric[T](State):
        value: T

    class Generic[T](State):
        nested: NestedGeneric[T]

    class Container(State):
        generic: Generic[str]

    assert isinstance(Generic[str](nested=NestedGeneric[str](value="ok")), Generic)
    assert isinstance(Generic(nested=NestedGeneric[str](value="ok")), Generic)
    assert isinstance(Generic(nested=NestedGeneric(value="ok")), Generic)

    assert isinstance(Generic[str](nested=NestedGeneric[str](value="ok")), Generic[Any])
    assert isinstance(Generic(nested=NestedGeneric[str](value="ok")), Generic[Any])
    assert isinstance(Generic[str](nested=NestedGeneric(value="ok")), Generic[Any])
    assert isinstance(Generic(nested=NestedGeneric(value="ok")), Generic[Any])

    assert isinstance(Generic[str](nested=NestedGeneric[str](value="ok")), Generic[str])
    assert isinstance(Generic(nested=NestedGeneric[str](value="ok")), Generic[str])
    assert isinstance(Generic[str](nested=NestedGeneric(value="ok")), Generic[str])
    assert isinstance(Generic(nested=NestedGeneric(value="ok")), Generic[str])

    with raises(TypeError):
        _ = Generic[int](nested=NestedGeneric[str](value="ok"))

    with raises(TypeError):
        _ = Container(generic=Generic(nested=NestedGeneric(value=42)))

    with raises(TypeError):
        _ = Container(generic=Generic[int](nested=NestedGeneric[str](value="ok")))

    # not raises
    _ = Container(generic=Generic(nested=NestedGeneric(value="ok")))


def test_copying_leaves_same_object() -> None:
    class Nested(State):
        string: str

    class Copied(State):
        string: str
        nested: Nested

    origin = Copied(string="42", nested=Nested(string="answer"))
    assert copy(origin) is origin
    assert deepcopy(origin) is origin
