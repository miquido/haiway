from collections.abc import Callable, Coroutine, Mapping, Sequence
from ssl import SSLContext
from types import TracebackType
from typing import Self
from urllib.parse import ParseResult, parse_qs, urlparse

from asyncpg import (  # pyright: ignore[reportMissingTypeStubs]
    Connection,
    Pool,
    create_pool,  # pyright: ignore [reportUnknownVariableType]
)
from asyncpg.pool import (  # pyright: ignore[reportMissingTypeStubs]
    PoolAcquireContext,
    PoolConnectionProxy,
)
from asyncpg.transaction import Transaction  # pyright: ignore[reportMissingTypeStubs]

from haiway.postgres.config import (
    POSTGRES_CONNECTIONS,
    POSTGRES_DATABASE,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_SSLMODE,
    POSTGRES_USER,
)
from haiway.postgres.state import (
    Postgres,
    PostgresConnection,
    PostgresConnectionContext,
    PostgresTransactionContext,
)
from haiway.postgres.types import (
    PostgresException,
    PostgresRow,
    PostgresValue,
)
from haiway.types import Immutable

__all__ = ("PostgresConnectionPool",)


async def _noop_initialize(connection: Connection) -> None:
    pass


class PostgresConnectionPool(Immutable):
    """Disposable Postgres connection pool backed by ``asyncpg``.

    The pool is intended to be installed into a Haiway scope via
    ``ctx.scope(..., disposables=(PostgresConnectionPool(),))``. Entering the
    disposable creates an ``asyncpg`` pool and exposes a contextual
    :class:`haiway.postgres.state.Postgres` state that can acquire individual
    connections on demand.

    Notes
    -----
    Connection defaults come from :mod:`haiway.postgres.config` and are read at
    import time.
    """

    @classmethod
    def of(
        cls,
        dsn: str,
        *,
        ssl: str = POSTGRES_SSLMODE,
        connection_limit: int = POSTGRES_CONNECTIONS,
        initialize: Callable[[Connection], Coroutine[None, None, None]] = _noop_initialize,
    ) -> Self:
        """Create a pool configuration from a Postgres DSN.

        Parameters
        ----------
        dsn : str
            Connection string using the ``postgres`` or ``postgresql`` scheme.
        ssl : str, default=POSTGRES_SSLMODE
            Fallback SSL mode used when the DSN does not define ``sslmode`` or
            ``ssl``.
        connection_limit : int, default=POSTGRES_CONNECTIONS
            Fallback maximum pool size used when the DSN does not define one of
            the supported query parameters.
        initialize : Callable[[Connection], Coroutine[None, None, None]]
            Optional async hook executed by ``asyncpg`` for every newly created
            connection.

        Returns
        -------
        Self
            Immutable pool configuration ready to be used as a disposable.

        Raises
        ------
        ValueError
            If the DSN scheme is not supported or the connection limit override
            cannot be parsed as an integer.

        Notes
        -----
        The DSN may override SSL behavior via ``sslmode`` or ``ssl`` and the
        pool size via ``connections``, ``connection_limit``, ``maxsize``, or
        ``max_size`` query parameters.
        """
        parsed: ParseResult = urlparse(dsn)
        if parsed.scheme not in {"postgres", "postgresql"}:
            raise ValueError(f"Unsupported Postgres DSN scheme: {parsed.scheme!r}")

        host: str = parsed.hostname or POSTGRES_HOST
        port: str = str(parsed.port) if parsed.port is not None else POSTGRES_PORT
        database_path: str = parsed.path.lstrip("/")
        database: str = database_path or POSTGRES_DATABASE
        user: str = parsed.username or POSTGRES_USER
        password: str = parsed.password or POSTGRES_PASSWORD

        query: Mapping[str, Sequence[str]] = parse_qs(
            parsed.query,
            keep_blank_values=True,
        )

        ssl_values: Sequence[str] = query.get("sslmode", query.get("ssl", ()))
        if ssl_values:
            normalized_ssl: str = ssl_values[-1].strip().lower()
            resolved_ssl: SSLContext | str | bool | None
            if normalized_ssl == "true":
                resolved_ssl = True

            elif normalized_ssl == "false":
                resolved_ssl = False

            else:
                resolved_ssl = normalized_ssl

        else:
            resolved_ssl = ssl

        for key in ("connections", "connection_limit", "maxsize", "max_size"):
            if values := query.get(key):
                try:
                    connection_limit = int(values[-1])
                    break  # use first value found

                except ValueError as exc:
                    raise ValueError(
                        f"Invalid connection limit value in Postgres DSN: {values}"
                    ) from exc

        return cls(
            host=host,
            port=port,
            database=database,
            user=user,
            password=password,
            ssl=resolved_ssl,
            connection_limit=connection_limit,
            initialize=initialize,
        )

    host: str = POSTGRES_HOST
    port: str = POSTGRES_PORT
    database: str = POSTGRES_DATABASE
    user: str = POSTGRES_USER
    password: str = POSTGRES_PASSWORD
    ssl: SSLContext | str | bool | None = POSTGRES_SSLMODE
    connection_limit: int = POSTGRES_CONNECTIONS
    initialize: Callable[[Connection], Coroutine[None, None, None]] = _noop_initialize
    _pool: Pool | None = None  # initialized on demand

    async def __aenter__(self) -> Postgres:
        object.__setattr__(
            self,
            "_pool",
            await create_pool(
                host=self.host,
                port=self.port,
                database=self.database,
                user=self.user,
                password=self.password,
                ssl=self.ssl,
                min_size=1,
                max_size=self.connection_limit,
                init=self.initialize,
            ),
        )

        return Postgres(connection_acquiring=self.acquire_connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._pool is not None, "Postgres connection pool is not initialized"  # nosec: B101
        try:
            await self._pool.close()

        except Exception:
            pass  # nosec: B110

    def acquire_connection(self) -> PostgresConnectionContext:
        """Return a connection-acquiring context bound to this pool.

        Returns
        -------
        PostgresConnectionContext
            Async context manager yielding a contextual
            :class:`haiway.postgres.state.PostgresConnection`.

        Raises
        ------
        AssertionError
            If the pool has not been entered yet.
        """

        assert self._pool is not None, "Postgres connection pool is not initialized"  # nosec: B101
        return _ConnectionContext(_pool_context=self._pool.acquire())  # pyright: ignore[reportUnknownMemberType, reportUnknownMemberType]


class _TransactionContext(Immutable):
    _transaction_context: Transaction

    async def __aenter__(self) -> None:
        await self._transaction_context.__aenter__()

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._transaction_context.__aexit__(  # pyright: ignore[reportUnknownMemberType]
            exc_type,
            exc_val,
            exc_tb,
        )


class _ConnectionContext(Immutable):
    _pool_context: PoolAcquireContext

    async def __aenter__(self) -> PostgresConnection:
        acquired_connection: PoolConnectionProxy = await self._pool_context.__aenter__()  # pyright: ignore[reportUnknownVariableType]

        async def execute(
            statement: str,
            /,
            *args: PostgresValue,
        ) -> Sequence[PostgresRow]:
            try:
                return tuple(
                    PostgresRow(record)  # pyright: ignore[reportUnknownArgumentType]
                    for record in await acquired_connection.fetch(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                        statement,
                        *args,
                    )
                )

            except Exception as exc:
                raise PostgresException("Failed to execute SQL statement") from exc

        def transaction() -> PostgresTransactionContext:
            return _TransactionContext(
                _transaction_context=acquired_connection.transaction(),  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
            )

        return PostgresConnection(
            statement_executing=execute,
            transaction_preparing=transaction,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._pool_context.__aexit__(  # pyright: ignore[reportUnknownMemberType]
            exc_type,
            exc_val,
            exc_tb,
        )
