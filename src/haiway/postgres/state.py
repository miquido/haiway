import pkgutil
from asyncio import sleep
from collections.abc import Callable, Coroutine, Generator, MutableMapping, Sequence
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from random import uniform
from time import monotonic
from types import ModuleType
from typing import Final, NamedTuple

from haiway.attributes import State
from haiway.context import ctx
from haiway.helpers import statemethod
from haiway.postgres.observability import (
    OPERATION_DURATION_METRIC,
    OPERATION_RETRIED_EVENT,
    OPERATION_RETRIES_METRIC,
    RETURNED_ROWS_METRIC,
    operation_attributes,
)
from haiway.postgres.types import (
    PostgresConnectionAcquiring,
    PostgresConnectionContext,
    PostgresErrorCode,
    PostgresException,
    PostgresMigrating,
    PostgresRow,
    PostgresStatementExecuting,
    PostgresStatementFetching,
    PostgresTransactionContext,
    PostgresTransactionIsolation,
    PostgresTransactionPreparing,
    PostgresValue,
)

__all__ = (
    "Postgres",
    "PostgresConnection",
    "PostgresConnectionContext",
    "PostgresTransactionContext",
)


# retries are spread out to keep two conflicting transactions from colliding
# again in lockstep, which is what a fixed delay guarantees
RETRY_BACKOFF_BASE: Final[float] = 0.05
RETRY_BACKOFF_LIMIT: Final[float] = 1.0


def _record_returned_rows(
    operation: str,
    rows: Sequence[PostgresRow],
) -> None:
    """Record how many rows a fetch transferred.

    A histogram rather than an attribute: the count is unbounded, and attaching it
    to the duration metric would open a time series per distinct value. It shows
    a statement pulling far more than its caller uses - `fetch_one` above all,
    which reads the whole result set and keeps one row of it.
    """
    ctx.record_info(
        metric=RETURNED_ROWS_METRIC,
        value=len(rows),
        unit="{row}",
        kind="histogram",
        attributes=operation_attributes(operation),
    )


async def _executed[Result](
    statement_calling: Callable[..., Coroutine[None, None, Result]],
    statement: str,
    args: tuple[PostgresValue, ...],
    retries: int,
    operation: str,
) -> Result:
    """Run the statement, recording what it cost and repeating transient failures.

    The recorded duration spans every attempt and the delays between them, so a
    statement which only succeeded on its third try reports what that took. It
    starts once a connection is in hand: a helper which acquires one does so
    before reaching here, and that wait is recorded separately by the driver as
    `db.client.connection.wait_time`. Adding the two gives what the caller waited
    for, while keeping a contended pool distinguishable from a slow statement.
    """
    started: float = monotonic()
    error: PostgresException | None = None
    try:
        return await _attempted(
            statement_calling,
            statement,
            args,
            retries,
            operation,
        )

    except PostgresException as exc:
        error = exc
        raise

    finally:
        # in `finally` so a failure and a cancellation are measured too - a
        # cancelled operation carries no SQLSTATE, so it reports as neither
        ctx.record_info(
            metric=OPERATION_DURATION_METRIC,
            value=monotonic() - started,
            unit="s",
            kind="histogram",
            attributes=operation_attributes(operation, error),
        )


