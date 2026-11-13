import gc
import json
import sysconfig
from collections.abc import Callable, Mapping, Sequence, Set
from enum import IntEnum, StrEnum
from typing import Annotated, Any, Literal

from pytest import mark, param, raises

from haiway import Alias, Meta, State, ValidationError, Validator
from haiway._native import nstruct
from haiway.attributes import native

# --- literals ---------------------------------------------------------------


class Color(StrEnum):
    RED = "red"


class Level(IntEnum):
    LOW = 1


class Literals(State):
    number: Literal[0, 1] = 0
    flag: Literal[True] = True
    text: Literal["a"] = "a"
    color: Literal[Color.RED] = Color.RED
    level: Literal[Level.LOW] = Level.LOW


@mark.parametrize(
    ("name", "value"),
    [
        ("number", True),  # True == 1
        ("number", False),  # False == 0
        ("number", 1.0),
        ("flag", 1),
        ("text", Color.RED),
        ("number", Level.LOW),  # equal to 1, but not the literal
    ],
)
def test_literal_refuses_a_value_which_is_only_equal(name: str, value: Any) -> None:
    with raises(ValidationError):
        Literals(**{name: value})


def test_literal_keeps_a_value_of_its_type() -> None:
    literals = Literals(number=1, flag=True, text="a")

    assert type(literals.number) is int
    assert literals.flag is True
    assert literals.text == "a"


def test_literal_of_enum_member_reads_its_value_as_the_member() -> None:
    literals = Literals.from_json('{"color": "red", "level": 1}')

    assert literals.color is Color.RED
    assert literals.level is Level.LOW
    assert Literals.from_json(literals.to_json()) == literals


def test_literal_in_union_prefers_the_value_as_it_is() -> None:
    class Either(State):
        value: Literal[Color.RED] | str

    assert type(Either(value="red").value) is str
    assert Either(value=Color.RED).value is Color.RED


# --- unions read from json --------------------------------------------------


class Leaf(State):
    a: int


class Other(State):
    a: int
    b: int = 0


class MappingFirst(State):
    value: Mapping[str, int] | Leaf


class AnyFirst(State):
    value: Any | Leaf


class KeysFirst(State):
    value: Set[str] | Leaf


class LeafFirst(State):
    value: Leaf | Mapping[str, int]


class ScalarsFirst(State):
    value: int | str | Sequence[int] | Other | Leaf | None


@mark.parametrize("cls", [MappingFirst, AnyFirst, KeysFirst, LeafFirst, ScalarsFirst])
@mark.parametrize(
    "document",
    ['{"value": {"a": 1}}', '{"value": {"a": 1, "b": 2}}', '{"value": {}}'],
)
def test_json_union_reads_what_validating_the_mapping_reads(
    cls: type[State],
    document: str,
) -> None:
    try:
        expected: Any = cls.from_mapping(json.loads(document))

    except ValidationError:
        with raises(ValueError):
            cls.from_json(document)

        return

    decoded: Any = cls.from_json(document)
    assert decoded == expected
    assert type(decoded.value) is type(expected.value)


def test_json_union_with_a_repeated_key_reads_the_last_one() -> None:
    class First(State):
        x: Leaf

    class Second(State):
        x: Mapping[str, int]

    class Either(State):
        value: First | Second

    # json.loads keeps the last `a`, which the first alternative accepts - the repeated
    # key must not make the first alternative look like one the object does not fit
    document = '{"value": {"x": {"a": 1, "a": 2}}}'

    assert Either.from_json(document) == Either.from_mapping(json.loads(document))
    assert Either.from_json(document).value == First(x=Leaf(a=2))


# --- garbage collection -----------------------------------------------------


class Described(State):
    name: str
    meta: Meta = Meta({})


_FREE_THREADED: bool = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


@mark.parametrize(
    "make",
    [
        param(
            lambda: Described(name="a"),
            id="default",
            # without the GIL only a container nothing else holds is untracked - and the
            # class holds the default
            marks=mark.skipif(_FREE_THREADED, reason="the class holds the default"),
        ),
        param(
            lambda: Described(name="a", meta={"key": "value", "nested": {"a": [1, 2]}}),
            id="validated",
        ),
        param(
            lambda: Described.from_json('{"name": "a", "meta": {"key": "value"}}'),
            id="from_json",
        ),
    ],
)
def test_state_holding_meta_is_not_tracked(make: Callable[[], Described]) -> None:
    assert not gc.is_tracked(make())


