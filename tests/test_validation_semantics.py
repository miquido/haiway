import json
import math
import re
from array import array
from collections import UserDict, UserList
from collections.abc import Callable, Mapping, Sequence, Set
from datetime import date, datetime
from enum import IntEnum, StrEnum
from types import MappingProxyType, new_class
from typing import Any, Literal
from unittest import mock

import pytest

from haiway import Default, DefaultFactory, Map, State, ValidationError
from haiway._native import nstruct

# What validation accepts and refuses for the plain types, the keys of a mapping and the
# values of the enums, sets and dates - and what the schema says about it.


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class Mode(StrEnum):
    FAST = "fast"
    SLOW = "slow"


class Crossed(StrEnum):
    X = "Y"
    Y = "X"


ARABIC_ONE = chr(0x661)
ARABIC_FLOAT = chr(0x661) + "." + chr(0x665)


class Early(date):
    pass


def holder(annotation: Any, /) -> Any:
    def body(namespace: dict[str, Any]) -> None:
        namespace["__module__"] = __name__
        namespace["__annotations__"] = {"v": annotation}

    return new_class("Holder", (State,), {}, body)


AnyHolder: Any = holder(Any)


def validated(annotation: Any, value: Any, /) -> Any:
    return holder(annotation).validate({"v": value}).v


def from_json_value(annotation: Any, text: str, /) -> Any:
    return holder(annotation).from_json(f'{{"v": {text}}}').v


def checked(annotation: Any, value: Any, /) -> bool:
    return nstruct.check(holder(annotation).__FIELDS__[0].spec, value)


def decoding_fails(annotation: Any, text: str, /) -> bool:
    try:
        holder(annotation).from_json(text)

    except ValueError:
        return True

    return False


def refused(annotation: Any, value: Any, /) -> bool:
    try:
        validated(annotation, value)

    except ValidationError, TypeError, ValueError:
        return True

    return False


def property_of(annotation: Any, /) -> Any:
    return holder(annotation).__SPECIFICATION__["properties"]["v"]


def key_pattern(annotation: Any, /) -> str:
    return property_of(Mapping[annotation, int])["propertyNames"]["pattern"]


# A. bool


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        (1, True),
        (0, False),
        ("true", True),
        ("TRUE", True),
        ("True", True),
        ("false", False),
        ("FaLsE", False),
    ],
)
def test_bool_accepts_booleans_zero_and_one_and_the_words(value: Any, expected: bool) -> None:
    assert validated(bool, value) is expected
    assert from_json_value(bool, json.dumps(value)) is expected


@pytest.mark.parametrize(
    "value",
    [2, -1, 2**70, "1", "0", "yes", "", " true", "true ", 1.0, 0.0, None, Level.LOW, b"true"],
)
def test_bool_refuses_everything_else(value: Any) -> None:
    assert refused(bool, value)


def test_bool_check_accepts_booleans_only() -> None:
    assert checked(bool, True)
    assert not checked(bool, 1)
    assert not checked(bool, "true")


@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
    [
        (bool | int, 1, 1),
        (int | bool, 1, 1),
        (bool | int, True, True),
        (int | bool, True, True),
        (bool | int, "true", True),
        (bool | Level, Level.LOW, Level.LOW),
        (bool | Level, 2, Level.HIGH),
        (bool | Level, 1, True),
        (Level | bool, 1, Level.LOW),
    ],
)
def test_bool_unions_prefer_the_alternative_a_value_matches_then_the_declared_order(
    annotation: Any, value: Any, expected: Any
) -> None:
    result: Any = validated(annotation, value)
    assert result == expected
    assert type(result) is type(expected)


def test_bool_schema_accepts_what_validation_accepts() -> None:
    assert property_of(bool) == {
        "anyOf": [
            {"type": "boolean"},
            {"enum": [0, 1]},
            {"type": "string", "pattern": "^([Tt][Rr][Uu][Ee]|[Ff][Aa][Ll][Ss][Ee])$"},
        ]
    }


# B. int and float


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1, 1),
        (-7, -7),
        (2**70, 2**70),
        (Level.HIGH, 2),
        (1.0, 1),
        (-3.0, -3),
        (float(2**53), 2**53),
        (-float(2**53), -(2**53)),
        (0.0, 0),
    ],
)
def test_int_accepts_ints_and_integral_floats(value: Any, expected: int) -> None:
    result: Any = validated(int, value)
    assert result == expected
    assert isinstance(result, int)
    assert not isinstance(result, float)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        "1",
        "x",
        "",
        " 1",
        "1.0",
        1.5,
        math.nan,
        math.inf,
        -math.inf,
        2.0**54,
        1e300,
        None,
    ],
)
def test_int_refuses_everything_else(value: Any) -> None:
    assert refused(int, value)


