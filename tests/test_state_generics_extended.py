import json
from collections.abc import Callable
from typing import Annotated, Any, Concatenate, Generic, TypeVar

import pytest

from haiway import State, Verifier
from haiway.attributes.validation import ValidationError


class Animal: ...


class Dog(Animal): ...


# PEP 696 - defaults of type parameters


class Pair[A, B = str](State):
    first: A
    second: B


def test_defaulted_parameter_may_be_left_out() -> None:
    assert Pair[int] is Pair[int, str]
    assert Pair[int].__args__ == (int, str)
    assert Pair[int](first=1, second="x").second == "x"


def test_default_is_validated_like_the_argument_it_stands_for() -> None:
    with pytest.raises(ValidationError):
        Pair[int](first=1, second=2.5)  # pyright: ignore[reportArgumentType]


def test_default_does_not_make_a_required_parameter_optional() -> None:
    with pytest.raises(TypeError, match="takes 2 type arguments"):
        Pair[()]  # pyright: ignore[reportInvalidTypeArguments]


def test_too_many_arguments_are_refused_with_defaults_declared() -> None:
    with pytest.raises(TypeError, match="takes 2 type arguments, 3 given"):
        Pair[int, str, bytes]  # pyright: ignore[reportInvalidTypeArguments]


def test_default_respects_the_bound() -> None:
    class Bounded[T: Animal = Dog](State):
        value: T

    assert Bounded[Dog].__args__ == (Dog,)

    with pytest.raises(TypeError, match="bound to"):
        Bounded[int]  # pyright: ignore[reportInvalidTypeArguments]


# PEP 646 - variadic parameters


class Row[*Ts](State):
    cells: tuple[*Ts]


def test_variadic_parameter_binds_the_arguments_in_order() -> None:
    row = Row[int, str](cells=(1, "a"))

    assert row.cells == (1, "a")
    assert Row[int, str].__args__ == (int, str)
    assert Row[int, str] is Row[int, str]
    assert Row[int, str] is not Row[str, int]


def test_variadic_parameter_validates_each_position() -> None:
    with pytest.raises(ValidationError):
        Row[int, str](cells=("a", 1))  # pyright: ignore[reportArgumentType]

    with pytest.raises(ValidationError):
        Row[int, str](cells=(1,))  # pyright: ignore[reportArgumentType]


def test_variadic_parameter_accepts_no_arguments() -> None:
    assert Row[()](cells=()).cells == ()


def test_unspecialized_variadic_class_takes_a_tuple_of_anything() -> None:
    assert Row(cells=(1, "a", None)).cells == (1, "a", None)


def test_variadic_parameter_next_to_others() -> None:
    class Mixed[Head, *Tail](State):
        head: Head
        tail: tuple[*Tail]

    value = Mixed[int, str, bytes](head=1, tail=("a", b"b"))

    assert value.tail == ("a", b"b")
    assert Mixed[int].__args__ == (int,)
    assert Mixed[int](head=1, tail=()).tail == ()

    with pytest.raises(TypeError, match="at least 1"):
        Mixed[()]  # pyright: ignore[reportInvalidTypeArguments]


def test_variadic_variants_follow_the_subclass_relation_per_position() -> None:
    assert issubclass(Row[Dog, int], Row[Animal, int])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Row[Animal, int], Row[Dog, int])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Row[Dog], Row[Animal, int])  # pyright: ignore[reportArgumentType]


def test_variadic_variant_serializes_and_reads_back() -> None:
    row = Row[int, str](cells=(1, "a"))
    encoded = row.to_json()

    assert json.loads(encoded) == {"cells": [1, "a"]}
    assert Row[int, str].from_json(encoded) == row
    assert Row[int, str].json_schema() != Row[str, int].json_schema()


# PEP 612 - parameter specifications


class Handler[**P, R](State):
    call: Callable[P, R]


def test_parameter_specification_is_the_same_variant_however_it_is_spelled() -> None:
    assert Handler[[int, str], bool] is Handler[[int, str], bool]
    assert Handler[[int], bool] is not Handler[[str], bool]
    assert Handler[..., bool] is Handler[..., bool]


def test_parameter_specification_variant_holds_a_callable() -> None:
    handler = Handler[[int], bool](call=lambda value: value > 0)

    assert handler.call(1) is True
    assert handler == Handler[[int], bool](call=handler.call)


def test_parameter_specification_refuses_what_is_not_a_signature() -> None:
    with pytest.raises(TypeError, match="ParamSpec"):
        Handler[int, bool]  # pyright: ignore[reportInvalidTypeArguments]


# declared and inferred variance

