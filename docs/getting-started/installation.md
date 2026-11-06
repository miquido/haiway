# Installation

## Requirements

- Python 3.14 or higher

## Native extension

Wheels carry a compiled extension speeding up `State`, which is not used unless asked for: set
`HAIWAY_NATIVE=1` to use it, and to fail when it is not available. Installing from the source
distribution compiles it, and goes without it when there is no compiler. Without the variable the
pure Python implementation runs.

## Install from PyPI

```bash
pip install haiway
```

## Optional Dependencies

You may choose to install haiway including optional support for OpenTelemetry, httpx, Postgres,
RabbitMQ, Starlette, and FastAPI.

### OpenTelemetry Support

For distributed tracing and observability:

```bash
pip install "haiway[opentelemetry]"
```

For the httpx implementation of the http client (installs `httpx2`):

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

For the Starlette integration - middleware and application helpers plugging the context into request
handling:

```bash
pip install "haiway[starlette]"
```

For the same integration with a FastAPI application factory (installs `fastapi`):

```bash
pip install "haiway[fastapi]"
```

Now you're ready to continue with the [Quick Start](quickstart.md) guide!