@pytest.mark.parametrize(("value", "expected"), [(1, 1.0), (2**70, float(2**70)), (1.5, 1.5)])
def test_float_accepts_ints_and_floats(value: Any, expected: float) -> None:
    result: Any = validated(float, value)
    assert result == expected
    assert type(result) is float


@pytest.mark.parametrize("value", [True, "nan", "inf", "1_0.0", "1.5", "", None])
def test_float_refuses_everything_else(value: Any) -> None:
    assert refused(float, value)


@pytest.mark.parametrize("annotation", [int, float])
def test_json_numbers_in_strings_are_not_read(annotation: Any) -> None:
    assert decoding_fails(annotation, '{"v": "1"}')


def test_int_union_with_none_refuses_text() -> None:
    assert refused(int | None, "3")


@pytest.mark.parametrize("key", ["0", "1", "-1", "10", "-250", "9" * 30], ids=repr)
def test_int_keys_read_canonical_integers(key: str) -> None:
    assert holder(Mapping[int, str]).from_json(f'{{"v": {{"{key}": "a"}}}}').v == {int(key): "a"}
    assert holder(Mapping[int, str]).validate({"v": {key: "a"}}).v == {int(key): "a"}


@pytest.mark.parametrize(
    "key",
    ["01", "+1", " 1", "1 ", "1_0", "-0", "1.0", "", "-", "--1", ARABIC_ONE, "1e3", "x", "0x1"],
    ids=repr,
)
def test_int_keys_refuse_every_other_spelling(key: str) -> None:
    assert decoding_fails(Mapping[int, str], json.dumps({"v": {key: "a"}}))
    with pytest.raises(ValidationError):
        holder(Mapping[int, str]).validate({"v": {key: "a"}})


@pytest.mark.parametrize(
    "key", ["0", "1", "-1", "1.5", "-1e5", "1E+5", "0.5", "2.5e-3", "1e999"], ids=repr
)
def test_float_keys_read_json_numbers(key: str) -> None:
    assert holder(Mapping[float, str]).from_json(f'{{"v": {{"{key}": "a"}}}}').v == {
        float(key): "a"
    }


@pytest.mark.parametrize(
    "key",
    [
        "nan",
        "inf",
        "-inf",
        "NaN",
        "Infinity",
        "+1",
        ".5",
        "1.",
        "01",
        " 1",
        "1_0",
        ARABIC_FLOAT,
        "0x1",
        "",
        "-",
    ],
    ids=repr,
)
def test_float_keys_refuse_every_other_spelling(key: str) -> None:
    assert decoding_fails(Mapping[float, str], json.dumps({"v": {key: "a"}}))


@pytest.mark.parametrize(("key", "expected"), [("true", True), ("false", False)])
def test_bool_keys_read_the_lowercase_words(key: str, expected: bool) -> None:
    assert holder(Mapping[bool, int]).from_json(f'{{"v": {{"{key}": 1}}}}').v == {expected: 1}


@pytest.mark.parametrize("key", ["True", "FALSE", "1", "0", "yes", ""], ids=repr)
def test_bool_keys_refuse_everything_else(key: str) -> None:
    assert decoding_fails(Mapping[bool, int], json.dumps({"v": {key: 1}}))


def test_none_keys_round_trip() -> None:
    cls: Any = holder(Mapping[None, int])
    instance: Any = cls(v={None: 1})
    assert json.loads(instance.to_json()) == {"v": {"null": 1}}
    assert cls.from_json(instance.to_json()) == instance
    assert instance.to_basic_object()["v"] == {"null": 1}
    assert decoding_fails(Mapping[None, int], '{"v": {"None": 1}}')


KEYS: Mapping[str, tuple[Any, Any]] = {
    "literal-int": (Mapping[Literal["a", 1], int], ("a", 1)),
    "literal-bool": (Mapping[Literal["a", True], int], ("a", True)),
    "literal-none": (Mapping[Literal["a", None], int], ("a", None)),
    "int-enum": (Mapping[Level, int], (Level.LOW, Level.HIGH)),
    "bool": (Mapping[bool, int], (True, False)),
    "float": (Mapping[float, int], (1.5, -2.0, 1e16)),
    "union": (Mapping[int | None, int], (3, None)),
    "int": (Mapping[int, int], (3, -4)),
}


