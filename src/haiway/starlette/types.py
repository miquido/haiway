from typing import Protocol, runtime_checkable

from haiway.context import Observability

__all__ = ("ObservabilityPreparing",)


@runtime_checkable
class ObservabilityPreparing(Protocol):
    """Prepare the observability backend recording a single request.

    Called for each request, before its scope context is entered, with the W3C
    trace context the request carries. A fresh backend per request is what
    allows the trace of the caller to be continued instead of a new one being
    started, which is why the trace context is resolved for every request and
    handed over here.

    ``OpenTelemetry.observability`` implements this protocol as it is, so
    continuing incoming traces takes no wiring beyond passing it::

        ServerContext(observability=OpenTelemetry.observability)

    Both values are ``None`` when the request carries no usable trace context,
    which is what a backend answers with a trace of its own. They are passed on
    as received - validating them is the responsibility of the backend, which
    the specification requires to reject a malformed one and start over.

    A backend has to be returned for every request - to record through logging,
    ``LoggerObservability`` builds one out of a ``Logger``, which is what the
    context does with a logger passed to it directly.
    """

    def __call__(
        self,
        *,
        traceparent: str | None,
        tracestate: str | None,
    ) -> Observability: ...