Contra_contra = TypeVar("Contra_contra", contravariant=True)
Co_co = TypeVar("Co_co", covariant=True)


# declared the old way - a variance can only be spelled out there
class Sink(State, Generic[Contra_contra]):  # noqa: UP046
    put: Callable[[Contra_contra], None]


class Source(State, Generic[Co_co]):  # noqa: UP046
    value: Co_co


def test_declared_contravariance_reverses_the_relation() -> None:
    assert issubclass(Sink[Animal], Sink[Dog])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Sink[Dog], Sink[Animal])  # pyright: ignore[reportArgumentType]


def test_declared_covariance_follows_the_arguments() -> None:
    assert issubclass(Source[Dog], Source[Animal])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Source[Animal], Source[Dog])  # pyright: ignore[reportArgumentType]


def test_inferred_variance_follows_where_the_parameter_is_used() -> None:
    class Producer[T](State):
        make: Callable[[], T]

    class Consumer[T](State):
        take: Callable[[T], None]

    class Both[T](State):
        make: Callable[[], T]
        take: Callable[[T], None]

    assert issubclass(Producer[Dog], Producer[Animal])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Producer[Animal], Producer[Dog])  # pyright: ignore[reportArgumentType]

    assert issubclass(Consumer[Animal], Consumer[Dog])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Consumer[Dog], Consumer[Animal])  # pyright: ignore[reportArgumentType]

    assert not issubclass(Both[Dog], Both[Animal])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Both[Animal], Both[Dog])  # pyright: ignore[reportArgumentType]
    assert issubclass(Both[Dog], Both[Dog])  # pyright: ignore[reportArgumentType]


def test_inferred_variance_of_a_plain_attribute_is_covariant() -> None:
    class Plain[T](State):
        value: T
        many: tuple[T, ...]

    assert issubclass(Plain[Dog], Plain[Animal])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Plain[Animal], Plain[Dog])  # pyright: ignore[reportArgumentType]


def test_variance_decides_where_a_variant_is_accepted_as_an_attribute() -> None:
    class Holder(State):
        sink: Sink[Dog]  # pyright: ignore[reportInvalidTypeArguments]

    wide: Any = Sink[Animal](put=lambda _: None)  # pyright: ignore[reportInvalidTypeArguments]
    narrow: Any = Sink[Dog](put=lambda _: None)  # pyright: ignore[reportInvalidTypeArguments]

    assert Holder(sink=wide).sink is wide

    class Strict(State):
        sink: Sink[Animal]  # pyright: ignore[reportInvalidTypeArguments]

    assert Strict(sink=wide).sink is wide

    with pytest.raises(ValidationError):
        Strict(sink=narrow)


# parameters left open


def test_open_parameters_make_an_alias_rather_than_a_variant() -> None:
    class Outer[T](State):
        inner: Row[T, int]  # pyright: ignore[reportInvalidTypeArguments]

    value = Outer[str](inner=Row[str, int](cells=("a", 1)))  # pyright: ignore[reportArgumentType]

    assert value.inner.cells == ("a", 1)


# defaults written in terms of other parameters, parameters given to nested generics


def test_default_may_refer_to_an_earlier_parameter() -> None:
    class Twin[A, B = A](State):
        first: A
        second: B

    assert Twin[int] is Twin[int, int]
    assert Twin[int](first=1, second=2).second == 2

    with pytest.raises(ValidationError):
        Twin[int](first=1, second="x")  # pyright: ignore[reportArgumentType]


def test_open_variadic_parameter_is_given_to_the_nested_generic() -> None:
    class Outer[*Ts](State):
        inner: Row[*Ts]  # pyright: ignore[reportInvalidTypeArguments]

    value = Outer[int, str](inner=Row[int, str](cells=(1, "a")))  # pyright: ignore[reportArgumentType]

    assert value.inner.cells == (1, "a")

    with pytest.raises(ValidationError):
        Outer[int, str](inner=Row[str, str](cells=("a", "b")))  # pyright: ignore[reportArgumentType]


def test_open_parameter_specification_is_given_to_the_nested_generic() -> None:
    class Outer[**P](State):
        handler: Handler[P, int]  # pyright: ignore[reportInvalidTypeArguments]

    accepted = Handler[[int], int](call=lambda value: value)
    refused = Handler[[str], int](call=lambda _: 1)

    assert Outer[[int]](handler=accepted).handler is accepted

    with pytest.raises(ValidationError):
        Outer[[int]](handler=refused)


def test_lone_parameter_specification_may_be_written_without_a_list() -> None:
    class Only[**P](State):
        call: Callable[P, None]

    assert Only[int, str] is Only[[int, str]]
    assert Only[...] is Only[...]


