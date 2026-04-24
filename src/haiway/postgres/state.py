import pkgutil
from collections.abc import Generator, MutableMapping, Sequence
from importlib import import_module
from types import ModuleType
from typing import Any, Final, overload

from haiway.attributes import State
from haiway.context import ctx
from haiway.helpers import statemethod
from haiway.postgres.types import (
    PostgresConnectionAcquiring,
    PostgresConnectionContext,
    PostgresMigrating,
    PostgresRow,
    PostgresStatementExecuting,
    PostgresTransactionContext,
    PostgresTransactionPreparing,
)

__all__ = (
    "Postgres",
    "PostgresConnection",
    "PostgresConnectionContext",
    "PostgresTransactionContext",
)


class PostgresConnection(State):
    """Contextual API bound to a single acquired Postgres connection.

    Instances are produced by :class:`haiway.postgres.client.PostgresConnectionPool`
    and installed into the current Haiway scope while a connection is checked
    out from the underlying pool. The ``@statemethod`` descriptor allows the
    methods to be called either on the instance itself or on the class when the
    state is present in context.
    """

    @overload
    @classmethod
    async def fetch_one(
        cls,
        statement: str,
        /,
        *args: Any,
    ) -> PostgresRow | None: ...

    @overload
    async def fetch_one(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> PostgresRow | None: ...

    @statemethod
    async def fetch_one(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> PostgresRow | None:
        """Return the first row of a query or ``None`` when no data is found.

        Parameters
        ----------
        statement : str
            SQL statement executed against the active connection.
        *args : Any
            Positional parameters forwarded to the driver.

        Returns
        -------
        PostgresRow | None
            First returned row wrapped as :class:`PostgresRow`, or ``None`` when
            the result set is empty.
        """

        return next(
            iter(
                await self._statement_executing(
                    statement,
                    *args,
                )
            ),
            None,
        )

    @overload
    @classmethod
    async def fetch(
        cls,
        statement: str,
        /,
        *args: Any,
    ) -> Sequence[PostgresRow]: ...

    @overload
    async def fetch(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> Sequence[PostgresRow]: ...

    @statemethod
    async def fetch(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> Sequence[PostgresRow]:
        """Execute the statement and return all resulting rows.

        Parameters
        ----------
        statement : str
            SQL statement executed against the active connection.
        *args : Any
            Positional parameters forwarded to the driver.

        Returns
        -------
        Sequence[PostgresRow]
            Immutable sequence of wrapped result rows.
        """

        return await self._statement_executing(
            statement,
            *args,
        )

    @overload
    @classmethod
    async def execute(
        cls,
        statement: str,
        /,
        *args: Any,
    ) -> None: ...

    @overload
    async def execute(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> None: ...

    @statemethod
    async def execute(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> None:
        """Execute the statement while discarding any returned rows.

        Parameters
        ----------
        statement : str
            SQL statement executed against the active connection.
        *args : Any
            Positional parameters forwarded to the driver.
        """

        await self._statement_executing(
            statement,
            *args,
        )

    @statemethod
    def transaction(self) -> PostgresTransactionContext:
        """Prepare a transaction context bound to this connection.

        Returns
        -------
        PostgresTransactionContext
            Async context manager that commits on success and rolls back when an
            exception escapes the block.
        """

        return self._transaction_preparing()

    _statement_executing: PostgresStatementExecuting
    _transaction_preparing: PostgresTransactionPreparing

    def __init__(
        self,
        statement_executing: PostgresStatementExecuting,
        transaction_preparing: PostgresTransactionPreparing,
    ) -> None:
        super().__init__(
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
        RuntimeError
            If called while a :class:`PostgresConnection` is already present in
            the current scope.
        """

        if ctx.contains_state(PostgresConnection):
            raise RuntimeError("Recursive Postgres connection acquiring is forbidden")

        return self._connection_acquiring()

    @overload
    @classmethod
    async def execute_migrations(
        cls,
        migrations: Sequence[PostgresMigrating] | str,
        /,
    ) -> None: ...

    @overload
    async def execute_migrations(
        self,
        migrations: Sequence[PostgresMigrating] | str,
        /,
    ) -> None: ...

    @statemethod
    async def execute_migrations(
        self,
        migrations: Sequence[PostgresMigrating] | str,
        /,
    ) -> None:
        """Run sequential migrations against the current database.

        ``migrations`` accepts either a list of callables or a dotted module path
        containing ``migration_<n>`` submodules. Each migration runs inside its
        own transaction and increments the internal ``migrations`` table on
        success.

        Parameters
        ----------
        migrations : Sequence[PostgresMigrating] | str
            Explicit sequence of migration callables or dotted package path used
            for discovery.

        Raises
        ------
        ValueError
            If discovered migration modules are not numbered continuously from
            ``migration_0``.
        Exception
            Re-raises any exception raised by a migration after logging it.
        """

        async with ctx.scope(
            "postgres_migrations",
            disposables=(self.acquire_connection(),),
        ):
            ctx.log_info("Preparing postgres migrations...")
            migration_sequence: Sequence[PostgresMigrating]
            if isinstance(migrations, str):
                ctx.log_info(f"...discovering migrations from {migrations}...")
                module: ModuleType = import_module(migrations)

                migration_sequence = [
                    import_module(f"{module.__name__}.{name}").migration
                    for name in _validated_migration_names(module=module)
                ]
                ctx.log_info(f"...found {len(migration_sequence)} migrations...")

            else:
                migration_sequence = migrations

            assert all(isinstance(migration, PostgresMigrating) for migration in migration_sequence)  # nosec: B101

            connection: PostgresConnection = ctx.state(PostgresConnection)
            # make sure migrations table exists
            await connection.execute(MIGRATIONS_TABLE_CREATE_STATEMENT)
            # get current version
            fetched_version: PostgresRow | None = await connection.fetch_one(
                CURRENT_MIGRATIONS_FETCH_STATEMENT
            )
            current_version: int
            if fetched_version is None:
                current_version = 0

            else:
                current_version = fetched_version.get_int("count", default=0)

            ctx.log_info(
                f"...current database version: {current_version},"
                f" migrations to apply: {len(migration_sequence) - current_version}..."
            )

            # perform migrations from current version to latest
            for idx, migration in enumerate(migration_sequence[current_version:]):
                ctx.log_info(f"...executing migration {current_version + idx}...")
                try:
                    async with connection.transaction():
                        await migration(connection)

                        await connection.execute(MIGRATION_COMPLETION_STATEMENT)

                except Exception as exc:
                    ctx.log_error(
                        f"...migration  {current_version + idx} failed...",
                        exception=exc,
                    )
                    raise

                else:
                    ctx.log_info(f"...migration  {current_version + idx} completed...")

            ctx.log_info("...migrations completed successfully!")

    @overload
    @classmethod
    async def fetch_one(
        cls,
        statement: str,
        /,
        *args: Any,
    ) -> PostgresRow | None: ...

    @overload
    async def fetch_one(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> PostgresRow | None: ...

    @statemethod
    async def fetch_one(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> PostgresRow | None:
        """Fetch a single row using a contextual or ad-hoc connection.

        When a :class:`PostgresConnection` is already present in context it is
        reused. Otherwise a temporary connection is acquired for the duration of
        the call.
        """

        if ctx.contains_state(PostgresConnection):
            return await PostgresConnection.fetch_one(statement, *args)

        async with self.acquire_connection() as connection:
            return await connection.fetch_one(statement, *args)

    @statemethod
    async def fetch(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> Sequence[PostgresRow]:
        """Fetch all rows using a contextual or ad-hoc connection.

        When a :class:`PostgresConnection` is already present in context it is
        reused. Otherwise a temporary connection is acquired for the duration of
        the call.
        """

        if ctx.contains_state(PostgresConnection):
            return await PostgresConnection.fetch(statement, *args)

        async with self.acquire_connection() as connection:
            return await connection.fetch(statement, *args)

    @statemethod
    async def execute(
        self,
        statement: str,
        /,
        *args: Any,
    ) -> None:
        """Execute a statement using a contextual or ad-hoc connection.

        When a :class:`PostgresConnection` is already present in context it is
        reused. Otherwise a temporary connection is acquired for the duration of
        the call.
        """

        if ctx.contains_state(PostgresConnection):
            return await PostgresConnection.execute(statement, *args)

        async with self.acquire_connection() as connection:
            return await connection.execute(statement, *args)

    _connection_acquiring: PostgresConnectionAcquiring

    def __init__(
        self,
        connection_acquiring: PostgresConnectionAcquiring,
    ) -> None:
        super().__init__(_connection_acquiring=connection_acquiring)


MIGRATIONS_TABLE_CREATE_STATEMENT: Final[str] = """\
CREATE TABLE IF NOT EXISTS migrations (
    id SERIAL PRIMARY KEY,
    executed_at TIMESTAMP NOT NULL DEFAULT NOW()
);\
"""

CURRENT_MIGRATIONS_FETCH_STATEMENT: Final[str] = """\
SELECT COUNT(*) as count FROM migrations;\
"""

# bump version by adding a row to migrations table
MIGRATION_COMPLETION_STATEMENT: Final[str] = """\
INSERT INTO migrations DEFAULT VALUES;\
"""


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
