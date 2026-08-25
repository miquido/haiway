import datetime
import enum
import pathlib
import uuid
from collections.abc import Callable, Mapping, MutableSequence, Sequence, Set
from typing import Annotated, Any, Literal, Protocol, TypedDict, runtime_checkable

import pytest

from haiway import MISSING, Map, Meta, Missing, State, Verifier
from haiway.attributes.annotations import AttributeAnnotation, resolve_attribute


class Leaf(State):
    number: int


class LeafDict(TypedDict):
    number: int


class Named(enum.StrEnum):
    RED = "red"


class Numbered(enum.IntEnum):
    ONE = 1


@runtime_checkable
class Implemented(Protocol):
    def method(self) -> None: ...


class Implementation:
    def method(self) -> None:
        pass


class Opaque:
    pass


def function(value: int) -> int:
    return value


IMPLEMENTATION = Implementation()
OPAQUE = Opaque()
UUID_VALUE = uuid.UUID("00000000-0000-0000-0000-000000000000")
DATETIME_VALUE = datetime.datetime(2024, 1, 1, 12, 0, tzinfo=datetime.UTC)
DATE_VALUE = datetime.date(2024, 1, 1)
TIME_VALUE = datetime.time(12, 0)

# each attribute kind, including every conversion it accepts - a verifier has to
# be applied to the validated value the same way a validator is applied to the
# raw one, no matter which branch produced it
VERIFIED_CASES: Sequence[tuple[str, Any, Any, Any]] = (
    ("any", Any, 42, 42),
    ("missing", Missing, MISSING, MISSING),
    ("none", None, None, None),
    ("literal", Literal["a"], "a", "a"),
    ("bool", bool, True, True),
    ("bool_from_int", bool, 1, True),
    ("int", int, 3, 3),
    ("int_from_float", int, 3.0, 3),
    ("float", float, 1.5, 1.5),
    ("float_from_int", float, 2, 2.0),
    ("bytes", bytes, b"value", b"value"),
    ("uuid", uuid.UUID, UUID_VALUE, UUID_VALUE),
    ("uuid_from_str", uuid.UUID, str(UUID_VALUE), UUID_VALUE),
    ("str", str, "value", "value"),
    ("datetime", datetime.datetime, DATETIME_VALUE, DATETIME_VALUE),
    ("datetime_from_str", datetime.datetime, DATETIME_VALUE.isoformat(), DATETIME_VALUE),
    ("date", datetime.date, DATE_VALUE, DATE_VALUE),
    ("date_from_str", datetime.date, DATE_VALUE.isoformat(), DATE_VALUE),
    ("time", datetime.time, TIME_VALUE, TIME_VALUE),
    ("time_from_str", datetime.time, TIME_VALUE.isoformat(), TIME_VALUE),
    ("path", pathlib.Path, pathlib.Path("file"), pathlib.Path("file")),
    ("path_from_str", pathlib.Path, "file", pathlib.Path("file")),
    ("tuple", tuple[int, str], (1, "a"), (1, "a")),
    ("sequence", Sequence[int], [1, 2], (1, 2)),
    ("set", Set[int], {1}, frozenset({1})),
    ("mapping", Mapping[str, int], {"a": 1}, Map({"a": 1})),
    ("meta", Meta, Meta.empty, Meta.empty),
    ("meta_from_mapping", Meta, {"key": "value"}, Meta({"key": "value"})),
    ("object", Leaf, Leaf(number=1), Leaf(number=1)),
    ("object_from_mapping", Leaf, {"number": 1}, Leaf(number=1)),
    ("typed_dict", LeafDict, {"number": 1}, Map({"number": 1})),
    ("function", Callable[[int], int], function, function),
    ("protocol", Implemented, IMPLEMENTATION, IMPLEMENTATION),
    ("union", int | str, 1, 1),
    ("union_converted", float | str, 1, 1.0),
    ("custom", Opaque, OPAQUE, OPAQUE),
    ("str_enum", Named, Named.RED, Named.RED),
    ("str_enum_from_value", Named, "red", Named.RED),
    ("str_enum_from_name", Named, "RED", Named.RED),
    ("int_enum", Numbered, Numbered.ONE, Numbered.ONE),
    ("int_enum_from_value", Numbered, 1, Numbered.ONE),
    ("int_enum_from_name", Numbered, "ONE", Numbered.ONE),
    ("int_enum_from_str_value", Numbered, "1", Numbered.ONE),
)


def annotation_of(annotation: Any) -> AttributeAnnotation:
    return resolve_attribute(
        annotation,
        module=__name__,
        resolved_parameters={},
        recursion_guard={},
    )


@pytest.mark.parametrize(
    ("annotation", "value", "expected"),
    [case[1:] for case in VERIFIED_CASES],
    ids=[case[0] for case in VERIFIED_CASES],
)
def test_verifier_receives_the_validated_value(
    annotation: Any,
    value: Any,
    expected: Any,
) -> None:
    verified: MutableSequence[Any] = []

    def verify(value: Any) -> Any:
        verified.append(value)
        return value

    attribute: AttributeAnnotation = annotation_of(Annotated[annotation, Verifier(verify)])

    assert attribute.validate(value) == expected
    assert verified == [expected]


@pytest.mark.parametrize(
    ("annotation", "value"),
    [case[1:3] for case in VERIFIED_CASES],
    ids=[case[0] for case in VERIFIED_CASES],
)
def test_verifier_failure_is_reported_instead_of_the_conversion(
    annotation: Any,
    value: Any,
) -> None:
    def reject(value: Any) -> Any:
        raise ValueError("refused")

    attribute: AttributeAnnotation = annotation_of(Annotated[annotation, Verifier(reject)])

    # the verification failure is the failure of the attribute - a conversion
    # which succeeded before it can't report a failure of its own instead
    with pytest.raises(ValueError, match="refused"):
        attribute.validate(value)


def test_verifier_result_replaces_the_validated_value() -> None:
    def normalized(value: str) -> str:
        return value.strip()

    attribute: AttributeAnnotation = annotation_of(Annotated[str, Verifier(normalized)])

    assert attribute.validate(" value ") == "value"