async def _attempted[Result](
    statement_calling: Callable[..., Coroutine[None, None, Result]],
    statement: str,
    args: tuple[PostgresValue, ...],
    retries: int,
    operation: str,
) -> Result:
    """Execute the statement, repeating it while the failure is transient.

    Only a serialization failure or a detected deadlock is repeated - see
    :attr:`PostgresException.retriable`. Anything else is raised on the first
    attempt, because running it again would fail the same way.

    This repeats one statement, which is the whole unit of work only when the
    statement is its own transaction. Inside an explicit transaction the abort
    doomed the entire transaction, so no retry can succeed - the server rejects
    the next statement with ``in_failed_sql_transaction``, which explains
    nothing, so the failure that actually caused the abort is raised instead.
    """
    failure: PostgresException | None = None
    attempt: int = 0
    allowed: int = max(0, retries)
    while True:
        try:
            return await statement_calling(statement, *args)

        except PostgresException as exc:
            if failure is not None and exc.sqlstate == PostgresErrorCode.in_failed_sql_transaction:
                # the statement runs inside a transaction which the previous
                # failure already doomed, so retrying was never going to work
                raise failure from exc

            if attempt >= allowed or not exc.retriable:
                raise

            failure = exc

        attempt += 1
        # retries are invisible without this - a workload silently repeating a
        # third of its statements looks healthy from latency alone
        ctx.record_info(
            metric=OPERATION_RETRIES_METRIC,
            value=1,
            unit="{retry}",
            kind="counter",
            attributes=operation_attributes(operation, failure),
        )
        ctx.record_debug(
            event=OPERATION_RETRIED_EVENT,
            attributes={
                **operation_attributes(operation, failure),
                "db.client.operation.attempt": attempt,
                "db.client.operation.retries_allowed": allowed,
            },
        )
        # full jitter, so concurrent retries spread out instead of realigning
        await sleep(uniform(0, min(RETRY_BACKOFF_BASE * (2**attempt), RETRY_BACKOFF_LIMIT)))  # nosec: B311


