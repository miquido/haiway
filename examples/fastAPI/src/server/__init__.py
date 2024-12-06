from haiway import load_env

load_env()  # load env first if needed


from server.application import app

__all__ = [
    "app",
]
