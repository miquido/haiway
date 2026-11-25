from typing import Annotated, Any, Literal

import pytest

from haiway import Missing, State, Validator, Verifier
from haiway._native import nstruct

type _Alias = int | Missing
type _Recursive = int | _Recursive | Missing | None


class _Plain(State):
    value: int


class _Validating(State):
    value: int

    @classmethod
    def validate(cls, value: Any) -> _Validating:
        return cls(value=1)


class _Validated:
    @classmethod
    def validate(cls, value: Any) -> _Validated:
        return cls()


class _Holder(State):
    anything: Any
    anything_object: object
    missing: Missing
    optional: int | Missing
    nullable: int | None
    alias: _Alias
    recursive: _Recursive
    literal: Literal[1, 2]
    literal_missing: Literal[1, Missing]
    plain: _Plain
    plain_optional: _Plain | Missing
    validating: _Validating
    validating_optional: _Validating | Missing
    validated: _Validated
    validated_optional: _Validated | Missing
    sequence: tuple[int, ...]
    validator: Annotated[int | Missing, Validator(lambda value: value)]
    verifier: Annotated[int | Missing, Verifier(lambda value: value)]
    verified_alias: Annotated[_Alias, Verifier(lambda value: value)]
    validated_any: Annotated[Any, Validator(lambda value: value)]
    verified_any: Annotated[Any, Verifier(lambda value: value)]
    inner_validator: Annotated[int, Validator(lambda value: value)] | Missing


OMITTABLE: frozenset[str] = frozenset(
    (
        "anything",
        "anything_object",
        "missing",
        "optional",
        "alias",
        "recursive",
        "literal_missing",
        "plain_optional",
        "validating_optional",
        "validated_optional",
        "inner_validator",
    )
)


@pytest.mark.parametrize("name", tuple(_Holder.__ANNOTATED__), ids=str)
def test_an_attribute_is_omittable_when_its_spec_accepts_missing(name: str) -> None:
    field = next(field for field in _Holder.__FIELDS__ if field.name == name)

    assert field.required is (name not in OMITTABLE)
    assert nstruct.accepts_missing(field.spec) is (name in OMITTABLE)


class _Described(State):
    literal: Literal[1, 2]
    literal_missing: Literal[1, 2, Missing]
    optional: int | Missing
    nullable: int | None
    required: int


class _Specified(State):
    literal: Literal[1, 2]
    optional: int | Missing
    nullable: int | None
    required: int


def test_the_signature_follows_the_spec() -> None:
    required: set[str] = {
        name
        for name, parameter in _Described.__signature__.parameters.items()
        if parameter.default is parameter.empty
    }

    assert required == {"literal", "nullable", "required"}


def test_the_schema_follows_the_spec() -> None:
    assert set(_Specified.__SPECIFICATION__["required"]) == {"literal", "nullable", "required"}
