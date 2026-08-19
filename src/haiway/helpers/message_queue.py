from collections.abc import AsyncIterable, Callable
from contextlib import AbstractAsyncContextManager
from types import TracebackType
from typing import Any, Protocol, final, overload, runtime_checkable

from haiway.attributes import State
from haiway.helpers.statemethods import statemethod
from haiway.types import FlatObject, Immutable, Meta

__all__ = (
    "MQMessage",
    "MQQueue",
)


@runtime_checkable
class MQMessageAcknowledging(Protocol):
    async def __call__(
        self,
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class MQMessageRejecting(Protocol):
    async def __call__(
        self,
        **extra: Any,
    ) -> None: ...


@final
class MQMessage[Content](Immutable):
    """Immutable message wrapper returned by queue consumers.
    The message holds the deserialized `content` and accompanying `meta`
    information plus queue-provided `acknowledge` / `reject` callables. It is
    designed for async usage only; acknowledge/reject operations must be awaited
    and are not thread-safe.
    When used as an async context manager, exiting the context without an
    exception calls `acknowledge`, while exiting with an exception calls
    `reject`. Either outcome commits the message in the broker so other
    consumers cannot inspect it unless the queue explicitly requeues it.
    Parameters
    ----------
    content : Content
        Parsed message payload returned by the queue adapter.
    acknowledge : MQMessageAcknowledging
        Callable invoked to mark the message as handled successfully.
    reject : MQMessageRejecting
        Callable invoked to mark the message as failed/undesirable.
    meta : Meta
        Transport-specific metadata attached to the message.
    Notes
    -----
    Use the async context manager for straightforward processing where the
    commit decision aligns with success/failure of the wrapped block. If you
    need to inspect the message before deciding, call the `acknowledge` /
    `reject` methods manually.
    Examples
    --------
    Automatic ack/reject:
        async with message as payload:
            await handle(payload)
    Manual decision after inspecting metadata:
        payload = message.content
        if should_retry(payload, message.meta):
            await message.reject()
        else:
            await message.acknowledge()

    Backend-specific settle options are passed as keyword arguments, for
    example ``await message.reject(requeue=False)`` to dead-letter instead of
    redelivering. Unsupported options raise rather than being ignored.
    """

    content: Content
    meta: Meta
    _acknowledge: MQMessageAcknowledging
    _reject: MQMessageRejecting

    def __init__(
        self,
        content: Content,
        acknowledge: MQMessageAcknowledging,
        reject: MQMessageRejecting,
        meta: Meta,
    ) -> None:
        super().__init__(
            content=content,
            _acknowledge=acknowledge,
            _reject=reject,
            meta=meta,
        )

    def map[MappedContent](
        self,
        mapping: Callable[[Content], MappedContent],
    ) -> MQMessage[MappedContent]:
        return MQMessage(
            content=mapping(self.content),
            acknowledge=self._acknowledge,
            reject=self._reject,
            meta=self.meta,
        )

    async def acknowledge(
        self,
        **extra: Any,
    ) -> None:
        """Mark the message as handled successfully.

        Parameters
        ----------
        **extra : Any
            Backend-specific settle options. Unsupported options raise.
        """
        await self._acknowledge(**extra)

    async def reject(
        self,
        **extra: Any,
    ) -> None:
        """Mark the message as failed or undesirable.

        Parameters
        ----------
        **extra : Any
            Backend-specific settle options, i.e. ``requeue=False`` to
            dead-letter instead of redelivering. Unsupported options raise.
        """
        await self._reject(**extra)

    async def __aenter__(self) -> Content:
        return self.content

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if exc_val is not None:
            await self._reject()
        else:
            await self._acknowledge()


@runtime_checkable
class MQQueuePublishing[Content](Protocol):
    async def __call__(
        self,
        message: Content,
        attributes: FlatObject | None,
        **extra: Any,
    ) -> None: ...


@runtime_checkable
class MQQueueConsuming[Content](Protocol):
    async def __call__(
        self,
        **extra: Any,
    ) -> AbstractAsyncContextManager[AsyncIterable[MQMessage[Content]]]: ...


class MQQueue[Content](State):
    """Generic message-queue interface binding broker adapters to Haiway state.
    `MQQueue` defines the minimal publishing/consuming surface used by Haiway
    helpers and application code. It is parameterized by `Content`, the
    deserialized payload type for a given queue. Concrete adapters embed
    connection details and acknowledge/reject semantics while keeping the
    structured-concurrency lifecycle aligned with `ctx.state` management.
    Typical lifecycle: configure a queue adapter as part of the application
    state, publish messages from within scoped tasks, and consume via async
    iteration inside a managed scope so acknowledgements/retries are tied to the
    task outcome. Instances are immutable `State` objects; callers can invoke
    the statemethods directly on the class via `ctx.state.MQQueue` or on an
    instantiated queue passed through the state graph.
    """

    @overload
    @classmethod
    async def publish(
        cls,
        /,
        message: Content,
        *,
        attributes: FlatObject | None = None,
        **extra: Any,
    ) -> None: ...
    @overload
    async def publish(
        self,
        /,
        message: Content,
        *,
        attributes: FlatObject | None = None,
        **extra: Any,
    ) -> None: ...
    @statemethod
    async def publish(
        self,
        /,
        message: Content,
        *,
        attributes: FlatObject | None = None,
        **extra: Any,
    ) -> None:
        """Publish a message to the queue.
        Parameters
        ----------
        message : Content
            The already-validated payload to send to the broker.
        attributes : FlatObject | None, optional
            Transport-specific headers/attributes to accompany the message;
            kept flat to simplify serialization. Defaults to ``None``.
        **extra : Any
            Backend-specific options (e.g. ``exchange`` for RabbitMQ); forwarded
            to the configured adapter, which raises for options it does not
            support instead of dropping them.
        Returns
        -------
        None
            The message is dispatched asynchronously; success is signaled by the
            absence of an exception.
        Notes
        -----
        Decorated with ``@statemethod`` so it can be invoked on the class when
        accessed through ``ctx.state`` or on an instance that is part of the
        state graph. Prefer the class-level call inside scoped tasks where the
        queue is attached to the active context; use an instance when you have a
        specific queue object already resolved.
        Examples
        --------
        Class-level call via state:
            await ctx.state.MQQueue.publish(message=payload, attributes={"k": "v"})
        Instance-level call:
            await queue_instance.publish(payload, exchange="events")
        """
        return await self.publishing(
            message=message,
            attributes=attributes,
            **extra,
        )

    @overload
    @classmethod
    async def consume(
        cls,
        **extra: Any,
    ) -> AbstractAsyncContextManager[AsyncIterable[MQMessage[Content]]]: ...
    @overload
    async def consume(
        self,
        **extra: Any,
    ) -> AbstractAsyncContextManager[AsyncIterable[MQMessage[Content]]]: ...
    @statemethod
    async def consume(
        self,
        **extra: Any,
    ) -> AbstractAsyncContextManager[AsyncIterable[MQMessage[Content]]]:
        """Open a scoped consumer over the queue.
        Parameters
        ----------
        **extra : Any
            Adapter-specific options (e.g. ``requeue_rejected`` or ``exclusive``
            for RabbitMQ) forwarded verbatim to the consuming backend, which
            raises for options it does not support instead of dropping them.
        Returns
        -------
        AbstractAsyncContextManager[AsyncIterable[MQMessage[Content]]]
            Context manager yielding the stream of messages. Entering registers
            the consumer with the backend, iterating yields `MQMessage` values
            wrapping the content, metadata, and acknowledge/reject callables,
            and exiting cancels the consumer and releases the deliveries it
            buffered but never handed out.
        Notes
        -----
        Consumption is scoped on purpose. A consumer keeps holding the messages
        the backend already handed it, so ending the iteration without telling
        the backend would strand them until the whole queue access is torn down.
        Leaving the context is what ends the subscription, whether the loop
        finished, broke out early, or raised.
        Exceptions raised inside the loop propagate; messages already handed to
        the application are the application's to settle, and everything still
        buffered is released on exit.
        Examples
        --------
        Class-level consumption:
            async with await ctx.state.MQQueue.consume() as messages:
                async for message in messages:
                    async with message as payload:
                        await handle(payload)
        Stopping early:
            async with await queue_instance.consume() as messages:
                async for message in messages:
                    await process(message.content)
                    break  # the consumer is cancelled on exit
        """
        return await self.consuming(**extra)

    publishing: MQQueuePublishing[Content]
    consuming: MQQueueConsuming[Content]
