from collections.abc import Callable, Collection, Mapping, MutableMapping, Sequence, Set
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum
from pathlib import Path
from re import Pattern
from types import EllipsisType, NoneType, UnionType
from typing import Any, Literal, Protocol, Self, Union, final, is_typeddict
from uuid import UUID

from haiway.state.attributes import AttributeAnnotation
from haiway.types import MISSING, Missing

__all__ = (
    "AttributeValidation",
    "AttributeValidationError",
    "AttributeValidator",
)


class AttributeValidation[Type](Protocol):
    """
    Protocol defining the interface for attribute validation functions.

    These functions validate and potentially transform input values to
    ensure they conform to the expected type or format.
    """

    def __call__(
        self,
        value: Any,
        /,
    ) -> Type: ...


class AttributeValidationError(Exception):
    """
    Exception raised when attribute validation fails.

    This exception indicates that a value failed to meet the
    validation requirements for an attribute.
    """

    pass


@final
class AttributeValidator[Type]:
    """
    Creates and manages validation functions for attribute types.

    This class is responsible for creating appropriate validation functions
    based on type annotations. It handles various types including primitives,
    containers, unions, and custom types like State classes.
    """

    @classmethod
    def of(
        cls,
        annotation: AttributeAnnotation,
        /,
        *,
        recursion_guard: MutableMapping[str, AttributeValidation[Any]],
    ) -> AttributeValidation[Any]:
        """
        Create a validation function for the given type annotation.

        This method analyzes the type annotation and creates an appropriate
        validation function that can validate and transform values to match
        the expected type.

        Parameters
        ----------
        annotation : AttributeAnnotation
            The type annotation to create a validator for
        recursion_guard : MutableMapping[str, AttributeValidation[Any]]
            A mapping used to detect and handle recursive types

        Returns
        -------
        AttributeValidation[Any]
            A validation function for the given type

        Raises
        ------
        TypeError
            If the annotation represents an unsupported type
        """
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

        elif isinstance(annotation.origin, type):
            # Handle arbitrary types as valid type annotations
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
        """
        Initialize a new attribute validator.

        Parameters
        ----------
        annotation : AttributeAnnotation
            The type annotation this validator is for
        validation : AttributeValidation[Type] | Missing
            The validation function, or MISSING if not yet set
        """
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
        """
        Validate a value against this validator's type annotation.

        Parameters
        ----------
        value : Any
            The value to validate

        Returns
        -------
        Any
            The validated and potentially transformed value

        Raises
        ------
        AssertionError
            If the validation function is not set
        Exception
            If validation fails
        """
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
    """
    Create a validator for the Any type.

    Since Any accepts any value, this validator simply returns the input value unchanged.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The Any type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion (unused for Any)

    Returns
    -------
    AttributeValidation[Any]
        A validator that accepts any value
    """

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
    """
    Create a validator for the None type.

    This validator only accepts None values.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The None type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion (unused for None)

    Returns
    -------
    AttributeValidation[Any]
        A validator that accepts only None
    """

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
    """
    Create a validator for the Missing type.

    This validator only accepts the MISSING sentinel value.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The Missing type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion (unused for Missing)

    Returns
    -------
    AttributeValidation[Any]
        A validator that accepts only the MISSING sentinel
    """

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
    """
    Create a validator for Literal types.

    This validator checks if the value is one of the literal values
    specified in the type annotation.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The Literal type annotation containing allowed values
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion (unused for Literal)

    Returns
    -------
    AttributeValidation[Any]
        A validator that accepts only the specified literal values
    """
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
    """
    Create a validator for simple types.

    This validator checks if the value is an instance of the specified type.
    Used for primitive types, enums, and custom classes.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion (unused for simple types)

    Returns
    -------
    AttributeValidation[Any]
        A validator that checks instance type
    """
    validated_type: type[Any] = annotation.origin
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        if isinstance(value, validated_type):
            return value

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_set(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    """
    Create a validator for set and frozenset types.

    This validator checks if the value is a set and validates each element
    according to the set's element type.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The set type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion for recursive types

    Returns
    -------
    AttributeValidation[Any]
        A validator that validates sets and their elements
    """
    element_validator: AttributeValidation[Any] = AttributeValidator.of(
        annotation.arguments[0],
        recursion_guard=recursion_guard,
    )
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        if isinstance(value, Set):
            return frozenset(element_validator(element) for element in value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_sequence(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    """
    Create a validator for sequence types.

    This validator checks if the value is a sequence and validates each element
    according to the sequence's element type. Sequences are converted to tuples.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The sequence type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion for recursive types

    Returns
    -------
    AttributeValidation[Any]
        A validator that validates sequences and their elements
    """
    element_validator: AttributeValidation[Any] = AttributeValidator.of(
        annotation.arguments[0],
        recursion_guard=recursion_guard,
    )
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        if isinstance(value, Sequence) and not isinstance(value, str | bytes):
            return tuple(element_validator(element) for element in value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_collection(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    """
    Create a validator for collection types.

    This validator checks if the value is a collection and validates each element
    according to the collection's element type. Collections are converted to tuples.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The collection type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion for recursive types

    Returns
    -------
    AttributeValidation[Any]
        A validator that validates collections and their elements
    """
    element_validator: AttributeValidation[Any] = AttributeValidator.of(
        annotation.arguments[0],
        recursion_guard=recursion_guard,
    )
    formatted_type: str = str(annotation)

    def validator(
        value: Any,
        /,
    ) -> Any:
        if isinstance(value, Collection) and not isinstance(value, str | bytes):
            return tuple(element_validator(element) for element in value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_mapping(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    """
    Create a validator for mapping types.

    This validator checks if the value is a mapping and validates each key and value
    according to their respective types.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The mapping type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion for recursive types

    Returns
    -------
    AttributeValidation[Any]
        A validator that validates mappings with their keys and values
    """
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
        if isinstance(value, Mapping):
            # TODO: make sure dict is not mutable with MappingProxyType?
            return {key_validator(key): value_validator(element) for key, element in value.items()}

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

    return validator


def _prepare_validator_of_tuple(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    """
    Create a validator for tuple types.

    This validator handles both fixed-length tuples (with specific types for each position)
    and variable-length tuples (with a repeating element type and Ellipsis).

    Parameters
    ----------
    annotation : AttributeAnnotation
        The tuple type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion for recursive types

    Returns
    -------
    AttributeValidation[Any]
        A validator that validates tuples based on their type specification
    """
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
            if isinstance(value, Collection) and not isinstance(value, str | bytes):
                return tuple(element_validator(element) for element in value)

            else:
                raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

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
            if isinstance(value, Sequence):
                if len(value) != elements_count:
                    raise ValueError(
                        f"'{value}' is not matching expected type of '{formatted_type}'"
                    )

                return tuple(element_validators[idx](element) for idx, element in enumerate(value))

            else:
                raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

        return validator


def _prepare_validator_of_union(
    annotation: AttributeAnnotation,
    /,
    recursion_guard: MutableMapping[str, AttributeValidation[Any]],
) -> AttributeValidation[Any]:
    """
    Create a validator for union types.

    This validator tries to validate the value against each type in the union,
    and succeeds if any validation succeeds.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The union type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion for recursive types

    Returns
    -------
    AttributeValidation[Any]
        A validator that validates against any type in the union
    """
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
    """
    Create a validator for callable types.

    This validator checks if the value is callable, but does not
    validate the callable's signature.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The callable type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion (unused for callable)

    Returns
    -------
    AttributeValidation[Any]
        A validator that checks if values are callable
    """
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
    """
    Create a validator for TypedDict types.

    This validator checks if the value is a mapping with keys and values
    matching the TypedDict specification. Required keys must be present.

    Parameters
    ----------
    annotation : AttributeAnnotation
        The TypedDict type annotation
    recursion_guard : MutableMapping[str, AttributeValidation[Any]]
        Mapping to prevent infinite recursion for recursive types

    Returns
    -------
    AttributeValidation[Any]
        A validator that validates TypedDict structures
    """

    def key_validator(
        value: Any,
        /,
    ) -> Any:
        if isinstance(value, str):
            return value

        else:
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
        if isinstance(value, Mapping):
            validated: MutableMapping[str, Any] = {}
            for key, validator in values_validators.items():
                element: Any = value.get(key, MISSING)
                if element is MISSING and key not in required_values:
                    continue  # skip missing and not required

                validated[key_validator(key)] = validator(element)

            # TODO: make sure dict is not mutable with MappingProxyType?
            return validated

        else:
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
    Collection: _prepare_validator_of_collection,
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
