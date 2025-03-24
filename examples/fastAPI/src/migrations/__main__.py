from asyncio import run

from haiway import ctx, setup_logging

from integrations.postgres import PostgresConnectionPool
from migrations.postgres import execute_postgres_migrations


async def migrate_databases() -> None:
    async with ctx.scope(
        "migrations",
        disposables=[
            PostgresConnectionPool(),
        ],
    ):
        ctx.log_warning("Running postgres migrations...")
        await execute_postgres_migrations()
        ctx.log_info("...postgres migrations completed")


def main() -> None:
    setup_logging("migrations")
    run(migrate_databases())


main()
