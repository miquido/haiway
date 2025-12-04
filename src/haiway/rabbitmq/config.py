from haiway.utils import getenv_str

__all__ = ("RABBITMQ_URL",)


RABBITMQ_URL: str = getenv_str(
    "RABBITMQ_URL",
    default="amqp://localhost:5672",
)
