import json
from collections.abc import Callable, Mapping, Sequence, Set
from datetime import date, datetime, time, timedelta
from enum import IntEnum, StrEnum
from pathlib import Path
from types import new_class
from typing import (
    Annotated,
    Any,
    Literal,
    NotRequired,
    Protocol,
    TypedDict,
    runtime_checkable,
)
from uuid import UUID

from pytest import mark, param, raises

from haiway import (
    Alias,
    Description,
    Meta,
    Missing,
    Sensitive,
    Specification,
    State,
    ValidationError,
    Validator,
    Verifier,
)


class Mode(StrEnum):
    FAST = "fast"
    SLOW = "slow"


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class Payload(TypedDict):
    name: str
    count: NotRequired[int]


@runtime_checkable
class Runner(Protocol):
    def run(self) -> int: ...


class Impl:
    def run(self) -> int:
        return 1


class Box[T](State):
    value: T


type Numbers = Sequence[int]
type Tree = int | Sequence[Tree]

IMPL = Impl()


def make_state(
    annotation: Any,
    /,
    **defaults: Any,
) -> type[State]:
    def populate(namespace: dict[str, Any]) -> None:
        namespace["__annotate_func__"] = lambda format, /: {  # noqa: A006
            "value": annotation
        }
        namespace.update(defaults)

    return new_class("Dynamic", (State,), {}, populate)


# (annotation, accepted raw value, expected validated value)
ACCEPTED: Sequence[Any] = (
    param(bool, True, True, id="bool"),
    param(bool, 5, True, id="bool-from-int"),
    param(bool, 0, False, id="bool-from-zero"),
    param(bool, "TRUE", True, id="bool-from-str"),
    param(bool, "false", False, id="bool-from-false-str"),
    param(int, 3, 3, id="int"),
    param(int, "5", 5, id="int-from-str"),
    param(int, 2.0, 2, id="int-from-float"),
    param(float, 1.5, 1.5, id="float"),
    param(float, 2, 2.0, id="float-from-int"),
    param(float, "1.5", 1.5, id="float-from-str"),
    param(str, "text", "text", id="str"),
    param(bytes, b"hi", b"hi", id="bytes"),
    param(bytes, "aGk=", b"hi", id="bytes-from-base64"),
    param(UUID, UUID(int=1), UUID(int=1), id="uuid"),
    param(UUID, str(UUID(int=1)), UUID(int=1), id="uuid-from-str"),
    param(datetime, datetime(2026, 1, 2, 3), datetime(2026, 1, 2, 3), id="datetime"),
    param(datetime, "2026-01-02T03:00:00", datetime(2026, 1, 2, 3), id="datetime-from-str"),
    param(date, date(2026, 1, 2), date(2026, 1, 2), id="date"),
    param(date, "2026-01-02", date(2026, 1, 2), id="date-from-str"),
    param(time, time(3, 4), time(3, 4), id="time"),
    param(time, "03:04:00", time(3, 4), id="time-from-str"),
    param(timedelta, timedelta(seconds=5), timedelta(seconds=5), id="timedelta"),
    param(timedelta, "PT5S", timedelta(seconds=5), id="timedelta-from-str"),
    param(Path, Path("/a"), Path("/a"), id="path"),
    param(Path, "/a", Path("/a"), id="path-from-str"),
    param(Mode, Mode.FAST, Mode.FAST, id="str-enum"),
    param(Mode, "slow", Mode.SLOW, id="str-enum-from-value"),
    param(Mode, "SLOW", Mode.SLOW, id="str-enum-from-name"),
    param(Level, Level.LOW, Level.LOW, id="int-enum"),
    param(Level, 2, Level.HIGH, id="int-enum-from-value"),
    param(Level, "HIGH", Level.HIGH, id="int-enum-from-name"),
    param(Level, "2", Level.HIGH, id="int-enum-from-str-value"),
    param(Literal["a", 1], "a", "a", id="literal-str"),
    param(Literal["a", 1], 1, 1, id="literal-int"),
    param(None, None, None, id="none"),
    param(Any, object, object, id="any"),
    param(tuple[int, str], (1, "a"), (1, "a"), id="tuple"),
    param(tuple[int, str], ["1", "a"], (1, "a"), id="tuple-from-list"),
    param(tuple[int, ...], [1, 2], (1, 2), id="variadic-tuple"),
    param(Sequence[int], [1, "2"], (1, 2), id="sequence"),
    param(list[int], [1, 2], (1, 2), id="list-becomes-immutable"),
    param(Set[int], [1, 1, 2], frozenset({1, 2}), id="set"),
    param(frozenset[int], {1}, frozenset({1}), id="frozenset"),
    param(Mapping[str, int], {"a": "1"}, {"a": 1}, id="mapping"),
    param(dict[str, int], {"a": 1}, {"a": 1}, id="dict"),
    param(int | str, "x", "x", id="union-str"),
    param(int | str, 3, 3, id="union-int"),
    param(int | None, None, None, id="optional-none"),
    param(Payload, {"name": "n"}, {"name": "n"}, id="typed-dict"),
    param(Callable[[str], int], len, len, id="callable"),
    param(Runner, IMPL, IMPL, id="protocol"),
    param(Numbers, [1, "2"], (1, 2), id="alias"),
    param(Tree, [1, [2, 3]], (1, (2, 3)), id="recursive-alias"),
    param(Box[int], {"value": "5"}, Box[int](value=5), id="nested-state"),
    param(Box[int], Box[int](value=1), Box[int](value=1), id="nested-state-instance"),
    param(Meta, Meta.of({"a": 1}), Meta.of({"a": 1}), id="meta"),
    param(int | Missing, 1, 1, id="optional-missing"),
)

