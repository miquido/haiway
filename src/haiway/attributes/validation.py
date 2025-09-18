from collections.abc import Sequence
from contextvars import ContextVar, Token
from types import TracebackType
from typing import (
    Any,
    ClassVar,
    Protocol,
    Self,
    final,
    runtime_checkable,
)

__all__ = (
    "ValidationContext",
    "ValidationError",
    "Validator",
)


@runtime_checkable
class Validator[Type](Protocol):
    """
    Protocol defining the interface for validation functions.

    These functions validate and potentially transform input values to
    ensure they conform to the expected type or format.
    """

    def __call__(
        self,
        value: Any,
        /,
    ) -> Type: ...


@final
class ValidationContext:
    _context: ClassVar[ContextVar[Sequence[str]]] = ContextVar[Sequence[str]]("ValidationContext")

    @classmethod
    def scope(
        cls,
        name: str,
        /,
    ) -> Self:
        try:
            context: Sequence[str] = cls._context.get()
            return cls((*context, name))

        except LookupError:
            return cls((name,))

    __slots__ = (
        "_path",
        "_token",
    )

    def __init__(
        self,
        path: Sequence[str],
    ) -> None:
        self._path: Sequence[str] = path
        self._token: Token[Sequence[str]] | None = None

    def __str__(self) -> str:
        return "".join(self._path)

    def __enter__(self) -> None:
        assert self._token is None, "ValidatorContext reentrance is not allowed"  # nosec: B101
        self._token = ValidationContext._context.set(self._path)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101

        ValidationContext._context.reset(self._token)
        self._token = None

        if isinstance(exc_val, Exception) and exc_type is not ValidationError:
            raise ValidationError(
                path=self._path,
                cause=exc_val,
            ) from exc_val


class ValidationError(Exception):
    """
    Exception raised when validation fails.

    This exception indicates that a value failed to meet the
    validation requirements for an attribute or argument.
    """

    __slots__ = (
        "cause",
        "path",
    )

    def __init__(
        self,
        *,
        path: Sequence[str],
        cause: Exception,
    ) -> None:
        super().__init__(f"Validation of {''.join(path)} failed: {cause}")
        self.path: Sequence[str] = path
        self.cause: Exception = cause


# def _prepare_validator_of_collection(
#     annotation: AttributeAnnotation,
#     /,
#     recursion_guard: MutableMapping[str, Validator[Any]],
# ) -> Validator[Any]:
#     """
#     Create a validator for collection types.

#     This validator checks if the value is a collection and validates each element
#     according to the collection's element type. Collections are converted to tuples.

#     Parameters
#     ----------
#     annotation : AttributeAnnotation
#         The collection type annotation
#     recursion_guard : MutableMapping[str, AttributeValidation[Any]]
#         Mapping to prevent infinite recursion for recursive types

#     Returns
#     -------
#     AttributeValidation[Any]
#         A validator that validates collections and their elements
#     """
#     element_validator: Validator[Any] = AttributeValidator.of(
#         cast(AttributeAnnotation, annotation.arguments[0]),
#         recursion_guard=recursion_guard,
#     )
#     formatted_type: str = str(annotation)

#     def validator(
#         value: Any,
#         /,
#     ) -> Any:
#         if isinstance(value, Collection) and not isinstance(value, str | bytes):

#             def validated_elements() -> Generator[Any]:
#                 for idx, element in enumerate(value):
#                     with ValidationContext.scope(f"[{idx}]"):
#                         yield element_validator(element)

#             return tuple(validated_elements())

#         else:
#             raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

#     return validator


# def _prepare_validator_of_tuple(
#     annotation: AttributeAnnotation,
#     /,
#     recursion_guard: MutableMapping[str, Validator[Any]],
# ) -> Validator[Any]:
#     """
#     Create a validator for tuple types.

#     This validator handles both fixed-length tuples (with specific types for each position)
#     and variable-length tuples (with a repeating element type and Ellipsis).

#     Parameters
#     ----------
#     annotation : AttributeAnnotation
#         The tuple type annotation
#     recursion_guard : MutableMapping[str, AttributeValidation[Any]]
#         Mapping to prevent infinite recursion for recursive types

#     Returns
#     -------
#     AttributeValidation[Any]
#         A validator that validates tuples based on their type specification
#     """
#     if (
#         cast(AttributeAnnotation, annotation.arguments[-1]).origin == Ellipsis
#         or cast(AttributeAnnotation, annotation.arguments[-1]).origin == EllipsisType
#     ):
#         element_validator: Validator[Any] = AttributeValidator.of(
#             cast(AttributeAnnotation, annotation.arguments[0]),
#             recursion_guard=recursion_guard,
#         )
#         formatted_type: str = str(annotation)

#         def validator(
#             value: Any,
#             /,
#         ) -> Any:
#             if isinstance(value, Collection) and not isinstance(value, str | bytes):

#                 def validated_elements() -> Generator[Any]:
#                     for idx, element in enumerate(value):
#                         with ValidationContext.scope(f"[{idx}]"):
#                             yield element_validator(element)

#                 return tuple(validated_elements())

#             else:
#                 raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

#         return validator

#     else:
#         element_validators: list[Validator[Any]] = [
#             AttributeValidator.of(
#                 cast(AttributeAnnotation, alternative), recursion_guard=recursion_guard
#             )
#             for alternative in annotation.arguments
#         ]
#         elements_count: int = len(element_validators)
#         formatted_type: str = str(annotation)

#         def validator(
#             value: Any,
#             /,
#         ) -> Any:
#             if isinstance(value, Sequence):
#                 if len(value) != elements_count:
#                     raise ValueError(
#                         f"'{value}' is not matching expected type of '{formatted_type}'"
#                     )

#                 def validated_elements() -> Generator[Any]:
#                     for idx, element in enumerate(value):
#                         with ValidationContext.scope(f"[{idx}]"):
#                             yield element_validators[idx](element)

#                 return tuple(validated_elements())

#             else:
#                 raise TypeError(f"'{value}' is not matching expected type of '{formatted_type}'")

#         return validator