class PostgresConnection(State):
    """Contextual API bound to a single acquired Postgres connection.

    Instances are produced by :class:`haiway.postgres.client.PostgresConnectionPool`
    and installed into the current Haiway scope while a connection is checked
    out from the underlying pool. The ``@statemethod`` descriptor allows the
    methods to be called either on the instance itself or on the class when the
    state is present in context.
    """

    @statemethod
    async def fetch_one(
        self,
        statement: str,
        /,
        *args: PostgresValue,
        retries: int = 0,
    ) -> PostgresRow | None:
        """Return the first row of a query or ``None`` when no data is found.

        Parameters
        ----------
        statement : str
            SQL statement executed against the active connection.
        *args : PostgresValue
            Positional parameters forwarded to the driver.
        retries : int, default=0
            How many times a transient failure - a serialization failure or a
            detected deadlock - is retried before raising. Meaningful when the
            statement is its own transaction; inside an explicit transaction the
            block has to be re-entered instead, see Notes.

        Returns
        -------
        PostgresRow | None
            First returned row wrapped as :class:`PostgresRow`, or ``None`` when
            the result set is empty.

        Notes
        -----
        ``retries`` repeats this statement alone. That is the whole unit of work
        under autocommit, but not inside an explicit transaction: a serialization
        failure aborts the transaction, so every later statement in it fails too
        and the block has to be re-run from the beginning. Retrying at that level
        means re-running the body, which this cannot do for you.
        """
        rows: Sequence[PostgresRow] = await _executed(
            self._statement_fetching,
            statement,
            args,
            retries,
            "fetch_one",
        )
        _record_returned_rows("fetch_one", rows)
        return next(iter(rows), None)

    @statemethod
    async def fetch(
        self,
        statement: str,
        /,
        *args: PostgresValue,
        retries: int = 0,
    ) -> Sequence[PostgresRow]:
        """Execute the statement and return all resulting rows.

        Parameters
        ----------
        statement : str
            SQL statement executed against the active connection.
        *args : PostgresValue
            Positional parameters forwarded to the driver.
        retries : int, default=0
            How many times a transient failure - a serialization failure or a
            detected deadlock - is retried before raising. Meaningful when the
            statement is its own transaction; inside an explicit transaction the
            block has to be re-entered instead, see Notes.

        Returns
        -------
        Sequence[PostgresRow]
            Immutable sequence of wrapped result rows.

        Notes
        -----
        ``retries`` repeats this statement alone. That is the whole unit of work
        under autocommit, but not inside an explicit transaction: a serialization
        failure aborts the transaction, so every later statement in it fails too
        and the block has to be re-run from the beginning. Retrying at that level
        means re-running the body, which this cannot do for you.
        """
        rows: Sequence[PostgresRow] = await _executed(
            self._statement_fetching,
            statement,
            args,
            retries,
            "fetch",
        )
        _record_returned_rows("fetch", rows)
        return rows

    @statemethod
    async def execute(
        self,
        statement: str,
        /,
        *args: PostgresValue,
        retries: int = 0,
    ) -> str:
        """Execute the statement without retrieving a result set.

        Parameters
        ----------
        statement : str
            SQL statement executed against the active connection. Passing no
            parameters allows a semicolon separated script, see Notes.
        *args : PostgresValue
            Positional parameters forwarded to the driver.
        retries : int, default=0
            How many times a transient failure - a serialization failure or a
            detected deadlock - is retried before raising. Meaningful when the
            statement is its own transaction; inside an explicit transaction the
            block has to be re-entered instead, see Notes.

        Returns
        -------
        str
            Raw command tag reported by the server, such as ``"UPDATE 3"``,
            ``"INSERT 0 1"``, or ``"CREATE TABLE"``. A script reports the tag of
            its last command only.

        Notes
        -----
        This does not retrieve rows. A statement with a result set still runs and
        the tag reports how many rows it produced, but the rows themselves are
        never transferred - use :meth:`fetch` to read them.

        With no parameters the statement goes through the simple query protocol,
        which carries a whole script, so several semicolon separated commands run
        in one round trip and nothing is prepared or cached. Passing parameters
        switches to the extended query protocol, which carries one command per
        request, so a parameterized script has to be split into separate calls.

        ``retries`` repeats this statement alone. That is the whole unit of work
        under autocommit, but not inside an explicit transaction: a serialization
        failure aborts the transaction, so every later statement in it fails too
        and the block has to be re-run from the beginning. Retrying at that level
        means re-running the body, which this cannot do for you.
        """
        return await _executed(
            self._statement_executing,
            statement,
            args,
            retries,
            "execute",
        )

    @statemethod
    def transaction(
        self,
        *,
        isolation: PostgresTransactionIsolation | None = None,
        readonly: bool = False,
        deferrable: bool = False,
    ) -> PostgresTransactionContext:
        """Prepare a transaction context bound to this connection.

        Parameters
        ----------
        isolation : PostgresTransactionIsolation | None, default=None
            Isolation level, one of ``"read_committed"``, ``"repeatable_read"``,
            or ``"serializable"``. The server default applies when omitted.
        readonly : bool, default=False
            Whether the transaction is READ ONLY.
        deferrable : bool, default=False
            Whether the transaction is DEFERRABLE. Only meaningful for a
            serializable read-only transaction.

        Returns
        -------
        PostgresTransactionContext
            Async context manager that commits on success and rolls back when an
            exception escapes the block.

        Notes
        -----
        Nesting is supported: a transaction prepared while another is already
        active becomes a savepoint. A savepoint carries no attributes of its
        own, so ``readonly`` and ``deferrable`` apply to the outermost
        transaction only, and a mismatching ``isolation`` is rejected.
        """
        return self._transaction_preparing(
            isolation=isolation,
            readonly=readonly,
            deferrable=deferrable,
        )

    _statement_fetching: PostgresStatementFetching
    _statement_executing: PostgresStatementExecuting
    _transaction_preparing: PostgresTransactionPreparing

    def __init__(
        self,
        statement_fetching: PostgresStatementFetching,
        statement_executing: PostgresStatementExecuting,
        transaction_preparing: PostgresTransactionPreparing,
    ) -> None:
        super().__init__(
            _statement_fetching=statement_fetching,
            _statement_executing=statement_executing,
            _transaction_preparing=transaction_preparing,
        )


