from logging.config import dictConfig

from haiway.utils.env import getenv_bool

__all__ = ("setup_logging",)


def setup_logging(
    *loggers: str,
    time: bool = True,
    debug: bool = getenv_bool("DEBUG_LOGGING", __debug__),
    disable_existing_loggers: bool = True,
) -> None:
    """\
    Configure standard-library logging for the current process.

    Parameters
    ----------
    *loggers: str
        Names of additional loggers to configure explicitly alongside the root logger.
    time: bool = True
        Include timestamps in log output. When enabled, timestamps include the
        local timezone offset.
    debug: bool = getenv_bool("DEBUG_LOGGING", __debug__)
        Whether to emit debug-level logs. The default is resolved from the
        ``DEBUG_LOGGING`` environment variable when this module is imported,
        falling back to ``__debug__``.
    disable_existing_loggers: bool = True
        Disable loggers that were created before calling this function.

    Returns
    -------
    None
        ``setup_logging`` configures logging in place and does not return a value.

    Raises
    ------
    ValueError
        Propagated when ``setup_logging`` receives an invalid logging configuration.
    OSError
        Propagated when ``setup_logging`` cannot access stdout while creating the console handler.

    Notes
    -----
    This helper configures the root logger plus any explicitly named loggers
    to write to stdout. It should normally be called once during application
    startup.
    """

    dictConfig(
        config={
            "version": 1,
            "disable_existing_loggers": disable_existing_loggers,
            "formatters": {
                "standard": {
                    "format": "%(asctime)s [%(levelname)-4s] [%(name)s] %(message)s",
                    "datefmt": "%d/%b/%Y:%H:%M:%S %z",
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
