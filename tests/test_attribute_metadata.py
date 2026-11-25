from typing import Annotated, Any, NotRequired, TypedDict

import pytest

from haiway import State
from haiway.attributes import Attribute
from haiway.attributes.annotations import (
    AttributeAnnotation,
    BoolAttribute,
    IntegerAttribute,
    ObjectAttribute,
    StringAttribute,
    ValidableAttribute,
)
from haiway.attributes.validation import ValidationError, Validator, Verifier
from haiway.types import Alias, Description, Meta, Sensitive, Specification


def attribute_of(cls: type[State], name: str) -> AttributeAnnotation:
    self_attribute = getattr(cls, "__SELF_ATTRIBUTE__", None)
    assert isinstance(self_attribute, ObjectAttribute)
    return self_attribute.attributes[name]


def _specification(minimum: int, /) -> Any:
    return {"type": "integer", "minimum": minimum}


def _recording(calls: list[str], name: str, /) -> Any:
    def record(value: Any) -> Any:
        calls.append(name)
        return value

    return record


def _adding(calls: list[str], name: str, amount: int, /) -> Any:
    def add(value: Any) -> Any:
        calls.append(name)
        return value + amount

    return add


# --- metadata is read the same wherever it is declared ----------------------------------


def test_last_declaration_of_each_kind_of_metadata_wins() -> None:
    class Example(State):
        value: Annotated[
            int,
            Alias("first"),
            Alias("second"),
            "first description",
            Description("second description"),
            Meta({"a": 1}),
            Meta({"b": 2}),
            Specification(_specification(1)),
            Specification(_specification(2)),
        ]

    annotation = attribute_of(Example, "value")
    assert annotation.alias == "second"
    assert annotation.description == "second description"
    assert annotation.meta == {"b": 2}
    assert annotation.specification == {"type": "integer", "minimum": 2}
    assert annotation.required is True
    assert annotation.redaction is None


def test_sensitive_class_and_instance_redact_the_same() -> None:
    class Example(State):
        by_class: Annotated[str, Sensitive]
        by_instance: Annotated[str, Sensitive(redaction="<hidden>")]

    assert attribute_of(Example, "by_class").redaction == Sensitive().redaction
    assert attribute_of(Example, "by_instance").redaction == "<hidden>"


def test_reannotating_keeps_what_was_declared_before() -> None:
    calls: list[str] = []

    class Scope:
        type Checked = Annotated[int, Verifier(_recording(calls, "verifier")), Alias("checked")]

    class Example(State):
        value: Annotated[Scope.Checked, Description("described")]

    annotation = attribute_of(Example, "value")
    assert annotation.alias == "checked"
    assert annotation.description == "described"
    Example(value=1)
    assert calls == ["verifier"]


def test_not_required_keeps_the_verifier_of_a_typed_dict_key() -> None:
    calls: list[str] = []

    class Mapped(TypedDict):
        value: NotRequired[Annotated[int, Verifier(_recording(calls, "verifier"))]]

    class Example(State):
        mapped: Mapped

    Example(mapped={"value": 1})
    assert calls == ["verifier"]


def test_identical_declarations_read_the_same() -> None:
    def make() -> AttributeAnnotation:
        class Example(State):
            value: Annotated[
                int,
                Alias("aliased"),
                Validator(_validator),
                Validator(_other_validator),
                Verifier(_validator),
                Verifier(_other_validator),
            ]

        return attribute_of(Example, "value")

    assert repr(make()) == repr(make())
    assert "object at 0x" not in repr(make())


def _validator(value: Any) -> Any:
    return value


def _other_validator(value: Any) -> Any:
    return value


# --- several validators and verifiers of one attribute compose --------------------------


def test_validators_run_in_declaration_order_each_given_the_previous_result() -> None:
    calls: list[str] = []

    class Example(State):
        value: Annotated[
            int,
            Validator(_adding(calls, "first", 1)),
            Validator(_adding(calls, "second", 10)),
            Validator(_adding(calls, "third", 100)),
        ]

    assert Example(value=0).value == 111
    assert calls == ["first", "second", "third"]


def test_verifiers_run_in_declaration_order_each_given_the_previous_result() -> None:
    calls: list[str] = []

    class Example(State):
        value: Annotated[
            int,
            Verifier(_adding(calls, "first", 1)),
            Verifier(_adding(calls, "second", 10)),
        ]

    assert Example(value=0).value == 11
    assert calls == ["first", "second"]


def test_validators_run_before_the_value_is_validated_and_verifiers_after() -> None:
    calls: list[str] = []

    class Example(State):
        value: Annotated[
            int,
            Verifier(_recording(calls, "verifier one")),
            Validator(_recording(calls, "validator one")),
            Verifier(_recording(calls, "verifier two")),
            Validator(_recording(calls, "validator two")),
        ]

    Example(value=1)
    assert calls == ["validator one", "validator two", "verifier one", "verifier two"]


def test_verifier_has_to_return_the_value() -> None:
    def forgetful(value: Any) -> Any:
        return None

    class Example(State):
        value: Annotated[Any, Verifier(forgetful)]

    assert Example(value=1).value is None


def test_nested_annotated_runs_inner_declarations_first() -> None:
    calls: list[str] = []

    class Example(State):
        value: Annotated[
            Annotated[
                int,
                Validator(_recording(calls, "inner validator")),
                Verifier(_recording(calls, "inner verifier")),
            ],
            Validator(_recording(calls, "outer validator")),
            Verifier(_recording(calls, "outer verifier")),
        ]

    Example(value=1)
    assert calls == [
        "inner validator",
        "outer validator",
        "inner verifier",
        "outer verifier",
    ]


def test_annotated_alias_runs_its_own_declarations_first() -> None:
    calls: list[str] = []

    class Scope:
        type Inner = Annotated[
            int,
            Validator(_recording(calls, "alias validator")),
            Verifier(_recording(calls, "alias verifier")),
        ]

    class Example(State):
        value: Annotated[
            Scope.Inner,
            Validator(_recording(calls, "outer validator")),
            Verifier(_recording(calls, "outer verifier")),
        ]

    Example(value=1)
    assert calls == [
        "alias validator",
        "outer validator",
        "alias verifier",
        "outer verifier",
    ]


def test_validators_of_one_annotation_are_one_validable() -> None:
    class Example(State):
        value: Annotated[int, Validator(_validator), Validator(_other_validator)]

    annotation = attribute_of(Example, "value")
    assert isinstance(annotation, ValidableAttribute)
    assert isinstance(annotation.attribute, IntegerAttribute)


def test_failure_of_the_second_validator_is_reported_as_it_is() -> None:
    failure = ValueError("second refused")

    def first(value: Any) -> Any:
        return value

    def second(value: Any) -> Any:
        raise failure

    class Example(State):
        value: Annotated[int, Validator(first), Validator(second)]

    with pytest.raises(ValidationError) as caught:
        Example(value=1)

    assert caught.value.cause is failure
    assert tuple(caught.value.path) == (".value",)


def test_failure_of_the_second_verifier_is_reported_as_it_is() -> None:
    failure = ValueError("second refused")

    def first(value: Any) -> Any:
        return value

    def second(value: Any) -> Any:
        raise failure

    class Example(State):
        value: Annotated[int, Verifier(first), Verifier(second)]

    with pytest.raises(ValidationError) as caught:
        Example(value=1)

    assert caught.value.cause is failure
    assert tuple(caught.value.path) == (".value",)


def test_second_validator_is_not_run_when_the_first_fails() -> None:
    calls: list[str] = []

    def first(value: Any) -> Any:
        raise ValueError("first refused")

    class Example(State):
        value: Annotated[int, Validator(first), Validator(_recording(calls, "second"))]

    with pytest.raises(ValidationError):
        Example(value=1)

    assert calls == []


# --- annotations refuse to be changed ----------------------------------------------------


@pytest.mark.parametrize(
    "annotation",
    (
        IntegerAttribute(),
        StringAttribute(),
        BoolAttribute(),
        ValidableAttribute(attribute=IntegerAttribute(), validating=_validator),
    ),
)
def test_annotations_refuse_assignment_and_deletion(annotation: Any) -> None:
    with pytest.raises(AttributeError):
        annotation.alias = "changed"

    with pytest.raises(AttributeError):
        annotation.metadata = None

    with pytest.raises(AttributeError):
        del annotation.alias


def test_attribute_refuses_assignment_and_deletion() -> None:
    class Example(State):
        value: int

    attribute: Attribute = Example.__FIELDS__[0]
    spec = attribute.spec
    with pytest.raises(AttributeError):
        attribute.spec = None  # replaced - would desync from the native plan

    with pytest.raises(AttributeError):
        attribute.name = "other"

    with pytest.raises(AttributeError):
        del attribute.spec

    assert attribute.spec is spec
    assert Example(value=1).value == 1
