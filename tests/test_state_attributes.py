from __future__ import annotations

import datetime
import enum
import pathlib
import typing
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Annotated, Any, Final, NotRequired, Required, TypedDict

import pytest
import typing_extensions as te

from haiway import State
from haiway.attributes.annotations import (
    AliasAttribute,
    AttributeAnnotation,
    CustomAttribute,
    DatetimeAttribute,
    IntegerAttribute,
    MetaAttribute,
    NoneAttribute,
    ObjectAttribute,
    PathAttribute,
    StrEnumAttribute,
    TimeAttribute,
    TupleAttribute,
    TypedDictAttribute,
    UUIDAttribute,
    ValidableAttribute,
)
from haiway.attributes.validation import Validator
from haiway.types import Alias, Description, Meta, Specification

_SENTINEL = object()


def ensure_positive(value: int) -> int:
    if value <= 0:
        raise ValueError("expected positive value")

    return value


@contextmanager
def register_type(name: str, obj: Any) -> Iterator[None]:
    previous = globals().get(name, _SENTINEL)
    obj.__module__ = __name__
    globals()[name] = obj
    try:
        yield
    finally:
        if previous is _SENTINEL:
            globals().pop(name, None)
        else:
            globals()[name] = previous


def attribute_of(cls: type[State], name: str) -> AttributeAnnotation:
    self_attribute = getattr(cls, "__SELF_ATTRIBUTE__", None)
    assert isinstance(self_attribute, ObjectAttribute)
    return self_attribute.attributes[name]


def test_alias_attribute_annotations_returns_empty_before_resolution() -> None:
    alias = AliasAttribute(
        type_alias="Example",
        module="tests.test_state_attributes",
    )

    assert alias.annotations == ()


def test_type_none_attribute_resolves_to_none_attribute() -> None:
    class Example(State):
        explicit_none: None

    annotation = attribute_of(Example, "explicit_none")

    assert isinstance(annotation, NoneAttribute)
    assert annotation.base is None
    assert annotation.validate(None) is None
    with pytest.raises(TypeError):
        annotation.validate("not-none")


def test_attribute_annotations_handle_string_metadata_as_description() -> None:
    class Example(State):
        value: Annotated[Annotated[int, "inner"], "outer"]

    annotation = attribute_of(Example, "value")
    assert isinstance(annotation, IntegerAttribute)
    assert annotation.alias is None
    assert annotation.description == "outer"
    assert annotation.specification is None
    assert annotation.required is True


def test_attribute_annotations_skip_final_wrapper() -> None:
    class Example(State):
        value: Final[int]

    annotation = attribute_of(Example, "value")
    assert isinstance(annotation, IntegerAttribute)
    assert annotation.alias is None
    assert annotation.description is None
    assert annotation.specification is None
    assert annotation.required is True


def test_attribute_annotations_attach_validator_metadata() -> None:
    class Example(State):
        value: Annotated[int, Validator(ensure_positive)]

    annotation = attribute_of(Example, "value")
    assert isinstance(annotation, ValidableAttribute)
    assert annotation.validate(5) == 5
    with pytest.raises(ValueError):
        annotation.validate(0)


class PositiveInt(int):
    @classmethod
    def validate(cls, value: Any) -> PositiveInt:
        candidate = cls(value)
        if candidate <= 0:
            raise ValueError("expected positive value")

        return candidate


def ensure_less_than_ten(value: PositiveInt) -> PositiveInt:
    if value >= 10:
        raise ValueError("expected value lower than ten")

    return value


class Example(State):
    value: Annotated[PositiveInt, Validator(ensure_less_than_ten)]


def test_attribute_annotations_stack_validators() -> None:
    annotation = attribute_of(Example, "value")
    assert isinstance(annotation, ValidableAttribute)
    validated_value = PositiveInt.validate(5)
    assert annotation.validate(validated_value) == validated_value

    with pytest.raises(ValueError):
        annotation.validate(-1)

    with pytest.raises(ValueError):
        annotation.validate(PositiveInt(10))


def test_attribute_annotations_include_alias_description_specification() -> None:
    class Example(State):
        value: Annotated[
            str,
            Alias("example"),
            Description("Example value"),
            Specification({"type": "string"}),
        ]

    annotation = attribute_of(Example, "value")
    assert annotation.alias == "example"
    assert annotation.description == "Example value"
    assert annotation.specification == {"type": "string"}