# (annotation, rejected raw value)
REJECTED: Sequence[Any] = (
    param(bool, 1.5, id="bool-float"),
    param(bool, "x", id="bool-str"),
    param(bool, None, id="bool-none"),
    param(int, True, id="int-bool"),
    param(int, "x", id="int-str"),
    param(int, None, id="int-none"),
    param(float, True, id="float-bool"),
    param(float, "x", id="float-str"),
    param(str, 1, id="str-int"),
    param(bytes, 5, id="bytes-int"),
    param(UUID, "nope", id="uuid-str"),
    param(UUID, 5, id="uuid-int"),
    param(datetime, "nope", id="datetime-str"),
    param(datetime, 5, id="datetime-int"),
    param(date, "nope", id="date-str"),
    param(date, 5, id="date-int"),
    param(time, "nope", id="time-str"),
    param(time, 5, id="time-int"),
    param(timedelta, 5, id="timedelta-int"),
    param(timedelta, "5s", id="timedelta-str"),
    param(Path, 5, id="path-int"),
    param(Mode, "nope", id="str-enum-str"),
    param(Mode, 5, id="str-enum-int"),
    param(Level, 9, id="int-enum-int"),
    param(Level, "NOPE", id="int-enum-str"),
    param(Level, 1.5, id="int-enum-float"),
    param(Literal["a", 1], "b", id="literal"),
    param(None, 1, id="none"),
    param(tuple[int, str], (1,), id="tuple-length"),
    param(tuple[int, str], "ab", id="tuple-str"),
    param(Sequence[int], "12", id="sequence-str"),
    param(Sequence[int], 5, id="sequence-int"),
    param(Set[int], 5, id="set-int"),
    param(Mapping[str, int], [1], id="mapping-list"),
    param(Mapping[str, int], {"a": "x"}, id="mapping-value"),
    param(int | str, None, id="union"),
    param(Payload, {"count": 1}, id="typed-dict-required"),
    param(Payload, 5, id="typed-dict-int"),
    param(Callable[[], int], 5, id="callable"),
    param(Runner, 5, id="protocol"),
    param(Numbers, "12", id="alias-str"),
    param(Box[int], {"value": "x"}, id="nested-state"),
    param(Meta, 5, id="meta"),
)


