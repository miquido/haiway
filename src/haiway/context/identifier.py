from collections.abc import Sequence
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Any, ClassVar, Self, final
from uuid import UUID, uuid4

from haiway.context.types import ContextMissing
from haiway.utils.formatting import escape_controls

__all__ = ("ContextIdentifier",)


@final  # consider immutable
class ContextIdentifier:
    @classmethod
    def current(
        cls,
        /,
    ) -> Self:
        try:
            return cls._context.get()

        except LookupError:
            raise ContextMissing("Context identifier requested but not defined!") from None

    @classmethod
    def scope(
        cls,
        name: str,
        /,
    ) -> Self:
        try:  # check for current scope
            current: Self = cls._context.get()
            scope_id: UUID = uuid4()
            return cls(
                name=name,
                scope_id=scope_id,
                # create nested scope
                parent_id=current.scope_id,
                path=(*current.path, scope_id),
            )

        except LookupError:  # create root scope when missing
            root_id: UUID = uuid4()
            return cls(
                name=name,
                scope_id=root_id,
                parent_id=root_id,  # own id is parent_id for root
                path=(root_id,),
            )

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextIdentifier")

    __slots__ = (
        "_token",
        "name",
        "parent_id",
        "path",
        "scope_id",
        "unique_name",
    )

    def __init__(
        self,
        parent_id: UUID,
        scope_id: UUID,
        name: str,
        path: Sequence[UUID],
    ) -> None:
        self.parent_id: UUID = parent_id
        self.scope_id: UUID = scope_id
        # identifiers of this scope and all of its ancestors, root first
        self.path: Sequence[UUID] = path
        # the name labels every record produced within the scope, so control
        # characters are escaped here instead of in each observability backend
        self.name: str = escape_controls(name)
        self.unique_name: str = f"[{self.name}] [{scope_id}]"
        self._token: Token[ContextIdentifier] | None = None

    @property
    def is_root(self) -> bool:
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
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        self._token = ContextIdentifier._context.set(self)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ContextIdentifier._context.reset(self._token)
        self._token = None
