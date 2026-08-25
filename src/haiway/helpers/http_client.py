from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Mapping,
    MutableSequence,
    Sequence,
)
from time import monotonic
from typing import Protocol, cast, final, overload, runtime_checkable

from haiway.attributes import State
from haiway.context import ctx
from haiway.helpers.statemethods import statemethod
from haiway.types import MISSING, Immutable, Missing

__all__ = (
    "HTTPBody",
    "HTTPBodyConsumedError",
    "HTTPClient",
    "HTTPClientError",
    "HTTPConnectionError",
    "HTTPHeaders",
    "HTTPQueryParams",
    "HTTPRequesting",
    "HTTPResponse",
    "HTTPStatusCode",
    "HTTPTimeoutError",
)
type HTTPStatusCode = int
type HTTPHeaders = Mapping[str, str]
type HTTPQueryParams = Mapping[
    str,
    Sequence[str] | Sequence[float] | Sequence[int] | Sequence[bool] | str | float | int | bool,
]
type HTTPBody = bytes | AsyncIterable[bytes]
"""Payload of a request or a response - buffered as ``bytes``, or streamed as
an async byte iterable.

A streamed request payload has no known length, so it is sent with chunked
transfer encoding, and it can be consumed only once, which means it cannot be
replayed across a redirect or a retry. Text has to be encoded by the caller -
``str`` is deliberately not accepted, so the charset is never guessed here."""


@final
class _ConsumedBody(Immutable):
    """Stands in for a response body stream which was already consumed.

    Iterating it raises instead of yielding nothing, so a consumed stream cannot
    read back as an empty - or truncated - payload. It raises on every attempt
    rather than only the first.
    """

    def __aiter__(self) -> AsyncIterator[bytes]:
        raise HTTPBodyConsumedError()


_CONSUMED_BODY: AsyncIterable[bytes] = _ConsumedBody()


