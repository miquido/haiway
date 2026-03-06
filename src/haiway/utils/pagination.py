from collections.abc import Iterable, Iterator, Sequence
from typing import Any, NoReturn, Self, SupportsIndex, final, overload
from uuid import UUID

from haiway.attributes import State
from haiway.types import BasicObject, BasicValue

__all__ = (
    "Paginated",
    "Pagination",
    "PaginationToken",
)


type PaginationToken = UUID | str | int


@final
class Pagination(State, serializable=True):
    """
    Immutable pagination request state.

    Stores the provider pagination token, page size limit, and additional
    provider-specific arguments passed between page fetches.
    """

    @classmethod
    def of(
        cls,
        *,
        token: PaginationToken | None = None,
        limit: int,
        **arguments: BasicValue,
    ) -> Self:
        """
        Construct pagination state from explicit arguments.

        Parameters
        ----------
        token : PaginationToken | None, optional
            Pagination cursor/token returned by the provider. ``None`` indicates
            the first page request.
        limit : int
            Maximum number of elements requested per page.
        **arguments : BasicValue
            Additional provider-specific query arguments.

        Returns
        -------
        Self
            New immutable pagination state.
        """
        return cls(
            token=token,
            limit=limit,
            arguments=arguments,
        )

    token: PaginationToken | None = None
    limit: int
    arguments: BasicObject

    @property
    def has_token(self) -> bool:
        """
        Check whether this request carries a pagination token.

        Returns
        -------
        bool
            ``True`` when ``token`` is present, otherwise ``False``.
        """
        return self.token is not None

    def with_token(
        self,
        token: PaginationToken | None,
        /,
    ) -> Self:
        """
        Return a copy with updated pagination token.

        Parameters
        ----------
        token : PaginationToken | None
            New pagination cursor/token.

        Returns
        -------
        Self
            Updated immutable pagination state.
        """
        return self.updating(token=token)

    def with_limit(
        self,
        limit: int,
        /,
    ) -> Self:
        """
        Return a copy with updated page size limit.

        Parameters
        ----------
        limit : int
            New maximum number of elements requested per page.

        Returns
        -------
        Self
            Updated immutable pagination state.
        """
        return self.updating(limit=limit)

    def with_arguments(
        self,
        /,
        **arguments: BasicValue,
    ) -> Self:
        """
        Return a copy with merged provider-specific arguments.

        Parameters
        ----------
        **arguments : BasicValue
            Arguments merged into existing ``arguments``. New keys override
            existing keys with the same name.

        Returns
        -------
        Self
            Same instance when no arguments are provided, otherwise an updated
            immutable pagination state.
        """
        if not arguments:
            return self

        return self.updating(arguments={**self.arguments, **arguments})


@final
class Paginated[Element](Sequence[Element]):
    """
    Immutable page of elements with pagination metadata.

    Behaves as a read-only sequence and keeps the original request/response
    pagination information needed to fetch subsequent pages.
    """

    @classmethod
    def of(
        cls,
        items: Iterable[Element],
        /,
        *,
        pagination: Pagination,
    ) -> Self:
        """
        Construct an immutable page from iterable items and pagination state.

        Parameters
        ----------
        items : Iterable[Element]
            Elements contained in the page.
        pagination : Pagination
            Pagination metadata associated with this page.

        Returns
        -------
        Self
            New immutable paginated result.
        """
        return cls(
            items=items,
            pagination=pagination,
        )

    __slots__ = (
        "items",
        "pagination",
    )

    def __init__(
        self,
        *,
        items: Iterable[Element],
        pagination: Pagination,
    ) -> None:
        """
        Initialize a paginated result.

        Parameters
        ----------
        items : Iterable[Element]
            Source iterable of page elements, coerced to an immutable tuple.
        pagination : Pagination
            Pagination metadata associated with ``items``.
        """
        self.items: tuple[Element, ...]
        object.__setattr__(
            self,
            "items",
            tuple(items),
        )
        self.pagination: Pagination
        object.__setattr__(
            self,
            "pagination",
            pagination,
        )

    @property
    def token(self) -> PaginationToken | None:
        """
        Return pagination token for the next page, if available.

        Returns
        -------
        PaginationToken | None
            Provider token/cursor or ``None`` when not provided.
        """
        return self.pagination.token

    @property
    def has_next_page(
        self,
    ) -> bool:
        """
        Check whether another page may exist.

        Returns
        -------
        bool
            ``True`` when the provider returned a token or when the page is at
            least as large as ``pagination.limit``.
        """
        # If provider does not return a token, assume a full page may have a continuation.
        return self.token is not None or len(self.items) >= self.pagination.limit

    @overload
    def __getitem__(
        self,
        index: SupportsIndex,
    ) -> Element: ...

    @overload
    def __getitem__(
        self,
        index: slice,
    ) -> tuple[Element, ...]: ...

    def __getitem__(
        self,
        index: SupportsIndex | slice,
    ) -> Element | tuple[Element, ...]:
        return self.items.__getitem__(index)

    def __iter__(self) -> Iterator[Element]:
        return self.items.__iter__()

    def __len__(self) -> int:
        return self.items.__len__()

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be deleted"
        )

    def __str__(self) -> str:
        attributes: str = ", ".join(f"{name}: {getattr(self, name)}" for name in self.__slots__)
        return f"{self.__class__.__name__}({attributes})"

    def __repr__(self) -> str:
        return str(self)

    def __copy__(self) -> Self:
        return self  # Immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        return self  # Immutable, no need to provide an actual copy
