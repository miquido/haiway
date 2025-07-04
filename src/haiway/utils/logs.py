from logging.config import dictConfig

from haiway.utils.env import getenv_bool

__all__ = ("setup_logging",)


def setup_logging(
    *loggers: str,
    time: bool = True,
    debug: bool = getenv_bool("DEBUG_LOGGING", __debug__),
) -> None:
    """\
    Setup logging configuration and prepare specified loggers.

    Parameters
    ----------
    *loggers: str
        names of additional loggers to configure.
    time: bool = True
        include timestamps in logs.
    debug: bool = __debug__
        include debug logs.

    NOTE: this function should be run only once on application start
    """

    dictConfig(
        config={
            "version": 1,
            "disable_existing_loggers": True,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s [%(levelname)-4s] [%(name)s] %(message)s",
                    "datefmt": "%d/%b/%Y:%H:%M:%S +0000",
                }
                if time
                else {
                    "format": "[%(levelname)-4s] [%(name)s] %(message)s",
                },
            },
            "handlers": {
                "console": {
                    "level": "DEBUG" if debug else "INFO",
                    "formatter": "standard",
                    "class": "logging.StreamHandler",
                    "stream": "ext://sys.stdout",
                },
            },
            "loggers": {
                name: {
                    "handlers": ["console"],
                    "level": "DEBUG" if debug else "INFO",
                    "propagate": False,
                }
                for name in loggers
            },
            "root": {  # root logger
                "handlers": ["console"],
                "level": "DEBUG" if debug else "INFO",
                "propagate": False,
            },
        },
    )
