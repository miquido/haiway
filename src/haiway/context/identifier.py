from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, Self, final
from uuid import uuid4

__all__ = ("ScopeIdentifier",)


@final
class ScopeIdentifier:
    """
    Identifies and manages scope context identities.

    ScopeIdentifier maintains a context-local scope identity including trace ID,
    scope ID, and parent ID. It provides a hierarchical structure for tracking
    execution scopes, supporting both root scopes and nested child scopes.

    This class is immutable after instantiation.
    """

    _context = ContextVar[Self]("ScopeIdentifier")

    @classmethod
    def current_trace_id(cls) -> str:
        """
        Get the trace ID of the current scope.

        Returns
        -------
        str
            The trace ID of the current scope context

        Raises
        ------
        RuntimeError
            If called outside of a scope context
        """
        try:
            return ScopeIdentifier._context.get().trace_id

        except LookupError as exc:
            raise RuntimeError("Attempting to access scope identifier outside of scope") from exc

    @classmethod
    def scope(
        cls,
        label: str,
        /,
    ) -> Self:
        """
        Create a new scope identifier.

        If called within an existing scope, creates a nested scope with a new ID
        but the same trace ID. If called outside any scope, creates a root scope
        with new trace and scope IDs.

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
            trace_id: str = uuid4().hex
            scope_id: str = uuid4().hex
            return cls(
                label=label,
                scope_id=scope_id,
                parent_id=scope_id,  # own id is parent_id for root
                trace_id=trace_id,
            )

        # create nested scope otherwise
        return cls(
            label=label,
            scope_id=uuid4().hex,
            parent_id=current.scope_id,
            trace_id=current.trace_id,
        )

    __slots__ = (
        "_token",
        "label",
        "parent_id",
        "scope_id",
        "trace_id",
        "unique_name",
    )

    def __init__(
        self,
        trace_id: str,
        parent_id: str,
        scope_id: str,
        label: str,
    ) -> None:
        self.trace_id: str
        object.__setattr__(
            self,
            "trace_id",
            trace_id,
        )
        self.parent_id: str
        object.__setattr__(
            self,
            "parent_id",
            parent_id,
        )
        self.scope_id: str
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
            f"[{trace_id}] [{label}] [{scope_id}]",
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
        return self.trace_id == self.parent_id

    def __str__(self) -> str:
        return self.unique_name

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, self.__class__):
            return False

        return self.scope_id == other.scope_id and self.trace_id == other.trace_id

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
