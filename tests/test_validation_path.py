from collections.abc import Mapping, Sequence, Set
from typing import Annotated, Any, TypedDict

from pytest import raises

from haiway import (
    Function,
    State,
    ValidationError,
    Validator,
    Verifier,
)


class Leaf(State):
    number: int


class LeafDict(TypedDict):
    number: int


class Middle(State):
    leaf: Leaf
    leaves: Mapping[str, Leaf]
    typed: LeafDict


class Root(State):
    middle: Middle
    sequence: Sequence[Leaf]
    pair: tuple[int, str]
    tags: Set[int]
    optional: Sequence[Leaf] | None = None


def middle_payload(**overrides: Any) -> Mapping[str, Any]:
    return {
        "leaf": {"number": 1},
        "leaves": {"key": {"number": 2}},
        "typed": {"number": 3},
        **overrides,
    }


def root_payload(**overrides: Any) -> Mapping[str, Any]:
    return {
        "middle": middle_payload(),
        "sequence": [{"number": 4}],
        "pair": (5, "six"),
        "tags": {7},
        **overrides,
    }


def test_attribute_path_names_the_field() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(pair="not a tuple"))

    assert exception.value.path == (".pair",)
    assert str(exception.value).startswith("Validation of .pair failed: ")


def test_nested_object_path_joins_attributes() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(middle=middle_payload(leaf={"number": "invalid"})))

    assert exception.value.path == (".middle", ".leaf", ".number")
    assert str(exception.value) == (
        "Validation of .middle.leaf.number failed:"
        " 'str' value is not matching expected format of 'int'"
    )


def test_sequence_element_path_carries_index() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(sequence=[{"number": 1}, {"number": "invalid"}]))

    assert exception.value.path == (".sequence", "[1]", ".number")


def test_tuple_element_path_carries_index() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(pair=(1, 2)))

    assert exception.value.path == (".pair", "[1]")


def test_set_element_path_carries_index() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(tags={"invalid"}))

    assert exception.value.path == (".tags", "[0]")


def test_mapping_element_path_carries_key() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(middle=middle_payload(leaves={"key": {"number": "invalid"}})))

    assert exception.value.path == (".middle", ".leaves", "[key]", ".number")


def test_typed_dict_element_path_quotes_key() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(middle=middle_payload(typed={"number": "invalid"})))

    assert exception.value.path == (".middle", ".typed", '["number"]')


def test_union_alternative_failures_stop_at_the_field() -> None:
    # every alternative failed, so the field itself is what could not be
    # resolved - the group holds one failure per alternative
    with raises(ValidationError) as exception:
        Root(**root_payload(optional=[{"number": "invalid"}]))

    assert exception.value.path == (".optional",)
    assert isinstance(exception.value.cause, BaseExceptionGroup)


def test_updating_reports_the_same_path() -> None:
    root: Root = Root(**root_payload())
    with raises(ValidationError) as exception:
        root.updating(middle=middle_payload(leaf={"number": "invalid"}))

    assert exception.value.path == (".middle", ".leaf", ".number")


def test_cause_is_the_original_error() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(middle=middle_payload(leaf={"number": "invalid"})))

    assert isinstance(exception.value.cause, ValueError)
    # a single error, chained from what actually failed rather than from the
    # enclosing report of it
    assert exception.value.__cause__ is exception.value.cause
    assert exception.value.__suppress_context__ is True


def test_validator_failure_reports_the_field() -> None:
    def reject(value: Any) -> int:
        raise ValueError("refused")

    class Validated(State):
        value: Annotated[int, Validator(reject)]

    with raises(ValidationError) as exception:
        Validated(value=1)

    assert exception.value.path == (".value",)
    assert isinstance(exception.value.cause, ValueError)


def test_verifier_failure_reports_the_element() -> None:
    def reject(value: int) -> int:
        raise ValueError("refused")

    class Verified(State):
        values: Sequence[Annotated[int, Verifier(reject)]]

    with raises(ValidationError) as exception:
        Verified(values=[1, 2])

    assert exception.value.path == (".values", "[0]")


def test_function_argument_paths() -> None:
    @Function
    def function(
        first: int,
        /,
        second: Sequence[int],
        *,
        third: int = 0,
    ) -> None:
        pass

    with raises(ValidationError) as exception:
        function("invalid", second=())  # pyright: ignore[reportArgumentType]

    assert exception.value.path == (".first",)

    with raises(ValidationError) as exception:
        function(1, second=[1, "invalid"])  # pyright: ignore[reportArgumentType]

    assert exception.value.path == (".second", "[1]")

    with raises(ValidationError) as exception:
        function(1, second=(), third="invalid")  # pyright: ignore[reportArgumentType]

    assert exception.value.path == (".third",)


def test_validation_passes_through_non_errors() -> None:
    def interrupting(value: Any) -> Any:
        raise KeyboardInterrupt

    class Interrupted(State):
        value: Annotated[str, Validator(interrupting)]

    # only an `Exception` is a failure to report a position for - an interrupt
    # or a cancellation passes through untouched instead of being reported as
    # a validation failure of the attribute it happened to pass through
    with raises(KeyboardInterrupt):
        Interrupted(value="a")

    with raises(KeyboardInterrupt):
        Interrupted(value="a").updating(value="b")


def test_missing_required_typed_dict_key_reports_the_key() -> None:
    with raises(ValidationError) as exception:
        Root(**root_payload(middle=middle_payload(typed={})))

    assert exception.value.path == (".middle", ".typed", '["number"]')
    assert isinstance(exception.value.cause, KeyError)


def test_prefixed_extends_the_path_keeping_the_cause() -> None:
    cause = ValueError("refused")
    error = ValidationError(
        path=("[2]",),
        cause=cause,
    )

    extended = error.prefixed(".field")

    assert extended.path == (".field", "[2]")
    assert extended.cause is cause
    assert str(extended) == "Validation of .field[2] failed: refused"
    # the reported error is left alone
    assert error.path == ("[2]",)


def test_reported_validation_error_is_extended_rather_than_nested() -> None:
    def reject(value: Any) -> int:
        raise ValidationError(
            path=("[2]",),
            cause=ValueError("refused"),
        )

    class Validated(State):
        value: Annotated[int, Validator(reject)]

    with raises(ValidationError) as exception:
        Validated(value=1)

    assert exception.value.path == (".value", "[2]")
    assert isinstance(exception.value.cause, ValueError)