# --- descriptions -----------------------------------------------------------

_ANY: Any = (native.Kind.ANY, None, None, None, None, None, ())


@mark.parametrize(
    "description",
    [
        (native.Kind.LITERAL, None, None, None, None, None, ()),  # no values
        (native.Kind.LITERAL, None, None, None, None, [1], ()),
        (native.Kind.STATE, None, int, None, None, None, ()),  # not a State
        (native.Kind.STATE, None, None, None, None, None, ()),
        (native.Kind.CUSTOM, None, None, None, None, None, ()),
        (native.Kind.STR_ENUM, None, None, None, None, None, ()),
        (native.Kind.SEQUENCE, None, None, None, None, None, ()),  # no element
        (native.Kind.MAPPING, None, None, None, None, None, (_ANY,)),
        (native.Kind.TYPED_DICT, None, dict, None, None, (("a", True),), ()),  # no spec of a key
        (native.Kind.TYPED_DICT, None, dict, None, None, None, ()),
        (native.Kind.TYPED_DICT, None, dict, None, None, ((1, True),), (_ANY,)),
        (native.Kind.INT, None, None, None, None, None, (_ANY,)),
    ],
)
def test_malformed_description_is_refused(description: Any) -> None:
    with raises(TypeError):
        nstruct.compile_spec(description)


# --- instances from dictionaries --------------------------------------------


class Aliased(State):
    first: int
    second: Annotated[str, Alias("other")] = "b"


def test_mapping_is_read_before_any_value_is_validated() -> None:
    source: dict[str, Any] = {"first": 1, "second": 2}

    def tamper(value: Any) -> Any:
        source["second"] = "tampered"
        return value

    class Tampered(State):
        first: Annotated[int, Validator(tamper)]
        second: int

    assert Tampered.from_mapping(source) == Tampered(first=1, second=2)


def test_mapping_reports_unknown_key_before_validating() -> None:
    with raises(TypeError, match="Unexpected attribute 'unknown'"):
        Aliased.from_mapping({"first": "not a number", "unknown": 1})


def test_mapping_refuses_attribute_given_by_name_and_alias() -> None:
    with raises(TypeError, match="Duplicate attribute"):
        Aliased.from_mapping({"first": 1, "second": "a", "other": "b"})


def test_mapping_with_a_key_which_is_not_a_str_is_refused() -> None:
    with raises(TypeError, match="Unexpected attribute"):
        Aliased.from_mapping({"first": 1, 2: "b"})  # pyright: ignore[reportArgumentType]


def test_mapping_keys_in_any_order_and_by_alias() -> None:
    assert Aliased.from_mapping({"other": "x", "first": 1}) == Aliased(first=1, second="x")
    assert Aliased.from_mapping({"first": 1}) == Aliased(first=1)


def test_mapping_of_a_dict_subclass_is_read() -> None:
    class Entries(dict[str, Any]):
        pass

    assert Aliased.from_mapping(Entries(first=1, other="x")) == Aliased(first=1, second="x")


_WIDE: int = 70  # more attributes than are gathered on the stack
Wide: Any = type(State)(
    "Wide",
    (State,),
    {"__annotations__": {f"a{index}": int for index in range(_WIDE)}, "__module__": __name__},
)


def test_wide_mapping_is_read() -> None:
    values: dict[str, Any] = {f"a{index}": index for index in range(_WIDE)}

    assert Wide.from_mapping(values) == Wide(**values)
    assert Wide.from_mapping(dict(reversed(values.items()))) == Wide(**values)


@mark.parametrize(
    ("entries", "error"),
    [
        ({"unknown": 1}, TypeError),
        ({"a0": "not a number"}, ValidationError),
        ({1: 1}, TypeError),
    ],
)
def test_wide_mapping_is_refused(entries: dict[Any, Any], error: type[Exception]) -> None:
    values: dict[Any, Any] = {f"a{index}": index for index in range(_WIDE)}

    with raises(error):
        Wide.from_mapping({**values, **entries})