@final
class HTTPResponse(Immutable):
    """Immutable HTTP response container.

    Encapsulates the status code, headers, and body of a completed HTTP
    request.

    The body may be supplied either as already-buffered ``bytes`` or as an
    async byte stream. Buffered access is available through ``body()``, while
    ``stream_body()`` preserves streaming semantics.

    Attributes
    ----------
    status_code : int
        HTTP status code (e.g., 200, 404, 500).
    headers : Mapping[str, str]
        Response headers as provided by the backend.

    Methods
    -------
    body() -> bytes
        Asynchronously read and cache the full response body content.
    stream_body() -> AsyncIterable[bytes]
        Hand over body chunks without retaining them.

    Notes
    -----
    Backends read the payload before returning unless the request asked for a
    streamed body. Consuming a streamed body is the caller's responsibility:
    until it is read the underlying resources stay held, and it has to be read
    within the scope that produced the response.

    The two accessors trade memory against reuse. ``stream_body()`` retains
    nothing, which keeps a streamed body bounded in memory regardless of
    payload size, but caches nothing for a later read: a stream reads once, and
    reading it again raises ``HTTPBodyConsumedError``. ``body()`` buffers the
    whole payload to cache it, so it stays re-readable - through either
    accessor - at the cost of holding it all at once.

    A stream is claimed before it is read, so a read which does not reach the
    end cannot be resumed by a later one. That is deliberate: resuming would
    hand back the unread remainder as though it were the whole payload.

    Both accessors release the iterator behind a streamed body when they are
    done with it, whether they reached the end or were abandoned - closing a
    ``stream_body()`` iterator, by exhausting it or through
    ``contextlib.aclosing``, releases the backend resources right away.

    Examples
    --------
    >>> response = HTTPResponse(
    ...     status_code=200,
    ...     headers={"Content-Type": "application/json"},
    ...     body=b'{"status": "ok"}'
    ... )
    >>> data = json.loads(await response.body())
    """

    status_code: HTTPStatusCode
    headers: HTTPHeaders
    _body: HTTPBody

    def __init__(
        self,
        status_code: HTTPStatusCode,
        headers: HTTPHeaders,
        body: HTTPBody,
    ) -> None:
        super().__init__(
            status_code=status_code,
            headers=headers,
            _body=body,
        )

    async def body(self) -> bytes:
        """Read and cache the full response body.

        Returns
        -------
        bytes
            The complete response payload.

        Notes
        -----
        When the body is backed by an async iterator, this method consumes the
        iterator to completion, releases it, and caches the resulting bytes for
        later reuse - so a buffered payload can be read any number of times,
        through this method or through ``stream_body()``.

        Raises ``HTTPBodyConsumedError`` when the stream was already consumed,
        including by a read which failed part way: the stream is claimed before
        it is read, so a failed read cannot be retried into a silently
        truncated payload.
        """
        if isinstance(self._body, bytes):
            return self._body

        # claim the stream before reading it - a read which does not reach the
        # end must not leave a partially consumed iterator to be resumed later
        iterator: AsyncIterator[bytes] = aiter(self._body)
        object.__setattr__(
            self,
            "_body",
            _CONSUMED_BODY,
        )
        parts: MutableSequence[bytes] = []
        try:
            async for part in iterator:
                parts.append(part)

        finally:  # release the backend also when the read is abandoned
            if hasattr(iterator, "aclose"):
                await iterator.aclose()  # pyright: ignore[reportUnknownMemberType,  reportAttributeAccessIssue]

        object.__setattr__(
            self,
            "_body",
            b"".join(parts),
        )
        return cast(bytes, self._body)

    async def stream_body(self) -> AsyncIterable[bytes]:
        """Iterate over response body chunks.

        Yields
        ------
        bytes
            Subsequent chunks from the response body.

        Notes
        -----
        Chunks are handed over without being retained, which is what keeps a
        streamed body bounded in memory - reach for this rather than `body()`
        for payloads too large to hold at once. Nothing is cached in exchange,
        so a stream reads once: iterating an already consumed one raises
        `HTTPBodyConsumedError` on the first step. A payload the backend already
        buffered is yielded whole instead, and stays re-readable.

        Closing the iterator - by exhausting it, or through
        `contextlib.aclosing` when leaving early - releases the backend
        resources behind it right away.
        """
        if isinstance(self._body, bytes):
            yield self._body
            return

        # claim the stream before reading it, as `body` does
        iterator: AsyncIterator[bytes] = aiter(self._body)
        object.__setattr__(
            self,
            "_body",
            _CONSUMED_BODY,
        )
        try:
            async for part in iterator:
                yield part

        finally:  # release the backend also when the stream is abandoned
            if hasattr(iterator, "aclose"):
                await iterator.aclose()  # pyright: ignore[reportUnknownMemberType,  reportAttributeAccessIssue]


@runtime_checkable
class HTTPRequesting(Protocol):
    """Protocol for HTTP request implementations.
    Defines the interface that concrete HTTP clients must implement to handle
    HTTP requests. This protocol allows for different backend implementations
    while maintaining a consistent interface.

    Parameters
    ----------
    method : str
        HTTP method (e.g., "GET", "POST", "PUT", "DELETE").
    url : str
        The URL to send the request to.
    query : HTTPQueryParams | None
        Query parameters to append to the URL.
    headers : HTTPHeaders | None
        HTTP headers to include in the request.
    body : HTTPBody | None
        Request body content - buffered `bytes`, or an async byte iterable
        streamed with chunked transfer encoding.
    timeout : float | None
        Request timeout in seconds. None uses client default.
    follow_redirects : bool | None
        Whether to follow redirects. None uses client default.
    stream : bool
        Whether to leave the body as a stream instead of reading it before
        returning the response.

    Returns
    -------
    HTTPResponse
        The response from the HTTP request.

    Raises
    ------
    HTTPClientError
        If the request fails for any reason.
    """

    async def __call__(
        self,
        method: str,
        /,
        *,
        url: str,
        query: HTTPQueryParams | None,
        headers: HTTPHeaders | None,
        body: HTTPBody | None,
        timeout: float | None,
        follow_redirects: bool | None,
        stream: bool,
    ) -> HTTPResponse: ...


