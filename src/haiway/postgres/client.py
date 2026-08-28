from asyncio import timeout
from collections.abc import Callable, Coroutine, Mapping, Sequence
from ssl import SSLContext
from time import monotonic
from types import TracebackType
from typing import Any, Self

from asyncpg import (
    Connection,
    Pool,
    PostgresError,
    create_pool,  # pyright: ignore[reportUnknownVariableType]
)
from asyncpg.pool import (
    PoolAcquireContext,
    PoolConnectionProxy,
)
from asyncpg.transaction import Transaction

from haiway.context import ctx
from haiway.postgres.observability import (
    CONNECTION_COUNT_METRIC,
    CONNECTION_FAILED_EVENT,
    CONNECTION_TIMEOUTS_METRIC,
    CONNECTION_WAIT_TIME_METRIC,
    TRANSACTION_DURATION_METRIC,
    operation_attributes,
)
from haiway.postgres.state import (
    Postgres,
    PostgresConnection,
)
from haiway.postgres.types import (
    PostgresConnectionContext,
    PostgresException,
    PostgresRow,
    PostgresTransactionContext,
    PostgresTransactionIsolation,
    PostgresValue,
)
from haiway.types import MISSING, Immutable, Missing, not_missing
from haiway.utils import getenv_float, getenv_int, getenv_str

__all__ = ("PostgresConnectionPool",)


