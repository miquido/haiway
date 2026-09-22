from collections.abc import Mapping
from typing import Final

from haiway.context import ObservabilityAttribute

__all__ = (
    "CONNECTION_DURATION_METRIC",
    "REQUEST_DURATION_METRIC",
    "RESPONSE_START_DURATION_ATTRIBUTE",
    "request_metric_attributes",
)

# Telemetry vocabulary for the Starlette integration, shared with FastAPI. Names
# follow the HTTP semantic conventions of OpenTelemetry where one exists, so
# existing dashboards read them without translation - a websocket connection has
# none to follow and keeps a name of its own. Metrics are recorded at INFO,
# which is where both backends and OpenTelemetry expect them.

REQUEST_DURATION_METRIC: Final[str] = "http.server.request.duration"
"""End to end handling of an HTTP request, from the middleware to the last message sent."""

CONNECTION_DURATION_METRIC: Final[str] = "websocket.server.duration"
"""Lifetime of a websocket connection, from the middleware until it is closed."""

RESPONSE_START_DURATION_ATTRIBUTE: Final[str] = "http.server.response.start.duration"
"""Wait for the headers of a response, in seconds - kept on the trace rather than aggregated."""

_METRIC_DIMENSIONS: Final[tuple[str, ...]] = (
    "http.request.method",
    "http.route",
    "http.response.status_code",
)


def request_metric_attributes(
    attributes: Mapping[str, ObservabilityAttribute],
    /,
) -> Mapping[str, ObservabilityAttribute]:
    """Select the dimensions a request duration is kept by.

    The bounded attributes of the request and no others, so a deployment holds a
    time series per route, method and status rather than per requested path -
    ``url.path`` carries whatever identifier a parameterized route matched, and
    one series per value of it is how a metrics store is brought down. The path
    stays on the trace, where one request costs one span whatever it holds.

    An absent dimension is not reported, and is a series of its own: a request
    which matched no route carries no ``http.route``, and one which was never
    answered - a failure, an abandoned request, an accepted websocket connection
    - carries no ``http.response.status_code``.

    Parameters
    ----------
    attributes : Mapping[str, ObservabilityAttribute]
        The attributes the request was recorded with.

    Returns
    -------
    Mapping[str, ObservabilityAttribute]
        The subset of them kept as dimensions of the duration.
    """
    return {name: attributes[name] for name in _METRIC_DIMENSIONS if name in attributes}
