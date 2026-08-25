from collections.abc import Callable, Mapping
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

__all__ = ("RabbitMQ",)


class RabbitMQ(State):
    @overload
    @classmethod
    async def queue[Content](
        cls,
        queue: str,
        /,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
        prefetch: int | None = None,
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]: ...
    @overload
    async def queue[Content](
        self,
        queue: str,
        /,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
        prefetch: int | None = None,
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]: ...
    @statemethod
    async def queue[Content](
        self,
        queue: str,
        /,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
        prefetch: int | None = None,
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]:
        """Acquire a typed RabbitMQ queue bound to the current state.

        Parameters
        ----------
        queue : str
            Name of the queue to access on the broker.
        content_encoder : Callable[[Content], bytes]
            Callable that serializes typed payloads to bytes before publish.
        content_decoder : Callable[[bytes], Content]
            Callable that deserializes received payloads into typed content.
        prefetch : int | None, default=None
            Maximum unacknowledged messages delivered to each consumer. ``None``
            resolves a bounded value from the ``RABBITMQ_PREFETCH`` environment
            variable; ``0`` means unlimited, which lets the whole backlog
            accumulate in memory.
        **extra : Any
            Reserved. Unsupported options raise ``RabbitMQException`` rather
            than being silently dropped.

        Returns
        -------
        AbstractAsyncContextManager[MQQueue[Content]]
            Context manager yielding an `MQQueue` configured with the provided
            encoder/decoder.

        Notes
        -----
        The encoder is invoked for every publish and the decoder for every
        consumed payload. Entering the context opens a channel and ensures
        clean teardown when the block exits.

        Publishes and consumers share that one channel, so a failure closing it
        ends both: pending confirmations fail, and consumers are re-established
        on the replacement channel unless recovery is disabled. Use a separate
        queue access where the two must not share a failure domain.

        Consumption is scoped by its own context manager. Leaving it cancels the
        consumer at the broker and requeues the deliveries it buffered but never
        handed out; leaving this context does the same for whatever is still
        running. Deliveries buffered when the channel itself drops are discarded
        instead, because the broker already requeued them.

        Examples
        --------
        ::

            async with await RabbitMQ.queue(
                "events",
                content_encoder=encode_event,
                content_decoder=decode_event,
            ) as queue:
                await queue.publish(event)

                async with await queue.consume() as messages:
                    async for message in messages:
                        ...
        """
        return await self.queue_accessing(
            queue,
            content_encoder=content_encoder,
            content_decoder=content_decoder,
            prefetch=prefetch,
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
        arguments: Mapping[str, Any] | None = None,
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
        arguments: Mapping[str, Any] | None = None,
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
        arguments: Mapping[str, Any] | None = None,
        **extra: Any,
    ) -> None:
        """Declare the queue, creating it when it does not exist.

        Parameters
        ----------
        queue : str
            Name of the queue to declare.
        passive : bool, default=False
            Only check for existence instead of creating the queue.
        durable : bool, default=False
            Whether the queue survives a broker restart.
        exclusive : bool, default=False
            Whether the queue is limited to this connection.
        auto_delete : bool, default=False
            Whether the queue is removed once the last consumer disconnects.
        arguments : Mapping[str, Any] | None, optional
            AMQP queue arguments, such as ``{"x-message-ttl": 60000}``.
        **extra : Any
            Reserved. Unsupported options raise ``RabbitMQException`` rather
            than being taken for queue arguments.
        """
        return await self.queue_declaring(
            queue=queue,
            passive=passive,
            durable=durable,
            exclusive=exclusive,
            auto_delete=auto_delete,
            arguments=arguments,
            **extra,
        )

    @overload
    @classmethod
    async def purge_queue(
        cls,
        queue: str,
        /,
    ) -> None: ...
    @overload
    async def purge_queue(
        self,
        queue: str,
        /,
    ) -> None: ...
    @statemethod
    async def purge_queue(
        self,
        queue: str,
        /,
    ) -> None:
        """Remove all messages from the queue.

        Parameters
        ----------
        queue : str
            Name of the queue to purge.
        """
        return await self.queue_purging(queue)

    @overload
    @classmethod
    async def delete_queue(
        cls,
        queue: str,
        /,
        *,
        if_unused: bool = False,
        if_empty: bool = False,
    ) -> None: ...
    @overload
    async def delete_queue(
        self,
        queue: str,
        /,
        *,
        if_unused: bool = False,
        if_empty: bool = False,
    ) -> None: ...
    @statemethod
    async def delete_queue(
        self,
        queue: str,
        /,
        *,
        if_unused: bool = False,
        if_empty: bool = False,
    ) -> None:
        """Delete the queue from the broker.

        Parameters
        ----------
        queue : str
            Name of the queue to delete.
        if_unused : bool, default=False
            Only delete the queue when it has no consumers.
        if_empty : bool, default=False
            Only delete the queue when it holds no messages.
        """
        return await self.queue_deleting(
            queue,
            if_unused=if_unused,
            if_empty=if_empty,
        )

    queue_accessing: RabbitMQQueueAccessing
    queue_declaring: RabbitMQQueueDeclaring
    queue_purging: RabbitMQQueuePurging
    queue_deleting: RabbitMQQueueDeleting
