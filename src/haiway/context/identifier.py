from contextvars import ContextVar, Token
from types import TracebackType
from typing import Self, final
from uuid import uuid4

__all__ = [
    "ScopeIdentifier",
]


@final
class ScopeIdentifier:
    _context = ContextVar[Self]("ScopeIdentifier")

    @classmethod
    def scope(
        cls,
        label: str,
        /,
    ) -> Self:
        current: Self
        try:  # check for current scope
            current = cls._context.get()

        except LookupError:
            # create root scope when missing
            trace_id: str = uuid4().hex
            return cls(
                label=label,
                scope_id=uuid4().hex,
                parent_id=trace_id,  # trace_id is parent_id for root
                trace_id=trace_id,
            )

        # create nested scope otherwise
        return cls(
            label=label,
            scope_id=uuid4().hex,
            parent_id=current.scope_id,
            trace_id=current.trace_id,
        )

    def __init__(
        self,
        trace_id: str,
        parent_id: str,
        scope_id: str,
        label: str,
    ) -> None:
        self.trace_id: str = trace_id
        self.parent_id: str = parent_id
        self.scope_id: str = scope_id
        self.label: str = label
        self.unique_name: str = f"[{trace_id}] [{label}] [{scope_id}]"

    @property
    def is_root(self) -> bool:
        return self.trace_id == self.parent_id

    def __str__(self) -> str:
        return self.unique_name

    def __enter__(self) -> None:
        assert not hasattr(self, "_token"), "Context reentrance is not allowed"  # nosec: B101
        self._token: Token[ScopeIdentifier] = ScopeIdentifier._context.set(self)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert hasattr(self, "_token"), "Unbalanced context enter/exit"  # nosec: B101
        ScopeIdentifier._context.reset(self._token)
        del self._token
