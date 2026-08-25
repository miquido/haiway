from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Final,
    Literal,
    NoReturn,
    Protocol,
    Self,
    final,
    overload,
    runtime_checkable,
)
from uuid import UUID

from asyncpg import Record

if TYPE_CHECKING:
    from haiway.postgres.state import PostgresConnection

__all__ = (
    "PostgresConnectionAcquiring",
    "PostgresConnectionContext",
    "PostgresErrorCode",
    "PostgresException",
    "PostgresMigrating",
    "PostgresRow",
    "PostgresStatementExecuting",
    "PostgresStatementFetching",
    "PostgresTransactionContext",
    "PostgresTransactionIsolation",
    "PostgresTransactionPreparing",
    "PostgresValue",
)


class PostgresErrorCode(StrEnum):
    """SQLSTATE codes worth branching on, so call sites need no magic strings.

    PostgreSQL reports every error with a five character SQLSTATE - a two
    character class followed by a three character condition. Comparing against
    a literal works but fails silently when mistyped: the branch simply never
    matches and the error escapes as an unhandled failure. These members are
    the codes application code actually dispatches on.

    The enum is a ``StrEnum``, so a member compares equal to the raw
    ``PostgresException.sqlstate`` without conversion, and ``startswith`` on the
    two character class still works for handling a whole class at once.

    Notes
    -----
    This is not the full SQLSTATE table - PostgreSQL defines several hundred
    codes. Anything missing is still available as the raw string.
    """

    # class 08 - connection exception
    connection_failure = "08006"
    connection_does_not_exist = "08003"
    # class 23 - integrity constraint violation
    not_null_violation = "23502"
    foreign_key_violation = "23503"
    unique_violation = "23505"
    check_violation = "23514"
    exclusion_violation = "23P01"
    # class 25 - invalid transaction state
    read_only_sql_transaction = "25006"
    in_failed_sql_transaction = "25P02"
    # class 40 - transaction rollback
    serialization_failure = "40001"
    deadlock_detected = "40P01"
    # class 42 - syntax error or access rule violation
    insufficient_privilege = "42501"
    undefined_table = "42P01"
    duplicate_prepared_statement = "42P05"
    # class 53 - insufficient resources
    too_many_connections = "53300"
    # class 55 - object not in prerequisite state
    lock_not_available = "55P03"
    # class 57 - operator intervention
    query_canceled = "57014"
    admin_shutdown = "57P01"


RETRIABLE_ERROR_CODES: Final[frozenset[str]] = frozenset(
    (
        # both are transaction aborts PostgreSQL resolves by throwing one side
        # out rather than blocking, and both succeed when simply run again
        PostgresErrorCode.serialization_failure,
        PostgresErrorCode.deadlock_detected,
    )
)
"""SQLSTATE codes for which re-running the same work is expected to succeed."""


class PostgresException(Exception):
    """Raised when an operation through the Postgres adapter fails.

    The exception wraps lower-level driver failures so application code can
    handle database errors through a stable Haiway-specific type.

    Parameters
    ----------
    message : str
        Description of the failure.
    sqlstate : str | None, optional
        PostgreSQL SQLSTATE code, when the driver reported one.

    Attributes
    ----------
    sqlstate : str | None
        PostgreSQL SQLSTATE code, when known.

    Notes
    -----
    Statements and parameter values are deliberately excluded from the message
    so credentials and personal data are never surfaced through error handling.
    """

    def __init__(
        self,
        message: str,
        *,
        sqlstate: str | None = None,
    ) -> None:
        if sqlstate:
            super().__init__(f"{message}|sqlstate={sqlstate}")

        else:
            super().__init__(message)

        self.sqlstate: str | None = sqlstate

    @property
    def retriable(self) -> bool:
        """Whether re-running the same work is expected to succeed.

        True for a serialization failure or a detected deadlock. PostgreSQL
        resolves both by aborting one transaction rather than blocking, and both
        are expected to succeed when the work is simply run again.

        Notes
        -----
        This says nothing about *what* has to be re-run. A statement which was
        its own transaction can be repeated on its own, while a statement inside
        an explicit transaction cannot - the abort doomed the whole transaction,
        so the block has to be re-entered from the beginning.
        """

        return self.sqlstate in RETRIABLE_ERROR_CODES


type PostgresValue = (
    UUID
    | datetime
    | date
    | time
    | timedelta
    | Decimal
    | str
    | bytes
    | float
    | int
    | bool
    | Sequence[Any]
    | Mapping[str, Any]
    | None
)


