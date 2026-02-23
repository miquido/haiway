# Installation

## Requirements

- Python 3.13 or higher

## Install from PyPI

```bash
pip install haiway
```

## Optional Dependencies

You may choose to install haiway including optional support for OpenTelemetry, httpx, Postgres, and
RabbitMQ.

### OpenTelemetry Support

For distributed tracing and observability:

```bash
pip install "haiway[opentelemetry]"
```

For httpx implementation of http client:

```bash
pip install "haiway[httpx]"
```

For Postgres (`asyncpg`) support:

```bash
pip install "haiway[postgres]"
```

For RabbitMQ (`pika`) support:

```bash
pip install "haiway[rabbitmq]"
```

Now you're ready to continue with the [Quick Start](quickstart.md) guide!