@mark.parametrize(("annotation", "raw", "expected"), ACCEPTED)
def test_attribute_converts_accepted_values(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    state = make_state(annotation)(value=raw)

    value = getattr(state, "value")  # noqa: B009
    assert value == expected
    if not isinstance(expected, Mapping):
        assert type(value) is type(expected)


@mark.parametrize(("annotation", "raw", "expected"), ACCEPTED)
def test_updating_converts_accepted_values(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    state = make_state(annotation, value=expected)().updating(value=raw)  # pyright: ignore[reportCallIssue]

    assert getattr(state, "value") == expected  # noqa: B009


@mark.parametrize(("annotation", "raw"), REJECTED)
def test_attribute_rejects_invalid_values_naming_the_attribute(
    annotation: Any,
    raw: Any,
) -> None:
    with raises(ValidationError, match=r"Validation of \.value"):
        make_state(annotation)(value=raw)


@mark.parametrize(("annotation", "raw"), REJECTED)
def test_updating_rejects_invalid_values(
    annotation: Any,
    raw: Any,
) -> None:
    state_type = make_state(annotation, value=None)

    with raises(ValidationError, match=r"Validation of \.value"):
        state_type().updating(value=raw)  # pyright: ignore[reportCallIssue]


# --- instance checks of specialized variants (attribute `check`) ------------

# (argument, instance value matching it, instance value not matching it)
CHECKED: Sequence[Any] = (
    param(bool, True, 1, id="bool"),
    param(int, 1, "1", id="int"),
    param(float, 1.5, 1, id="float"),
    param(str, "a", 1, id="str"),
    param(bytes, b"a", "a", id="bytes"),
    param(UUID, UUID(int=1), "x", id="uuid"),
    param(datetime, datetime(2026, 1, 1), date(2026, 1, 1), id="datetime"),
    param(date, date(2026, 1, 1), "2026-01-01", id="date"),
    param(time, time(1), "01:00", id="time"),
    param(timedelta, timedelta(1), 1, id="timedelta"),
    param(Path, Path("/a"), "/a", id="path"),
    param(Mode, Mode.FAST, "fast", id="str-enum"),
    param(Level, Level.LOW, 1, id="int-enum"),
    param(Literal["a"], "a", "b", id="literal"),
    param(None, None, 1, id="none"),
    param(Any, 1, 1, id="any"),
    param(tuple[int, str], (1, "a"), (1,), id="tuple"),
    param(Sequence[int], (1, 2), (1, "2"), id="sequence"),
    param(Set[int], frozenset({1}), frozenset({"a"}), id="set"),
    param(Mapping[str, int], {"a": 1}, {"a": "x"}, id="mapping"),
    param(int | str, "a", 1.5, id="union"),
    param(Payload, {"name": "n"}, {"count": 1}, id="typed-dict"),
    param(Callable[[str], int], len, 1, id="callable"),
    param(Runner, Impl(), 1, id="protocol"),
    param(Box[int], Box[int](value=1), Box[str](value="a"), id="nested-state"),
    param(Annotated[int, Validator(lambda value: value)], 1, "1", id="validable"),
    param(Annotated[int, Alias("renamed")], 1, "1", id="annotated"),
)


@mark.parametrize(("argument", "good", "bad"), CHECKED)
def test_specialized_instance_check_uses_attribute_check(
    argument: Any,
    good: Any,
    bad: Any,
) -> None:
    variant: Any = Box[argument]

    # the instances are created through the unspecialized origin, which holds
    # anything - the specialized check is the only thing which can refuse them
    assert isinstance(Box(value=good), variant)
    if argument is not Any:
        assert not isinstance(Box(value=bad), variant)


# --- metadata applied through Annotated -------------------------------------

# (annotation, valid raw value, expected, accepted by a pre-validation hook)
METADATA: Sequence[Any] = tuple(
    param(*case.values[:3], id=case.id)
    for case in ACCEPTED
    if case.id
    in {
        "bool",
        "int",
        "float",
        "str",
        "bytes",
        "uuid",
        "datetime",
        "date",
        "time",
        "timedelta",
        "path",
        "str-enum",
        "int-enum",
        "literal-str",
        "none",
        "any",
        "tuple",
        "sequence",
        "set",
        "mapping",
        "union-str",
        "typed-dict",
        "callable",
        "protocol",
        "alias",
        "nested-state",
        "meta",
        "optional-missing",
    }
)


@mark.parametrize(("annotation", "raw", "expected"), METADATA)
def test_annotations_alias_description_redaction_and_specification(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    state_type: Any = make_state(
        Annotated[
            annotation,
            Alias("external"),
            Description("described"),
            Specification({"type": "string", "description": "specified"}),
            Sensitive(redaction="<hidden>"),
            Meta.of({"custom": "meta"}),
        ],
    )

    state = state_type(external=raw)

    assert state.value == expected
    assert state_type(value=raw).value == expected  # the name stays accepted as well
    assert set(state.to_mapping()) == {"external"}
    assert str(state) == "Dynamic(external: <hidden>)"
    assert state_type.__FIELDS__[0].alias == "external"
    assert state_type.__FIELDS__[0].description == "described"
    assert state_type.__FIELDS__[0].redaction == "<hidden>"
    assert state_type.__FIELDS__[0].specification == {
        "type": "string",
        "description": "specified",
    }
    assert state_type.__FIELDS__[0].annotation.meta == {"custom": "meta"}
    assert json.loads(state_type.json_schema())["properties"] == {
        "external": {"type": "string", "description": "specified"}
    }


@mark.parametrize(("annotation", "raw", "expected"), METADATA)
def test_annotations_verifier_runs_after_validation(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    seen: list[Any] = []

    def verify(value: Any) -> Any:
        seen.append(value)
        return value

    state_type: Any = make_state(Annotated[annotation, Verifier(verify)])

    assert state_type(value=raw).value == expected
    assert seen == [expected]


@mark.parametrize(("annotation", "raw", "expected"), METADATA)
def test_annotations_verifier_can_reject(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    def refuse(value: Any) -> Any:
        raise ValueError("refused")

    state_type: Any = make_state(Annotated[annotation, Verifier(refuse)])

    with raises(ValidationError, match="refused"):
        state_type(value=raw)


@mark.parametrize(("annotation", "raw", "expected"), METADATA)
def test_annotations_validator_runs_before_validation(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    def convert(value: Any) -> Any:
        return raw if value == "pre-validation" else value

    state_type: Any = make_state(Annotated[annotation, Validator(convert)])

    assert state_type(value="pre-validation").value == expected
    assert state_type(value=raw).value == expected


@mark.parametrize(("annotation", "raw", "expected"), METADATA)
def test_annotations_stack_validator_and_verifier(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    order: list[str] = []

    def pre(value: Any) -> Any:
        order.append("validator")
        return value

    def post(value: Any) -> Any:
        order.append("verifier")
        return value

    state_type: Any = make_state(Annotated[annotation, Validator(pre), Verifier(post)])

    assert state_type(value=raw).value == expected
    assert order == ["validator", "verifier"]


@mark.parametrize(("annotation", "raw", "expected"), METADATA)
def test_annotated_attribute_with_default_is_not_required(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    state_type: Any = make_state(
        Annotated[annotation, Specification({"type": "string"})],
        value=raw,
    )

    assert state_type().value == expected
    assert json.loads(state_type.json_schema())["required"] == []


@mark.parametrize(("annotation", "raw", "expected"), METADATA)
def test_string_metadata_is_a_description(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    state_type: Any = make_state(Annotated[annotation, "plain description"])

    assert state_type.__FIELDS__[0].description == "plain description"


@mark.parametrize(("annotation", "raw", "expected"), METADATA)
def test_annotations_can_be_nested_and_later_ones_win(
    annotation: Any,
    raw: Any,
    expected: Any,
) -> None:
    state_type: Any = make_state(
        Annotated[
            Annotated[annotation, Alias("first"), "first"],
            Alias("second"),
            "second",
        ],
    )

    assert state_type.__FIELDS__[0].alias == "second"
    assert state_type.__FIELDS__[0].description == "second"
    assert state_type(second=raw).value == expected


# --- descriptions of serializable types --------------------------------------


@mark.parametrize(
    "annotation",
    [
        *(bool, int, float, str, UUID, datetime, date, time, timedelta, bytes, Path),
        *(Mode, Level, Literal["a"], None),
    ],
    ids=lambda value: getattr(value, "__name__", None) or repr(value),
)
def test_description_reaches_json_schema(annotation: Any) -> None:
    state_type: Any = make_state(Annotated[annotation, Description("described")])

    assert json.loads(state_type.json_schema())["properties"]["value"]["description"] == (
        "described"
    )


# --- missing and optional ----------------------------------------------------


def test_missing_union_clears_attribute() -> None:
    state_type: Any = make_state(int | Missing)

    assert state_type().to_mapping() == {}
    assert state_type(value=1).to_mapping() == {"value": 1}
    assert state_type(value=1).updating(value=Missing).to_mapping() == {}


def test_not_required_typed_dict_key_is_optional() -> None:
    state_type: Any = make_state(Payload)

    assert state_type(value={"name": "n", "count": "3"}).value == {"name": "n", "count": 3}
    assert state_type(value={"name": "n"}).value == {"name": "n"}


# --- annotation markers and fields -------------------------------------------


@mark.parametrize("marker", [Validator(lambda value: value), Verifier(lambda value: value)])
def test_validator_and_verifier_markers_are_immutable(marker: Any) -> None:
    with raises(AttributeError, match="can't be modified"):
        marker.anything = 1

    with raises(AttributeError, match="can't be modified"):
        del marker.anything


def test_field_specification_falls_back_to_the_type_specification() -> None:
    state_type: Any = make_state(int)

    assert state_type.__FIELDS__[0].specification == {"type": "integer"}
    assert state_type.__FIELDS__[0].key == "value"
    assert state_type.__FIELDS__[0].required is True
    assert make_state(int, value=1).__FIELDS__[0].required is False


def test_validator_sees_raw_input_ahead_of_the_validate_of_a_custom_class() -> None:
    class Token:
        def __init__(self, text: str) -> None:
            self.text = text

        @classmethod
        def validate(cls, value: Any) -> Token:
            if isinstance(value, Token):
                return value

            if isinstance(value, str):
                return cls(value)

            raise TypeError("not a token")

    def from_number(value: Any) -> Any:
        return str(value) if isinstance(value, int) else value

    state_type: Any = make_state(Annotated[Token, Validator(from_number)])

    assert state_type(value=5).value.text == "5"
    assert state_type(value="a").value.text == "a"
    assert isinstance(state_type(value=Token("b")).value, Token)

    with raises(ValidationError, match="not a token"):
        state_type(value=1.5)


def test_validator_sees_raw_mapping_ahead_of_state_validation() -> None:
    class Inner(State):
        number: int

    def from_number(value: Any) -> Any:
        return {"number": value} if isinstance(value, int) else value

    state_type: Any = make_state(Annotated[Inner, Validator(from_number)])

    assert state_type(value=5).value == Inner(number=5)
    assert state_type(value={"number": "6"}).value == Inner(number=6)
    assert state_type(value=Inner(number=7)).value == Inner(number=7)

    with raises(ValidationError):
        state_type(value="nope")
