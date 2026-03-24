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
        await queue.publish({"task": "refresh"}, attributes=None)

        async for message in await queue.consume():
            async with message as payload:
                print(payload["task"])
                break
```

## Working with Queues

`RabbitMQ.queue(...)` returns an async context manager yielding `MQQueue[Content]`.

- `await queue.publish(message, attributes=...)` publishes one typed payload
- `await queue.consume()` returns an async iterable of `MQMessage[Content]`
- leaving the queue context closes the channel used for that queue access

The encoder runs on publish and must return `bytes`. The decoder runs for consumed payloads and
should raise when the incoming bytes cannot be parsed into your target type.

## Message Handling

`MQMessage[Content]` wraps the decoded payload plus broker callbacks.

```python
async for message in await queue.consume():
    async with message as payload:
        await handle(payload)
```

Using the message as an async context manager acknowledges on success and rejects on exception. If
you need manual control, call the underscore-prefixed callbacks directly:

```python
async for message in await queue.consume():
    if should_retry(message.content):
        await message._reject(requeue=True)
        continue

    await message._acknowledge()
```

## Queue Management

The `RabbitMQ` state also exposes queue-level operations:

```python
from haiway.rabbitmq import RabbitMQ

await RabbitMQ.declare_queue("jobs", durable=True)
await RabbitMQ.purge_queue("jobs")
await RabbitMQ.delete_queue("jobs")
```

These are `@statemethod`s, so class calls resolve the current `RabbitMQ` instance from context.

## Operational Notes

- The connection URL defaults to `RABBITMQ_URL`
- `RabbitMQClient(url=..., connection_timeout=...)` lets you override connection settings
- Queue access opens a channel on demand and reopens it if needed while the queue context is alive
- Decoder failures are logged and the message is rejected

## Testing

Keep tests at the `MQQueue` or `RabbitMQ` protocol boundary by injecting fake queue accessors
instead of reaching a real broker.

```python
from collections.abc import AsyncIterable

from haiway import MQMessage


async def consume_once() -> AsyncIterable[MQMessage[dict[str, str]]]:
    async def acknowledge() -> None:
        return None

    async def reject(**_: object) -> None:
        return None

    yield MQMessage(
        content={"task": "refresh"},
        acknowledge=acknowledge,
        reject=reject,
        meta={},
    )
```

For application tests, prefer wiring a fake `RabbitMQ` state into `ctx.scope(...)` and asserting on
published payloads or consumed messages without network access.