@final
class PostgresRow(Mapping[str, PostgresValue]):
    """Immutable view over an ``asyncpg.Record``.

    The row keeps mapping semantics while providing typed accessors for frequent
    column shapes. Values are checked before returning them so callers receive a
    predictable Python representation.

    Notes
    -----
    Every ``get_*`` accessor shares one contract, and only differs in the type it
    coerces to:

    - ``key`` names the column to read.
    - ``default`` is returned when the column is missing or ``NULL``, and is
      ``None`` itself by default. A missing column is indistinguishable from a
      ``NULL`` one.
    - ``required=True`` raises ``ValueError`` instead, unless an explicit
      ``default`` was provided - a default always wins over ``required``.
    - a present value of an unexpected type raises ``TypeError`` rather than
      being coerced, keeping type assumptions honest at runtime.
    """

    __slots__ = ("_record",)

    def __init__(
        self,
        record: Record,
    ) -> None:
        """Wrap an ``asyncpg.Record`` as an immutable typed mapping.

        Parameters
        ----------
        record : Record
            Raw record returned by ``asyncpg``.
        """
        self._record: Record
        object.__setattr__(
            self,
            "_record",
            record,
        )

    @overload
    def get_uuid(
        self,
        key: str,
    ) -> UUID | None: ...

    @overload
    def get_uuid(
        self,
        key: str,
        *,
        default: UUID,
    ) -> UUID: ...

    @overload
    def get_uuid(
        self,
        key: str,
        *,
        required: Literal[True],
    ) -> UUID: ...

    def get_uuid(
        self,
        key: str,
        *,
        default: UUID | None = None,
        required: bool = False,
    ) -> UUID | None:
        """Return the column as ``UUID`` when present.

        Accepts native ``UUID`` values or string representations. ``default`` is
        returned when the column does not exist or resolves to ``NULL``.
        """

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if isinstance(value, UUID):
            return value

        if isinstance(value, str):
            try:
                return UUID(value)

            except ValueError as exc:
                raise ValueError(f"Malformed UUID value for '{key}'") from exc

        raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'UUID'")

    @overload
    def get_datetime(
        self,
        key: str,
    ) -> datetime | None: ...

    @overload
    def get_datetime(
        self,
        key: str,
        *,
        default: datetime,
    ) -> datetime: ...

    @overload
    def get_datetime(
        self,
        key: str,
        *,
        required: Literal[True],
    ) -> datetime: ...

    def get_datetime(
        self,
        key: str,
        *,
        default: datetime | None = None,
        required: bool = False,
    ) -> datetime | None:
        """Return the column as ``datetime`` when present.

        ``str`` values are parsed using ``datetime.fromisoformat``. ``default``
        is returned for missing or ``NULL`` entries.
        """

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if isinstance(value, datetime):
            return value

        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)

            except ValueError as exc:
                raise ValueError(f"Malformed datetime value for '{key}'") from exc

        raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'datetime'")

    @overload
    def get_str(
        self,
        key: str,
    ) -> str | None: ...

    @overload
    def get_str(
        self,
        key: str,
        *,
        default: str,
    ) -> str: ...

    @overload
    def get_str(
        self,
        key: str,
        *,
        required: Literal[True],
    ) -> str: ...

    def get_str(
        self,
        key: str,
        *,
        default: str | None = None,
        required: bool = False,
    ) -> str | None:
        """Return the column as ``str`` when present.

        Values of any other type are rejected rather than stringified.
        """

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if not isinstance(value, str):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'str'")

        return value

    @overload
    def get_int(
        self,
        key: str,
    ) -> int | None: ...

    @overload
    def get_int(
        self,
        key: str,
        *,
        default: int,
    ) -> int: ...

    @overload
    def get_int(
        self,
        key: str,
        *,
        required: Literal[True],
    ) -> int: ...

    def get_int(
        self,
        key: str,
        *,
        default: int | None = None,
        required: bool = False,
    ) -> int | None:
        """Return the column as ``int`` when present.

        ``bool`` is rejected even though it subclasses ``int``, so a BOOLEAN
        column cannot silently become ``0`` or ``1``. ``Decimal`` is rejected
        too, since narrowing it would be lossy - use :meth:`get_float`.
        """

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        # bool is a subclass of int, so accepting it here would silently turn
        # a BOOLEAN column into 0 or 1
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'int'")

        return value

    @overload
    def get_float(
        self,
        key: str,
    ) -> float | None: ...

    @overload
    def get_float(
        self,
        key: str,
        *,
        default: float,
    ) -> float: ...

    @overload
    def get_float(
        self,
        key: str,
        *,
        required: Literal[True],
    ) -> float: ...

    def get_float(
        self,
        key: str,
        *,
        default: float | None = None,
        required: bool = False,
    ) -> float | None:
        """Return the column as ``float`` when present.

        Accepts native ``float`` values as well as the ``int`` values asyncpg
        returns for integer columns. ``bool`` is rejected so a BOOLEAN column
        cannot become ``0.0`` or ``1.0``, and ``Decimal`` is rejected because a
        binary float cannot represent every decimal fraction - read
        ``numeric``/``decimal`` columns through :meth:`get_decimal` instead.

        Widening an ``int`` is exact only below ``2**53``, so a column holding
        larger values belongs to :meth:`get_int`.
        """

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if isinstance(value, bool):
            # a BOOLEAN column must not silently become 0.0 or 1.0
            raise TypeError(f"Unexpected value 'bool' for {key}, expected 'float'")

        if isinstance(value, float):
            return value

        if isinstance(value, Decimal):
            # a NUMERIC column is chosen for exactness, which a binary float
            # cannot hold - narrowing it here would discard that silently
            raise TypeError(
                f"Unexpected value 'Decimal' for {key}, expected 'float' - use 'get_decimal'"
            )

        if isinstance(value, int):
            return float(value)

        raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'float'")

    @overload
    def get_decimal(
        self,
        key: str,
    ) -> Decimal | None: ...

    @overload
    def get_decimal(
        self,
        key: str,
        *,
        default: Decimal,
    ) -> Decimal: ...

    @overload
    def get_decimal(
        self,
        key: str,
        *,
        required: Literal[True],
    ) -> Decimal: ...

    def get_decimal(
        self,
        key: str,
        *,
        default: Decimal | None = None,
        required: bool = False,
    ) -> Decimal | None:
        """Return the column as ``Decimal`` when present.

        This is the accessor for ``numeric``/``decimal`` columns, which asyncpg
        returns as ``Decimal``. ``int`` is accepted because widening it is exact,
        and ``str`` is parsed the way :meth:`get_uuid` accepts a textual
        representation. ``float`` is rejected: it has already lost the exactness
        a decimal column exists to keep, so converting it here would only make
        that loss harder to notice. ``bool`` is rejected as well.

        ``numeric`` also admits ``NaN`` and, since PostgreSQL 14, ``Infinity``.
        Those arrive as the matching non-finite ``Decimal`` and are returned
        unchanged rather than treated as missing.
        """

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if isinstance(value, Decimal):
            return value

        # bool is a subclass of int, so accepting it here would silently turn
        # a BOOLEAN column into 0 or 1
        if isinstance(value, bool):
            raise TypeError(f"Unexpected value 'bool' for {key}, expected 'Decimal'")

        if isinstance(value, int):
            return Decimal(value)

        if isinstance(value, str):
            try:
                return Decimal(value)

            except InvalidOperation as exc:
                raise ValueError(f"Malformed decimal value for '{key}'") from exc

        raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'Decimal'")

    @overload
    def get_bool(
        self,
        key: str,
    ) -> bool | None: ...

    @overload
    def get_bool(
        self,
        key: str,
        *,
        default: bool,
    ) -> bool: ...

    @overload
    def get_bool(
        self,
        key: str,
        *,
        required: Literal[True],
    ) -> bool: ...

    def get_bool(
        self,
        key: str,
        *,
        default: bool | None = None,
        required: bool = False,
    ) -> bool | None:
        """Return the column as ``bool`` when present.

        Only a native ``bool`` is accepted - no truthiness coercion.
        """

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if not isinstance(value, bool):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'bool'")

        return value

    def __contains__(
        self,
        element: Any,
    ) -> bool:
        """Delegate membership checks to the underlying record."""

        return element in self._record

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

    def __getitem__(
        self,
        key: str,
    ) -> PostgresValue:
        """Expose mapping access for the original values."""

        return self._record[key]

    def __iter__(self) -> Iterator[str]:
        """Iterate over column names."""

        return iter(self._record.keys())

    def __len__(self) -> int:
        """Return number of columns in the row."""

        return len(self._record)

    def __copy__(self) -> Self:
        """Return ``self`` because the row is immutable."""

        return self  # Immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        """Return ``self`` because the row is immutable."""

        return self  # Consider immutable, no need to provide an actual copy


