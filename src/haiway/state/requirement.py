from collections.abc import Callable, Collection, Iterable
from typing import Any, Literal, Self, cast, final

from haiway.state.path import AttributePath

__all__ = [
    "AttributeRequirement",
]


@final
class AttributeRequirement[Root]:
    @classmethod
    def equal[Parameter](
        cls,
        value: Parameter,
        /,
        path: AttributePath[Root, Parameter] | Parameter,
    ) -> Self:
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
    def not_equal[Parameter](
        cls,
        value: Parameter,
        /,
        path: AttributePath[Root, Parameter] | Parameter,
    ) -> Self:
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
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare attribute path by using Self._.path.to.property or explicitly"

        def check_contains_any(root: Root) -> None:
            checked: Any = cast(AttributePath[Root, Parameter], path)(root)
            if any(element in checked for element in value):
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
        self.lhs: Any
        object.__setattr__(
            self,
            "lhs",
            lhs,
        )
        self.operator: Literal[
            "equal",
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