class PostgresConnectionPool(Immutable):
    """Disposable Postgres connection pool backed by ``asyncpg``.

    The pool is intended to be installed into a Haiway scope via
    ``ctx.scope(..., disposables=(PostgresConnectionPool(),))``. Entering the
    disposable creates an ``asyncpg`` pool and exposes a contextual
    :class:`haiway.postgres.state.Postgres` state that can acquire individual
    connections on demand.

    Parameters
    ----------
    host : str, optional
        Server hostname, defaulting to ``POSTGRES_HOST`` or ``localhost``.
    port : str, optional
        Server port, defaulting to ``POSTGRES_PORT`` or ``5432``.
    database : str, optional
        Database name, defaulting to ``POSTGRES_DATABASE`` or ``postgres``.
    user : str, optional
        Role to authenticate as, defaulting to ``POSTGRES_USER`` or
        ``postgres``.
    password : str | None, optional
        Password, defaulting to ``POSTGRES_PASSWORD``. There is deliberately no
        fallback - an unset one is passed to the driver as absent, which lets it
        resolve ``PGPASSWORD`` or a ``.pgpass`` entry and fail loudly when the
        server wants one.
    ssl : SSLContext | str | bool | None, optional
        TLS specification handed to ``asyncpg``, defaulting to
        ``POSTGRES_SSLMODE`` or ``require``. ``require`` encrypts the connection
        and refuses a plaintext fallback, unlike the libpq default of ``prefer``
        which silently downgrades and skips verification. ``verify-ca`` or
        ``verify-full`` additionally validate the server certificate.
    connection_limit : int, optional
        Maximum number of connections kept by the pool, defaulting to
        ``POSTGRES_CONNECTIONS`` or ``1``.
    acquire_timeout : float, optional
        Seconds to wait for a free connection, defaulting to
        ``POSTGRES_ACQUIRE_TIMEOUT`` or ``30``; ``0`` waits indefinitely. It
        bounds pool contention, so it fails with a diagnosable error instead of
        hanging.
    command_timeout : float, optional
        Per-statement limit in seconds, defaulting to
        ``POSTGRES_COMMAND_TIMEOUT`` or ``0``, which disables it, matching
        ``asyncpg``.
    close_timeout : float, optional
        Seconds to wait for connections to be released on pool close,
        defaulting to ``POSTGRES_CLOSE_TIMEOUT`` or ``30``; ``0`` waits
        indefinitely. It bounds scope exit, so a leaked connection ends in
        termination instead of blocking it.
    statement_cache : int, optional
        Size of the per-connection prepared statement cache, defaulting to
        ``POSTGRES_STATEMENT_CACHE`` or ``100``, matching ``asyncpg``; ``0``
        disables it, which is required behind a proxy pooling by transaction.
    initialize : Callable[[Connection], Coroutine[None, None, None]] | None, optional
        Async hook executed by ``asyncpg`` for every newly created connection.

    Notes
    -----
    Every ``POSTGRES_*`` variable is read when the pool is constructed rather
    than when this module is imported, so ``load_env`` and test monkeypatching
    apply regardless of import order.

    The resolved configuration is captured by the closures creating, acquiring
    from and closing the pool, so the instance itself carries no connection
    parameter - nothing to read back, and no credential reachable through
    ``repr`` or an observability payload rendering it.

    The connection target comes from exactly one source, never a mix of the two.
    Constructing the pool directly connects through the ``host``, ``port``,
    ``database``, ``user``, ``password``, and ``ssl`` arguments described above.
    :meth:`of` connects through a dsn and nothing else - no argument or default
    is merged into it, no parameter is taken out of it, and anything it omits is
    resolved by ``asyncpg`` itself.

    Pool behavior - the connection limit, the timeouts, the statement cache, and
    the initialization hook - is configured by arguments either way, as a
    connection string cannot express it.

    One instance owns a single pool and supports one active scope at a time.
    """

    _pool: Pool | None  # initialized on demand
    # the whole configuration lives in these closures - see the note above
    _prepare_pool: Callable[[], Pool]
    _prepare_connection_context: Callable[[Pool], PostgresConnectionContext]
    _close_pool: Callable[[Pool], Coroutine[None, None, None]]

    @classmethod
    def of(
        cls,
        dsn: str,
        *,
        connection_limit: int | Missing = MISSING,
        acquire_timeout: float | Missing = MISSING,
        command_timeout: float | Missing = MISSING,
        close_timeout: float | Missing = MISSING,
        statement_cache: int | Missing = MISSING,
        initialize: Callable[[Connection], Coroutine[None, None, None]] | None = None,
    ) -> Self:
        """Create a pool connecting through the given dsn.

        Parameters
        ----------
        dsn : str
            Complete connection specification using the ``postgres`` or
            ``postgresql`` scheme, handed to ``asyncpg`` verbatim.
        connection_limit : int, optional
            Maximum number of connections kept by the pool.
        acquire_timeout : float, optional
            Seconds to wait for a free connection; ``0`` waits indefinitely.
        command_timeout : float, optional
            Per-statement limit in seconds; ``0`` disables it.
        close_timeout : float, optional
            Seconds to wait for connections to be released on pool close.
        statement_cache : int, optional
            Size of the per-connection prepared statement cache; ``0`` disables
            it, which is required behind a proxy pooling by transaction.
        initialize : Callable[[Connection], Coroutine[None, None, None]] | None, default=None
            Optional async hook executed by ``asyncpg`` for every newly created
            connection.

        Returns
        -------
        Self
            Immutable pool configuration ready to be used as a disposable.

        Notes
        -----
        The dsn is the sole connection specification: nothing is merged into it
        and nothing is taken out of it, so host lists, unix socket directories,
        percent-encoded credentials, and unmodelled options such as
        ``sslrootcert``, ``application_name`` or ``target_session_attrs`` all
        keep working with full libpq fidelity. Every connection parameter the
        dsn omits falls back to ``asyncpg``'s own resolution - ``PGHOST``,
        ``PGUSER``, ``PGPASSWORD``, ``.pgpass``, service files - rather than to
        the ``POSTGRES_*`` defaults, which apply to the connection arguments
        only. The pool settings above still resolve their own variables, as
        those describe the pool rather than the connection.

        The dsn is not inspected, so anything ``asyncpg`` does not recognize as
        a connection parameter reaches the server as a startup setting, which
        rejects the connection with ``unrecognized configuration parameter``.
        That covers the pool settings - ``connection_limit`` and friends - which
        a connection string cannot express, as well as ``connect_timeout``: pass
        those as arguments instead.

        TLS belongs to the connection specification, so the dsn owns it through
        ``sslmode`` and the ``ssl*`` options. Mind that a dsn without ``sslmode``
        therefore connects under ``asyncpg``'s default of ``prefer``, which
        silently downgrades to plaintext, and not under the ``require`` default
        of the ``ssl`` argument - state ``sslmode`` in the dsn explicitly.

        ``connect_timeout`` is worth knowing about as well: ``asyncpg`` does not
        consume it from a dsn, so it would reach the server as a startup setting.
        ``statement_cache_size`` behaves the same way - ``asyncpg`` resolves it as
        a client setting rather than a connection parameter, so it is reachable
        only through the ``statement_cache`` argument here.
        """
        return cls(
            connection_limit=connection_limit,
            acquire_timeout=acquire_timeout,
            command_timeout=command_timeout,
            close_timeout=close_timeout,
            statement_cache=statement_cache,
            initialize=initialize,
            _dsn=dsn,
        )

    def __init__(
        self,
        *,
        host: str | Missing = MISSING,
        port: str | Missing = MISSING,
        database: str | Missing = MISSING,
        user: str | Missing = MISSING,
        password: str | Missing | None = MISSING,
        ssl: SSLContext | str | bool | Missing | None = MISSING,
        connection_limit: int | Missing = MISSING,
        acquire_timeout: float | Missing = MISSING,
        command_timeout: float | Missing = MISSING,
        close_timeout: float | Missing = MISSING,
        statement_cache: int | Missing = MISSING,
        initialize: Callable[[Connection], Coroutine[None, None, None]] | None = None,
        _dsn: str | None = None,  # provided by `of`, which owns the dsn path
    ) -> None:
        connection_arguments: Mapping[str, Any]
        if _dsn is not None:
            # a dsn is a complete specification - nothing else is resolved
            # alongside it, as asyncpg gives explicit arguments precedence over
            # a dsn and any default here would shadow what it defines
            connection_arguments = {"dsn": _dsn}

        else:
            connection_arguments = {
                "host": host if not_missing(host) else getenv_str("POSTGRES_HOST", "localhost"),
                "port": port if not_missing(port) else getenv_str("POSTGRES_PORT", "5432"),
                "database": database
                if not_missing(database)
                else getenv_str("POSTGRES_DATABASE", "postgres"),
                "user": user if not_missing(user) else getenv_str("POSTGRES_USER", "postgres"),
                "password": password if not_missing(password) else getenv_str("POSTGRES_PASSWORD"),
                "ssl": ssl if not_missing(ssl) else getenv_str("POSTGRES_SSLMODE", "require"),
            }

        resolved_connection_limit: int = (
            connection_limit
            if not_missing(connection_limit)
            else getenv_int("POSTGRES_CONNECTIONS", 1)
        )
        resolved_command_timeout: float = (
            command_timeout
            if not_missing(command_timeout)
            else getenv_float("POSTGRES_COMMAND_TIMEOUT", 0.0)
        )
        resolved_statement_cache: int = (
            statement_cache
            if not_missing(statement_cache)
            else getenv_int("POSTGRES_STATEMENT_CACHE", 100)
        )
        resolved_acquire_timeout: float = (
            acquire_timeout
            if not_missing(acquire_timeout)
            else getenv_float("POSTGRES_ACQUIRE_TIMEOUT", 30.0)
        )
        resolved_close_timeout: float = (
            close_timeout
            if not_missing(close_timeout)
            else getenv_float("POSTGRES_CLOSE_TIMEOUT", 30.0)
        )

        def prepare_pool() -> Pool:
            return create_pool(
                **connection_arguments,
                min_size=1,
                max_size=resolved_connection_limit,
                init=initialize,
                command_timeout=resolved_command_timeout or None,
                statement_cache_size=resolved_statement_cache,
            )

        def prepare_connection_context(
            pool: Pool,
            /,
        ) -> PostgresConnectionContext:
            return _ConnectionContext(
                _pool=pool,
                _acquire_timeout=resolved_acquire_timeout or None,
            )

        async def close_pool(
            pool: Pool,
            /,
        ) -> None:
            async with timeout(resolved_close_timeout or None):
                await pool.close()

        object.__setattr__(self, "_pool", None)
        object.__setattr__(self, "_prepare_pool", prepare_pool)
        object.__setattr__(self, "_prepare_connection_context", prepare_connection_context)
        object.__setattr__(self, "_close_pool", close_pool)

    async def __aenter__(self) -> Postgres:
        """Create the connection pool and expose contextual Postgres state.

        Returns
        -------
        Postgres
            State able to acquire individual connections from this pool.

        Raises
        ------
        PostgresException
            If this instance is already entered, or the pool cannot be created.
        """
        if self._pool is not None:
            raise PostgresException(
                "Postgres connection pool is already entered"
                " - use a separate instance for each concurrent scope",
            )

        pool: Pool | None = None
        try:
            pool = self._prepare_pool()
            object.__setattr__(self, "_pool", pool)
            await pool

        except BaseException as exc:
            object.__setattr__(self, "_pool", None)
            if pool is not None:
                # asyncpg does not clean up after a failed initialization, so
                # connections opened before the failing one have to be dropped
                pool.terminate()

            # cancellation propagates untouched, everything else is translated
            if isinstance(exc, Exception):
                raise PostgresException(
                    "Failed to create Postgres connection pool",
                    sqlstate=exc.sqlstate  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
                    if isinstance(exc, PostgresError)
                    else None,
                ) from exc

            raise

        return Postgres(connection_acquiring=self.acquire_connection)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Close the connection pool, releasing all of its connections.

        Notes
        -----
        ``Pool.close()`` waits for every connection to be released, which never
        completes when a connection was leaked. The wait is therefore bounded by
        ``close_timeout``, after which the pool is terminated instead. That
        termination is ``asyncpg``'s own - ``Pool.close()`` terminates the pool
        on any failure, the cancellation raised by the expiring timeout
        included - so there is nothing left to clean up here. Failures are
        reported through observability rather than raised, so they cannot mask
        an exception propagating out of the scope, while cancellation of the
        surrounding task still propagates.

        This is unconditional, unlike connection release and transaction
        completion which raise when nothing else is propagating: those report a
        failure the caller can still act on - a lost commit above all - whereas
        a pool being discarded after ``asyncpg`` already terminated it leaves
        nothing to act on.
        """
        pool: Pool | None = self._pool
        object.__setattr__(self, "_pool", None)
        if pool is None:
            return  # never entered or already closed

        try:
            await self._close_pool(pool)

        except Exception as exc:
            ctx.log_error(
                "Failed to close Postgres connection pool, terminated it instead",
                exception=exc,
            )

    def acquire_connection(self) -> PostgresConnectionContext:
        """Return a connection-acquiring context bound to this pool.

        Returns
        -------
        PostgresConnectionContext
            Async context manager yielding a contextual
            :class:`haiway.postgres.state.PostgresConnection`.

        Raises
        ------
        PostgresException
            If the pool has not been entered, or was already closed.

        Notes
        -----
        Nothing is executed against the server here - the connection is taken
        from the pool and handed over as is, so acquiring costs no round trip
        and ``acquire_timeout`` bounds the whole call.
        """
        pool: Pool | None = self._pool
        if pool is None:
            raise PostgresException(
                "Postgres connection pool is not initialized",
            )

        return self._prepare_connection_context(pool)


class _TransactionContext(Immutable):
    _transaction_context: Transaction
    _entered_at: float | None = None  # set when the transaction begins

    async def __aenter__(self) -> None:
        try:
            await self._transaction_context.__aenter__()
            object.__setattr__(self, "_entered_at", monotonic())

        except Exception as exc:
            raise PostgresException(
                "Failed to begin Postgres transaction",
                sqlstate=exc.sqlstate  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
                if isinstance(exc, PostgresError)
                else None,
            ) from exc

    def _record_completed(
        self,
        outcome: str,
    ) -> None:
        """Record the transaction lifetime, split by how it actually ended.

        A long transaction holds its snapshot and its locks for as long as it
        lives, so the duration matters on its own, and separating a commit from a
        rollback shows contention which the statement metrics cannot.

        The outcome is what the completion attempt returned, not what it intended:
        ``commit_failed`` is a transaction whose work is lost despite the block
        leaving cleanly, which is the one outcome worth alerting on.
        """
        entered_at: float | None = self._entered_at
        if entered_at is None:
            return  # never began, so there is no lifetime to report

        ctx.record_info(
            metric=TRANSACTION_DURATION_METRIC,
            value=monotonic() - entered_at,
            unit="s",
            kind="histogram",
            attributes={
                "db.system.name": "postgresql",
                "db.transaction.outcome": outcome,
            },
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # asyncpg commits when nothing propagates out of the block and rolls back
        # otherwise, so what is leaving it decides which completion is attempted -
        # whether that completion succeeded is only known below
        outcome: str = "rolled_back" if exc_val is not None else "committed"
        try:
            await self._transaction_context.__aexit__(  # pyright: ignore[reportUnknownMemberType]
                exc_type,
                exc_val,
                exc_tb,
            )

        except Exception as exc:
            # A failed commit is the only failure there is, so it has to be
            # raised - a failed rollback must not replace the exception that
            # caused the rollback, which is the more useful of the two
            if exc_val is not None:
                outcome = "rollback_failed"
                ctx.log_error(
                    "Failed to roll back Postgres transaction",
                    exception=exc,
                )
                return

            outcome = "commit_failed"
            raise PostgresException(
                "Failed to commit Postgres transaction",
                sqlstate=exc.sqlstate  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
                if isinstance(exc, PostgresError)
                else None,
            ) from exc

        finally:
            # in `finally` so the return and the raise above are recorded too
            self._record_completed(outcome)


class _ConnectionContext(Immutable):
    _pool: Pool
    _acquire_timeout: float | None
    _pool_context: PoolAcquireContext | None = None  # set by the acquire below

    def _record_acquired(
        self,
        started: float,
    ) -> None:
        """Record the wait for a connection and the pool occupancy it left behind.

        The wait is measured separately from the statement duration recorded above
        the protocol, so a slow call can be attributed to a contended pool rather
        than to the server. Occupancy is sampled here because acquiring is the
        moment it changes and the only moment the pool is in hand.
        """
        ctx.record_info(
            metric=CONNECTION_WAIT_TIME_METRIC,
            value=monotonic() - started,
            unit="s",
            kind="histogram",
            attributes=operation_attributes("acquire_connection"),
        )
        idle: int
        used: int
        try:
            idle = self._pool.get_idle_size()
            used = self._pool.get_size() - idle

        except Exception:
            # occupancy is introspection, not part of acquiring a connection, so a
            # pool which cannot answer loses the gauge rather than the connection.
            # `ctx.record_*` swallows its own failures the same way
            return

        ctx.record_info(
            metric=CONNECTION_COUNT_METRIC,
            value=idle,
            unit="{connection}",
            kind="gauge",
            attributes={"db.system.name": "postgresql", "state": "idle"},
        )
        ctx.record_info(
            metric=CONNECTION_COUNT_METRIC,
            value=used,
            unit="{connection}",
            kind="gauge",
            attributes={"db.system.name": "postgresql", "state": "used"},
        )

    async def _acquire(self) -> PoolConnectionProxy:
        """Take a connection from the pool and hand it over as is.

        Nothing is executed against the server here: ``asyncpg`` replaces a
        pooled connection it knows to be closed and recycles the ones which sat
        idle for too long, so a round trip per acquire would only pay for the
        rarer case of a socket dropped without a FIN - a failover, or an idle
        timeout on a proxy or NAT in between - which the statement about to run
        reports anyway.
        """
        started: float = monotonic()
        pool_context: PoolAcquireContext = self._pool.acquire(  # pyright: ignore[reportUnknownMemberType]
            timeout=self._acquire_timeout,
        )
        acquired_connection: PoolConnectionProxy
        try:
            acquired_connection = await pool_context.__aenter__()  # pyright: ignore[reportUnknownVariableType]

        except Exception as exc:
            if isinstance(exc, TimeoutError):
                # every connection is busy and the wait ran out, which no
                # amount of statement tuning will fix - raise the limit
                ctx.record_info(
                    metric=CONNECTION_TIMEOUTS_METRIC,
                    value=1,
                    unit="{timeout}",
                    kind="counter",
                    attributes=operation_attributes("acquire_connection"),
                )

            # the wait is otherwise only recorded when it produced a
            # connection, leaving a failed acquire with nothing to show
            ctx.record_warning(
                event=CONNECTION_FAILED_EVENT,
                attributes={
                    "db.system.name": "postgresql",
                    "error.type": type(exc).__qualname__,
                    "db.client.connection.wait_time": monotonic() - started,
                },
            )
            raise PostgresException(
                "Failed to acquire Postgres connection",
                sqlstate=exc.sqlstate  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
                if isinstance(exc, PostgresError)
                else None,
            ) from exc

        object.__setattr__(self, "_pool_context", pool_context)
        self._record_acquired(started)
        return acquired_connection  # pyright: ignore[reportUnknownVariableType]

    async def __aenter__(self) -> PostgresConnection:
        acquired_connection: PoolConnectionProxy = await self._acquire()

        async def fetch(
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
                raise PostgresException(
                    "Failed to fetch SQL statement rows",
                    sqlstate=exc.sqlstate  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
                    if isinstance(exc, PostgresError)
                    else None,
                ) from exc

        async def execute(
            statement: str,
            /,
            *args: PostgresValue,
        ) -> str:
            # asyncpg reaches for the simple query protocol when no parameters
            # are bound, which carries a whole script and prepares nothing, and
            # for the extended one otherwise - either way it returns the command
            # tag rather than decoding a result set
            try:
                return await acquired_connection.execute(  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
                    statement,
                    *args,
                )

            except Exception as exc:
                raise PostgresException(
                    "Failed to execute SQL statement",
                    sqlstate=exc.sqlstate  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
                    if isinstance(exc, PostgresError)
                    else None,
                ) from exc

        def transaction(
            *,
            isolation: PostgresTransactionIsolation | None = None,
            readonly: bool = False,
            deferrable: bool = False,
        ) -> PostgresTransactionContext:
            return _TransactionContext(
                _transaction_context=acquired_connection.transaction(  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
                    isolation=isolation,
                    readonly=readonly,
                    deferrable=deferrable,
                ),
            )

        return PostgresConnection(
            statement_fetching=fetch,
            statement_executing=execute,
            transaction_preparing=transaction,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pool_context: PoolAcquireContext | None = self._pool_context
        object.__setattr__(self, "_pool_context", None)
        if pool_context is None:
            return  # nothing was ever acquired

        try:
            await pool_context.__aexit__(
                exc_type,
                exc_val,
                exc_tb,
            )

        except Exception as exc:
            # releasing back to the pool is cleanup, so it must not replace the
            # exception that is already leaving the block - when nothing is, the
            # failed release is the only failure there is and has to be raised
            if exc_val is not None:
                ctx.log_error(
                    "Failed to release Postgres connection",
                    exception=exc,
                )
                return

            raise PostgresException(
                "Failed to release Postgres connection",
                sqlstate=exc.sqlstate  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType, reportUnknownArgumentType]
                if isinstance(exc, PostgresError)
                else None,
            ) from exc
