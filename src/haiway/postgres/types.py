from collections.abc import Iterator, Mapping, Sequence
from datetime import date, datetime, time
from decimal import Decimal
from types import TracebackType
from typing import (
    TYPE_CHECKING,
    Any,
    Literal,
    NoReturn,
    Protocol,
    Self,
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
    "PostgresException",
    "PostgresMigrating",
    "PostgresRow",
    "PostgresStatementExecuting",
    "PostgresTransactionContext",
    "PostgresTransactionPreparing",
    "PostgresValue",
)


class PostgresException(Exception):
    """Raised when an unexpected database failure occurs."""


type PostgresValue = UUID | datetime | date | time | str | bytes | float | int | bool | None


class PostgresRow(Mapping[str, PostgresValue]):
    """Immutable view over an ``asyncpg.Record``.

    The row keeps mapping semantics while providing typed accessors for frequent
    column shapes. Values are checked before returning them so callers receive a
    predictable Python representation.
    """

    __slots__ = ("_record",)

    def __init__(
        self,
        record: Record,
    ) -> None:
        assert isinstance(record, Record)  # nosec: B101
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

            except Exception:
                pass  # nosec: B110 will raise anyway

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

            except Exception:
                pass  # nosec: B110 will raise anyway

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
        """Return the column as ``str`` when present."""

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
        """Return the column as ``int`` when present."""

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if not isinstance(value, int):
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

        Accepts native ``float`` values as well as numeric ``int`` or ``Decimal`` instances
        returned by asyncpg for ``numeric``/``decimal`` columns.
        """

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if isinstance(value, float):
            return value

        if isinstance(value, int | Decimal):
            return float(value)

        raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'float'")

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
        """Return the column as ``bool`` when present."""

        value: PostgresValue = self._record.get(key, None)
        if value is None:
            if required and default is None:
                raise ValueError(f"Missing required value for '{key}'")

            return default

        if not isinstance(value, bool):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'bool'")

        return value

    def __bool__(self) -> bool:
        """Mirror truthiness of the wrapped record."""

        return bool(self._record)

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

    def __setitem__(
        self,
        key: str,
        value: PostgresValue,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" item - '{key}' cannot be modified"
        )

    def __delitem__(
        self,
        key: str,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__} item - '{key}' cannot be deleted"
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

        return self  # Metadata is immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        """Return ``self`` because the row is immutable."""

        return self  # Metadata is immutable, no need to provide an actual copy


@runtime_checkable
class PostgresStatementExecuting(Protocol):
    """Callable that executes a SQL statement and returns rows."""

    async def __call__(
        self,
        statement: str,
        /,
        *args: PostgresValue,
    ) -> Sequence[PostgresRow]: ...


@runtime_checkable
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

    def __call__(self) -> PostgresTransactionContext: ...


@runtime_checkable
class PostgresConnectionContext(Protocol):
    """Async context manager yielding a `PostgresConnection`."""

    async def __aenter__(self) -> "PostgresConnection": ...

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
        connection: "PostgresConnection",
    ) -> None: ...
