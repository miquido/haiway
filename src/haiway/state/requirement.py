import re
import unicodedata
from collections.abc import Callable, Collection, Iterable, Sequence, Set
from typing import Any, Literal, Self, cast, final

from haiway.state.path import AttributePath

__all__ = ("AttributeRequirement",)


@final
class AttributeRequirement[Root]:
    """
    Represents a requirement or constraint on an attribute value.

    This class provides a way to define and check constraints on attribute values
    within State objects. It supports various comparison operations like equality,
    containment, and logical combinations of requirements.

    The class is generic over the Root type, which is the type of object that
    contains the attribute being constrained.

    Requirements can be combined using logical operators:
    - & (AND): Both requirements must be met
    - | (OR): At least one requirement must be met
    """

    @classmethod
    def equal[Parameter](
        cls,
        value: Parameter,
        /,
        path: AttributePath[Root, Parameter] | Parameter,
    ) -> Self:
        """
        Create a requirement that an attribute equals a specific value.

        Parameters
        ----------
        value : Parameter
            The value to check equality against
        path : AttributePath[Root, Parameter] | Parameter
            The path to the attribute to check

        Returns
        -------
        Self
            A new requirement instance

        Raises
        ------
        AssertionError
            If path is not an AttributePath
        """
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare attribute path by using Self._.path.to.property or explicitly"

        def check_equal(root: Root) -> None:
            checked: Any = cast(AttributePath[Root, Parameter], path)(root)
            if checked != value:
                raise ValueError(f"{checked} is not equal {value} for '{path.__repr__()}'")

        return cls(
            path,
            "equal",
            value,
            check=check_equal,
        )

    @classmethod
    def text_match[Parameter](
        cls,
        value: str,
        /,
        path: AttributePath[Root, str] | str,
    ) -> Self:
        """
        Create a requirement that performs text matching on an attribute.

        Parameters
        ----------
        value : str
            The search term (can contain multiple words separated by spaces/punctuation)
        path : AttributePath[Root, str] | str
            The path to the string attribute to search in

        Returns
        -------
        Self
            A new requirement instance

        Raises
        ------
        AssertionError
            If path is not an AttributePath
        """
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare attribute path by using Self._.path.to.property or explicitly"

        def check_text_match(root: Root) -> None:
            checked: Any = path(root)
            if not isinstance(checked, str):
                raise ValueError(
                    f"Attribute value must be a string for like operation, got {type(checked)}"
                    f" for '{path.__repr__()}'"
                )

            # Perform full text search with proper Unicode support and word boundaries
            def tokenize_text(text: str) -> Sequence[str]:
                # Normalize and case-fold the text
                normalized = unicodedata.normalize("NFC", text).casefold()
                # Split on word boundaries and filter out empty strings
                # re.UNICODE handles international characters, multiline text works by default
                tokens = re.findall(r"\b\w+\b", normalized, re.UNICODE)
                return tokens

            # Tokenize both search terms and target text
            search_tokens: Sequence[str] = tokenize_text(value)
            target_tokens: Sequence[str] = tokenize_text(checked)
            target_tokens_set: Set[str] = set(target_tokens)

            # Check if all search tokens are found as complete words in target text
            missing_terms = [
                original_term
                for original_term, token in zip(
                    value.split(),
                    search_tokens,
                    strict=False,
                )
                if token not in target_tokens_set
            ]

            if missing_terms:
                raise ValueError(
                    f"Text search failed: '{checked}' is not like '{value}'. "
                    f"Missing tokens: {missing_terms} for '{path.__repr__()}'"
                )

        return cls(
            path,
            "text_match",
            value,
            check=check_text_match,
        )

    @classmethod
    def not_equal[Parameter](
        cls,
        value: Parameter,
        /,
        path: AttributePath[Root, Parameter] | Parameter,
    ) -> Self:
        """
        Create a requirement that an attribute does not equal a specific value.

        Parameters
        ----------
        value : Parameter
            The value to check inequality against
        path : AttributePath[Root, Parameter] | Parameter
            The path to the attribute to check

        Returns
        -------
        Self
            A new requirement instance

        Raises
        ------
        AssertionError
            If path is not an AttributePath
        """
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare attribute path by using Self._.path.to.property or explicitly"

        def check_not_equal(root: Root) -> None:
            checked: Any = cast(AttributePath[Root, Parameter], path)(root)
            if checked == value:
                raise ValueError(f"{checked} is equal {value} for '{path.__repr__()}'")

        return cls(
            path,
            "not_equal",
            value,
            check=check_not_equal,
        )

    @classmethod
    def contains[Parameter](
        cls,
        value: Parameter,
        /,
        path: AttributePath[
            Root,
            Collection[Parameter] | tuple[Parameter, ...] | list[Parameter] | set[Parameter],
        ]
        | Collection[Parameter]
        | tuple[Parameter, ...]
        | list[Parameter]
        | set[Parameter],
    ) -> Self:
        """
        Create a requirement that a collection attribute contains a specific value.

        Parameters
        ----------
        value : Parameter
            The value that should be contained in the collection
        path : AttributePath[Root, Collection[Parameter] | ...] | Collection[Parameter] | ...
            The path to the collection attribute to check

        Returns
        -------
        Self
            A new requirement instance

        Raises
        ------
        AssertionError
            If path is not an AttributePath
        """
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare attribute path by using Self._.path.to.property or explicitly"

        def check_contains(root: Root) -> None:
            checked: Any = cast(AttributePath[Root, Parameter], path)(root)
            if value not in checked:
                raise ValueError(f"{checked} does not contain {value} for '{path.__repr__()}'")

        return cls(
            path,
            "contains",
            value,
            check=check_contains,
        )

    @classmethod
    def contains_any[Parameter](
        cls,
        value: Collection[Parameter],
        /,
        path: AttributePath[
            Root,
            Collection[Parameter] | tuple[Parameter, ...] | list[Parameter] | set[Parameter],
        ]
        | Collection[Parameter]
        | tuple[Parameter, ...]
        | list[Parameter]
        | set[Parameter],
    ) -> Self:
        """
        Create a requirement that a collection attribute contains any of the specified values.

        Parameters
        ----------
        value : Collection[Parameter]
            The collection of values, any of which should be contained
        path : AttributePath[Root, Collection[Parameter] | ...] | Collection[Parameter] | ...
            The path to the collection attribute to check

        Returns
        -------
        Self
            A new requirement instance

        Raises
        ------
        AssertionError
            If path is not an AttributePath
        """
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare attribute path by using Self._.path.to.property or explicitly"

        def check_contains_any(root: Root) -> None:
            checked: Any = cast(AttributePath[Root, Parameter], path)(root)
            if not any(element in checked for element in value):
                raise ValueError(
                    f"{checked} does not contain any of {value} for '{path.__repr__()}'"
                )

        return cls(
            path,
            "contains_any",
            value,
            check=check_contains_any,
        )

    @classmethod
    def contained_in[Parameter](
        cls,
        value: Collection[Parameter],
        /,
        path: AttributePath[Root, Parameter] | Parameter,
    ) -> Self:
        """
        Create a requirement that an attribute value is contained in a specific collection.

        Parameters
        ----------
        value : Collection[Parameter]
            The collection that should contain the attribute value
        path : AttributePath[Root, Parameter] | Parameter
            The path to the attribute to check

        Returns
        -------
        Self
            A new requirement instance

        Raises
        ------
        AssertionError
            If path is not an AttributePath
        """
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare attribute path by using Self._.path.to.property or explicitly"

        def check_contained_in(root: Root) -> None:
            checked: Any = cast(AttributePath[Root, Parameter], path)(root)
            if checked not in value:
                raise ValueError(f"{value} does not contain {checked} for '{path.__repr__()}'")

        return cls(
            value,
            "contained_in",
            path,
            check=check_contained_in,
        )

    __slots__ = (
        "_check",
        "lhs",
        "operator",
        "rhs",
    )

    def __init__(
        self,
        lhs: Any,
        operator: Literal[
            "equal",
            "text_match",
            "not_equal",
            "contains",
            "contains_any",
            "contained_in",
            "and",
            "or",
        ],
        rhs: Any,
        check: Callable[[Root], None],
    ) -> None:
        """
        Initialize a new attribute requirement.

        Parameters
        ----------
        lhs : Any
            The left-hand side of the requirement (typically a path or value)
        operator : Literal["equal", "not_equal", "contains", "contains_any", "contained_in", "and", "or"]
            The operator that defines the type of requirement
        rhs : Any
            The right-hand side of the requirement (typically a value or path)
        check : Callable[[Root], None]
            A function that validates the requirement, raising ValueError if not met
        """  # noqa: E501
        self.lhs: Any
        object.__setattr__(
            self,
            "lhs",
            lhs,
        )
        self.operator: Literal[
            "equal",
            "text_match",
            "not_equal",
            "contains",
            "contains_any",
            "contained_in",
            "and",
            "or",
        ]
        object.__setattr__(
            self,
            "operator",
            operator,
        )
        self.rhs: Any
        object.__setattr__(
            self,
            "rhs",
            rhs,
        )
        self._check: Callable[[Root], None]
        object.__setattr__(
            self,
            "_check",
            check,
        )

    def __and__(
        self,
        other: Self,
    ) -> Self:
        """
        Combine this requirement with another using logical AND.

        Creates a new requirement that is satisfied only if both this requirement
        and the other requirement are satisfied.

        Parameters
        ----------
        other : Self
            Another requirement to combine with this one

        Returns
        -------
        Self
            A new requirement representing the logical AND of both requirements
        """

        def check_and(root: Root) -> None:
            self.check(root)
            other.check(root)

        return self.__class__(
            self,
            "and",
            other,
            check=check_and,
        )

    def __or__(
        self,
        other: Self,
    ) -> Self:
        """
        Combine this requirement with another using logical OR.

        Creates a new requirement that is satisfied if either this requirement
        or the other requirement is satisfied.

        Parameters
        ----------
        other : Self
            Another requirement to combine with this one

        Returns
        -------
        Self
            A new requirement representing the logical OR of both requirements
        """

        def check_or(root: Root) -> None:
            try:
                self.check(root)
            except ValueError:
                other.check(root)

        return self.__class__(
            self,
            "or",
            other,
            check=check_or,
        )

    def check(
        self,
        root: Root,
        /,
        *,
        raise_exception: bool = True,
    ) -> bool:
        """
        Check if the requirement is satisfied by the given root object.

        Parameters
        ----------
        root : Root
            The object to check the requirement against
        raise_exception : bool, default=True
            If True, raises an exception when the requirement is not met

        Returns
        -------
        bool
            True if the requirement is satisfied, False otherwise

        Raises
        ------
        ValueError
            If the requirement is not satisfied and raise_exception is True
        """
        try:
            self._check(root)
            return True

        except Exception as exc:
            if raise_exception:
                raise exc

            else:
                return False

    def filter(
        self,
        values: Iterable[Root],
    ) -> list[Root]:
        """
        Filter an iterable of values, keeping only those that satisfy this requirement.

        Parameters
        ----------
        values : Iterable[Root]
            The values to filter

        Returns
        -------
        list[Root]
            A list containing only the values that satisfy this requirement
        """
        return [value for value in values if self.check(value, raise_exception=False)]

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
