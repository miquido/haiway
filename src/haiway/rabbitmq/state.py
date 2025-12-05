from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from typing import Any, overload

from haiway.attributes import State
from haiway.helpers import MQQueue, statemethod
from haiway.rabbitmq.types import (
    RabbitMQQueueAccessing,
    RabbitMQQueueDeclaring,
    RabbitMQQueueDeleting,
    RabbitMQQueuePurging,
)
from haiway.types import BasicObject

__all__ = ("RabbitMQ",)


class RabbitMQ(State):
    @overload
    @classmethod
    async def queue[Content](
        cls,
        queue: str,
        /,
        content_encoder: Callable[[Content], BasicObject],
        content_decoder: Callable[[BasicObject], Content],
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]: ...

    @overload
    async def queue[Content](
        self,
        queue: str,
        /,
        content_encoder: Callable[[Content], BasicObject],
        content_decoder: Callable[[BasicObject], Content],
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]: ...

    @statemethod
    async def queue[Content](
        self,
        queue: str,
        /,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]:
        """
        Acquire an async context manager for a typed RabbitMQ queue bound to the current state.

        Parameters
        ----------
        queue : str
            Name of the queue to access on the broker.
        content_encoder : Callable[[Content], BasicObject]
            Callable that transforms typed payloads into broker-serializable objects before publish.
        content_decoder : Callable[[BasicObject], Content]
            Callable that converts received broker payloads into typed content for consumers.
        **extra : Any
            Additional options forwarded to the underlying queue accessor
            (e.g., channel parameters or QoS).

        Returns
        -------
        AbstractAsyncContextManager[MQQueue[Content]]
            Context manager yielding an `MQQueue` configured with the provided encoder/decoder.

        Notes
        -----
        Typical usage::

            async with RabbitMQ.queue(
                "events",
                content_encoder=encode_event,
                content_decoder=decode_event,
            ) as queue:
                await queue.publish(event)
                async for message in await queue.consume():
                    ...

        The encoder is invoked for every publish and the decoder for every consumed payload.
        Entering the context establishes queue access and ensures clean teardown when
        the block exits.
        """
        return await self.queue_accessing(
            queue,
            content_encoder=content_encoder,
            content_decoder=content_decoder,
            **extra,
        )

    @overload
    @classmethod
    async def declare_queue(
        cls,
        queue: str,
        /,
        *,
        passive: bool = False,
        durable: bool = False,
        exclusive: bool = False,
        auto_delete: bool = False,
        **extra: Any,
    ) -> None: ...

    @overload
    async def declare_queue(
        self,
        queue: str,
        /,
        *,
        passive: bool = False,
        durable: bool = False,
        exclusive: bool = False,
        auto_delete: bool = False,
        **extra: Any,
    ) -> None: ...

    @statemethod
    async def declare_queue(
        self,
        queue: str,
        /,
        *,
        passive: bool = False,
        durable: bool = False,
        exclusive: bool = False,
        auto_delete: bool = False,
        **extra: Any,
    ) -> None:
        return await self.queue_declaring(
            queue=queue,
            passive=passive,
            durable=durable,
            exclusive=exclusive,
            auto_delete=auto_delete,
            **extra,
        )

    @overload
    @classmethod
    async def purge_queue(
        cls,
        queue: str,
        /,
        **extra: Any,
    ) -> None: ...

    @overload
    async def purge_queue(
        self,
        queue: str,
        /,
        **extra: Any,
    ) -> None: ...

    @statemethod
    async def purge_queue(
        self,
        queue: str,
        /,
        **extra: Any,
    ) -> None:
        return await self.queue_purging(
            queue,
            **extra,
        )

    @overload
    @classmethod
    async def delete_queue(
        cls,
        queue: str,
        /,
        **extra: Any,
    ) -> None: ...

    @overload
    async def delete_queue(
        self,
        queue: str,
        /,
        **extra: Any,
    ) -> None: ...

    @statemethod
    async def delete_queue(
        self,
        queue: str,
        /,
        **extra: Any,
    ) -> None:
        return await self.queue_deleting(
            queue,
            **extra,
        )

    queue_accessing: RabbitMQQueueAccessing
    queue_declaring: RabbitMQQueueDeclaring
    queue_purging: RabbitMQQueuePurging
    queue_deleting: RabbitMQQueueDeleting