class Postgres(State):
    """High-level Postgres service exposed as Haiway state.

    This state provides ergonomic query helpers that transparently acquire a
    connection when necessary and reuse the current
    :class:`PostgresConnection` when one is already present in the active
    context.
    """

    @statemethod
    def acquire_connection(self) -> PostgresConnectionContext:
        """Provide a disposable yielding a single contextual connection.

        Returns
        -------
        PostgresConnectionContext
            Async context manager that installs :class:`PostgresConnection`
            while the connection is acquired.

        Raises
        ------
        PostgresException
            If called while a :class:`PostgresConnection` is already present in
            the current scope.
        """
        if ctx.contains_state(PostgresConnection):
            raise PostgresException("Recursive Postgres connection acquiring is forbidden")

        return self._connection_acquiring()

    @statemethod
    async def execute_migrations(
        self,
        migrations: Sequence[PostgresMigrating] | str,
        /,
        timeout: int = 300,
    ) -> None:
        """Run sequential migrations against the current database.

        ``migrations`` accepts either a list of callables or a dotted module path
        containing ``migration_<n>`` submodules. Each migration runs inside its
        own transaction and increments the internal ``migrations`` table on
        success.

        Each recorded row also keeps the migration identifier and, for migrations
        discovered from a package, the SHA-256 checksum of the module source
        file. Already applied rows are compared against the current sequence
        before anything runs, and any drift - a renamed or reordered migration,
        or a source file edited after it was applied - is logged as a warning.
        Verification never fails the run: the applied count alone decides what
        still has to execute, and no automatic repair is possible once the
        database has moved on, so the finding is reported for a human to judge.

        Parameters
        ----------
        migrations : Sequence[PostgresMigrating] | str
            Explicit sequence of migration callables or dotted package path used
            for discovery.
        timeout : int, default=300
            Seconds any single statement may wait for a lock before raising, set
            as PostgreSQL ``lock_timeout`` for the whole migration session. It
            bounds acquiring the advisory lock and, because the setting is not
            statement scoped, every lock a migration itself waits on. ``0``
            disables the bound.

        Raises
        ------
        ValueError
            If discovered migration modules are not numbered continuously from
            ``migration_0``, or if the recorded database version exceeds the
            number of available migrations.
        Exception
            Re-raises any exception raised by a migration after logging it.
        """
        async with ctx.scope(
            "postgres_migrations",
            disposables=(self.acquire_connection(),),
        ):
            ctx.log_info("Preparing postgres migrations...")
            migration_sequence: Sequence[_Migration]
            if isinstance(migrations, str):
                ctx.log_info(f"...discovering migrations from {migrations}...")
                module: ModuleType = import_module(migrations)
                migration_sequence = [
                    _discovered_migration(
                        package=module.__name__,
                        name=name,
                    )
                    for name in _validated_migration_names(module=module)
                ]
                ctx.log_info(f"...found {len(migration_sequence)} migrations...")

            else:
                migration_sequence = [_provided_migration(migration) for migration in migrations]

            connection: PostgresConnection = ctx.state(PostgresConnection)
            try:
                await connection.execute(
                    MIGRATIONS_LOCK_TIMEOUT_STATEMENT,
                    f"{int(timeout)}s",
                )
                await connection.execute(
                    MIGRATIONS_ADVISORY_LOCK_STATEMENT,
                    MIGRATIONS_ADVISORY_LOCK_KEY,
                )
                # make sure migrations table exists and matches the expected shape
                await connection.execute(MIGRATIONS_TABLE_CREATE_STATEMENT)
                await connection.execute(MIGRATIONS_TABLE_UPGRADE_STATEMENT)
                # the applied rows are the version - reading them instead of
                # counting them costs nothing on a migrations table and makes the
                # recorded bookkeeping available for verification below
                applied: Sequence[PostgresRow] = await connection.fetch(
                    APPLIED_MIGRATIONS_FETCH_STATEMENT
                )
                current_version: int = len(applied)

                if current_version > len(migration_sequence):
                    raise ValueError(
                        f"Database version {current_version} exceeds"
                        f" available migrations {len(migration_sequence)}"
                    )

                _verify_migration_records(
                    applied=applied,
                    available=migration_sequence,
                )

                ctx.log_info(
                    f"...current database version: {current_version},"
                    f" migrations to apply: {len(migration_sequence) - current_version}..."
                )
                # perform migrations from current version to latest
                for idx, migration in enumerate(migration_sequence[current_version:]):
                    ctx.log_info(
                        f"...executing migration {current_version + idx}"
                        f" [{migration.identifier}]..."
                    )
                    try:
                        async with connection.transaction():
                            await migration.performing(connection)
                            await connection.execute(
                                MIGRATION_COMPLETION_STATEMENT,
                                migration.identifier,
                                migration.checksum,
                            )

                    except Exception as exc:
                        ctx.log_error(
                            f"...migration {current_version + idx} failed...",
                            exception=exc,
                        )
                        raise

                    else:
                        ctx.log_info(f"...migration {current_version + idx} completed...")

                ctx.log_info("...migrations completed successfully!")

            finally:
                # a failed migration commonly leaves the connection unusable, so
                # cleanup failures are reported rather than allowed to replace
                # the exception explaining why migrations failed. asyncpg also
                # runs pg_advisory_unlock_all() and RESET ALL when releasing the
                # connection, so this is best-effort tidying rather than required
                try:
                    await connection.execute(
                        MIGRATIONS_ADVISORY_UNLOCK_STATEMENT,
                        MIGRATIONS_ADVISORY_LOCK_KEY,
                    )

                except Exception as exc:
                    ctx.log_error(
                        "...failed to release migration lock state...",
                        exception=exc,
                    )

                try:
                    await connection.execute(
                        MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT,
                    )

                except Exception as exc:
                    ctx.log_error(
                        "...failed to reset migration lock timeout...",
                        exception=exc,
                    )

    @statemethod
    async def fetch_one(
        self,
        statement: str,
        /,
        *args: PostgresValue,
        retries: int = 0,
    ) -> PostgresRow | None:
        """Fetch a single row using a contextual or ad-hoc connection.

        When a :class:`PostgresConnection` is already present in context it is
        reused. Otherwise a temporary connection is acquired for the duration of
        the call.

        Parameters
        ----------
        statement : str
            SQL statement to execute.
        *args : PostgresValue
            Positional parameters forwarded to the driver.
        retries : int, default=0
            How many times a transient failure - a serialization failure or a
            detected deadlock - is retried before raising. Meaningful when the
            statement is its own transaction; inside an explicit transaction the
            block has to be re-entered instead.

        Returns
        -------
        PostgresRow | None
            First returned row, or ``None`` when the result set is empty.
        """
        if ctx.contains_state(PostgresConnection):
            return await PostgresConnection.fetch_one(statement, *args, retries=retries)

        async with self.acquire_connection() as connection:
            return await connection.fetch_one(statement, *args, retries=retries)

    @statemethod
    async def fetch(
        self,
        statement: str,
        /,
        *args: PostgresValue,
        retries: int = 0,
    ) -> Sequence[PostgresRow]:
        """Fetch all rows using a contextual or ad-hoc connection.

        When a :class:`PostgresConnection` is already present in context it is
        reused. Otherwise a temporary connection is acquired for the duration of
        the call.

        Parameters
        ----------
        statement : str
            SQL statement to execute.
        *args : PostgresValue
            Positional parameters forwarded to the driver.
        retries : int, default=0
            How many times a transient failure - a serialization failure or a
            detected deadlock - is retried before raising. Meaningful when the
            statement is its own transaction; inside an explicit transaction the
            block has to be re-entered instead.

        Returns
        -------
        Sequence[PostgresRow]
            Immutable sequence of all returned rows.
        """
        if ctx.contains_state(PostgresConnection):
            return await PostgresConnection.fetch(statement, *args, retries=retries)

        async with self.acquire_connection() as connection:
            return await connection.fetch(statement, *args, retries=retries)

    @statemethod
    async def execute(
        self,
        statement: str,
        /,
        *args: PostgresValue,
        retries: int = 0,
    ) -> str:
        """Execute a statement using a contextual or ad-hoc connection.

        When a :class:`PostgresConnection` is already present in context it is
        reused. Otherwise a temporary connection is acquired for the duration of
        the call.

        Parameters
        ----------
        statement : str
            SQL statement to execute. Without parameters this may be a semicolon
            separated script; with parameters it has to be a single statement, as
            the extended query protocol carries one command per request.
        *args : PostgresValue
            Positional parameters forwarded to the driver.
        retries : int, default=0
            How many times a transient failure - a serialization failure or a
            detected deadlock - is retried before raising. Meaningful when the
            statement is its own transaction; inside an explicit transaction the
            block has to be re-entered instead.

        Returns
        -------
        str
            Raw command tag reported by the server, such as ``"UPDATE 3"``.
        """
        if ctx.contains_state(PostgresConnection):
            return await PostgresConnection.execute(statement, *args, retries=retries)

        async with self.acquire_connection() as connection:
            return await connection.execute(statement, *args, retries=retries)

    _connection_acquiring: PostgresConnectionAcquiring

    def __init__(
        self,
        connection_acquiring: PostgresConnectionAcquiring,
    ) -> None:
        super().__init__(_connection_acquiring=connection_acquiring)