@runtime_checkable
class PostgresStatementFetching(Protocol):
    """Callable that runs a SQL statement and returns its rows."""

    async def __call__(
        self,
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]: ...


@runtime_checkable
class PostgresStatementExecuting(Protocol):
    """Callable that runs a SQL statement and returns its command status.

    Distinct from :class:`PostgresStatementFetching`: this executes without
    retrieving a result set, and reports what the server did instead - the raw
    command tag, such as ``"UPDATE 3"`` or ``"CREATE TABLE"``.
    """

    async def __call__(
        self,
        statement: str,
        /,
        *args: PostgresValue,
    ) -> str: ...


PostgresTransactionIsolation = Literal[
    "read_committed",
    "repeatable_read",
    "serializable",
]
"""Transaction isolation level accepted by the Postgres adapter."""


class PostgresTransactionContext(Protocol):
    """Async context manager representing an active transaction."""

    async def __aenter__(self) -> None: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


@runtime_checkable
class PostgresTransactionPreparing(Protocol):
    """Callable that prepares a transaction context manager."""

    def __call__(
        self,
        *,
        isolation: PostgresTransactionIsolation | None = None,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> PostgresTransactionContext: ...


class PostgresConnectionContext(Protocol):
    """Async context manager yielding a `PostgresConnection`."""

    async def __aenter__(self) -> PostgresConnection: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...


@runtime_checkable
class PostgresConnectionAcquiring(Protocol):
    """Callable returning a `PostgresConnectionContext`."""

    def __call__(self) -> PostgresConnectionContext: ...


@runtime_checkable
class PostgresMigrating(Protocol):
    """Coroutine that mutates the schema/data during migrations."""

    async def __call__(
        self,
        connection: PostgresConnection,
    ) -> None: ...