@pytest.mark.parametrize(("annotation", "keys"), KEYS.values(), ids=KEYS.keys())
def test_keys_read_back_as_they_are_written(annotation: Any, keys: Sequence[Any]) -> None:
    cls: Any = holder(annotation)
    instance: Any = cls(v=dict.fromkeys(keys, 1))
    for text in (instance.to_json(), json.dumps(instance.to_basic_object())):
        restored: Any = cls.from_json(text)
        assert restored == instance
        assert [type(key) for key in restored.v] == [type(key) for key in instance.v]

    assert cls.from_mapping(instance.to_basic_object()) == instance


def test_enum_keys_read_names_and_values() -> None:
    assert holder(Mapping[Level, int]).from_json('{"v": {"1": 1, "HIGH": 2}}').v == {
        Level.LOW: 1,
        Level.HIGH: 2,
    }
    assert holder(Mapping[Mode, int]).from_json('{"v": {"fast": 1, "SLOW": 2}}').v == {
        Mode.FAST: 1,
        Mode.SLOW: 2,
    }


def test_text_keys_stay_text_in_a_union() -> None:
    assert holder(Mapping[int | str, int]).from_json('{"v": {"1": 1}}').v == {"1": 1}


def test_key_patterns_are_the_spellings_read() -> None:
    integer: str = key_pattern(int)
    number: str = key_pattern(float)
    for key in ("0", "-12", "7"):
        assert re.search(integer, key)
    for key in ("01", "+1", " 1", "1_0", "-0", "", ARABIC_ONE, "1.0"):
        assert not re.search(integer, key)
    for key in ("0", "-1.5", "1e5", "2.5E-3", "10"):
        assert re.search(number, key)
    for key in ("nan", "inf", "+1", ".5", "1.", "01", " 1", ""):
        assert not re.search(number, key)
    assert property_of(Mapping[bool, int])["propertyNames"] == {
        "type": "string",
        "pattern": "^(true|false)$",
    }
    assert property_of(Mapping[None, int])["propertyNames"] == {
        "type": "string",
        "pattern": "^null$",
    }


def test_literal_and_enum_key_schemas_list_every_spelling() -> None:
    assert property_of(Mapping[Literal["a", 1, True, None], int])["propertyNames"] == {
        "type": "string",
        "enum": ["a", "1", "true", "null"],
    }
    assert property_of(Mapping[Level, int])["propertyNames"] == {
        "type": "string",
        "enum": ["1", "2", "LOW", "HIGH"],
    }


# C. enums


@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
    [
        (Mode, Mode.FAST, Mode.FAST),
        (Mode, "fast", Mode.FAST),
        (Mode, "SLOW", Mode.SLOW),
        (Level, Level.HIGH, Level.HIGH),
        (Level, 2, Level.HIGH),
        (Level, "LOW", Level.LOW),
        (Crossed, "Y", Crossed.X),
        (Crossed, "X", Crossed.Y),
    ],
)
def test_enums_read_members_then_values_then_names(
    annotation: Any, value: Any, expected: Any
) -> None:
    assert validated(annotation, value) is expected


@pytest.mark.parametrize(
    ("annotation", "value"),
    [
        (Level, True),
        (Level, False),
        (Level, "1"),
        (Level, "2"),
        (Level, 3),
        (Level, 1.0),
        (Level, "low"),
        (Mode, 1),
        (Mode, "other"),
    ],
)
def test_enums_refuse_the_rest(annotation: Any, value: Any) -> None:
    assert refused(annotation, value)
    assert not checked(annotation, value)


def test_enum_schemas_hold_values_and_names() -> None:
    assert property_of(Mode) == {"type": "string", "enum": ["fast", "slow", "FAST", "SLOW"]}
    assert property_of(Level) == {
        "anyOf": [
            {"type": "integer", "enum": [1, 2]},
            {"type": "string", "enum": ["LOW", "HIGH"]},
        ]
    }


# D. sets


@pytest.mark.parametrize(
    "value",
    [{1, 2}, frozenset({1, 2}), [1, 2, 2], (1, 2), range(1, 3)],
    ids=lambda value: type(value).__name__,
)
def test_set_accepts_sets_and_sequences(value: Any) -> None:
    assert validated(Set[int], value) == frozenset({1, 2})


