from asyncio import CancelledError, sleep
from collections.abc import Callable, Generator, MutableMapping
from typing import Any

import pytest
from pytest import MonkeyPatch, raises

from haiway.postgres import PostgresException
from haiway.postgres import client as client_module
from haiway.postgres.client import PostgresConnectionPool

# every POSTGRES_* variable resolves when a pool is constructed, so the ambient
# environment would otherwise decide what the tests below observe
POSTGRES_VARIABLES = (
    "POSTGRES_ACQUIRE_TIMEOUT",
    "POSTGRES_CLOSE_TIMEOUT",
    "POSTGRES_COMMAND_TIMEOUT",
    "POSTGRES_CONNECTIONS",
    "POSTGRES_DATABASE",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_SSLMODE",
    "POSTGRES_STATEMENT_CACHE",
    "POSTGRES_USER",
)


@pytest.fixture(autouse=True)
def clear_postgres_environment(monkeypatch: MonkeyPatch) -> None:
    for name in POSTGRES_VARIABLES:
        monkeypatch.delenv(name, raising=False)


class _FakePool:
    """Stand-in for an ``asyncpg`` pool, awaited on creation like the real one."""

    def __await__(self) -> Generator[Any, None, _FakePool]:
        async def ready() -> _FakePool:
            return self

        return ready().__await__()

    async def close(self) -> None:
        pass

    def terminate(self) -> None:
        pass


class _AcquireContext:
    """Stand-in for the context ``Pool.acquire`` returns."""

    async def __aenter__(self) -> object:
        return object()  # only closed over by the connection state

    async def __aexit__(
        self,
        *args: Any,
    ) -> None:
        pass


class _AcquiringPool(_FakePool):
    """Pool recording the timeout every acquisition is bounded by."""

    def __init__(self) -> None:
        self.acquire_timeouts: list[float | None] = []

    def acquire(
        self,
        *,
        timeout: float | None = None,
    ) -> _AcquireContext:
        self.acquire_timeouts.append(timeout)
        return _AcquireContext()


def _fixed_create_pool(
    pool: _FakePool,
    /,
) -> Callable[..., _FakePool]:
    def create_pool(
        dsn: str | None = None,
        **kwargs: Any,
    ) -> _FakePool:
        return pool

    return create_pool


def _recording_create_pool(
    recorded: MutableMapping[str, Any],
    /,
) -> Callable[..., _FakePool]:
    def create_pool(
        dsn: str | None = None,
        **kwargs: Any,
    ) -> _FakePool:
        recorded.update(kwargs)
        recorded["dsn"] = dsn
        return _FakePool()

    return create_pool


async def _driver_arguments(
    pool: PostgresConnectionPool,
    /,
    monkeypatch: MonkeyPatch,
) -> MutableMapping[str, Any]:
    """Enter the pool and return what reached ``asyncpg.create_pool``.

    The configuration is captured by the closure creating the pool rather than
    kept as attributes, so what the driver is called with is what there is to
    assert on.
    """
    recorded: MutableMapping[str, Any] = {}
    monkeypatch.setattr(client_module, "create_pool", _recording_create_pool(recorded))

    async with pool:
        pass

    return recorded


async def _acquire_timeouts(
    pool: PostgresConnectionPool,
    /,
    monkeypatch: MonkeyPatch,
) -> list[float | None]:
    """Enter the pool, acquire once, and return the timeouts asyncpg was given."""
    acquiring_pool = _AcquiringPool()
    monkeypatch.setattr(client_module, "create_pool", _fixed_create_pool(acquiring_pool))

    async with pool:
        async with pool.acquire_connection():
            pass

    return acquiring_pool.acquire_timeouts


@pytest.mark.asyncio
async def test_postgres_connection_pool_of_keeps_the_dsn_verbatim(
    monkeypatch: MonkeyPatch,
) -> None:
    # a dsn is a complete specification - it reaches asyncpg exactly as written,
    # percent encoded credentials and unmodelled options included
    dsn = "postgresql://us%40er:p%40ss%2Fword@db.example.com:5433/my%20db?application_name=svc"
    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool.of(dsn),
        monkeypatch,
    )

    assert recorded["dsn"] == dsn
    # nothing is merged into it - no connection argument accompanies the dsn,
    # which asyncpg would give precedence over what the dsn defines
    assert "host" not in recorded
    assert "user" not in recorded
    assert "password" not in recorded
    assert "ssl" not in recorded
    assert recorded["max_size"] == 1


