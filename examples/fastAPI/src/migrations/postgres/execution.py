import os
import pkgutil
from collections.abc import Sequence
from importlib import import_module

from haiway import ctx

from integrations.postgres import Postgres, PostgresConnection, PostgresRow
from migrations.postgres.types import Migration

__all__ = [
    "execute_postgres_migrations",
]

# auto load all Migration classes from all files starting with "migration_" and sort by number
MIGRATIONS: Sequence[Migration] = [
    import_module(name=f"migrations.postgres.{name}").execute_migration
    for name, _ in sorted(
        [
            (module, int(module[len("migration_") :]))
            for _, module, _ in pkgutil.iter_modules([os.path.dirname(__file__)])
            if module.startswith("migration_")
        ],
        key=lambda element: int(element[1]),
    )
]


async def execute_postgres_migrations() -> None:
    async with ctx.scope(
        "postgres_migrations",
        disposables=(Postgres.connection(),),
    ):
        # make sure migrations table exists
        await PostgresConnection.execute(MIGRATIONS_TABLE_CREATE_STATEMENT)
        # get current version
        fetched_version: Sequence[PostgresRow] = await PostgresConnection.fetch(
            CURRENT_MIGRATIONS_FETCH_STATEMENT
        )
        current_version: int
        if fetched_version:
            current_version = int(fetched_version[0].get("count", 0))

        else:
            current_version = 0

        ctx.log_info(f"Current database version: {current_version}")

        # perform migrations from current version to latest
        for idx, migration in enumerate(MIGRATIONS[current_version:]):
            ctx.log_info(f"Executing migration {current_version + idx}")
            try:
                async with PostgresConnection.transaction():
                    await migration()

                    await PostgresConnection.execute(MIGRATION_COMPLETION_STATEMENT)

            except Exception as exc:
                ctx.log_info(f"Migration  {current_version + idx} failed")
                raise exc

            else:
                ctx.log_info(f"Migration  {current_version + idx} completed")


MIGRATIONS_TABLE_CREATE_STATEMENT: str = """\
CREATE TABLE IF NOT EXISTS migrations (
    id SERIAL PRIMARY KEY,
    executed_at TIMESTAMP NOT NULL DEFAULT NOW()
);\
"""

CURRENT_MIGRATIONS_FETCH_STATEMENT: str = """\
SELECT COUNT(*) as count FROM migrations;\
"""

# bump version by adding a row to migrations table
MIGRATION_COMPLETION_STATEMENT: str = """\
INSERT INTO migrations DEFAULT VALUES;\
"""
