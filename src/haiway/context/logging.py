from contextvars import ContextVar, Token
from logging import DEBUG, ERROR, INFO, WARNING, Logger, getLogger
from time import monotonic
from types import TracebackType
from typing import Any, Self, final

from haiway.context.identifier import ScopeIdentifier

__all__ = [
    "LoggerContext",
]


@final
class LoggerContext:
    _context = ContextVar[Self]("LoggerContext")

    @classmethod
    def scope(
        cls,
        scope: ScopeIdentifier,
        /,
        *,
        logger: Logger | None,
    ) -> Self:
        current: Self
        try:  # check for current scope
            current = cls._context.get()

        except LookupError:
            # create root scope when missing
            return cls(
                scope=scope,
                logger=logger,
            )

        # create nested scope otherwise
        return cls(
            scope=scope,
            logger=logger or current._logger,
        )

    @classmethod
    def log_error(
        cls,
        message: str,
        /,
        *args: Any,
        exception: BaseException | None = None,
    ) -> None:
        try:
            cls._context.get().log(
                ERROR,
                message,
                *args,
                exception=exception,
            )

        except LookupError:
            getLogger().log(
                ERROR,
                message,
                *args,
                exc_info=exception,
            )

    @classmethod
    def log_warning(
        cls,
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
    ) -> None:
        try:
            cls._context.get().log(
                WARNING,
                message,
                *args,
                exception=exception,
            )

        except LookupError:
            getLogger().log(
                WARNING,
                message,
                *args,
                exc_info=exception,
            )

    @classmethod
    def log_info(
        cls,
        message: str,
        /,
        *args: Any,
    ) -> None:
        try:
            cls._context.get().log(
                INFO,
                message,
                *args,
            )

        except LookupError:
            getLogger().log(
                INFO,
                message,
                *args,
            )

    @classmethod
    def log_debug(
        cls,
        message: str,
        /,
        *args: Any,
        exception: Exception | None = None,
    ) -> None:
        try:
            cls._context.get().log(
                DEBUG,
                message,
                *args,
                exception=exception,
            )

        except LookupError:
            getLogger().log(
                DEBUG,
                message,
                *args,
                exc_info=exception,
            )

    __slots__ = (
        "_entered",
        "_logger",
        "_prefix",
        "_token",
    )

    def __init__(
        self,
        scope: ScopeIdentifier,
        logger: Logger | None,
    ) -> None:
        self._prefix: str
        object.__setattr__(
            self,
            "_prefix",
            scope.unique_name,
        )
        self._logger: Logger
        object.__setattr__(
            self,
            "_logger",
            logger or getLogger(name=scope.label),
        )
        self._token: Token[LoggerContext] | None
        object.__setattr__(
            self,
            "_token",
            None,
        )
        self._entered: float | None
        object.__setattr__(
            self,
            "_entered",
            None,
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    def log(
        self,
        level: int,
        message: str,
        /,
        *args: Any,
        exception: BaseException | None = None,
    ) -> None:
        self._logger.log(
            level,
            f"{self._prefix} {message}",
            *args,
            exc_info=exception,
        )

    def __enter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        assert self._entered is None, "Context reentrance is not allowed"  # nosec: B101
        object.__setattr__(
            self,
            "_token",
            LoggerContext._context.set(self),
        )
        object.__setattr__(
            self,
            "_entered",
            monotonic(),
        )
        self.log(DEBUG, "Entering context...")

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert (  # nosec: B101
            self._token is not None and self._entered is not None
        ), "Unbalanced context enter/exit"
        LoggerContext._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )
        self.log(DEBUG, f"...exiting context after {monotonic() - self._entered:.2f}s")
        object.__setattr__(
            self,
            "_entered",
            None,
        )