MIGRATIONS_TABLE_CREATE_STATEMENT: Final[str] = """\
CREATE TABLE IF NOT EXISTS migrations (
    id SERIAL PRIMARY KEY,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    identifier TEXT,
    checksum TEXT
);\
"""
# CREATE TABLE IF NOT EXISTS leaves an existing table alone, so a table created
# when the column was TIMESTAMP is converted here instead - guarded on the
# current type to avoid rewriting the table on every run. The conversion reads
# the naive values in the session time zone, which is the one they were written
# with, and runs under the advisory lock so it cannot race another migrator.
# The bookkeeping columns are added the same way, nullable so that rows recorded
# before they existed remain valid
MIGRATIONS_TABLE_UPGRADE_STATEMENT: Final[str] = """\
DO $upgrade$
DECLARE
    -- resolve through search_path exactly like the unqualified ALTER below does,
    -- so the guard inspects the very table which is about to be altered
    migrations_table OID := to_regclass('migrations');
BEGIN
    IF EXISTS (
        SELECT 1
        FROM pg_catalog.pg_attribute
        WHERE attrelid = migrations_table
            AND attname = 'executed_at'
            AND attnum > 0
            AND NOT attisdropped
            AND atttypid = 'pg_catalog.timestamp'::REGTYPE
    ) THEN
        ALTER TABLE migrations
            ALTER COLUMN executed_at TYPE TIMESTAMPTZ,
            ALTER COLUMN executed_at SET DEFAULT NOW();
    END IF;

    ALTER TABLE migrations
        ADD COLUMN IF NOT EXISTS identifier TEXT,
        ADD COLUMN IF NOT EXISTS checksum TEXT;
END $upgrade$;\
"""
MIGRATIONS_ADVISORY_LOCK_KEY: Final[int] = 1264456023
MIGRATIONS_ADVISORY_LOCK_STATEMENT: Final[str] = """\
SELECT pg_advisory_lock($1::BIGINT);\
"""
MIGRATIONS_ADVISORY_UNLOCK_STATEMENT: Final[str] = """\
SELECT pg_advisory_unlock($1::BIGINT);\
"""
# plain SET cannot be parameterized, set_config can
MIGRATIONS_LOCK_TIMEOUT_STATEMENT: Final[str] = """\
SELECT set_config('lock_timeout', $1, false);\
"""
# RESET restores the configured default instead of pinning the pooled connection to 0
MIGRATIONS_LOCK_TIMEOUT_RESET_STATEMENT: Final[str] = """\
RESET lock_timeout;\
"""
# ordered by the serial primary key, so the rows line up with the migration
# sequence positionally the same way the applied count does
APPLIED_MIGRATIONS_FETCH_STATEMENT: Final[str] = """\
SELECT identifier, checksum FROM migrations ORDER BY id ASC;\
"""
# bump version by adding a row to migrations table, keeping the applied
# migration identifier and source checksum for later auditing
MIGRATION_COMPLETION_STATEMENT: Final[str] = """\
INSERT INTO migrations (identifier, checksum) VALUES ($1::TEXT, $2::TEXT);\
"""


