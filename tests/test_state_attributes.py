from typing import Annotated, Final, NotRequired, Required, TypedDict

import typing_extensions as te

from haiway import State


def test_attribute_annotations_preserve_annotated_metadata() -> None:
    class Example(State):
        value: Annotated[Annotated[int, "inner"], "outer"]

    annotation = Example.__ATTRIBUTES__["value"].annotation  # pyright: ignore[reportAttributeAccessIssue]
    assert annotation.annotations == ("inner", "outer")


def test_attribute_annotations_mark_final_wrapper() -> None:
    class Example(State):
        value: Final[int]

    annotation = Example.__ATTRIBUTES__["value"].annotation  # pyright: ignore[reportAttributeAccessIssue]
    assert Final in annotation.annotations


def test_typed_dict_annotations_preserve_metadata_and_required() -> None:
    class ExampleMapping(TypedDict):
        annotated: Annotated[int, "meta"]
        required_value: Required[Annotated[int, "inner"]]
        optional_value: NotRequired[int]

    class Example(State):
        mapping: ExampleMapping

    annotation = Example.__ATTRIBUTES__["mapping"].annotation  # pyright: ignore[reportAttributeAccessIssue]
    attributes = annotation.attributes
    assert attributes["annotated"].annotations == ("meta",)
    required_annotation = attributes["required_value"]
    assert Required in required_annotation.annotations
    assert "inner" in required_annotation.annotations
    assert NotRequired in attributes["optional_value"].annotations


def test_typed_dict_not_required_metadata_from_typing_extensions() -> None:
    class MappingExt(TypedDict):
        flag: te.NotRequired[bool]

    class Example(State):
        mapping: MappingExt

    annotation = Example.__ATTRIBUTES__["mapping"].annotation  # pyright: ignore[reportAttributeAccessIssue]
    flag_annotation = annotation.attributes["flag"]
    assert te.NotRequired in flag_annotation.annotations


def test_private_annotations_are_ignored() -> None:
    class Example(State):
        value: int
        _cache: dict[str, str]

    attributes = Example.__ATTRIBUTES__  # pyright: ignore[reportAttributeAccessIssue]
    assert "value" in attributes
    assert "_cache" not in attributes
    assert "_cache" not in getattr(Example, "__slots__", ())


def test_generic_alias_preserves_arguments() -> None:
    class Box[T](State):
        item: T

    class Container(State):
        box: Box[int]

    box_attribute = Container.__ATTRIBUTES__["box"].annotation  # pyright: ignore[reportAttributeAccessIssue]
    assert box_attribute.arguments
    first_argument = box_attribute.arguments[0]
    assert isinstance(first_argument, type(box_attribute))
    assert first_argument.origin is int


def test_generic_alias_preserves_nested_type_arguments() -> None:
    class Box[T](State):
        item: T

    class Wrapper[T](State):
        box: Box[T]

    box_attribute = Wrapper[int].__ATTRIBUTES__["box"].annotation  # pyright: ignore[reportAttributeAccessIssue]
    assert box_attribute.arguments
    first_argument = box_attribute.arguments[0]
    assert isinstance(first_argument, type(box_attribute))
    assert first_argument.origin is int