@pytest.mark.parametrize(
    "value",
    [
        {"a": 1},
        Map({"a": 1}),
        {"a": 1}.keys(),
        {"a": 1}.values(),
        {"a": 1}.items(),
        (x for x in (1, 2)),
        iter([1, 2]),
        "ab",
        b"ab",
        bytearray(b"ab"),
        1,
    ],
    ids=lambda value: type(value).__name__,
)
def test_set_refuses_everything_else(value: Any) -> None:
    with pytest.raises(ValidationError, match="expected type of 'Set'"):
        holder(Set[Any])(v=value)

    assert not checked(Set[Any], value)


def test_set_check_agrees_for_the_sets_accepted() -> None:
    assert checked(Set[int], {1})
    assert checked(Set[int], frozenset({1}))
    assert not checked(Set[int], [1])


# E. date


def test_date_refuses_a_datetime() -> None:
    with pytest.raises(ValidationError, match="is not matching expected type of 'date'"):
        holder(date)(v=datetime(2026, 1, 2, 3))

    assert not checked(date, datetime(2026, 1, 2))


@pytest.mark.parametrize("value", [date(2026, 1, 2), Early(2026, 1, 2), "2026-01-02"])
def test_date_accepts_dates_and_their_text(value: Any) -> None:
    assert validated(date, value) == date(2026, 1, 2)
    assert checked(date, date(2026, 1, 2))


@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
    [
        (datetime | date, date(2026, 1, 2), date(2026, 1, 2)),
        (datetime | date, datetime(2026, 1, 2, 3), datetime(2026, 1, 2, 3)),
        (date | datetime, date(2026, 1, 2), date(2026, 1, 2)),
        (date | datetime, datetime(2026, 1, 2, 3), datetime(2026, 1, 2, 3)),
        (date | datetime, "2026-01-02", date(2026, 1, 2)),
        (date | datetime, "2026-01-02T03:04:05", datetime(2026, 1, 2, 3, 4, 5)),
        (datetime | date, "2026-01-02T03:04:05", datetime(2026, 1, 2, 3, 4, 5)),
        (datetime | date, "2026-01-02", datetime(2026, 1, 2)),
    ],
)
def test_date_unions_keep_a_datetime_a_datetime(annotation: Any, value: Any, expected: Any) -> None:
    result: Any = validated(annotation, value)
    assert result == expected
    assert type(result) is type(expected)


# F. converted keys which collide


@pytest.mark.parametrize(
    ("annotation", "value"),
    [
        (Mapping[int, str], {"1": "a", 1: "b"}),
        (Mapping[int, str], {1: "a", "1": "b"}),
        (Mapping[float, str], {"1": "a", 1.0: "b"}),
        (Mapping[bool, str], {"true": "a", True: "b"}),
        (Mapping[Level, str], {"1": "a", Level.LOW: "b"}),
        (Mapping[Level, str], {"LOW": "a", 1: "b"}),
    ],
)
def test_keys_which_collide_after_conversion_are_refused(annotation: Any, value: Any) -> None:
    with pytest.raises(ValidationError, match="already"):
        holder(annotation).validate({"v": value})


def test_json_keys_which_collide_after_conversion_are_refused() -> None:
    with pytest.raises(ValueError, match="already"):
        holder(Mapping[float, str]).from_json('{"v": {"1": "a", "1.0": "b"}}')


def test_json_key_repeated_as_written_keeps_the_last() -> None:
    assert holder(Mapping[int, str]).from_json('{"v": {"1": "a", "1": "b"}}').v == {1: "b"}


# G. to_json and to_basic_object with keys which collide once spelled


COLLIDING: Sequence[Any] = (
    {1: "a", "1": "b"},
    {"1": "a", 1: "b"},
    {True: "a", "true": "b"},
    {None: "a", "null": "b"},
    {1.5: "a", "1.5": "b"},
    {Level.LOW: "a", "1": "b"},
    Map({1: "a", "1": "b"}),
)


@pytest.mark.parametrize("value", COLLIDING, ids=repr)
def test_to_json_refuses_keys_which_collide_once_spelled(value: Any) -> None:
    instance: Any = AnyHolder(v=value)
    with pytest.raises(ValueError):
        instance.to_json()

    with pytest.raises(ValueError):
        instance.to_json(indent=2)

    with pytest.raises(ValueError):
        instance.to_basic_object()


