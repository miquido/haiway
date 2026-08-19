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
    url : str | None, default=None
        AMQP connection URL forwarded to ``RabbitMQConnection``. ``None`` resolves
        it from the ``RABBITMQ_URL`` environment variable when the client is
        created.
    connection_timeout : float, default=5.0
        Seconds allowed for opening the broker connection.
    operation_timeout : float, default=5.0
        Seconds allowed for a channel operation, such as opening a channel,
        declaring a queue, or starting a consumer.
    publish_timeout : float, default=30.0
        Seconds allowed for a publish confirmation. Kept apart from
        ``operation_timeout`` because a broker under load can take far longer to
        confirm a persistent write than to answer a channel operation.
    publisher_confirms : bool, default=True
        Whether ``publish`` waits for the broker to acknowledge the message.
    mandatory : bool, default=True
        Whether an unroutable message fails the publish instead of being
        discarded by the broker. Requires ``publisher_confirms``.
    persistent : bool, default=True
        Whether messages are published with persistent delivery mode.
    recovery_attempts : int, default=3
        How many times a consumer is re-established after the broker takes it
        away - a channel or connection that dropped, or a cancellation the
        broker initiated. ``0`` disables recovery, ending the iteration with a
        retryable ``RabbitMQException`` instead.
    recovery_delay : float, default=1.0
        Seconds before the second recovery attempt, doubling for each further
        one. The first attempt runs immediately.

    Returns
    -------
    RabbitMQ
        State handle yielded by ``__aenter__`` that exposes queue access helpers.

    Raises
    ------
    RabbitMQException
        If ``mandatory`` is requested without ``publisher_confirms``, if the
        recovery settings are out of range, or if the broker connection cannot
        be opened, including on timeout. The originating ``pika`` error is
        preserved as ``__cause__``.

    Notes
    -----
    Every failure carries ``retryable``, telling a transient broker-side
    condition from one repeating cannot fix. A broker under a resource alarm is
    reported as flow control rather than as an opaque publish timeout, since the
    confirmation cannot arrive until the alarm clears.

    Recovery re-establishes the subscription, not the work in flight: the broker
    requeues everything left unacknowledged, so a message being processed when
    the channel dropped is delivered again. Settle operations on the lost
    channel fail, and ``meta["attempt"]`` counts the redeliveries.

    Examples
    --------
    The client is an async context manager; entering opens the connection and exiting
    closes it safely, even on error::

        async with RabbitMQClient() as mq:
            queue_access = await mq.queue(
                "jobs",
                content_encoder=encode,
                content_decoder=decode,
            )
            async with queue_access as queue:
                await queue.publish({"task": "refresh"})

                async with await queue.consume() as messages:
                    async for message in messages:
                        async with message as payload:
                            await handle(payload)
    """

    __slots__ = ()

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
