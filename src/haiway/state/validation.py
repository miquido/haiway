from collections.abc import Callable, Mapping, MutableMapping, Sequence, Set
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from re import Pattern
from types import EllipsisType, NoneType, UnionType
from typing import Any, Literal, Protocol, Self, Union, final, is_typeddict
from uuid import UUID

from haiway.state.attributes import AttributeAnnotation
from haiway.types import MISSING, Missing

__all__ = [
    "AttributeValidation",
    "AttributeValidationError",
    "AttributeValidator",
]


class AttributeValidation[Type](Protocol):
    def __call__(
        self,
        value: Any,
        /,
    ) -> Type: ...


class AttributeValidationError(Exception):
    pass


@final
class AttributeValidator[Type]:
    @classmethod
    def of(
        cls,
        annotation: AttributeAnnotation,
        /,
        *,
        recursion_guard: MutableMapping[str, AttributeValidation[Any]],
    ) -> AttributeValidation[Any]:
        if isinstance(annotation.origin, NotImplementedError | RuntimeError):
            raise annotation.origin  # raise an error if origin was not properly resolved

        if recursive := recursion_guard.get(str(annotation)):
            return recursive

        validator: Self = cls(
            annotation,
            validation=MISSING,
        )
        recursion_guard[str(annotation)] = validator

        if common := VALIDATORS.get(annotation.origin):
            object.__setattr__(
                validator,
                "validation",
                common(annotation, recursion_guard),
            )

        elif hasattr(annotation.origin, "__IMMUTABLE__"):
            object.__setattr__(
                validator,
                "validation",
                _prepare_validator_of_type(annotation, recursion_guard),
            )

        elif is_typeddict(annotation.origin):
            object.__setattr__(
                validator,
                "validation",
                _prepare_validator_of_typed_dict(annotation, recursion_guard),
            )

        elif issubclass(annotation.origin, Protocol):
            object.__setattr__(
                validator,
                "validation",
                _prepare_validator_of_type(annotation, recursion_guard),
            )

        elif issubclass(annotation.origin, Enum):
            object.__setattr__(
                validator,
                "validation",
                _prepare_validator_of_type(annotation, recursion_guard),
            )

        else:
            raise TypeError(f"Unsupported type annotation: {annotation}")

        return validator

    __slots__ = (
        "annotation",
        "validation",
    )

    def __init__(
        self,
        annotation: AttributeAnnotation,
        validation: AttributeValidation[Type] | Missing,
    ) -> None:
        self.annotation: AttributeAnnotation
        object.__setattr__(
            self,
            "annotation",
            annotation,
        )
        self.validation: AttributeValidation[Type] | Missing
        object.__setattr__(
            self,
            "validation",
            validation,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    def __call__(
        self,
        value: Any,
        /,
    ) -> Any:
        assert self.validation is not MISSING  # nosec: B101
        return self.validation(value)  # pyright: ignore[reportCallIssue, reportUnknownVariableType]

    def __str__(self) -> str:
        return f"Validator[{self.annotation}]"

    def __repr__(self) -> str:
        return f"Validator[{self.annotation}]"


def _prepare_validator_of_any(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    def validator(
        value: Any,
        /,
    ) -> Any:
        return value  # any is always valid

    return validator


def _prepare_validator_of_none(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    def validator(
        value: Any,
        /,
    ) -> Any:
        if value is None:
            return value

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'None'")

    return validator


def _prepare_validator_of_missing(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    def validator(
        value: Any,
        /,
    ) -> Any:
        if value is MISSING:
            return value

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'Missing'")

    return validator


def _prepare_validator_of_literal(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    elements: Sequence[Any] = annotation.arguments
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        if value in elements:
            return value

        else:
            raise ValueError(f"'{value}' is not matching expected values of '{formatted_type}'")

    return validator


def _prepare_validator_of_type(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    validated_type: type[Any] = annotation.origin
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        match value:
            case value if isinstance(value, validated_type):
                return value

            case _:
                raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_set(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    element_validator: AttributeValidation[Any] = AttributeValidator.of(
        annotation.arguments[0],
        recursion_guard=recursion_guard,
    )
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        if isinstance(value, set):
            return frozenset(element_validator(element) for element in value)  # pyright: ignore[reportUnknownVariableType]

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_sequence(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    element_validator: AttributeValidation[Any] = AttributeValidator.of(
        annotation.arguments[0],
        recursion_guard=recursion_guard,
    )
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
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
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    key_validator: AttributeValidation[Any] = AttributeValidator.of(
        annotation.arguments[0],
        recursion_guard=recursion_guard,
    )
    value_validator: AttributeValidation[Any] = AttributeValidator.of(
        annotation.arguments[1],
        recursion_guard=recursion_guard,
    )
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        match value:
            case {**elements}:
                # TODO: make sure dict is not mutable with MappingProxyType?
                return {
                    key_validator(key): value_validator(element)
                    for key, element in elements.items()
                }

            case _:
                raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_tuple(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    if (
        annotation.arguments[-1].origin == Ellipsis
        or annotation.arguments[-1].origin == EllipsisType
    ):
        element_validator: AttributeValidation[Any] = AttributeValidator.of(
            annotation.arguments[0],
            recursion_guard=recursion_guard,
        )
        formatted_type: str = str(annotation)

        def validator(
            value: Any,
            /,
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
        element_validators: list[AttributeValidation[Any]] = [
            AttributeValidator.of(alternative, recursion_guard=recursion_guard)
            for alternative in annotation.arguments
        ]
        elements_count: int = len(element_validators)
        formatted_type: str = str(annotation)

        def validator(
            value: Any,
            /,
        ) -> Any:
            match value:
                case [*elements]:
                    if len(elements) != elements_count:
                        raise ValueError(
                            f"'{value}' is not matching expected type of '{formatted_type}'"
                        )

                    return tuple(
                        element_validators[idx](element) for idx, element in enumerate(elements)
                    )

                case _:
                    raise TypeError(
                        f"'{value}' is not matching expected type of '{formatted_type}'"
                    )

        return validator


def _prepare_validator_of_union(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    validators: list[AttributeValidation[Any]] = [
        AttributeValidator.of(alternative, recursion_guard=recursion_guard)
        for alternative in annotation.arguments
    ]
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
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
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        if callable(value):
            # TODO: we could verify callable signature here
            return value

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_typed_dict(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    def key_validator(
        value: Any,
        /,
    ) -> Any:
        match value:
            case value if isinstance(value, str):
                return value

            case _:
                raise TypeError(f"'{value}' is not matching expected type of 'str'")

    formatted_type: str = str(annotation)
    values_validators: dict[str, AttributeValidation[Any]] = {
        key: AttributeValidator.of(element, recursion_guard=recursion_guard)
        for key, element in annotation.extra["attributes"].items()
    }
    required_values: Set[str] = annotation.extra["required"]

    def validator(
        value: Any,
        /,
    ) -> Any:
        match value:
            case {**elements}:
                validated: MutableMapping[str, Any] = {}
                for key, validator in values_validators.items():
                    element: Any = elements.get(key, MISSING)
                    if element is MISSING and key not in required_values:
                        continue  # skip missing and not required

                    validated[key_validator(key)] = validator(element)

                # TODO: make sure dict is not mutable with MappingProxyType?
                return validated

            case _:
                raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


VALIDATORS: Mapping[
    Any,
    Callable[
        [
            AttributeAnnotation,
            MutableMapping[str, AttributeValidation[Any]],
        ],
        AttributeValidation[Any],
    ],
] = {
    Any: _prepare_validator_of_any,
    NoneType: _prepare_validator_of_none,
    Missing: _prepare_validator_of_missing,
    type: _prepare_validator_of_type,
    bool: _prepare_validator_of_type,
    int: _prepare_validator_of_type,
    float: _prepare_validator_of_type,
    complex: _prepare_validator_of_type,
    bytes: _prepare_validator_of_type,
    str: _prepare_validator_of_type,
    tuple: _prepare_validator_of_tuple,
    frozenset: _prepare_validator_of_set,
    Set: _prepare_validator_of_set,
    Sequence: _prepare_validator_of_sequence,
    Mapping: _prepare_validator_of_mapping,
    Literal: _prepare_validator_of_literal,
    range: _prepare_validator_of_type,
    UUID: _prepare_validator_of_type,
    date: _prepare_validator_of_type,
    datetime: _prepare_validator_of_type,
    time: _prepare_validator_of_type,
    timedelta: _prepare_validator_of_type,
    timezone: _prepare_validator_of_type,
    Path: _prepare_validator_of_type,
    Pattern: _prepare_validator_of_type,
    Union: _prepare_validator_of_union,
    UnionType: _prepare_validator_of_union,
    Callable: _prepare_validator_of_callable,
}