def test_state_specification_uses_alias_and_description_metadata() -> None:
    class Example(State):
        value: Annotated[str, Alias("external_name"), Description("External name description")]

    specification = Example.__SPECIFICATION__
    assert specification is not None

    properties = specification["properties"]
    assert "external_name" in properties
    assert properties["external_name"]["description"] == "External name description"


def test_state_specification_marks_required_fields() -> None:
    class Example(State):
        required_value: str
        alias_required: Annotated[int, Alias("external")]
        optional_default: int = 1

    specification = Example.__SPECIFICATION__
    assert specification is not None
    assert specification["required"] == ["required_value", "external"]


def test_state_specification_missing_when_child_has_no_specification() -> None:
    class Example(State):
        callback: typing.Callable[[int], int]

    assert Example.__SPECIFICATION__ is None


def test_state_specification_manual_override_for_unspecified_type() -> None:
    class Example(State):
        callback: Annotated[typing.Callable[[int], int], Specification({"type": "string"})]

    specification = Example.__SPECIFICATION__
    assert specification is not None
    assert specification["properties"]["callback"] == {"type": "string"}


def test_meta_attribute_resolution_and_specification() -> None:
    class Example(State):
        meta: Meta

    annotation = attribute_of(Example, "meta")
    assert isinstance(annotation, MetaAttribute)
    meta_value = Meta.of({"name": "example"})
    assert annotation.validate(meta_value) is meta_value
    validated = annotation.validate({"kind": "example"})
    assert isinstance(validated, Meta)

    specification = Example.__SPECIFICATION__
    assert specification is not None
    assert specification["properties"]["meta"] == {
        "type": "object",
        "additionalProperties": True,
    }


def test_typed_dict_annotations_converts() -> None:
    class ExampleMapping(TypedDict):
        annotated: Annotated[int, "meta"]
        required_value: Required[int]
        optional_value: NotRequired[int]

    with register_type("ExampleMapping", ExampleMapping):

        class Example(State):
            mapping: ExampleMapping

    annotation = attribute_of(Example, "mapping")
    assert isinstance(annotation, TypedDictAttribute)

    attributes = annotation.attributes
    annotated_attribute = attributes["annotated"]
    assert isinstance(annotated_attribute, IntegerAttribute)
    assert annotated_attribute.alias is None
    assert annotated_attribute.description == "meta"
    assert annotated_attribute.specification is None
    assert annotated_attribute.required is True

    required_attribute = attributes["required_value"]
    assert isinstance(required_attribute, IntegerAttribute)
    assert required_attribute.required is True

    optional_attribute = attributes["optional_value"]
    assert isinstance(optional_attribute, IntegerAttribute)
    assert optional_attribute.required is False


def test_typed_dict_required_preserves_inner_annotations() -> None:
    class ExampleMapping(TypedDict):
        required_value: Required[Annotated[int, "inner"]]
        required: Required[Annotated[int, Description("required")]]

    with register_type("ExampleMapping", ExampleMapping):

        class Example(State):
            mapping: ExampleMapping

    annotation = attribute_of(Example, "mapping")
    required_value = annotation.attributes["required_value"]
    assert isinstance(required_value, IntegerAttribute)
    assert required_value.alias is None
    assert required_value.description == "inner"
    assert required_value.specification is None

    required_attribute = annotation.attributes["required"]
    assert isinstance(required_attribute, IntegerAttribute)
    assert required_attribute.description == "required"


def test_typed_dict_not_required_metadata_from_typing_extensions() -> None:
    class MappingExt(TypedDict):
        flag: te.NotRequired[bool]

    with register_type("MappingExt", MappingExt):

        class Example(State):
            mapping: MappingExt

    annotation = attribute_of(Example, "mapping")
    flag_annotation = annotation.attributes["flag"]
    assert flag_annotation.required is False


def test_private_annotations_are_exposed() -> None:
    class Example(State):
        value: int
        _cache: dict[str, str]

    self_attribute = Example.__SELF_ATTRIBUTE__
    assert "value" in self_attribute.attributes
    assert "_cache" in self_attribute.attributes
    assert "_cache" in getattr(Example, "__slots__", ())


