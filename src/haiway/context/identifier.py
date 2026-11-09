import os
from collections.abc import Sequence
from contextvars import ContextVar, Token
from random import Random
from types import TracebackType
from typing import Any, ClassVar, Literal, Self, final
from uuid import UUID

from haiway.context.types import ContextMissing
from haiway.utils.formatting import escape_controls

__all__ = (
    "ContextIdentifier",
    "ScopeKind",
    "random_identifier",
)

type ScopeKind = Literal["internal", "server", "client", "producer", "consumer"]

# the bits of a version 4 uuid which are not random: the version and the variant
_UUID4_CLEARED: int = ~((0xF000 << 64) | (0xC000 << 48)) & ((1 << 128) - 1)
_UUID4_FIXED: int = (0x4000 << 64) | (0x8000 << 48)


class _Identifiers:
    # a generator of its own - the one of the `random` module can be reseeded by the
    # application, and a repeated seed would repeat the identifiers. it is seeded by the
    # operating system, and again in a forked process - which would otherwise continue
    # the very sequence of its parent. reseeding there, rather than checking the process
    # on every identifier, keeps a system call off the way into every scope
    __slots__ = ("_generator",)

    def __init__(self) -> None:
        self._generator: Random = Random()  # nosec: B311

    def reseed(self) -> None:
        self._generator = Random()  # nosec: B311

    def bits(self) -> int:
        return self._generator.getrandbits(128)  # nosec: B311


_identifiers: _Identifiers = _Identifiers()
if hasattr(os, "register_at_fork"):  # a platform which can't fork has nothing to reseed
    os.register_at_fork(after_in_child=_identifiers.reseed)


def random_identifier() -> UUID:
    """
    Create a random version 4 UUID, the identifier of a scope or a trace.

    Identifiers tell scopes and traces apart, they are not secrets - so they are not
    drawn from the operating system the way ``uuid4`` does, which costs a system call
    each. A generator of their own is seeded from it, and again in any other process.

    Returns
    -------
    UUID
        New identifier.
    """
    return UUID(int=(_identifiers.bits() & _UUID4_CLEARED) | _UUID4_FIXED)


@final
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
        kind: ScopeKind = "internal",
    ) -> Self:
        # read without raising - a root scope, entered with no scope around it, is
        # entered for every request a server handles
        current: Self | None = cls._context.get(None)
        if current is None:  # create root scope when missing
            root_id: UUID = random_identifier()
            return cls(
                name=name,
                kind=kind,
                scope_id=root_id,
                parent_id=root_id,  # own id is parent_id for root
                path=(root_id,),
            )

        scope_id: UUID = random_identifier()
        return cls(
            name=name,
            kind=kind,
            scope_id=scope_id,
            # create nested scope
            parent_id=current.scope_id,
            path=(*current.path, scope_id),
        )

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextIdentifier")

    __slots__ = (
        "_token",
        "_unique_name",
        "kind",
        "name",
        "parent_id",
        "path",
        "scope_id",
    )

    def __init__(
        self,
        parent_id: UUID,
        scope_id: UUID,
        name: str,
        path: Sequence[UUID],
        kind: ScopeKind = "internal",
    ) -> None:
        self.parent_id: UUID = parent_id
        self.scope_id: UUID = scope_id
        # identifiers of this scope and all of its ancestors, root first
        self.path: Sequence[UUID] = path
        # the name labels every record produced within the scope, so control
        # characters are escaped here instead of in each observability backend
        self.name: str = escape_controls(name)
        # the role the scope plays in a trace, closed to what tracing systems expect
        self.kind: ScopeKind = kind
        self._unique_name: str | None = None
        self._token: Token[ContextIdentifier] | None = None

    @property
    def unique_name(self) -> str:
        # built on demand and kept - only log prefixes and error messages need it,
        # so a scope which records nothing never pays for formatting the UUID
        unique_name: str | None = self._unique_name
        if unique_name is None:
            unique_name = f"[{self.name}] [{self.scope_id}]"
            self._unique_name = unique_name

        return unique_name

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
