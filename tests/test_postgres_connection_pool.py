from pytest import raises

from haiway.postgres.client import PostgresConnectionPool
from haiway.postgres.config import (
    POSTGRES_CONNECTIONS,
    POSTGRES_DATABASE,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_SSLMODE,
    POSTGRES_USER,
)


def test_postgres_connection_pool_of_parses_dsn_components() -> None:
    pool = PostgresConnectionPool.of(
        "postgresql://alice:secret@db.example.com:5433/sample?sslmode=require",
    )

    assert pool.host == "db.example.com"
    assert pool.port == "5433"
    assert pool.database == "sample"
    assert pool.user == "alice"
    assert pool.password == "secret"
    assert pool.ssl == "require"
    assert pool.connection_limit == POSTGRES_CONNECTIONS


def test_postgres_connection_pool_of_applies_defaults_when_missing_components() -> None:
    pool = PostgresConnectionPool.of(
        "postgresql://db.internal/service",
        ssl="verify-full",
        connection_limit=4,
    )

    assert pool.host == "db.internal"
    assert pool.port == POSTGRES_PORT
    assert pool.database == "service"
    assert pool.user == POSTGRES_USER
    assert pool.password == POSTGRES_PASSWORD
    assert pool.ssl == "verify-full"
    assert pool.connection_limit == 4


def test_postgres_connection_pool_of_uses_defaults_when_database_missing() -> None:
    pool = PostgresConnectionPool.of("postgresql://localhost")

    assert pool.host == "localhost"
    assert pool.port == POSTGRES_PORT
    assert pool.database == POSTGRES_DATABASE
    assert pool.user == POSTGRES_USER
    assert pool.password == POSTGRES_PASSWORD
    assert pool.ssl == POSTGRES_SSLMODE


def test_postgres_connection_pool_of_rejects_unknown_scheme() -> None:
    with raises(ValueError, match="Unsupported Postgres DSN scheme"):
        PostgresConnectionPool.of("mysql://user:pass@db.example.com:3306/sample")