def test_generic_alias_preserves_specialized_base() -> None:
    class Box[T](State):
        item: T

    with register_type("Box", Box):

        class Container(State):
            box: Box[int]

    annotation = attribute_of(Container, "box")
    assert isinstance(annotation, ValidableAttribute)
    assert annotation.base is Box[int]


def test_generic_alias_records_type_parameters_on_specialization() -> None:
    class Box[T](State):
        item: T

    with register_type("Box", Box):
        specialized_box = Box[int]
    assert specialized_box.__TYPE_PARAMETERS__ == {"T": int}


def test_generic_alias_nested_specialization_propagates() -> None:
    class Box[T](State):
        item: T

    with register_type("Box", Box):

        class Wrapper[T](State):
            box: Box[T]

        with register_type("Wrapper", Wrapper):
            annotation = attribute_of(Wrapper[int], "box")
    assert annotation.base is Box[int]


def test_uuid_attribute_accepts_string_representation() -> None:
    class Example(State):
        identifier: uuid.UUID

    annotation = attribute_of(Example, "identifier")
    assert isinstance(annotation, UUIDAttribute)
    generated = uuid.uuid4()
    assert annotation.validate(generated) is generated
    assert annotation.validate(str(generated)) == generated
    with pytest.raises(ValueError):
        annotation.validate("not-a-uuid")


def test_datetime_attribute_parses_iso_strings() -> None:
    class Example(State):
        created_at: datetime.datetime

    annotation = attribute_of(Example, "created_at")
    assert isinstance(annotation, DatetimeAttribute)
    iso_value = "2024-05-06T07:08:09"
    parsed = annotation.validate(iso_value)
    assert parsed == datetime.datetime.fromisoformat(iso_value)
    with pytest.raises(TypeError):
        annotation.validate(123)


def test_time_attribute_parses_iso_strings() -> None:
    class Example(State):
        scheduled_at: datetime.time

    annotation = attribute_of(Example, "scheduled_at")
    assert isinstance(annotation, TimeAttribute)
    iso_value = "07:08:09"
    parsed = annotation.validate(iso_value)
    assert parsed == datetime.time.fromisoformat(iso_value)
    with pytest.raises(TypeError):
        annotation.validate(123)


def test_tuple_attribute_requires_fixed_length() -> None:
    class Example(State):
        payload: tuple[int, str]

    annotation = attribute_of(Example, "payload")
    assert isinstance(annotation, TupleAttribute)
    result = annotation.validate([1, "ok"])
    assert result == (1, "ok")

    with pytest.raises(ValueError, match="expected tuple length 2"):
        annotation.validate([1])


def test_timedelta_attribute_requires_explicit_timedelta() -> None:
    class Example(State):
        timeout: datetime.timedelta

    annotation = attribute_of(Example, "timeout")
    assert isinstance(annotation, CustomAttribute)
    valid = annotation.validate(datetime.timedelta(seconds=5))
    assert valid == datetime.timedelta(seconds=5)
    with pytest.raises(TypeError):
        annotation.validate(1.5)


def test_path_attribute_accepts_path_like_values() -> None:
    class Example(State):
        location: pathlib.Path

    annotation = attribute_of(Example, "location")
    assert isinstance(annotation, PathAttribute)
    resolved = annotation.validate("data/file.txt")
    assert resolved == pathlib.Path("data/file.txt")
    assert isinstance(resolved, pathlib.Path)


def test_str_enum_attribute_accepts_value_and_name() -> None:
    class Color(enum.StrEnum):
        RED = "red"
        BLUE = "blue"

    with register_type("Color", Color):

        class Example(State):
            color: Color

    annotation = attribute_of(Example, "color")
    assert isinstance(annotation, StrEnumAttribute)
    assert annotation.validate("red") is Color.RED
    assert annotation.validate("RED") is Color.RED
    with pytest.raises(ValueError):
        annotation.validate("green")


def test_int_enum_attribute_accepts_value_name_and_stringified_value() -> None:
    class Priority(enum.IntEnum):
        LOW = 1
        HIGH = 2

    with register_type("Priority", Priority):

        class Example(State):
            priority: Priority

    annotation = attribute_of(Example, "priority")
    assert annotation.validate(1) is Priority.LOW
    assert annotation.validate("HIGH") is Priority.HIGH
    assert annotation.validate("2") is Priority.HIGH
    with pytest.raises(ValueError):
        annotation.validate("MISSING")