def test_to_json_writes_keys_which_do_not_collide() -> None:
    instance: Any = AnyHolder(v={1: "a", "2": "b", None: "d"})
    assert json.loads(instance.to_json()) == {"v": {"1": "a", "2": "b", "null": "d"}}


# H. NaN and the infinities of a document


@pytest.mark.parametrize(
    ("text", "shared"),
    [
        ("NaN", "NaN"),
        ("Infinity", "PosInf"),
        ("-Infinity", "NegInf"),
    ],
)
def test_json_constants_are_the_objects_of_the_standard_library(text: str, shared: str) -> None:
    values: Any = from_json_value(Sequence[float], f"[{text}, {text}]")
    assert values[0] is values[1]
    assert values[0] is getattr(json.decoder, shared)
    assert values[0] is json.loads(text)


@pytest.mark.parametrize("text", ["NaN", "Infinity", "-Infinity"])
def test_set_of_floats_reads_a_repeated_constant_once(text: str) -> None:
    assert len(from_json_value(Set[float], f"[{text}, {text}]")) == 1


# I. the hash of an instance agrees with its equality


HASHED: Sequence[tuple[Any, Any]] = (
    (Map({"a": 1}), {"a": 1}),
    (frozenset({1, 2}), {1, 2}),
    (b"ab", bytearray(b"ab")),
    (b"ab", memoryview(bytearray(b"ab"))),
    ((Map({"a": 1}),), ({"a": 1},)),
    ((frozenset({1}),), ({1},)),
    ((b"a",), (bytearray(b"a"),)),
    (Map({"a": Map({"b": 1})}), {"a": {"b": 1}}),
    (Map({"a": ({"b": 1},)}), {"a": (Map({"b": 1}),)}),
    (Map({"a": frozenset({1})}), {"a": {1}}),
    ({"a": [1, {"b": 2}]}, Map({"a": [1, Map({"b": 2})]})),
    ([Map({"a": 1})], [{"a": 1}]),
    ([frozenset({1})], [{1}]),
    ([1, 2], [1, 2]),
    (1, 1.0),
    ("s", "s"),
)


@pytest.mark.parametrize(("first", "second"), HASHED, ids=repr)
def test_equal_instances_agree_on_their_hash(first: Any, second: Any) -> None:
    left: Any = AnyHolder(v=first)
    right: Any = AnyHolder(v=second)
    assert left == right
    assert hash(left) == hash(right)


def test_equal_instances_of_a_typed_attribute_agree_on_their_hash() -> None:
    cls: Any = holder(Mapping[str, int])
    assert cls(v=Map({"a": 1})) == cls(v={"a": 1})
    assert hash(cls(v=Map({"a": 1}))) == hash(cls(v={"a": 1}))


def test_instances_which_differ_mostly_differ_in_their_hash() -> None:
    cls: Any = AnyHolder
    assert len({hash(cls(v={"a": index})) for index in range(50)}) > 45


def test_values_which_can_not_be_hashed_are_left_out() -> None:
    class Unhashable:
        __hash__ = None  # pyright: ignore[reportAssignmentType]

        def __eq__(self, other: object) -> bool:
            return isinstance(other, Unhashable)

    cls: Any = AnyHolder
    assert hash(cls(v=Unhashable())) == hash(cls(v=Unhashable()))
    assert hash(cls(v=(Unhashable(),))) == hash(cls(v=(Unhashable(),)))


class Uninitialized(State):
    x: Any


def test_hash_raises_for_a_value_which_is_not_initialized() -> None:
    cls: Any = AnyHolder
    broken: Any = Uninitialized.__new__(Uninitialized)

    class Raising:
        def __hash__(self) -> int:
            return hash(broken)

    for value in (
        broken,
        (broken,),
        [broken],
        {"a": broken},
        Map({"a": broken}),
        Raising(),
        (Raising(),),
    ):
        with pytest.raises(TypeError, match="not initialized"):
            hash(cls(v=value))


# J. equality leaves the comparison to the other operand


def test_equality_with_a_foreign_operand_is_reflected() -> None:
    instance: Any = AnyHolder(v=1)
    assert instance == mock.ANY
    assert mock.ANY == instance
    assert not instance != mock.ANY
    assert instance.__eq__(1) is NotImplemented
    assert instance.__ne__(1) is NotImplemented
    assert instance != 1
    assert not instance == 1


