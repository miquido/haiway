from collections.abc import Sequence
from typing import (
    Any,
    NoReturn,
    Protocol,
    Self,
    final,
    runtime_checkable,
)

__all__ = (
    "Validating",
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


class ValidationError(Exception):
    """
    Exception raised when validation fails.

    This exception wraps the original validation error together with the nested
    attribute path at which the failure occurred.

    Attributes
    ----------
    path : Sequence[str]
        Position of the failure within the validated structure, outermost
        segment first, rendered by joining the segments - ``.field[2].nested``.
    cause : Exception
        The failure itself, always the original one rather than another
        ``ValidationError`` reporting it from further in.
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

    def prefixed(
        self,
        name: str,
        /,
    ) -> Self:
        """
        Report this failure from one position further out.

        Parameters
        ----------
        name : str
            Path segment enclosing the one already reported.

        Returns
        -------
        Self
            New error reporting the same cause at the extended path.
        """
        return self.__class__(
            path=(name, *self.path),
            cause=self.cause,
        )

    @classmethod
    def report(
        cls,
        name: str,
        /,
        cause: Exception,
    ) -> NoReturn:
        """
        Raise a failure as coming from the given position.

        Always raises - call it from the ``except`` clause of the validation
        which failed, so the position is spelled out only when there is a
        failure to report it for.

        Haiway reports its own attributes and elements through this. Reach for
        it within a ``Validating`` or ``Verifying`` callable of your own, where
        the nested position of a failure would otherwise be lost.

        Parameters
        ----------
        name : str
            Path segment naming the position which failed, i.e. ``".field"`` or
            ``"[2]"``.
        cause : Exception
            The failure to report.

        Raises
        ------
        ValidationError
            Reporting ``cause`` at ``name``, or at ``name`` followed by the path
            it was already reported at from further in.

        Examples
        --------
        >>> def validated_items(value: Any) -> Sequence[Item]:
        ...     items: list[Item] = []
        ...     for index, element in enumerate(value):
        ...         try:
        ...             items.append(Item.validate(element))
        ...
        ...         except Exception as exc:
        ...             ValidationError.report(f"[{index}]", exc)
        ...
        ...     return items
        """
        if isinstance(cause, cls):
            # already reported from further in - extend that path instead of
            # nesting another report of the same failure within it
            raise cause.prefixed(name) from cause.cause

        raise cls(
            path=(name,),
            cause=cause,
        ) from cause


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
    ) -> NoReturn:
        raise AttributeError("Verifier can't be modified")
