from collections.abc import Callable, Mapping
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol, runtime_checkable

from haiway.helpers import MQQueue

__all__ = (
    "RabbitMQException",
    "RabbitMQQueueAccessing",
    "RabbitMQQueueDeclaring",
    "RabbitMQQueueDeleting",
    "RabbitMQQueuePurging",
)


class RabbitMQException(Exception):
    """Raised when an operation through the RabbitMQ adapter fails.

    Wraps ``pika`` failures and adapter misuse so application code can handle
    broker errors through a stable Haiway-specific type.

    Parameters
    ----------
    message : str
        Description of the failure.
    operation : str | None, optional
        Adapter operation that failed, such as ``"publish"`` or ``"consume"``.
    queue : str | None, optional
        Queue the failed operation targeted.
    cause : Exception | None, optional
        Underlying driver exception, preserved as ``__cause__``.
    retryable : bool, default=False
        Whether repeating the same operation can reasonably succeed.

    Attributes
    ----------
    operation : str | None
        Adapter operation that failed, when known.
    queue : str | None
        Queue the failed operation targeted, when known.
    retryable : bool
        ``True`` for transient broker-side failures - a lost connection or
        channel, a timeout, a broker rejection, a consumer the broker cancelled
        - where repeating the operation is a sensible response. ``False`` for
        failures repeating cannot fix: adapter misuse, an unroutable message, a
        payload the encoder could not serialize, or settling a delivery whose
        channel is already gone. Callers branch on it instead of matching on
        the message text.

    Notes
    -----
    Message payloads are deliberately excluded from the message so their
    contents are never surfaced through error handling.
    """

    def __init__(
        self,
        message: str,
        *,
        operation: str | None = None,
        queue: str | None = None,
        cause: Exception | None = None,
        retryable: bool = False,
    ) -> None:
        context_parts: list[str] = []
        if operation:
            context_parts.append(f"operation={operation}")

        if queue:
            context_parts.append(f"queue={queue}")

        context: str = f" ({', '.join(context_parts)})" if context_parts else ""
        super().__init__(f"{message}{context}")
        self.operation: str | None = operation
        self.queue: str | None = queue
        self.retryable: bool = retryable
        # assigning __cause__ also sets __suppress_context__, which would hide
        # the implicit exception context, so only assign when there is a cause
        if cause is not None:
            self.__cause__ = cause


@runtime_checkable
class RabbitMQQueueAccessing(Protocol):
    async def __call__[Content](
        self,
        queue: str,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
        prefetch: int | None = None,
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]: ...


@runtime_checkable
class RabbitMQQueueDeclaring(Protocol):
    async def __call__(
        self,
        queue: str,
        passive: bool = False,
        durable: bool = False,
        exclusive: bool = False,
        auto_delete: bool = False,
        arguments: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class RabbitMQQueuePurging(Protocol):
    async def __call__(
        self,
        queue: str,
    ) -> None: ...


@runtime_checkable
class RabbitMQQueueDeleting(Protocol):
    async def __call__(
        self,
        queue: str,
        if_unused: bool = False,
        if_empty: bool = False,
    ) -> None: ...
