from types import TracebackType
from typing import final

from haiway.rabbitmq.connection import RabbitMQConnection
from haiway.rabbitmq.state import RabbitMQ

__all__ = ("RabbitMQClient",)


@final
class RabbitMQClient(RabbitMQConnection):
    """Async context-managed RabbitMQ client.

    Parameters
    ----------
    url : str, optional
        AMQP connection URL forwarded to ``RabbitMQConnection``; defaults to ``RABBITMQ_URL``.

    Returns
    -------
    RabbitMQ
        State handle yielded by ``__aenter__`` that exposes queue access helpers.

    Raises
    ------
    asyncio.TimeoutError
        If establishing the broker connection exceeds the configured timeout.
    pika.exceptions.AMQPConnectionError
        If the underlying AMQP connection cannot be opened.

    Examples
    --------
    The client is an async context manager; entering opens the connection and exiting
    closes it safely, even on error::

        async with RabbitMQClient() as mq:
            async with mq.queue("jobs", content_encoder=encode, content_decoder=decode) as queue:
                await queue.publishing({"task": "refresh"}, attributes=None)
    """

    async def __aenter__(self) -> RabbitMQ:
        await self._ensure_connection()

        return RabbitMQ(
            queue_accessing=self._queue_access,
            queue_declaring=self._queue_declare,
            queue_purging=self._queue_purge,
            queue_deleting=self._queue_delete,
        )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        await self._disconnect()
