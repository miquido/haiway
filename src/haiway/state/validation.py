from collections.abc import Callable, Mapping, Sequence, Set
from enum import Enum
from types import MappingProxyType, NoneType, UnionType
from typing import Any, Literal, Protocol, Union
from uuid import UUID

from haiway.state.attributes import AttributeAnnotation
from haiway.types import MISSING, Missing

__all__ = [
    "attribute_validator",
]


def attribute_validator(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    if validator := VALIDATORS.get(annotation.origin):
        return validator(annotation)

    elif hasattr(annotation.origin, "__IMMUTABLE__"):
        return _prepare_validator_of_type(annotation)

    elif issubclass(annotation.origin, Protocol):
        return _prepare_validator_of_type(annotation)

    elif issubclass(annotation.origin, Enum):
        return _prepare_validator_of_type(annotation)

    else:
        raise TypeError(f"Unsupported type annotation: {annotation}")


def _prepare_validator_of_any(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    def validator(
        value: Any,
    ) -> Any:
        return value  # any is always valid

    return validator


def _prepare_validator_of_none(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    def validator(
        value: Any,
    ) -> Any:
        if value is None:
            return value

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'None'")

    return validator


def _prepare_validator_of_missing(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    def validator(
        value: Any,
    ) -> Any:
        if value is MISSING:
            return value

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'Missing'")

    return validator


def _prepare_validator_of_literal(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    elements: list[Any] = annotation.arguments
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
    ) -> Any:
        if value in elements:
            return value

        else:
            raise ValueError(f"'{value}' is not matching expected values of '{formatted_type}'")

    return validator


def _prepare_validator_of_type(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    validated_type: type[Any] = annotation.origin
    formatted_type: str = str(annotation)

    def type_validator(
        value: Any,
    ) -> Any:
        match value:
            case value if isinstance(value, validated_type):
                return value

            case _:
                raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return type_validator


def _prepare_validator_of_set(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    element_validator: Callable[[Any], Any] = attribute_validator(annotation.arguments[0])
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
    ) -> Any:
        if isinstance(value, Set):
            return frozenset(element_validator(element) for element in value)  # pyright: ignore[reportUnknownVariableType]

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_sequence(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    element_validator: Callable[[Any], Any] = attribute_validator(annotation.arguments[0])
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
    ) -> Any:
        match value:
            case [*elements]:
                return tuple(element_validator(element) for element in elements)

            case _:
                raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_mapping(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    key_validator: Callable[[Any], Any] = attribute_validator(annotation.arguments[0])
    value_validator: Callable[[Any], Any] = attribute_validator(annotation.arguments[1])
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
    ) -> Any:
        match value:
            case {**elements}:
                return MappingProxyType(
                    {key_validator(key): value_validator(value) for key, value in elements}
                )

            case _:
                raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_tuple(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    if annotation.arguments[-1].origin == Ellipsis:
        element_validator: Callable[[Any], Any] = attribute_validator(annotation.arguments[0])
        formatted_type: str = str(annotation)

        def validator(
            value: Any,
        ) -> Any:
            match value:
                case [*elements]:
                    return tuple(element_validator(element) for element in elements)

                case _:
                    raise TypeError(
                        f"'{value}' is not matching expected type of '{formatted_type}'"
                    )

        return validator

    else:
        element_validators: list[Callable[[Any], Any]] = [
            attribute_validator(alternative) for alternative in annotation.arguments
        ]
        elements_count: int = len(element_validators)
        formatted_type: str = str(annotation)

        def validator(
            value: Any,
        ) -> Any:
            match value:
                case [*elements]:
                    if len(elements) != elements_count:
                        raise ValueError(
                            f"'{value}' is not matching expected type of '{formatted_type}'"
                        )

                    return tuple(
                        element_validators[idx](value) for idx, value in enumerate(elements)
                    )

                case _:
                    raise TypeError(
                        f"'{value}' is not matching expected type of '{formatted_type}'"
                    )

        return validator


def _prepare_validator_of_union(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    validators: list[Callable[[Any], Any]] = [
        attribute_validator(alternative) for alternative in annotation.arguments
    ]
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
    ) -> Any:
        errors: list[Exception] = []
        for validator in validators:
            try:
                return validator(value)

            except Exception as exc:
                errors.append(exc)

        raise ExceptionGroup(
            f"'{value}' is not matching expected type of '{formatted_type}'",
            errors,
        )

    return validator


def _prepare_validator_of_callable(
    annotation: AttributeAnnotation,
    /,
) -> Callable[[Any], Any]:
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
    ) -> Any:
        if callable(value):
            return value

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


VALIDATORS: Mapping[Any, Callable[[AttributeAnnotation], Callable[[Any], Any]]] = {
    Any: _prepare_validator_of_any,
    NoneType: _prepare_validator_of_none,
    Missing: _prepare_validator_of_missing,
    type: _prepare_validator_of_type,
    bool: _prepare_validator_of_type,
    int: _prepare_validator_of_type,
    float: _prepare_validator_of_type,
    bytes: _prepare_validator_of_type,
    str: _prepare_validator_of_type,
    tuple: _prepare_validator_of_tuple,
    frozenset: _prepare_validator_of_set,
    Literal: _prepare_validator_of_literal,
    Set: _prepare_validator_of_set,
    Sequence: _prepare_validator_of_sequence,
    Mapping: _prepare_validator_of_mapping,
    UUID: _prepare_validator_of_type,
    Union: _prepare_validator_of_union,
    UnionType: _prepare_validator_of_union,
    Callable: _prepare_validator_of_callable,
}
