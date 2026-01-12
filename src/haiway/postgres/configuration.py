from collections.abc import Sequence
from typing import Any, cast

from haiway.context import ctx
from haiway.helpers import ConfigurationRepository, cache
from haiway.helpers.configuration import Configuration
from haiway.postgres.state import Postgres
from haiway.postgres.types import PostgresRow
from haiway.types import Meta

__all__ = ("PostgresConfigurationRepository",)


def PostgresConfigurationRepository(
    cache_limit: int = 32,
    cache_expiration: float = 600.0,  # 10 min
) -> ConfigurationRepository:
    """Return a repository storing configuration snapshots in Postgres.

    Parameters
    ----------
    cache_limit: int = 32
        Maximum number of configuration documents kept in the in-memory cache.
    cache_expiration: float = 600.0
        Lifetime in seconds for cached entries before a fresh query is issued.

    Notes
    -----
    Requires the ``configurations`` table to exist; see the schema comment
    below or apply the appropriate Postgres migration before using this repository.

    Example schema:
    ```
    CREATE TABLE configurations (
        identifier TEXT NOT NULL,
        name TEXT NOT NULL,
        content JSONB NOT NULL,
        created TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
        PRIMARY KEY (identifier, created)
    );

    CREATE INDEX IF NOT EXISTS
        configurations_idx

    ON
        configurations (identifier, created DESC);
    ```
    """

    @cache(
        limit=64,
        expiration=cache_expiration,
    )
    async def listing(
        config: type[Configuration] | None,
        **extra: Any,
    ) -> Sequence[str]:
        ctx.log_info("Listing configurations...")
        results: Sequence[PostgresRow]
        if config is None:
            results = await Postgres.fetch(
                """
                SELECT DISTINCT ON (identifier)
                    identifier::TEXT

                FROM
                    configurations

                ORDER BY
                    identifier,
                    created
                DESC;
                """
            )
            ctx.log_info(f"...{len(results)} configurations found!")

        else:
            results = await Postgres.fetch(
                """
                SELECT DISTINCT ON (identifier)
                    identifier::TEXT

                FROM
                    configurations

                WHERE
                    name = $1

                ORDER BY
                    identifier,
                    created
                DESC;
                """,
                config.__name__,
            )

            ctx.log_info(f"...{len(results)} {config.__name__} configurations found!")

        return tuple(cast(str, record["identifier"]) for record in results)

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
            """
            SELECT DISTINCT ON (identifier)
                identifier::TEXT,
                name::TEXT,
                content::JSONB

            FROM
                configurations

            WHERE
                identifier = $1

            ORDER BY
                identifier,
                created
            DESC

            LIMIT 1;
            """,
            identifier,
        )

        if loaded is None:
            ctx.log_info("...configuration not found!")
            return None

        assert loaded["name"] == config.__name__  # nosec: B101
        assert isinstance(loaded["content"], str | bytes)  # nosec: B101
        ctx.log_info("...configuration loaded!")
        return config.from_json(cast(str, loaded["content"]))

    async def defining(
        identifier: str,
        value: Configuration,
        **extra: Any,
    ) -> None:
        ctx.log_info(f"Defining configuration {identifier}...")
        await Postgres.execute(
            """
            INSERT INTO
                configurations (
                    identifier,
                    name,
                    content
                )

            VALUES (
                $1::TEXT,
                $2::TEXT,
                $3::JSONB
            );
            """,
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
            """
            DELETE FROM
                configurations

            WHERE
                identifier = $1;
            """,
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
