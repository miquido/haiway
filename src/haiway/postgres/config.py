"""Environment-driven configuration defaults for the Postgres integration."""

from typing import Final

from haiway.utils import getenv_int, getenv_str

__all__ = (
    "POSTGRES_CONNECTIONS",
    "POSTGRES_DATABASE",
    "POSTGRES_HOST",
    "POSTGRES_PASSWORD",
    "POSTGRES_PORT",
    "POSTGRES_SSLMODE",
    "POSTGRES_USER",
)

POSTGRES_DATABASE: Final[str] = getenv_str(
    "POSTGRES_DATABASE",
    default="postgres",
)
POSTGRES_HOST: Final[str] = getenv_str(
    "POSTGRES_HOST",
    default="localhost",
)
POSTGRES_PORT: Final[str] = getenv_str(
    "POSTGRES_PORT",
    default="5432",
)
POSTGRES_USER: Final[str] = getenv_str(
    "POSTGRES_USER",
    default="postgres",
)
POSTGRES_PASSWORD: Final[str] = getenv_str(
    "POSTGRES_PASSWORD",
    default="postgres",
)
POSTGRES_SSLMODE: Final[str] = getenv_str(
    "POSTGRES_SSLMODE",
    default="prefer",
)
POSTGRES_CONNECTIONS: Final[int] = getenv_int(
    "POSTGRES_CONNECTIONS",
    default=1,
)
