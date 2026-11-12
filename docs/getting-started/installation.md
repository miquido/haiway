# Installation

## Requirements

- Python 3.15 or higher

## Native extension

`State` is stored by a compiled extension, which the wheels carry for Linux, macOS and Windows.
Installing from the source distribution compiles it, which needs a C compiler. The extension
requires the GIL - on a free-threaded interpreter, importing it enables the GIL.

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