class HTTPClientError(Exception):
    """Error raised when an HTTP request cannot produce a response.

    HTTP status codes such as 4xx and 5xx are not failures: they are returned
    as ordinary ``HTTPResponse`` values. This exception is reserved for
    transport and adapter failures, where no response is available at all.

    Backends may raise one of the more specific subclasses - see
    ``HTTPTimeoutError`` and ``HTTPConnectionError`` - so callers that only
    need coarse handling can keep catching ``HTTPClientError``.

    Parameters
    ----------
    message : str
        Description of the failure.
    method : str | None, optional
        HTTP method of the failed request.
    url : str | None, optional
        Target URL of the failed request.

    Attributes
    ----------
    method : str | None
        HTTP method of the failed request, when the raise site knows it.
    url : str | None
        Target URL of the failed request, when the raise site knows it, with
        the query, the fragment and the userinfo already dropped.

    Notes
    -----
    ``method`` and ``url`` are rendered as a ``"{method} {url}|{message}"``
    prefix so the details survive logging, while also staying available as
    attributes for programmatic handling. Request bodies and headers are
    deliberately excluded to avoid leaking credentials.

    So is everything within the URL which could authorize a request rather than
    identify one: it is redacted on the way in, by the same rule the recorded
    attributes use. An error is logged, wrapped and reported far more often than
    a metric is recorded - a backend passing a URL carrying an access token in
    its query, or credentials in its authority, must not turn a failure into a
    disclosure. A raise site needing the URL it was given still has it.

    A backend raising for a request in flight always knows both and is expected
    to pass them. They are optional only for the response side, which cannot:
    ``HTTPBodyConsumedError`` is raised by ``HTTPResponse``, and a response does
    not carry the request it came from. Such a failure renders its message
    alone.
    """

    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        url: str | None = None,
    ) -> None:
        # redacted here rather than at each raise site, so a backend outside of
        # this package inherits it instead of having to remember it
        redacted: str | None = _recorded_url(url) if url is not None else None
        if method is not None and redacted is not None:
            super().__init__(f"{method} {redacted}|{message}")

        else:
            super().__init__(message)

        self.method: str | None = method
        self.url: str | None = redacted


@final
class HTTPTimeoutError(HTTPClientError):
    """Error raised when an HTTP request exceeds its allotted time.

    Covers connect, read, write, and connection-pool timeouts alike. Subclass
    of ``HTTPClientError``, so existing coarse handling keeps working.

    Notes
    -----
    Timeouts are frequently transient, which makes this the exception type
    most worth retrying - see ``haiway.helpers.retry``.
    """


@final
class HTTPBodyConsumedError(HTTPClientError):
    """Error raised when a response body is read after it was already consumed.

    A streamed body can be read once. Reading it a second time - or reading it
    after a read which did not reach the end - has nothing left to return, so it
    fails here rather than presenting a truncated or empty payload as if it were
    the whole one.

    Subclass of ``HTTPClientError``, so existing coarse handling keeps working.
    ``method`` and ``url`` are ``None``: ``HTTPResponse`` does not carry the
    request it came from.

    Notes
    -----
    Unlike the other subclasses this reports a caller mistake rather than a
    transport failure, so it is never worth retrying - keep it out of a
    ``haiway.helpers.retry`` predicate rather than letting a coarse
    ``HTTPClientError`` match pick it up.

    Reach for ``body()`` rather than ``stream_body()`` when a payload has to
    stay re-readable: a buffered body is cached, so it reads any number of
    times.
    """

    def __init__(
        self,
        message: str = "HTTP response body was already consumed",
    ) -> None:
        super().__init__(message)


@final
class HTTPConnectionError(HTTPClientError):
    """Error raised when a connection cannot be established or maintained.

    Covers connect, read, write, and close failures at the network level, such
    as a refused connection, DNS failure, or a connection dropped mid-request.
    Subclass of ``HTTPClientError``, so existing coarse handling keeps working.
    """


