try:
    import pika  # pyright: ignore[reportUnusedImport]

except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "haiway.rabbitmq requires the 'rabbitmq' extra (pika). "
        "Install via `pip install haiway[rabbitmq]`."
    ) from exc

from haiway.rabbitmq.client import RabbitMQClient
from haiway.rabbitmq.state import RabbitMQ
from haiway.rabbitmq.types import RabbitMQException

__all__ = (
    "RabbitMQ",
    "RabbitMQClient",
    "RabbitMQException",
)
