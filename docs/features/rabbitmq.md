# RabbitMQ

Haiway provides a context-aware RabbitMQ integration built on top of `pika`. It exposes typed queue
access through `RabbitMQ` state and message-level helpers through `MQQueue` and `MQMessage`.

## Overview

- **Context Managed**: install a single `RabbitMQClient` in a scope and resolve `RabbitMQ` from
  context
- **Typed Queues**: open queues with explicit encoder and decoder functions for your payload type
- **Async Consumption**: consume messages as `MQMessage[Content]` values with async acknowledge and
  reject semantics
- **Queue Operations**: declare, purge, and delete queues through state methods

## Installation

Install the RabbitMQ extra to pull in `pika`:

```bash
pip install "haiway[rabbitmq]"
```

## Quick Start

Use `RabbitMQClient` as a disposable resource and open a typed queue from `RabbitMQ`:

```python
import json

from haiway import MQMessage, ctx
from haiway.rabbitmq import RabbitMQ, RabbitMQClient


def encode_job(payload: dict[str, str]) -> bytes:
    return json.dumps(payload).encode()


def decode_job(payload: bytes) -> dict[str, str]:
    return json.loads(payload.decode())


async with ctx.scope("mq", disposables=(RabbitMQClient(),)):
    await RabbitMQ.declare_queue("jobs", durable=True)

    async with await RabbitMQ.queue(
        "jobs",
        content_encoder=encode_job,
        content_decoder=decode_job,
    ) as queue:
        await queue.publish({"task": "refresh"}, attributes={"trace": "abc"})

        async with await queue.consume() as messages:
            async for message in messages:
                async with message as payload:
                    print(payload["task"])
                    break
```

## Working with Queues

`RabbitMQ.queue(...)` returns an async context manager yielding `MQQueue[Content]`.

- `await queue.publish(message, attributes=...)` publishes one typed payload; `attributes` become
  AMQP headers on the message. Pass `exchange=` to publish through a named exchange and
  `routing_key=` to route on something other than the queue name
- `await queue.consume()` returns an async context manager yielding an async generator of
  `MQMessage[Content]`; each call registers its own consumer with its own buffer, so several
  concurrent consumers are supported
- leaving the consume context cancels that one consumer at the broker and requeues the deliveries it
  buffered but never handed out, while the channel stays open for publishing and further consumers
- leaving the queue context closes the channel, which cancels whatever consumers are still running
  and requeues any delivery still in flight. It waits for the broker to acknowledge the close, so
  the consumers are really gone by the time the block exits

The encoder runs on publish and must return `bytes`. The decoder runs for consumed payloads and
should raise when the incoming bytes cannot be parsed into your target type.

### Delivery Guarantees

By default `publish` waits for the broker to confirm the message and raises `RabbitMQException` when
it is rejected or cannot be routed to any queue, and messages are published with persistent delivery
mode. That makes an awaited publish mean what it looks like it means, at the cost of a round-trip
per message. Trade it for throughput explicitly:

```python
RabbitMQClient(
    publisher_confirms=False,  # do not wait for broker acknowledgement
    mandatory=False,           # allow unroutable messages to be discarded
    persistent=False,          # transient delivery mode
)
```

`mandatory` requires `publisher_confirms`; with both enabled the adapter correlates an unroutable
return back to its publish through an `x-haiway-publish-id` header, since a `Basic.Return` carries
no delivery tag. That header is an adapter detail: it is stripped before `meta` reaches your code,
but it does travel on the wire, so consumers outside Haiway will see it.

### Prefetch

Each consumer receives at most `RABBITMQ_PREFETCH` unacknowledged messages (8 unless the environment
variable overrides it), keeping the in-memory backlog bounded. The variable is read when the queue
is accessed, not when the module is imported, so `load_env()` applies regardless of import order.
Override it per queue with `prefetch`, where `0` restores unlimited delivery:

```python
async with await RabbitMQ.queue(
    "jobs",
    content_encoder=encode_job,
    content_decoder=decode_job,
    prefetch=100,  # at most 100 unacknowledged messages per consumer
) as queue:
    ...
```

## Message Handling

`MQMessage[Content]` wraps the decoded payload plus broker callbacks.

```python
async with await queue.consume() as messages:
    async for message in messages:
        async with message as payload:
            await handle(payload)
```

Using the message as an async context manager acknowledges on success and rejects on exception. If
you need manual control, call the settlement methods directly:

```python
async with await queue.consume() as messages:
    async for message in messages:
        if should_retry(message.content):
            await message.reject(requeue=True)
            continue

        await message.acknowledge()
```

`message.meta` carries the broker headers plus `attempt`, the number of this delivery counted from
RabbitMQ's `x-delivery-count` header, so it is `1` on a first delivery. Classic queues do not
publish that header, so there `attempt` falls back to the `redelivered` flag and saturates at `2` no
matter how many times the message was already delivered - declare the queue as
`x-queue-type: quorum` to count attempts properly. Use it to stop retrying a poison message:

```python
async with await queue.consume() as messages:
    async for message in messages:
        if message.meta.get("attempt", 1) > 3:
            await message.reject(requeue=False)  # dead-letter it, or drop it without a DLX
            continue

        async with message as payload:
            await handle(payload)
```

A payload the decoder cannot parse is rejected without requeueing regardless of the consumer's
policy, since redelivering it would loop forever.

Rejecting without requeueing only dead-letters the message when the source queue was declared with
an `x-dead-letter-exchange` argument; without a DLX the broker discards it. Declare the queue with
one to keep those messages:

