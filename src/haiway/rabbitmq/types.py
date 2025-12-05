from collections.abc import Callable
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
    pass


@runtime_checkable
class RabbitMQQueueAccessing(Protocol):
    async def __call__[Content](
        self,
        queue: str,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
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
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class RabbitMQQueuePurging(Protocol):
    async def __call__(
        self,
        queue: str,
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class RabbitMQQueueDeleting(Protocol):
    async def __call__(
        self,
        queue: str,
        **extra: Any,
    ) -> None: ...