_DURATION_METRIC: str = "http.client.request.duration"
"""Distribution of request durations, in seconds - the OpenTelemetry HTTP client
convention, so the usual dashboards and alerts apply to it unchanged."""


def _recorded_url(
    url: str,
    /,
) -> str:
    """Render a URL safe to record as an observability attribute or report in an error.

    Credentials and query values both routinely carry secrets - an access token
    in a query parameter, userinfo in the authority - so the query, the fragment
    and the userinfo are dropped rather than recorded. What is left is the part
    which identifies the request without being able to authorize it.

    Applying it twice is the same as applying it once, so a caller which already
    holds a rendered URL can pass it on without checking.

    A relative URL is left as it is, minus the same parts: the base URL it
    resolves against belongs to the backend, so the facade never sees it.
    """
    # cutting at the first delimiter rather than parsing keeps this from
    # failing on a URL which the backend will reject anyway
    location: str = url.partition("#")[0].partition("?")[0]
    scheme, separator, remainder = location.partition("://")
    if not separator:
        return location  # relative or opaque - no authority to redact

    authority, path_separator, path = remainder.partition("/")
    if "@" in authority:
        # only the userinfo is replaced - the host stays, it is the useful part
        authority = f"REDACTED@{authority.rpartition('@')[2]}"

    return f"{scheme}://{authority}{path_separator}{path}"


def _recorded_host(
    url: str,
    /,
) -> str | Missing:
    """Host the request is addressed to, when the URL names one.

    Kept apart from ``_recorded_url`` because this one goes on a metric, where
    only bounded attributes belong - a host is one of a handful, a URL is not.

    Missing for a relative URL: the base URL it resolves against belongs to the
    backend, which the facade does not see.
    """
    _, separator, remainder = url.partition("://")
    if not separator:
        return MISSING  # relative or opaque - no authority to read

    authority: str = remainder.partition("/")[0].partition("?")[0].partition("#")[0]
    # userinfo never reaches an attribute, and the port belongs to `server.port`
    host: str = authority.rpartition("@")[2]
    if host.startswith("["):  # IPv6 literal - bare address, without the brackets
        return host.partition("]")[0].removeprefix("[")

    return host.partition(":")[0]


def _record_failure(
    exception: Exception,
    /,
    *,
    method: str,
    url: str,
    host: str | Missing,
    started: float,
) -> None:
    """Record a request which produced no response at all."""
    duration: float = monotonic() - started
    # the concrete type - a timeout and a refused connection are both an
    # HTTPClientError, and they call for different actions
    error_type: str = type(exception).__name__
    ctx.record_error(
        event="http.request.error",
        attributes={
            "http.request.method": method,
            "url": url,
            "error.type": error_type,
            "duration": duration,
        },
    )
    # recorded at error level, so a backend filtering out everything below it
    # still measures the failures - and only those
    ctx.record_error(
        metric=_DURATION_METRIC,
        value=duration,
        unit="s",
        kind="histogram",
        attributes={
            "http.request.method": method,
            "error.type": error_type,
            "server.address": host,
        },
    )