def test_equality_of_different_states_is_false() -> None:
    first: Any = AnyHolder(v=1)

    class Other(State):
        v: Any

    assert first != Other(v=1)
    assert not first == Other(v=1)
    assert first.__eq__(Other(v=1)) is False


# K. mutable defaults


@pytest.mark.parametrize(
    "default",
    [
        [],
        {},
        set(),
        bytearray(),
        [1],
        {"a": 1},
        {1},
        type("L", (list,), {})(),
        type("D", (dict,), {})(),
    ],
    ids=lambda value: type(value).__name__,
)
def test_mutable_plain_defaults_are_refused(default: Any) -> None:
    def body(namespace: dict[str, Any]) -> None:
        namespace["__annotations__"] = {"x": Any}
        namespace["x"] = default

    with pytest.raises(TypeError, match="DefaultFactory"):
        new_class("Mutable", (State,), {}, body)


def test_default_holding_a_mutable_value_is_refused() -> None:
    def body(namespace: dict[str, Any]) -> None:
        namespace["__annotations__"] = {"x": Any}
        namespace["x"] = Default([])

    with pytest.raises(TypeError, match="DefaultFactory"):
        new_class("Mutable", (State,), {}, body)


def test_immutable_defaults_and_factories_are_kept() -> None:
    class Defaults(State):
        a: Any = ()
        b: Any = frozenset()
        c: Any = Map({"a": 1})
        d: Any = "text"
        e: Any = 1
        f: Any = Level.LOW
        g: Any = DefaultFactory(list)

    first: Defaults = Defaults()
    second: Defaults = Defaults()
    assert first.g == []
    assert first.g is not second.g
    assert first.a == ()


# L. ParamSpec and constrained type parameters


class Handler[**P, R](State):
    call: Callable[P, R]


class Constrained[T: (int, str)](State):
    item: T


def test_specialized_param_spec_is_enforced_like_a_callable() -> None:
    assert Handler[[int], bool](call=lambda value: True).call(1) is True
    with pytest.raises(ValidationError):
        Handler[[int], bool](call=lambda: True)

    with pytest.raises(ValidationError):
        Handler[[int], bool](call=lambda first, second: True)

    assert Handler[[], bool](call=lambda: True).call() is True
    with pytest.raises(ValidationError):
        Handler[[], bool](call=lambda value: True)

    assert Handler[..., bool](call=lambda: True).call() is True
    assert Handler(call=lambda: True).call() is True


def test_unspecialized_constraints_accept_any_of_them() -> None:
    assert Constrained(item=1).item == 1
    assert Constrained(item="a").item == "a"
    with pytest.raises(ValidationError):
        Constrained(item=1.5)

    with pytest.raises(ValidationError):
        Constrained(item=None)

    assert Constrained[int](item=1).item == 1
    with pytest.raises(ValidationError):
        Constrained[int](item="a")


def test_unspecialized_constraints_are_required() -> None:
    assert Constrained.__SPECIFICATION__["required"] == ["item"]
    with pytest.raises(ValidationError):
        Constrained()


# the paths of the extension which the cases above do not meet


def test_int_key_too_long_to_convert_is_refused() -> None:
    assert decoding_fails(Mapping[int, str], json.dumps({"v": {"9" * 5000: "a"}}))
    with pytest.raises(ValidationError):
        holder(Mapping[int, str]).validate({"v": {"9" * 5000: "a"}})


@pytest.mark.parametrize("key", ["9", "01", "-1", "x", ""], ids=repr)
def test_enum_keys_refuse_what_is_neither_value_nor_name(key: str) -> None:
    assert decoding_fails(Mapping[Level, int], json.dumps({"v": {key: 1}}))


def test_literal_keys_refuse_what_no_literal_spells() -> None:
    assert decoding_fails(Mapping[Literal["a", 1, False], int], '{"v": {"2": 1}}')
    assert decoding_fails(Mapping[Literal["a", 1, False], int], '{"v": {"true": 1}}')
    assert holder(Mapping[Literal["a", 1, False], int]).from_json('{"v": {"false": 1}}').v == {
        False: 1
    }
    assert holder(Mapping[Literal["a", Level.HIGH], int]).from_json('{"v": {"2": 1}}').v == {
        Level.HIGH: 1
    }


