from collections.abc import Sequence
from typing import Any, Final, final

from haiway.context import ctx
from haiway.helpers import ConfigurationRepository, cache
from haiway.helpers.configuration import Configuration, ConfigurationInvalid
from haiway.postgres.state import Postgres
from haiway.postgres.types import PostgresRow
from haiway.types import Meta

__all__ = ("PostgresConfigurationRepository",)


@final
class PostgresConfigurationRepository:
    """Provide Postgres-backed persistence for ``Configuration`` snapshots.

    This repository stores configuration values as append-only JSONB records in the
    ``configurations`` table, keyed by identifier and creation timestamp. Reads
    resolve the newest snapshot for a given identifier.
    """

    @staticmethod
    async def migrate() -> None:
        """Create database structures required by configuration repository.

        This asynchronous method creates the `configurations` table and
        its supporting index when they do not already exist.

        Returns
        -------
        None
            Completes when the schema migration statements finish.

        Raises
        ------
        Exception
            Raised when PostgreSQL command execution fails, for example due to
            connection or database-level errors.
        """
        await Postgres.execute(CONFIGURATIONS_TABLE_CREATE_STATEMENT)
        await Postgres.execute(CONFIGURATIONS_INDEX_CREATE_STATEMENT)

    @staticmethod
    def prepare(
        *,
        cache_limit: int = 64,
        cache_expiration: float = 600.0,  # 10 min
    ) -> ConfigurationRepository:
        """Return a repository storing configuration snapshots in Postgres.

        Parameters
        ----------
        cache_limit: int = 64
            Maximum number of configuration documents kept in the in-memory cache.
        cache_expiration: float = 600.0
            Lifetime in seconds for cached entries before a fresh query is issued.

        Returns
        -------
        ConfigurationRepository
            Repository state backed by the ``configurations`` Postgres table. New
            values are stored as append-only snapshots; reads resolve the newest row
            for each identifier.

        Notes
        -----
        Requires the ``configurations`` table to exist. Call :meth:`migrate` to
        create it, or apply an equivalent migration - the expected schema is
        ``CONFIGURATIONS_TABLE_CREATE_STATEMENT`` together with
        ``CONFIGURATIONS_INDEX_CREATE_STATEMENT``.

        The cache is per process and is cleared wholesale on every write, so a
        write in one process leaves the others serving the previous snapshot
        until ``cache_expiration`` elapses. Cache keys cover ``**extra`` as
        well, which therefore has to be hashable.
        """

        @cache(
            limit=cache_limit,
            expiration=cache_expiration,
        )
        async def listing(
            config: type[Configuration] | None,
            **extra: Any,
        ) -> Sequence[str]:
            ctx.log_info("Listing configurations...")
            results: Sequence[PostgresRow] = (
                await Postgres.fetch(CONFIGURATIONS_LIST_STATEMENT)
                if config is None
                else await Postgres.fetch(
                    CONFIGURATIONS_LIST_NAMED_STATEMENT,
                    config.__name__,
                )
            )
            ctx.log_info(f"...{len(results)} configurations found!")

            return tuple(row.get_str("identifier", required=True) for row in results)

        @cache(
            limit=cache_limit,
            expiration=cache_expiration,
        )
        async def loading[Config: Configuration](
            config: type[Config],
            identifier: str,
            **extra: Any,
        ) -> Config | None:
            ctx.log_info(f"Loading configuration for {identifier}...")
            loaded: PostgresRow | None = await Postgres.fetch_one(
                CONFIGURATION_FETCH_STATEMENT,
                identifier,
            )

            if loaded is None:
                ctx.log_info("...configuration not found!")
                return None

            # validated instead of asserted - the stored name and content shape
            # come from the database, so `-O` must not skip checking them
            name: str | None = loaded.get_str("name")
            if name != config.__name__:
                raise ConfigurationInvalid(
                    identifier=identifier,
                    reason=f"expected '{config.__name__}' configuration, stored is '{name}'",
                )

            content: Any = loaded["content"]
            if not isinstance(content, str | bytes):
                raise ConfigurationInvalid(
                    identifier=identifier,
                    reason=f"expected JSON content, got '{type(content).__name__}'",
                )

            ctx.log_info("...configuration loaded!")
            return config.from_json(content)

        async def defining(
            identifier: str,
            value: Configuration,
            **extra: Any,
        ) -> None:
            ctx.log_info(f"Defining configuration {identifier}...")
            await Postgres.execute(
                CONFIGURATION_INSERT_STATEMENT,
                identifier,
                value.__class__.__name__,
                value.to_json(),
            )
            ctx.log_info("...clearing cache...")
            await loading.clear_cache()
            await listing.clear_cache()
            ctx.log_info("...configuration definition completed!")

        async def removing(
            identifier: str,
            **extra: Any,
        ) -> None:
            ctx.log_info(f"Removing configuration {identifier}...")
            await Postgres.execute(
                CONFIGURATION_DELETE_STATEMENT,
                identifier,
            )
            ctx.log_info("...clearing cache...")
            await loading.clear_cache()
            await listing.clear_cache()
            ctx.log_info("...configuration removal completed!")

        return ConfigurationRepository(
            listing=listing,
            loading=loading,
            defining=defining,
            removing=removing,
            meta=Meta.of({"source": "postgres"}),
        )


# clock_timestamp() advances within a transaction, unlike CURRENT_TIMESTAMP which
# is the transaction start time and would collide on (identifier, created) for two
# snapshots of the same configuration stored within a single transaction
CONFIGURATIONS_TABLE_CREATE_STATEMENT: Final[str] = """\
CREATE TABLE IF NOT EXISTS configurations (
    identifier TEXT NOT NULL,
    name TEXT NOT NULL,
    content JSONB NOT NULL,
    created TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (identifier, created)
);\
"""
CONFIGURATIONS_INDEX_CREATE_STATEMENT: Final[str] = """\
CREATE INDEX IF NOT EXISTS configurations_idx ON configurations (identifier, created DESC);\
"""
# DISTINCT is sufficient - a filtered listing intentionally reports every
# identifier which ever held the requested configuration name
CONFIGURATIONS_LIST_STATEMENT: Final[str] = """\
SELECT DISTINCT identifier FROM configurations;\
"""
CONFIGURATIONS_LIST_NAMED_STATEMENT: Final[str] = """\
SELECT DISTINCT identifier FROM configurations WHERE name = $1;\
"""
# the identifier is pinned by the predicate, so ordering by created alone walks
# the (identifier, created DESC) index straight to the newest snapshot
CONFIGURATION_FETCH_STATEMENT: Final[str] = """\
SELECT name, content
FROM configurations
WHERE identifier = $1
ORDER BY created DESC
LIMIT 1;\
"""
# created is set explicitly so existing tables keep the intended semantics
# regardless of the column default they were created with
CONFIGURATION_INSERT_STATEMENT: Final[str] = """\
INSERT INTO configurations (identifier, name, content, created)
VALUES ($1::TEXT, $2::TEXT, $3::JSONB, clock_timestamp());\
"""
CONFIGURATION_DELETE_STATEMENT: Final[str] = """\
DELETE FROM configurations WHERE identifier = $1;\
"""
