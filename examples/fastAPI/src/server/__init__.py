from haiway import load_env, setup_logging

load_env()  # load env first if needed
setup_logging("server")  # then setup logging before loading the app

from server.application import app

__all__ = [
    "app",
]