def test_variance_is_taken_through_a_nested_generic_state() -> None:
    class Wrapper[T](State):
        sink: Sink[T]  # pyright: ignore[reportInvalidTypeArguments]

    assert issubclass(Wrapper[Animal], Wrapper[Dog])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Wrapper[Dog], Wrapper[Animal])  # pyright: ignore[reportArgumentType]


# an unspecialized generic stands for the defaults of its parameters (PEP 696)


def test_unspecialized_generic_validates_against_the_defaults() -> None:
    class Box[T = int](State):
        value: T

    assert Box(value=1).value == 1

    with pytest.raises(ValidationError):
        Box(value="x")  # pyright: ignore[reportArgumentType]

    assert Box[str](value="x").value == "x"


def test_unspecialized_generic_without_a_default_takes_anything() -> None:
    class Open[T](State):
        value: T

    assert Open(value="x").value == "x"
    assert Open(value=1).value == 1


def test_unspecialized_generic_mixes_defaulted_and_open_parameters() -> None:
    class Mixed[A, B = str](State):
        first: A
        second: B

    assert Mixed(first=object(), second="x").second == "x"

    with pytest.raises(ValidationError):
        Mixed(first=1, second=2)  # pyright: ignore[reportArgumentType]


def test_unspecialized_default_refers_to_an_earlier_parameter() -> None:
    class Chain[A = int, B = A](State):
        first: A
        second: B

    assert Chain(first=1, second=2).second == 2

    with pytest.raises(ValidationError):
        Chain(first=1, second="x")  # pyright: ignore[reportArgumentType]


def test_unspecialized_default_applies_to_variadic_and_signature_parameters() -> None:
    class Tail[*Ts = *tuple[int, str]](State):
        cells: tuple[*Ts]

    assert Tail(cells=(1, "a")).cells == (1, "a")

    with pytest.raises(ValidationError):
        Tail(cells=("a", 1))  # pyright: ignore[reportArgumentType]


def test_unspecialized_defaulted_class_is_not_its_default_variant() -> None:
    class Box[T = int](State):
        value: T

    assert Box is not Box[int]
    assert isinstance(Box(value=1), Box)


def test_unspecialized_variadic_parameter_keeps_the_elements_around_it() -> None:
    class Framed[*Ts](State):
        cells: tuple[int, *Ts, str]

    assert Framed(cells=(1, "z")).cells == (1, "z")
    assert Framed(cells=(1, None, 2.5, "z")).cells == (1, None, 2.5, "z")

    with pytest.raises(ValidationError):
        Framed(cells=("s", "z"))  # pyright: ignore[reportArgumentType]

    with pytest.raises(ValidationError):
        Framed(cells=(1, 2))  # pyright: ignore[reportArgumentType]

    with pytest.raises(ValidationError):
        Framed(cells=(1,))  # pyright: ignore[reportArgumentType]

    assert Framed[bytes](cells=(1, b"x", "z")).cells == (1, b"x", "z")


def test_signature_parameter_of_a_callable_is_contravariant() -> None:
    class Receiver[**P](State):
        call: Callable[P, None]

    # a receiver of any animal is a receiver of a dog
    assert issubclass(Receiver[[Animal]], Receiver[[Dog]])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Receiver[[Dog]], Receiver[[Animal]])  # pyright: ignore[reportArgumentType]


def test_specialized_instance_check_trusts_the_construction() -> None:
    checks: list[Any] = []

    def verify(value: Any) -> Any:
        checks.append(value)
        return value

    class Box[T](State):
        value: Annotated[T, Verifier(verify)]

    box = Box[int](value=1)
    checks.clear()

    assert isinstance(box, Box[int])  # pyright: ignore[reportArgumentType]
    assert checks == []  # nothing is verified again - the instance can't have changed
    assert not isinstance(Box[str](value="x"), Box[int])  # pyright: ignore[reportArgumentType]


# what a type alias and a Concatenate say about a parameter

type Callback[T] = Callable[[T], None]


def test_variance_is_read_through_a_type_alias() -> None:
    class Wrapped[T](State):
        call: Callback[T]

    assert issubclass(Wrapped[Animal], Wrapped[Dog])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Wrapped[Dog], Wrapped[Animal])  # pyright: ignore[reportArgumentType]


def test_variance_is_read_through_a_concatenate() -> None:
    class Prefixed[**P](State):
        call: Callable[Concatenate[int, P], None]

    assert issubclass(Prefixed[[Animal]], Prefixed[[Dog]])  # pyright: ignore[reportArgumentType]
    assert not issubclass(Prefixed[[Dog]], Prefixed[[Animal]])  # pyright: ignore[reportArgumentType]
