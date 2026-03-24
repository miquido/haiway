from collections.abc import Sequence
from contextvars import ContextVar, Token
from types import TracebackType
from typing import (
    Any,
    ClassVar,
    NoReturn,
    Protocol,
    Self,
    final,
    runtime_checkable,
)

__all__ = (
    "Validating",
    "ValidationContext",
    "ValidationError",
    "Validator",
    "Verifier",
    "Verifying",
)


@runtime_checkable
class Validating[Type](Protocol):
    """
    Protocol defining the interface for pre-validation callables.

    A ``Validating`` callable receives the raw incoming value before the base
    attribute validator runs. It may coerce, normalize, or reject the input by
    raising an exception.
    """

    def __call__(
        self,
        value: Any,
    ) -> Type: ...


@runtime_checkable
class Verifying[Type](Protocol):
    """
    Protocol defining the interface for post-validation callables.

    A ``Verifying`` callable receives a value that has already been validated
    against the base attribute type. It can enforce additional invariants while
    preserving the typed result.
    """

    def __call__(
        self,
        value: Type,
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

        if isinstance(exc_val, Exception) and not isinstance(exc_val, ValidationError):
            raise ValidationError(
                path=self._path,
                cause=exc_val,
            ) from exc_val


class ValidationError(Exception):
    """
    Exception raised when validation fails.

    This exception wraps the original validation error together with the nested
    attribute path at which the failure occurred.
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


@final
class Validator[Type]:
    """
    Wrapper for a pre-validation callable used inside ``typing.Annotated``.

    ``Validator`` runs before the base attribute validation logic. Use it when
    you need to coerce or reject raw input values before type-specific
    validation happens.
    """

    __slots__ = ("validator",)

    def __init__(
        self,
        validator: Validating[Type],
        /,
    ) -> None:
        assert validator  # nosec: B101

        self.validator: Validating[Type]
        object.__setattr__(
            self,
            "validator",
            validator,
        )

    def __setattr__(
        self,
        __name: str,
        __value: Any,
    ) -> NoReturn:
        raise AttributeError("Validator can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> NoReturn:
        raise AttributeError("Validator can't be modified")


@final
class Verifier[Type]:
    """
    Wrapper for a post-validation callable used inside ``typing.Annotated``.

    ``Verifier`` runs after the base attribute validation logic. Use it when
    the value must already be typed before enforcing an additional invariant.
    """

    __slots__ = ("verifier",)

    def __init__(
        self,
        verifier: Verifying[Type],
        /,
    ) -> None:
        assert verifier  # nosec: B101

        self.verifier: Verifying[Type]
        object.__setattr__(
            self,
            "verifier",
            verifier,
        )

    def __setattr__(
        self,
        __name: str,
        __value: Any,
    ) -> NoReturn:
        raise AttributeError("Verifier can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> None:
        raise AttributeError("Verifier can't be modified")