```python
await RabbitMQ.declare_queue(
    "jobs",
    durable=True,
    arguments={"x-dead-letter-exchange": "jobs.dlx"},
)
```

## Queue Management

The `RabbitMQ` state also exposes queue-level operations:

```python
from haiway.rabbitmq import RabbitMQ

await RabbitMQ.declare_queue("jobs", durable=True, arguments={"x-message-ttl": 60000})
await RabbitMQ.purge_queue("jobs")
await RabbitMQ.delete_queue("jobs", if_unused=True, if_empty=True)
```

These are `@statemethod`s, so class calls resolve the current `RabbitMQ` instance from context. AMQP
queue arguments go through `declare_queue(arguments=...)`; every other option a mistyped call could
name is rejected rather than taken for a queue argument or silently dropped.

## Failure Handling

Every failure surfaces as `RabbitMQException` carrying `operation`, `queue`, and `retryable`, with
the originating `pika` error preserved as `__cause__`. Branch on `retryable` instead of matching on
the message text:

```python
try:
    await queue.publish(payload)

except RabbitMQException as exc:
    if not exc.retryable:
        raise  # unroutable, or the encoder refused the payload

    await backoff_and_retry(payload)
```

`retryable` is `True` for transient broker-side conditions - a lost connection or channel, a
timeout, a `Basic.Nack`, a consumer the broker cancelled, a recovery that ran out of attempts. It is
`False` where repeating cannot help: adapter misuse, an unroutable message, a payload the encoder
could not serialize, and settling a delivery whose channel is already gone (the broker requeues that
one regardless, so the work returns through a redelivery rather than through a retry).

### Consumer Recovery

When the broker takes a consumer away - the channel or connection dropped, or it sent a
`Basic.Cancel` because the queue moved to another node - the adapter re-registers the consumer on a
replacement channel and the `async for` continues uninterrupted. It retries `recovery_attempts`
times (3 by default), immediately and then after `recovery_delay` seconds doubling each time, before
ending the iteration with a retryable `RabbitMQException`:

```python
RabbitMQClient(
    recovery_attempts=0,  # end the iteration instead of re-establishing consumers
    recovery_delay=1.0,
)
```

Recovery restores the subscription, not the work in flight. Everything unacknowledged goes back to
the queue when the channel dies, so a message being processed at that moment is delivered again -
settling it fails, and `meta["attempt"]` counts the redeliveries. Keep handlers idempotent, or
disable recovery where a duplicate is worse than a stopped consumer.

### Flow Control

A broker under a resource alarm sends `Connection.Blocked` and stops reading from the socket, so no
confirmation can arrive until the alarm clears. That is reported as flow control rather than as an
opaque timeout, and logged as a warning when the alarm is raised and lifted.

Publish confirmations get their own `publish_timeout` (30 s) rather than sharing `operation_timeout`
(5 s), since a broker under load can take far longer to confirm a persistent write than to answer a
channel operation.

## Operational Notes

- The connection URL defaults to `RABBITMQ_URL` (`amqp://localhost:5672` when unset), read when the
  client is created rather than when the module is imported
- `RabbitMQClient(url=..., connection_timeout=..., operation_timeout=..., publish_timeout=...)`
  overrides connection settings; `operation_timeout` bounds channel operations, `publish_timeout`
  bounds publish confirmations
- Queue access opens a channel on demand and reopens it for publishing if it closed
- Channel acquisition and teardown share one lock, so leaving the queue context concurrently with a
  publish or consume that is reopening the channel still closes that channel and ends its consumers
- Unsupported options passed to queue access, declare, publish, consume, acknowledge, or reject
  raise instead of being silently dropped or taken for AMQP arguments
- Leaving the client context waits for the `Connection.Close` handshake, so the socket is released
  before `__aexit__` returns rather than during event loop teardown
- The queue is bound to its context; publishing or consuming through a `MQQueue` after its context
  exited raises `RabbitMQException` instead of opening a channel nobody closes
- Consumption is bound to its own context; leaving it releases the deliveries the iteration never
  asked for, which the broker would otherwise hold until the whole channel closed
- Decoder failures are logged and the message is rejected without requeueing, which dead-letters it
  only when the queue has an `x-dead-letter-exchange` configured and discards it otherwise

## Testing

Keep tests at the `MQQueue` or `RabbitMQ` protocol boundary by injecting fake queue accessors
instead of reaching a real broker.

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from haiway import MQMessage, ctx


async def one_message() -> AsyncGenerator[MQMessage[dict[str, str]]]:
    # both settle callables accept backend-specific keyword options
    async def acknowledge(**_: object) -> None:
        return None

    async def reject(**_: object) -> None:
        return None

    yield MQMessage(
        content={"task": "refresh"},
        acknowledge=acknowledge,
        reject=reject,
        meta={},
    )


@asynccontextmanager
async def consume_once(**_: object) -> AsyncGenerator[AsyncGenerator[MQMessage[dict[str, str]]]]:
    # consuming is scoped, so the fake is a context manager as well - and leaving
    # it ends the message stream, exactly as cancelling a real consumer does
    async with ctx.closing(one_message()) as messages:
        yield messages
```

For application tests, prefer wiring a fake `RabbitMQ` state into `ctx.scope(...)` and asserting on
published payloads or consumed messages without network access. The adapter protocols are exported
for exactly that:

```python
from haiway.rabbitmq import (
    RabbitMQ,
    RabbitMQQueueAccessing,
    RabbitMQQueueDeclaring,
    RabbitMQQueueDeleting,
    RabbitMQQueuePurging,
)
```
