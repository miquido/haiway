from typing import Any

from haiway.context.observability import ContextObservability, ObservabilityLevel

__all__ = (
    "record_debug",
    "record_error",
    "record_warning",
)


# the managed tasks package is imported by `ctx`, so it records through the observability
# directly - never with an argument, a value or an exception message of a task


def record_debug(
    message: str,
    /,
    *args: Any,
) -> None:
    ContextObservability.record_log(
        ObservabilityLevel.DEBUG,
        message,
        args,
        exception=None,
    )


def record_warning(
    message: str,
    /,
    *args: Any,
) -> None:
    ContextObservability.record_log(
        ObservabilityLevel.WARNING,
        message,
        args,
        exception=None,
    )


def record_error(
    message: str,
    /,
    *args: Any,
) -> None:
    ContextObservability.record_log(
        ObservabilityLevel.ERROR,
        message,
        args,
        exception=None,
    )