class _Migration(NamedTuple):
    identifier: str
    checksum: str | None
    performing: PostgresMigrating


def _verify_migration_records(
    applied: Sequence[PostgresRow],
    available: Sequence[_Migration],
) -> None:
    """Report drift between the applied migration records and current sources.

    Findings are logged rather than raised. The applied count is what decides
    which migrations still run, and a mismatch cannot be repaired from here -
    re-running or skipping against a database which already moved on would do
    more damage than the drift itself. Reporting leaves that call to a human.

    Rows recorded before the bookkeeping columns existed carry ``NULL`` in both,
    and migrations provided as callables have no source file to hash, so a
    missing value is treated as nothing to compare rather than as a mismatch.
    """
    for idx, (record, migration) in enumerate(zip(applied, available, strict=False)):
        identifier: str | None = record.get_str("identifier")
        if identifier is None:
            continue  # recorded before the identifier column existed

        if identifier != migration.identifier:
            ctx.log_warning(
                f"...migration {idx} was applied as '{identifier}'"
                f" but position {idx} now holds '{migration.identifier}'"
                " - the sequence was renamed or reordered..."
            )
            # a different migration entirely, comparing its checksum says nothing
            continue

        checksum: str | None = record.get_str("checksum")
        if checksum is None or migration.checksum is None:
            continue  # no source hash on either side, nothing to compare

        if checksum != migration.checksum:
            ctx.log_warning(
                f"...migration {idx} [{migration.identifier}] source changed"
                " since it was applied - the database reflects the previous"
                " contents of the file..."
            )


