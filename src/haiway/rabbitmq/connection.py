from asyncio import AbstractEventLoop, Future, Lock, get_running_loop, timeout
from collections.abc import AsyncGenerator, AsyncIterable, Callable, Mapping
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from pika import BaseConnection, BasicProperties, URLParameters
from pika.adapters.asyncio_connection import AsyncioConnection
from pika.channel import Channel
from pika.connection import Parameters
from pika.spec import Basic

from haiway.context import ctx
from haiway.helpers import MQMessage, MQQueue
from haiway.rabbitmq.config import RABBITMQ_URL
from haiway.types import FlatObject, Meta
from haiway.utils import AsyncQueue

__all__ = ("RabbitMQConnection",)


class RabbitMQConnection:
    __slots__ = (
        "_connection",
        "_connection_timeout",
        "_lock",
        "_loop",
        "_parameters",
    )

    def __init__(
        self,
        url: str = RABBITMQ_URL,
        connection_timeout: float = 5.0,
        loop: AbstractEventLoop | None = None,
    ) -> None:
        self._lock: Lock = Lock()
        self._loop: AbstractEventLoop | None = loop
        self._parameters: Parameters = URLParameters(url)
        self._connection: BaseConnection | None = None
        self._connection_timeout: float = connection_timeout

    def _require_loop(self) -> AbstractEventLoop:
        if self._loop is None:
            self._loop = get_running_loop()

        return self._loop

    async def _queue_access[Content](  # noqa: C901
        self,
        queue: str,
        content_encoder: Callable[[Content], bytes],
        content_decoder: Callable[[bytes], Content],
        **extra: Any,
    ) -> AbstractAsyncContextManager[MQQueue[Content]]:
        @asynccontextmanager
        async def context() -> AsyncGenerator[MQQueue[Content]]:  # noqa: C901
            lock: Lock = Lock()
            channel: Channel = await self._open_channel()
            messages_queue = AsyncQueue[MQMessage[Content]]()

            async def ensure_open() -> None:
                nonlocal channel

                if channel.is_open:
                    return  # already open

                async with lock:
                    if channel.is_open:
                        return  # reopened elsewhere

                    channel = await self._open_channel()

            async def publish_message(
                message: Content,
                attributes: FlatObject | None,
                exchange: str | None = None,
                **extra: Any,
            ) -> None:
                await ensure_open()

                channel.basic_publish(
                    exchange=exchange if exchange is not None else "",
                    routing_key=queue,
                    body=content_encoder(message),
                    **extra,
                )

            async def consume_messages(
                requeue_rejected: bool = True,
                **extra: Any,
            ) -> AsyncIterable[MQMessage[Content]]:
                await ensure_open()

                def consume_message(
                    channel: Channel,
                    method: Basic.Deliver,
                    properties: BasicProperties,
                    body: bytes,
                ) -> None:
                    if not method.delivery_tag:
                        ctx.log_error(
                            "Received invalid message: Missing delivery tag",
                            exception=Exception("Message delivery_tag is missing"),
                        )
                        return  # can't process

                    try:
                        content: Content = content_decoder(body)

                    except Exception as exc:
                        channel.basic_reject(
                            delivery_tag=method.delivery_tag,
                            requeue=requeue_rejected,
                        )
                        ctx.log_error(
                            f"Failed to decode message content: {exc}",
                            exception=exc,
                        )
                        return  # can't process

                    async def acknowledge(
                        **extra: Any,
                    ) -> None:
                        channel.basic_ack(
                            delivery_tag=method.delivery_tag,
                            **extra,
                        )

                    async def reject(
                        requeue: bool | None = None,
                        **extra: Any,
                    ) -> None:
                        channel.basic_reject(
                            delivery_tag=method.delivery_tag,
                            requeue=requeue if requeue is not None else requeue_rejected,
                            **extra,
                        )

                    headers: Mapping[str, Any] = properties.headers or {}

                    messages_queue.enqueue(
                        MQMessage(
                            content=content,
                            acknowledge=acknowledge,
                            reject=reject,
                            meta=Meta(attempt=headers.get("x-redelivery-count", 0)),
                        ),
                    )

                channel.basic_consume(
                    queue=queue,
                    on_message_callback=consume_message,
                    **extra,
                )

                return messages_queue

            yield MQQueue[Content](
                publishing=publish_message,
                consuming=consume_messages,
            )
            messages_queue.finish()
            channel.close()

        return context()

    async def _queue_declare(
        self,
        queue: str,
        passive: bool = False,
        durable: bool = False,
        exclusive: bool = False,
        auto_delete: bool = False,
        **extra: Any,
    ) -> None:
        channel: Channel = await self._open_channel()
        loop: AbstractEventLoop = self._require_loop()
        declare_future: Future[Any] = loop.create_future()
        channel.queue_declare(
            queue=queue,
            passive=passive,
            durable=durable,
            exclusive=exclusive,
            arguments=extra,
            auto_delete=auto_delete,
            callback=declare_future.set_result,
        )
        await declare_future
        channel.close()

    async def _queue_purge(
        self,
        queue: str,
        **extra: Any,
    ) -> None:
        loop: AbstractEventLoop = self._require_loop()
        channel: Channel = await self._open_channel()
        purge_future: Future[Any] = loop.create_future()
        channel.queue_purge(
            queue=queue,
            callback=purge_future.set_result,
            **extra,
        )
        await purge_future
        channel.close()

    async def _queue_delete(
        self,
        queue: str,
        **extra: Any,
    ) -> None:
        loop: AbstractEventLoop = self._require_loop()
        channel: Channel = await self._open_channel()
        delete_future: Future[Any] = loop.create_future()
        channel.queue_delete(
            queue=queue,
            callback=delete_future.set_result,
            **extra,
        )
        await delete_future
        channel.close()

    async def _open_channel(self) -> Channel:
        connection: BaseConnection = await self._ensure_connection()

        loop: AbstractEventLoop = self._require_loop()
        ready: Future[Any] = loop.create_future()

        def channel_ready(channel: Channel) -> None:
            channel.basic_qos(callback=ready.set_result)

        channel: Channel = connection.channel(on_open_callback=channel_ready)

        await ready

        return channel

    async def _ensure_connection(self) -> BaseConnection:
        loop: AbstractEventLoop = self._require_loop()
        async with timeout(delay=self._connection_timeout):
            async with self._lock:
                if self._connection is not None and self._connection.is_open:
                    return self._connection  # connection already available

                ctx.log_info("Opening rabbitmq connection...")
                connected: Future[Any] = loop.create_future()
                self._connection = AsyncioConnection(
                    parameters=self._parameters,
                    on_open_callback=connected.set_result,
                    custom_ioloop=loop,
                )

                await connected

                ctx.log_info("...rabbitmq connection open!")

                return self._connection

    async def _disconnect(self) -> None:
        async with self._lock:
            if self._connection is None:
                return  # no connection available

            self._connection.close()
            self._connection = None
