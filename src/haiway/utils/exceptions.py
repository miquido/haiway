from asyncio import CancelledError
from collections.abc import Sequence
from types import TracebackType
from typing import cast

__all__ = (
    "raise_collected",
    "thrown_exception",
)


def thrown_exception(
    typ: type[BaseException] | BaseException,
    val: object = None,
    tb: TracebackType | None = None,
    /,
) -> BaseException:
    """
    Resolve the arguments of a generator ``athrow`` call into a single exception.

    Mirrors the argument handling of ``Generator.throw`` - an exception instance
    may not be paired with a separate value, a type is instantiated with the
    value when one was provided unless the value is already an instance of that
    type, and a traceback is attached when given.

    Parameters
    ----------
    typ : type[BaseException] | BaseException
        Exception type or instance which was thrown in
    val : object, default=None
        Exception instance or its argument when a type was provided
    tb : TracebackType | None, default=None
        Optional traceback to attach to the exception

    Returns
    -------
    BaseException
        The exception to raise

    Raises
    ------
    TypeError
        When an exception instance was paired with a separate value
    """
    exception: BaseException
    if isinstance(typ, BaseException):
        if val is not None:
            raise TypeError("Instance exception may not have a separate value")

        exception = typ

    elif val is None:
        exception = typ()

    elif isinstance(val, typ):
        exception = val

    else:
        exception = typ(val)

    if tb is not None:
        return exception.with_traceback(tb)

    return exception


def raise_collected(
    exceptions: Sequence[BaseException],
    /,
    message: str,
) -> None:
    match exceptions:
        case ():
            return  # no errors

        case (exception,):
            # raising replaces the carefully prepared context - keep it to restore
            fixed_context: BaseException | None = exception.__context__
            try:
                raise exception

            except BaseException:
                exception.__context__ = fixed_context
                raise

        case _:
            if all(isinstance(exc, CancelledError) for exc in exceptions):
                raise CancelledError()  # cancelled

            if all(isinstance(exc, Exception) for exc in exceptions):
                raise ExceptionGroup(message, cast(Sequence[Exception], exceptions))

            raise BaseExceptionGroup(message, exceptions)
