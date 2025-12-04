from collections.abc import AsyncIterable, Callable
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
    need to inspect the message before deciding, call the provided
    `acknowledge` / `reject` callables manually.

    Examples
    --------
    Automatic ack/reject:
        async with message as payload:
            await handle(payload)

    Manual decision after inspecting metadata:
        payload = message.content
        if should_retry(payload, message.meta):
            await message._reject(reason="transient")
        else:
            await message._acknowledge()
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
    ) -> "MQMessage[MappedContent]":
        return MQMessage(
            content=mapping(self.content),
            acknowledge=self._acknowledge,
            reject=self._reject,
            meta=self.meta,
        )

    async def __aenter__(self) -> Content:
        return self.content

    async def __aexit__(
        self,
        exc_type: BaseException | None,
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
    ) -> AsyncIterable[MQMessage[Content]]: ...


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
            Backend-specific options (e.g., routing keys, delay settings);
            forwarded to the configured adapter untouched.

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
            await queue_instance.publish(payload, priority="high")
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
    ) -> AsyncIterable[MQMessage[Content]]: ...

    @overload
    async def consume(
        self,
        **extra: Any,
    ) -> AsyncIterable[MQMessage[Content]]: ...

    @statemethod
    async def consume(
        self,
        **extra: Any,
    ) -> AsyncIterable[MQMessage[Content]]:
        """Consume messages as an async iterator.

        Parameters
        ----------
        **extra : Any
            Adapter-specific options (e.g., prefetch limits, timeouts) forwarded
            verbatim to the consuming backend.

        Yields
        ------
        AsyncIterator[MQMessage[Content]]
            Each item is an `MQMessage` wrapping the content, metadata, and
            acknowledge/reject callables. Iteration proceeds until the backend
            signals completion or the consumer breaks. Exceptions raised inside
            the loop propagate; the adapter is responsible for ensuring in-flight
            messages are settled appropriately.

        Notes
        -----
        Use ``async for`` to stream messages; leaving the loop ends consumption
        cleanly. The method is available as a class-level statemethod through
        ``ctx.state.MQQueue`` or as an instance method on a specific queue.

        Examples
        --------
        Class-level consumption:
            async for message in ctx.state.MQQueue.consume(prefetch=10):
                async with message as payload:
                    await handle(payload)

        Instance-level consumption:
            async for message in queue_instance.consume():
                await process(message.content)
        """
        return await self.consuming(**extra)

    publishing: MQQueuePublishing[Content]
    consuming: MQQueueConsuming[Content]
