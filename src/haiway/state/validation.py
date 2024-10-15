import types
import typing
from collections.abc import Callable, Sequence
from typing import Any

from haiway import types as _types
from haiway.state.attributes import AttributeAnnotation

__all__ = [
    "attribute_type_validator",
]


def attribute_type_validator(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    match annotation.origin:
        case types.NoneType:
            return _none_validator

        case _types.Missing:
            return _missing_validator

        case types.UnionType:
            return _prepare_union_validator(annotation.arguments)

        case typing.Literal:
            return _prepare_literal_validator(annotation.arguments)

        case typing.Any:
            return _any_validator

        case type() as other_type:
            return _prepare_type_validator(other_type)

        case other:
            raise TypeError(f"Unsupported type annotation: {other}")


def _none_validator(
    value: Any,
) -> Any:
    match value:
        case None:
            return None

        case _:
            raise TypeError(f"Type '{type(value)}' is not matching expected type 'None'")


def _missing_validator(
    value: Any,
) -> Any:
    match value:
        case _types.Missing():
            return _types.MISSING

        case _:
            raise TypeError(f"Type '{type(value)}' is not matching expected type 'Missing'")


def _any_validator(
    value: Any,
) -> Any:
    return value  # any is always valid


def _prepare_union_validator(
    elements: Sequence[AttributeAnnotation],
    /,
) -> Callable[[Any], Any]:
    validators: list[Callable[[Any], Any]] = [
        attribute_type_validator(alternative) for alternative in elements
    ]

    def union_validator(
        value: Any,
    ) -> Any:
        errors: list[Exception] = []
        for validator in validators:
            try:
                return validator(value)

            except Exception as exc:
                errors.append(exc)

        raise ExceptionGroup("Multiple errors", errors)

    return union_validator


def _prepare_literal_validator(
    elements: Sequence[Any],
    /,
) -> Callable[[Any], Any]:
    def literal_validator(
        value: Any,
    ) -> Any:
        if value in elements:
            return value

        else:
            raise ValueError(f"Value '{value}' is not matching expected '{elements}'")

    return literal_validator


def _prepare_type_validator(
    validated_type: type[Any],
    /,
) -> Callable[[Any], Any]:
    def type_validator(
        value: Any,
    ) -> Any:
        match value:
            case value if isinstance(value, validated_type):
                return value

            case _:
                raise TypeError(
                    f"Type '{type(value)}' is not matching expected type '{validated_type}'"
                )

    return type_validator
