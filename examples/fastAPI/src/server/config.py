from typing import Final

from haiway import getenv_int, getenv_str

__all__ = [
    "SERVER_HOST",
    "SERVER_PORT",
]

SERVER_HOST: Final[str] = getenv_str("SERVER_HOST", default="localhost")
SERVER_PORT: Final[int] = getenv_int("SERVER_PORT", default=8080)