def _discovered_migration(
    package: str,
    name: str,
) -> _Migration:
    identifier: str = f"{package}.{name}"
    module: ModuleType = import_module(identifier)
    return _Migration(
        identifier=identifier,
        checksum=_module_checksum(module),
        performing=module.migration,
    )


def _provided_migration(
    migration: PostgresMigrating,
) -> _Migration:
    # migrations are structurally typed, dunder metadata is best effort here
    module_name: str = getattr(migration, "__module__", "")
    qualified_name: str = getattr(migration, "__qualname__", type(migration).__qualname__)
    return _Migration(
        identifier=f"{module_name}.{qualified_name}" if module_name else qualified_name,
        # only file based discovery has a source file to hash
        checksum=None,
        performing=migration,
    )


def _module_checksum(
    module: ModuleType,
) -> str | None:
    source_path: str | None = getattr(module, "__file__", None)
    if source_path is None:
        ctx.log_warning(f"...migration module {module.__name__} has no source file to hash...")
        return None

    try:
        return sha256(Path(source_path).read_bytes()).hexdigest()

    except OSError as exc:
        ctx.log_warning(
            f"...failed to hash migration module {module.__name__} source...",
            exception=exc,
        )
        return None


def _validated_migration_names(
    module: ModuleType,
) -> Generator[str]:
    names: list[str] = [
        module_name
        for _, module_name, _ in pkgutil.iter_modules(module.__path__)
        if module_name.startswith("migration_")
    ]
    yield from _validate_migration_names(names)


def _validate_migration_names(
    names: Sequence[str],
) -> Generator[str]:
    discovered: MutableMapping[int, str] = {}
    for module_name in names:
        if not module_name.startswith("migration_"):
            raise ValueError("Migration modules must start with 'migration_'")

        suffix: str = module_name[len("migration_") :]
        if not suffix.isdigit():
            raise ValueError(f"Migration module `{module_name}` suffix must be an integer")

        number: int = int(suffix)
        if number in discovered:
            raise ValueError("Migration modules must not contain duplicates")

        discovered[number] = module_name

    for idx in range(len(discovered)):
        if migration := discovered.get(idx):
            yield migration

        else:
            raise ValueError("Migrations numbers must use continuous values starting from '0'")
