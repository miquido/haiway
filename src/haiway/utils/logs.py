from datetime import datetime
from json import dumps
from logging import Formatter, LogRecord
from logging.config import dictConfig
from traceback import format_exception_only, format_tb
from types import TracebackType
from typing import Any, Final, final

from haiway.utils.env import getenv_bool

__all__ = (
    "JSONLogFormatter",
    "setup_logging",
)


def setup_logging(
    *loggers: str,
    formatter: Formatter = Formatter(  # noqa: B008
        fmt="%(asctime)s [%(levelname)-4s] [%(name)s] %(message)s",
        datefmt="%d/%b/%Y:%H:%M:%S %z",
    ),
    disable_existing_loggers: bool = True,
    debug: bool = getenv_bool("DEBUG_LOGGING", __debug__),
) -> None:
    """\
    Configure standard-library logging for the current process.

    Parameters
    ----------
    *loggers: str
        Names of additional loggers to configure explicitly alongside the root logger.
    formatter: Formatter
        Formatter applied to all configured loggers. The default includes timestamps
        with the local timezone offset. Provide a plain
        ``Formatter("[%(levelname)-4s] [%(name)s] %(message)s")`` to omit timestamps.
    disable_existing_loggers: bool = True
        Disable loggers that were created before calling this function.
    debug: bool = getenv_bool("DEBUG_LOGGING", __debug__)
        Whether to emit debug-level logs. The default is resolved from the
        ``DEBUG_LOGGING`` environment variable when this module is imported,
        falling back to ``__debug__``.

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
                "standard": {"()": lambda: formatter},
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


@final
class JSONLogFormatter(Formatter):
    """\
    Formatter rendering each log record as a single-line JSON object.

    Every attribute of the record becomes a field of the resulting object under its original
    name, including all fields passed through the ``extra`` argument of logging calls. Nothing
    is filtered out or renamed, only ``time`` and ``message`` are rendered on top.

    Fields left on the record by other formatters - ``message`` and ``asctime``, which the
    logging module refuses to accept through ``extra`` - are ignored, keeping the output
    independent of how many handlers rendered the same record before this one.

    Conflicts between the remaining record fields and rendered fields are caught by a
    debug-only assertion. Optimized builds contain no trace of that check, letting the record
    field win instead.

    Noise is reduced by omitting fields holding ``None``, which JSON ingestion treats the same
    as absent ones, and by rendering ``exc_info`` as the formatted traceback instead of its
    raw contents. Remaining values which are not natively JSON serializable are resolved to
    readable strings without memory addresses - exceptions to their message, types to their
    qualified name, tracebacks to their formatted frames and anything else through ``str``.
    Unsupported payloads can still fail rendering - a failing string conversion, a circular
    container or an invalid mapping key propagates out of the formatter, making
    ``Handler.emit`` call ``handleError`` and drop the record.

    Examples
    --------
    >>> setup_logging("app", formatter=JSONLogFormatter())
    >>> getLogger("app").info("processed %d items", 42, extra={"request_id": "req-7"})
    {"time": "2026-08-20T13:09:56.123+02:00", "message": "processed 42 items", "name": "app", \
"msg": "processed %d items", "args": [42], "levelname": "INFO", "levelno": 20, ..., \
"request_id": "req-7"}
    """

    def format(
        self,
        record: LogRecord,
    ) -> str:
        """\
        Render a log record as a JSON object.

        Parameters
        ----------
        record: LogRecord
            Record to render.

        Returns
        -------
        str
            Single-line JSON object containing all attributes of the record except those
            holding ``None``.

        Raises
        ------
        AssertionError
            When record fields conflict with rendered fields. Debug builds only.
        """

        payload: dict[str, Any] = {
            "time": self.formatTime(record, self.datefmt)
            if self.datefmt is not None
            else datetime.fromtimestamp(record.created)
            .astimezone()
            .isoformat(timespec="milliseconds"),
            "message": record.getMessage(),
        }

        assert not (payload.keys() & record.__dict__.keys()) - _FORMATTER_FIELDS, (  # nosec: B101
            f"Log fields conflict with rendered fields:"
            f" {sorted((payload.keys() & record.__dict__.keys()) - _FORMATTER_FIELDS)}"
        )

        payload.update(
            (key, value)
            for key, value in record.__dict__.items()
            if value is not None and key not in _FORMATTER_FIELDS
        )

        if record.exc_info is not None:
            payload["exc_info"] = self.formatException(record.exc_info)

        return dumps(payload, default=_resolved_value)


def _resolved_value(value: Any) -> Any:
    """
    Resolve a value which is not natively JSON serializable to a readable representation.

    Parameters
    ----------
    value: Any
        Value rejected by the JSON encoder.

    Returns
    -------
    Any
        Readable representation of the value, free of memory addresses where possible.
    """

    match value:
        case BaseException():
            return "".join(format_exception_only(value)).strip()

        case TracebackType():
            return "".join(format_tb(value)).strip()

        case type():
            return value.__qualname__

        case _ if type(value).__str__ is object.__str__ and type(value).__repr__ is object.__repr__:
            # default repr carries a memory address, making every line unique for no gain
            return f"<{type(value).__qualname__}>"

        case _:
            return str(value)


# `logging.Formatter.format` leaves its rendered message - and its rendered time
# when a date format is used - on the shared record, so a handler rendering the
# same record afterwards sees them. `Logger.makeRecord` rejects both names in
# `extra`, which makes their presence a formatter artifact rather than caller data
_FORMATTER_FIELDS: Final[frozenset[str]] = frozenset(("message", "asctime"))
