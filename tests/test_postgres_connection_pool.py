from pytest import raises

from haiway.postgres.client import PostgresConnectionPool
from haiway.postgres.config import (
    POSTGRES_CONNECTIONS,
    POSTGRES_DATABASE,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
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
    assert pool.ssl is True
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
    assert getattr(pool.ssl, "check_hostname", False) is True
    assert pool.connection_limit == 4


def test_postgres_connection_pool_of_uses_defaults_when_database_missing() -> None:
    pool = PostgresConnectionPool.of("postgresql://localhost")

    assert pool.host == "localhost"
    assert pool.port == POSTGRES_PORT
    assert pool.database == POSTGRES_DATABASE
    assert pool.user == POSTGRES_USER
    assert pool.password == POSTGRES_PASSWORD
    assert pool.ssl is None


def test_postgres_connection_pool_of_rejects_unknown_scheme() -> None:
    with raises(ValueError, match="Unsupported Postgres DSN scheme"):
        PostgresConnectionPool.of("mysql://user:pass@db.example.com:3306/sample")


def test_postgres_sslmode_disable_maps_to_bool_false() -> None:
    pool = PostgresConnectionPool.of("postgresql://localhost?sslmode=disable")
    assert pool.ssl is False


def test_postgres_sslmode_require_maps_to_bool_true() -> None:
    pool = PostgresConnectionPool.of("postgresql://localhost?sslmode=require")
    assert pool.ssl is True


def test_postgres_sslmode_verify_full_creates_context() -> None:
    pool = PostgresConnectionPool.of("postgresql://localhost?sslmode=verify-full")
    assert pool.ssl is not None
    assert getattr(pool.ssl, "check_hostname", False) is True


def test_postgres_sslmode_unknown_raises_value_error() -> None:
    with raises(ValueError, match="Unsupported sslmode"):
        PostgresConnectionPool.of("postgresql://localhost?sslmode=weird")