@pytest.mark.asyncio
async def test_postgres_connection_pool_of_configures_the_pool_by_arguments(
    monkeypatch: MonkeyPatch,
) -> None:
    # pool behavior cannot be expressed by a connection string, so it stays
    # configured by arguments - with no dsn parameter competing for precedence
    pool = PostgresConnectionPool.of(
        "postgresql://db.internal/service",
        connection_limit=4,
        acquire_timeout=5.0,
        command_timeout=1.5,
        close_timeout=2.5,
    )

    assert await _acquire_timeouts(pool, monkeypatch) == [5.0]

    recorded: MutableMapping[str, Any] = await _driver_arguments(pool, monkeypatch)

    assert recorded["max_size"] == 4
    assert recorded["command_timeout"] == 1.5


@pytest.mark.asyncio
async def test_postgres_connection_pool_arguments_configure_the_dsn_less_connection(
    monkeypatch: MonkeyPatch,
) -> None:
    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool(),
        monkeypatch,
    )

    assert recorded["dsn"] is None
    assert recorded["host"] == "localhost"
    assert recorded["port"] == "5432"
    assert recorded["database"] == "postgres"
    assert recorded["user"] == "postgres"
    # ssl defaults to an encrypted connection which cannot fall back to plaintext
    assert recorded["ssl"] == "require"
    # an unset password is passed to the driver as absent, not as a guessable default
    assert recorded["password"] is None
    assert recorded["max_size"] == 1
    # zero disables the per-statement limit, which asyncpg spells as None
    assert recorded["command_timeout"] is None
    assert recorded["statement_cache_size"] == 100
    assert await _acquire_timeouts(PostgresConnectionPool(), monkeypatch) == [30.0]


@pytest.mark.asyncio
async def test_postgres_connection_pool_reads_the_environment_when_constructed(
    monkeypatch: MonkeyPatch,
) -> None:
    # the variables are exported after this module was imported, which is what an
    # ordinary `load_env()` in `main()` does - reading them at import time would
    # leave every argument below at its built in default
    monkeypatch.setenv("POSTGRES_HOST", "db.prod.internal")
    monkeypatch.setenv("POSTGRES_PORT", "6432")
    monkeypatch.setenv("POSTGRES_DATABASE", "service")
    monkeypatch.setenv("POSTGRES_USER", "service_user")
    monkeypatch.setenv("POSTGRES_PASSWORD", "secret")
    monkeypatch.setenv("POSTGRES_SSLMODE", "verify-full")
    monkeypatch.setenv("POSTGRES_CONNECTIONS", "9")
    monkeypatch.setenv("POSTGRES_ACQUIRE_TIMEOUT", "2.5")
    monkeypatch.setenv("POSTGRES_COMMAND_TIMEOUT", "1.5")
    monkeypatch.setenv("POSTGRES_CLOSE_TIMEOUT", "3.5")
    monkeypatch.setenv("POSTGRES_STATEMENT_CACHE", "0")

    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool(),
        monkeypatch,
    )

    assert recorded["host"] == "db.prod.internal"
    assert recorded["port"] == "6432"
    assert recorded["database"] == "service"
    assert recorded["user"] == "service_user"
    assert recorded["password"] == "secret"
    assert recorded["ssl"] == "verify-full"
    assert recorded["max_size"] == 9
    assert recorded["command_timeout"] == 1.5
    assert recorded["statement_cache_size"] == 0
    assert await _acquire_timeouts(PostgresConnectionPool(), monkeypatch) == [2.5]


@pytest.mark.asyncio
async def test_postgres_connection_pool_of_reads_the_environment_for_pool_settings(
    monkeypatch: MonkeyPatch,
) -> None:
    # a dsn owns the connection, the pool settings still come from the environment
    monkeypatch.setenv("POSTGRES_CONNECTIONS", "6")
    monkeypatch.setenv("POSTGRES_STATEMENT_CACHE", "0")

    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool.of(
            "postgresql://db.internal/service",
            statement_cache=32,
        ),
        monkeypatch,
    )

    assert recorded["max_size"] == 6
    # an explicit argument wins over the environment
    assert recorded["statement_cache_size"] == 32


@pytest.mark.asyncio
async def test_postgres_connection_pool_arguments_win_over_the_environment(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.setenv("POSTGRES_HOST", "db.prod.internal")

    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool(host="db.staging.internal"),
        monkeypatch,
    )

    assert recorded["host"] == "db.staging.internal"