def test_hash_of_a_released_memoryview_fails() -> None:
    view: memoryview = memoryview(bytearray(b"ab"))
    instance: Any = AnyHolder(v=view)
    view.release()
    with pytest.raises(ValueError):
        hash(instance)


def test_hash_of_containers_of_subclasses_agrees_with_their_base() -> None:
    class Dict(dict[str, int]):
        pass

    class List(list[int]):
        pass

    class Set(set[int]):
        pass

    assert hash(AnyHolder(v=Dict(a=1))) == hash(AnyHolder(v={"a": 1}))
    assert hash(AnyHolder(v=List([1]))) == hash(AnyHolder(v=[1]))
    assert hash(AnyHolder(v=Set([1]))) == hash(AnyHolder(v=frozenset({1})))


def test_hash_of_a_container_holding_itself_is_refused() -> None:
    cycle: list[Any] = []
    cycle.append(cycle)
    with pytest.raises(RecursionError):
        hash(AnyHolder(v=cycle))


# review follow-ups


Quirky: Any = IntEnum("Quirky", [("1", 2), ("2", 1)])
QuirkyText: Any = StrEnum("QuirkyText", [("a", "b"), ("b", "a")])


def test_enum_keys_are_read_as_member_then_value_then_name() -> None:
    cls: Any = holder(Mapping[Quirky, int])
    instance: Any = cls(v={Quirky(1): 0, Quirky(2): 1})
    assert json.loads(instance.to_json()) == {"v": {"1": 0, "2": 1}}
    assert cls.from_json(instance.to_json()) == instance
    assert cls.from_mapping(instance.to_basic_object()) == instance
    assert cls.from_json('{"v": {"1": 5}}').v == {Quirky(1): 5}


def test_enum_values_are_read_before_names() -> None:
    assert validated(Quirky, 1) is Quirky(1)
    assert validated(QuirkyText, "a") is QuirkyText("a")
    assert validated(QuirkyText, "b") is QuirkyText("b")
    cls: Any = holder(Mapping[QuirkyText, int])
    assert cls.from_json('{"v": {"a": 1}}').v == {QuirkyText("a"): 1}


def test_float_refuses_an_int_too_large_for_it() -> None:
    with pytest.raises(ValidationError, match="too large"):
        holder(float)(v=10**400)

    assert refused(float | None, 10**400)
    assert refused(Sequence[float], [10**400])
    assert decoding_fails(float, '{"v": 1' + "0" * 400 + "}")


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (UserDict(a=1), {"a": 1}),
        (UserDict(a=1), Map({"a": 1})),
        (UserList([1]), [1]),
        ((UserList([1]),), ([1],)),
        ((UserDict(a=1),), ({"a": 1},)),
        (MappingProxyType({"a": 1}), {"a": 1}),
        (MappingProxyType({"a": [1]}), {"a": [1]}),
        ((MappingProxyType({"a": 1}),), ({"a": 1},)),
        ({"a": UserList([1])}, {"a": [1]}),
        ({"a": 1}.keys(), {"a"}),
        ({"a": 1}.keys(), frozenset({"a"})),
        ({"a": 1}.items(), {("a", 1)}),
        (memoryview(array("i", [1])), memoryview(array("b", [1]))),
        (memoryview(b"a"), b"a"),
        (memoryview(b"a"), bytearray(b"a")),
        (memoryview(array("d", [1.0])), memoryview(array("B", [1]))),
        (memoryview(array("i", [1000])), memoryview(array("h", [1000]))),
        ((memoryview(b"a"),), (b"a",)),
    ],
    ids=repr,
)
def test_hash_agrees_with_equality_for_every_container_which_compares_equal(
    first: Any, second: Any
) -> None:
    assert first == second
    assert hash(AnyHolder(v=first)) == hash(AnyHolder(v=second))


def test_hash_of_a_mutated_container_changes() -> None:
    values: list[int] = [1]
    instance: Any = AnyHolder(v=values)
    before: int = hash(instance)
    values.append(2)
    assert hash(instance) != before


def test_hash_raises_for_a_released_memoryview_inside_a_container() -> None:
    view: memoryview = memoryview(bytearray(b"ab"))
    instance: Any = AnyHolder(v=(view,))
    view.release()
    with pytest.raises(ValueError):
        hash(instance)


def test_check_refuses_what_validation_refuses() -> None:
    assert not checked(Set[int], {"a": 1}.keys())
    assert not checked(date, datetime(2026, 1, 2))
    assert not checked(date | None, datetime(2026, 1, 2))