@final
class HTTPClient(State):
    """Context-aware HTTP client for making HTTP requests.
    Provides a functional interface for HTTP operations using the context
    system for dependency injection. The actual HTTP implementation is
    provided through the `requesting` protocol.
    This class serves as the main interface for HTTP operations in Haiway,
    offering convenience methods for GET, POST, and PUT while maintaining
    flexibility through the general `request` method for other verbs.

    Attributes
    ----------
    requesting : HTTPRequesting
        The protocol implementation that performs actual HTTP requests.

    Notes
    -----
    - When accessed on the class, `@statemethod` resolves the current
      `HTTPClient` instance from the active Haiway context.
    - Every request records observability events within the current scope:
      `http.request` and `http.response` at debug level, `http.request.error`
      at error level. They carry the method, the URL, the status code, the
      elapsed time and the error type. Recorded URLs are stripped of userinfo,
      query and fragment, and neither headers nor bodies are ever recorded, so
      credentials do not reach the observability backend. For a streamed
      response the recorded duration is the time to its headers - the body is
      transferred after the call returned.
    - Each request is also measured into the `http.client.request.duration`
      histogram, in seconds - at info level when it produced a response, at
      error level when it failed, so a backend filtering out everything below
      error still measures the failures. Its attributes are only the ones a
      metric can afford, a separate stream being stored per combination of them:
      the method, the status code or the error type, and `server.address` when
      the request URL names a host. A relative URL resolves against a base URL
      held by the backend, which the facade does not see, so it carries no host.
    - Requesting `trace_propagation` on a request adds the current trace
      context to its headers - the W3C `traceparent`, and `tracestate` when
      present - so the called service continues this trace instead of starting
      its own. It is asked for per request, and defaults to `False`, because it
      exposes internal trace identifiers to whoever is called: ask for it
      towards services you own, not towards third party APIs. Headers passed to
      the request are never overridden, and an observability backend with no
      trace context to hand out - the default logger among them - propagates
      nothing.
    - HTTP status codes such as 4xx and 5xx are returned as normal
      `HTTPResponse` values. `HTTPClientError` is reserved for transport or
      adapter failures, with `HTTPTimeoutError` and `HTTPConnectionError`
      available for finer-grained handling.
    - Response bodies are read before returning unless `stream=True` is
      requested. Consuming a streamed body is the caller's responsibility: it
      keeps a connection checked out until read to the end, so read it within
      the scope that issued the request. `HTTPResponse.stream_body()` retains
      nothing, which is what bounds its memory use.
    - Request bodies stream too: pass an async byte iterable as `body` to send
      a payload without holding it in memory. Such a payload is sent with
      chunked transfer encoding and cannot be replayed, so it does not survive
      a redirect or a retry. Buffered payloads are `bytes` - encode text
      yourself rather than relying on a guessed charset.

    Examples
    --------
    >>> # Using with HTTPXClient
    >>> async with HTTPXClient() as http_client:
    ...     async with ctx.scope("api_calls", http_client):
    ...         response = await HTTPClient.get(url="https://api.example.com/data")
    ...         data = json.loads(await response.body())
    ...
    >>> # Making a POST request
    >>> response = await HTTPClient.post(
    ...     url="https://api.example.com/users",
    ...     body=json.dumps({"name": "Alice"}).encode(),
    ...     headers={"Content-Type": "application/json"}
    ... )
    ...
    >>> # Streaming an upload without buffering it
    >>> async def chunks() -> AsyncIterator[bytes]:
    ...     async for chunk in source.read():
    ...         yield chunk
    ...
    >>> response = await HTTPClient.put(url="/upload", body=chunks())
    """

    @overload
    @classmethod
    async def get(
        cls,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse: ...
    @overload
    async def get(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse: ...
    @statemethod
    async def get(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse:
        """Perform an HTTP GET request.

        Parameters
        ----------
        url : str
            The URL to send the GET request to.
        query : HTTPQueryParams | None, optional
            Query parameters to append to the URL.
        headers : HTTPHeaders | None, optional
            HTTP headers to include in the request.
        timeout : float | None, optional
            Request timeout in seconds.
        follow_redirects : bool | None, optional
            Whether to follow redirects.
        trace_propagation : bool, optional
            Whether the current trace context is attached to this request.
            Defaults to ``False`` - see the class notes.
        stream : bool, optional
            Whether to leave the body as a stream instead of reading it before
            returning. Defaults to ``False``.

        Returns
        -------
        HTTPResponse
            The response from the GET request.

        Raises
        ------
        HTTPClientError
            If the request fails.
        """
        return await self._request(
            "GET",
            url=url,
            query=query,
            headers=headers,
            body=None,
            timeout=timeout,
            follow_redirects=follow_redirects,
            trace_propagation=trace_propagation,
            stream=stream,
        )

    @overload
    @classmethod
    async def put(
        cls,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse: ...
    @overload
    async def put(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse: ...
    @statemethod
    async def put(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse:
        """Perform an HTTP PUT request.

        Parameters
        ----------
        url : str
            The URL to send the PUT request to.
        query : HTTPQueryParams | None, optional
            Query parameters to append to the URL.
        headers : HTTPHeaders | None, optional
            HTTP headers to include in the request.
        body : HTTPBody | None, optional
            Request body content - buffered `bytes`, or an async byte
            iterable to stream the payload instead of holding it in memory.
        timeout : float | None, optional
            Request timeout in seconds.
        follow_redirects : bool | None, optional
            Whether to follow redirects.
        trace_propagation : bool, optional
            Whether the current trace context is attached to this request.
            Defaults to ``False`` - see the class notes.
        stream : bool, optional
            Whether to leave the body as a stream instead of reading it before
            returning. Defaults to ``False``.

        Returns
        -------
        HTTPResponse
            The response from the PUT request.

        Raises
        ------
        HTTPClientError
            If the request fails.
        """
        return await self._request(
            "PUT",
            url=url,
            query=query,
            headers=headers,
            body=body,
            timeout=timeout,
            follow_redirects=follow_redirects,
            trace_propagation=trace_propagation,
            stream=stream,
        )

    @overload
    @classmethod
    async def post(
        cls,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse: ...
    @overload
    async def post(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse: ...
    @statemethod
    async def post(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse:
        """Perform an HTTP POST request.

        Parameters
        ----------
        url : str
            The URL to send the POST request to.
        query : HTTPQueryParams | None, optional
            Query parameters to append to the URL.
        headers : HTTPHeaders | None, optional
            HTTP headers to include in the request.
        body : HTTPBody | None, optional
            Request body content - buffered `bytes`, or an async byte
            iterable to stream the payload instead of holding it in memory.
        timeout : float | None, optional
            Request timeout in seconds.
        follow_redirects : bool | None, optional
            Whether to follow redirects.
        trace_propagation : bool, optional
            Whether the current trace context is attached to this request.
            Defaults to ``False`` - see the class notes.
        stream : bool, optional
            Whether to leave the body as a stream instead of reading it before
            returning. Defaults to ``False``.

        Returns
        -------
        HTTPResponse
            The response from the POST request.

        Raises
        ------
        HTTPClientError
            If the request fails.
        """
        return await self._request(
            "POST",
            url=url,
            query=query,
            headers=headers,
            body=body,
            timeout=timeout,
            follow_redirects=follow_redirects,
            trace_propagation=trace_propagation,
            stream=stream,
        )

    @overload
    @classmethod
    async def request(
        cls,
        method: str,
        /,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse: ...
    @overload
    async def request(
        self,
        method: str,
        /,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse: ...
    @statemethod
    async def request(
        self,
        method: str,
        /,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: HTTPBody | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
        trace_propagation: bool = False,
        stream: bool = False,
    ) -> HTTPResponse:
        """Perform an HTTP request with the specified method.
        This is the general-purpose method for making HTTP requests. The
        convenience methods (get, post, put) delegate to this method.

        Parameters
        ----------
        method : str
            HTTP method (e.g., "GET", "POST", "PUT", "DELETE", "PATCH").
        url : str
            The URL to send the request to.
        query : HTTPQueryParams | None, optional
            Query parameters to append to the URL.
        headers : HTTPHeaders | None, optional
            HTTP headers to include in the request.
        body : HTTPBody | None, optional
            Request body content - buffered `bytes`, or an async byte
            iterable to stream the payload instead of holding it in memory.
        timeout : float | None, optional
            Request timeout in seconds. None uses client default.
        follow_redirects : bool | None, optional
            Whether to follow redirects. None uses client default.
        trace_propagation : bool, optional
            Whether the current trace context is attached to this request.
            Defaults to ``False`` - see the class notes.
        stream : bool, optional
            Whether to leave the body as a stream instead of reading it before
            returning. Defaults to ``False``.

        Returns
        -------
        HTTPResponse
            The response from the HTTP request.

        Raises
        ------
        HTTPClientError
            If the request fails for any reason.

        Examples
        --------
        >>> # Custom HTTP method
        >>> response = await HTTPClient.request(
        ...     "PATCH",
        ...     url="https://api.example.com/users/123",
        ...     body=json.dumps({"status": "active"}).encode(),
        ...     headers={"Content-Type": "application/json"}
        ... )
        """
        return await self._request(
            method,
            url=url,
            query=query,
            headers=headers,
            body=body,
            timeout=timeout,
            follow_redirects=follow_redirects,
            trace_propagation=trace_propagation,
            stream=stream,
        )

    async def _request(
        self,
        method: str,
        /,
        *,
        url: str,
        query: HTTPQueryParams | None,
        headers: HTTPHeaders | None,
        body: HTTPBody | None,
        timeout: float | None,
        follow_redirects: bool | None,
        trace_propagation: bool,
        stream: bool,
    ) -> HTTPResponse:
        """Perform a request through the backend, recorded within the current scope.

        The single path every facade method delegates to, so observability and
        trace propagation apply to all of them - and to every `HTTPRequesting`
        implementation - identically.
        """
        recorded_url: str = _recorded_url(url)
        recorded_host: str | Missing = _recorded_host(url)
        ctx.record_debug(
            event="http.request",
            attributes={
                "http.request.method": method,
                "url": recorded_url,
                # a streamed payload has no known length - recording one would
                # mean buffering it, which is the opposite of what it is for
                "http.request.body.size": len(body) if isinstance(body, bytes) else MISSING,
            },
        )
        started: float = monotonic()
        response: HTTPResponse
        try:
            response = await self.requesting(
                method,
                url=url,
                query=query,
                headers=self._propagated_headers(headers) if trace_propagation else headers,
                body=body,
                timeout=timeout,
                follow_redirects=follow_redirects,
                stream=stream,
            )

        except HTTPClientError as exc:
            _record_failure(
                exc,
                method=method,
                url=recorded_url,
                host=recorded_host,
                started=started,
            )
            raise  # pass unchanged

        # cancellation is not caught here - it is a BaseException, being routine
        # control flow under structured concurrency rather than a failure
        except Exception as exc:
            _record_failure(
                exc,
                method=method,
                url=recorded_url,
                host=recorded_host,
                started=started,
            )
            raise HTTPClientError(
                f"HTTP request failed due to an error: {type(exc).__name__}",
                method=method,
                url=url,
            ) from exc

        duration: float = monotonic() - started
        ctx.record_debug(
            event="http.response",
            attributes={
                "http.request.method": method,
                "url": recorded_url,
                "http.response.status_code": response.status_code,
                # for a streamed response this is the time to its headers - the
                # body is transferred afterwards, outside of this call
                "duration": duration,
            },
        )
        # recorded at info level, unlike the events - the aggregate is the signal
        # worth keeping on in production, where per request detail is not.
        # attributes stay deliberately few: a metric is stored per combination of
        # them, so the URL - unbounded - is never one of them
        ctx.record_info(
            metric=_DURATION_METRIC,
            value=duration,
            unit="s",
            kind="histogram",
            attributes={
                "http.request.method": method,
                "http.response.status_code": response.status_code,
                "server.address": recorded_host,
            },
        )
        return response

    def _propagated_headers(
        self,
        headers: HTTPHeaders | None,
        /,
    ) -> HTTPHeaders | None:
        """Extend request headers with the current trace context, when there is one."""
        trace_context: Mapping[str, str] = ctx.trace_context()
        if not trace_context:
            return headers  # no trace position to propagate

        if headers is None:
            return trace_context

        # explicit headers win - a caller managing trace context itself, or
        # deliberately suppressing it for one request, is not overridden here
        provided: set[str] = {name.lower() for name in headers}
        additional: Mapping[str, str] = {
            name: value for name, value in trace_context.items() if name.lower() not in provided
        }
        if not additional:
            return headers

        return {**headers, **additional}

    requesting: HTTPRequesting