def test_postgres_connection_pool_rendering_carries_no_credentials() -> None:
    # the configuration is captured by closures rather than kept as attributes,
    # so nothing renders it into a log or an observability payload
    field_rendering = str(PostgresConnectionPool(password="super-secret"))
    assert "super-secret" not in field_rendering

    dsn_rendering = str(PostgresConnectionPool.of("postgresql://user:super-secret@db/service"))
    assert "super-secret" not in dsn_rendering


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "mode",
    ("disable", "allow", "prefer", "require", "verify-ca", "verify-full"),
)
async def test_postgres_dsn_sslmode_is_resolved_by_asyncpg(
    mode: str,
    monkeypatch: MonkeyPatch,
) -> None:
    # sslmode is a libpq parameter asyncpg consumes itself, so it stays in the dsn
    dsn = f"postgresql://localhost/db?sslmode={mode}"
    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool.of(dsn),
        monkeypatch,
    )

    assert recorded["dsn"] == dsn
    assert "ssl" not in recorded


@pytest.mark.asyncio
async def test_synchronous_pool_rejection_arrives_as_postgres_exception() -> None:
    # asyncpg validates pool arguments synchronously, inside create_pool rather
    # than while awaiting it - those failures have to be translated too
    pool = PostgresConnectionPool(connection_limit=0, ssl="disable")

    with raises(PostgresException) as exc_info:
        async with pool:
            pass  # pragma: no cover

    assert "Failed to create Postgres connection pool" in str(exc_info.value)
    assert isinstance(exc_info.value.__cause__, ValueError)
    assert pool._pool is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_initialize_defaults_to_no_init_hook(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # asyncpg's own default is None; a no-op hook would cost an await per
    # newly created connection
    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool(ssl="disable"),
        monkeypatch,
    )

    assert recorded["init"] is None


@pytest.mark.asyncio
async def test_close_failure_is_reported_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # a failure closing the pool must not mask whatever is leaving the scope
    class _FailingPool(_FakePool):
        async def close(self) -> None:
            raise RuntimeError("close boom")

    monkeypatch.setattr(client_module, "create_pool", _fixed_create_pool(_FailingPool()))

    pool = PostgresConnectionPool(ssl="disable")
    async with pool:  # must not raise on exit
        pass

    assert pool._pool is None  # pyright: ignore[reportPrivateUsage]


class _HangingPool(_FakePool):
    """Pool whose close never completes, as one with a leaked connection."""

    def __init__(self) -> None:
        self.terminated: list[bool] = []

    async def close(self) -> None:
        try:
            await sleep(30)

        except CancelledError:
            self.terminate()  # what asyncpg's own close() does
            raise

    def terminate(self) -> None:
        self.terminated.append(True)


@pytest.mark.asyncio
async def test_close_timeout_expires_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pool.close() never completes while a connection is leaked, so the wait is
    # bounded - asyncpg terminates the pool itself when the wait is cancelled
    hanging_pool = _HangingPool()
    monkeypatch.setattr(client_module, "create_pool", _fixed_create_pool(hanging_pool))

    pool = PostgresConnectionPool(ssl="disable", close_timeout=0.01)
    async with pool:  # must not raise on exit
        pass

    assert hanging_pool.terminated == [True]
    assert pool._pool is None  # pyright: ignore[reportPrivateUsage]


@pytest.mark.asyncio
async def test_close_timeout_is_read_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the bound on closing is captured when the pool is constructed, so the
    # variable has to be resolved there rather than at import time
    monkeypatch.setenv("POSTGRES_CLOSE_TIMEOUT", "0.01")
    hanging_pool = _HangingPool()
    monkeypatch.setattr(client_module, "create_pool", _fixed_create_pool(hanging_pool))

    async with PostgresConnectionPool(ssl="disable"):  # must not raise on exit
        pass

    assert hanging_pool.terminated == [True]


@pytest.mark.asyncio
async def test_statement_cache_defaults_to_the_driver_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool(ssl="disable"),
        monkeypatch,
    )

    assert recorded["statement_cache_size"] == 100


@pytest.mark.asyncio
async def test_statement_cache_can_be_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 0 makes asyncpg prepare unnamed statements, leaving no session state for a
    # transaction pooling proxy to hand to the wrong backend
    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool(ssl="disable", statement_cache=0),
        monkeypatch,
    )

    assert recorded["statement_cache_size"] == 0


@pytest.mark.asyncio
async def test_statement_cache_applies_to_a_dsn_pool(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # asyncpg resolves it as a client setting rather than a connection parameter,
    # so a dsn cannot carry it and this argument is the only way to reach it
    recorded: MutableMapping[str, Any] = await _driver_arguments(
        PostgresConnectionPool.of(
            "postgresql://localhost/test?sslmode=disable",
            statement_cache=0,
        ),
        monkeypatch,
    )

    assert recorded["dsn"] == "postgresql://localhost/test?sslmode=disable"
    assert recorded["statement_cache_size"] == 0
