from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, Self, final
from uuid import UUID, uuid4

__all__ = ("ScopeIdentifier",)


@final
class ScopeIdentifier:
    """
    Identifies and manages scope context identities.

    ScopeIdentifier maintains a context-local scope identity including
    scope ID, and parent ID. It provides a hierarchical structure for tracking
    execution scopes, supporting both root scopes and nested child scopes.

    This class is immutable after instantiation.
    """

    _context = ContextVar[Self]("ScopeIdentifier")

    @classmethod
    def current(
        cls,
        /,
    ) -> Self:
        return cls._context.get()

    @classmethod
    def scope(
        cls,
        label: str,
        /,
    ) -> Self:
        """
        Create a new scope identifier.

        If called within an existing scope, creates a nested scope with a new ID.
        If called outside any scope, creates a root scope with new scope ID.

        Parameters
        ----------
        label: str
            The name of the scope

        Returns
        -------
        Self
            A newly created scope identifier
        """
        current: Self
        try:  # check for current scope
            current = cls._context.get()

        except LookupError:
            # create root scope when missing

            scope_id: UUID = uuid4()
            return cls(
                label=label,
                scope_id=scope_id,
                parent_id=scope_id,  # own id is parent_id for root
            )

        # create nested scope otherwise
        return cls(
            label=label,
            scope_id=uuid4(),
            parent_id=current.scope_id,
        )

    __slots__ = (
        "_token",
        "label",
        "parent_id",
        "scope_id",
        "unique_name",
    )

    def __init__(
        self,
        parent_id: UUID,
        scope_id: UUID,
        label: str,
    ) -> None:
        self.parent_id: UUID
        object.__setattr__(
            self,
            "parent_id",
            parent_id,
        )
        self.scope_id: UUID
        object.__setattr__(
            self,
            "scope_id",
            scope_id,
        )
        self.label: str
        object.__setattr__(
            self,
            "label",
            label,
        )
        self.unique_name: str
        object.__setattr__(
            self,
            "unique_name",
            f"[{label}] [{scope_id.hex}]",
        )
        self._token: Token[ScopeIdentifier] | None
        object.__setattr__(
            self,
            "_token",
            None,
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

    @property
    def is_root(self) -> bool:
        """
        Check if this scope is a root scope.

        A root scope is one that was created outside of any other scope.

        Returns
        -------
        bool
            True if this is a root scope, False if it's a nested scope
        """
        return self.scope_id == self.parent_id

    def __str__(self) -> str:
        return self.unique_name

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, self.__class__):
            return False

        return self.scope_id == other.scope_id

    def __hash__(self) -> int:
        return hash(self.scope_id)

    def __enter__(self) -> None:
        """
        Enter this scope identifier's context.

        Sets this identifier as the current scope identifier in the context.

        Raises
        ------
        AssertionError
            If this context is already active
        """
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        object.__setattr__(
            self,
            "_token",
            ScopeIdentifier._context.set(self),
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Exit this scope identifier's context.

        Restores the previous scope identifier in the context.

        Parameters
        ----------
        exc_type: type[BaseException] | None
            Type of exception that caused the exit
        exc_val: BaseException | None
            Exception instance that caused the exit
        exc_tb: TracebackType | None
            Traceback for the exception

        Raises
        ------
        AssertionError
            If this context is not active
        """
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ScopeIdentifier._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )
