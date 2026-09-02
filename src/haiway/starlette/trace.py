from collections.abc import Mapping

from starlette.types import Scope

__all__ = ("request_trace_context",)


def request_trace_context(
    scope: Scope,
    /,
) -> Mapping[str, str]:
    """Read the W3C trace context carried by an incoming request.

    Resolved for every request by ``ServerContext``, which hands the result
    to the observability backend it prepares - continuing the trace of the caller
    requires no wiring of its own. Available separately for an application
    reading the trace context for something else, like propagating it onwards.

    Parameters
    ----------
    scope : Scope
        ASGI scope of the incoming request. A scope without headers - anything
        other than an ``http`` or ``websocket`` scope - carries no trace. Header
        names are matched case insensitively, so a server which does not
        lowercase them as the ASGI specification requires is tolerated.

    Returns
    -------
    Mapping[str, str]
        The ``traceparent`` entry, accompanied by ``tracestate`` when present.
        Empty when the request carries no usable ``traceparent`` - ``tracestate``
        alone identifies no trace position, so it is never reported by itself.

    Notes
    -----
    A request carrying more than one ``traceparent`` header has no single trace
    position to continue, so the whole trace context is discarded - the
    specification requires such a request to start a new trace rather than to
    join an arbitrary one of them. Only the first ``tracestate`` header is read,
    which is what a caller splitting a long trace state across several of them
    has to account for.

    Surrounding whitespace is stripped, since a header value carries it without
    it being a part of the value, and an entry left empty by that is reported as
    the absent one it is. Values are otherwise passed on as received - deciding
    what to make of a malformed one belongs to the observability backend, which
    validates it as the specification requires. The OpenTelemetry integration
    continues a valid trace and starts its own for anything else.
    """
    traceparent: str | None = None
    tracestate: str | None = None
    conflicting: bool = False

    # read from the raw headers rather than through `Headers`, which reads the
    # scope of a request without any and rewrites the one it is given
    for raw_name, raw_value in scope.get("headers", ()):
        name: bytes = raw_name.lower()
        if name == b"traceparent":
            if traceparent is not None:
                conflicting = True  # no single position to continue

            traceparent = raw_value.decode("latin-1").strip()

        elif name == b"tracestate" and tracestate is None:
            tracestate = raw_value.decode("latin-1").strip()

    if conflicting or not traceparent:
        return {}

    if tracestate:
        return {
            "traceparent": traceparent,
            "tracestate": tracestate,
        }

    return {"traceparent": traceparent}
