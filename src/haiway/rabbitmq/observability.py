from typing import Final

__all__ = (
    "OPERATION_DURATION_METRIC",
    "SENT_MESSAGES_METRIC",
)

# Telemetry vocabulary for the RabbitMQ adapter, following the messaging semantic
# conventions of OpenTelemetry. Recorded values are the system, the operation, the
# destination - an exchange or a queue, names fixed by configuration - and the exception
# type. Message bodies and headers are never among them.

SYSTEM_NAME: Final[str] = "rabbitmq"

OPERATION_DURATION_METRIC: Final[str] = "messaging.client.operation.duration"
"""Publishing of a message, up to the confirmation of the broker when confirms are enabled."""

SENT_MESSAGES_METRIC: Final[str] = "messaging.client.sent.messages"
"""Messages handed over to the broker, failed attempts included."""
