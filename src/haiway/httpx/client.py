from collections.abc import AsyncGenerator, Callable
from http.cookiejar import CookieJar, DefaultCookiePolicy
from types import TracebackType
from typing import Any, Self, final

from httpx2 import (
    URL,
    USE_CLIENT_DEFAULT,
    AsyncClient,
    NetworkError,
    Response,
    TimeoutException,
)

from haiway.helpers import (
    HTTPBody,
    HTTPClient,
    HTTPClientError,
    HTTPConnectionError,
    HTTPHeaders,
    HTTPQueryParams,
    HTTPResponse,
    HTTPTimeoutError,
)
from haiway.types import Immutable
from haiway.utils.exceptions import thrown_exception

__all__ = ("HTTPXClient",)


@final
class HTTPXClient(Immutable):
    """HTTPX-based implementation of the HTTP client.

    Provides an async HTTP client using ``httpx2.AsyncClient`` as the backend.
    Implements the `HTTPRequesting` protocol and integrates with Haiway's
    context system through the disposable async context manager interface.

    The client is configured with sensible defaults including disabled cookies,
    explicit redirect handling and a 5 second timeout. Within an entered scope
    it reuses one HTTPX connection pool, then closes it on exit.

    Parameters
    ----------
    base_url : str, optional
        Base URL for all requests. Relative URLs will be resolved against this.
        Defaults to empty, which requires absolute request URLs.
    headers : HTTPHeaders | None, optional
        Default headers to include in all requests.
    timeout : float, optional
        Default timeout in seconds for all requests, applied to each of the
        connect, read, write and pool phases. Defaults to 5 seconds, matching
        the httpx2 default, and can be overridden per request. Disabling
        timeouts is deliberately not supported - a request that never times out
        holds its connection until the pool is closed.
    follow_redirects : bool, optional
        Whether to follow redirects by default. Defaults to ``False``, and can
        be overridden per request.
    max_redirects : int, optional
        How many redirects to follow before failing. Defaults to 20, matching
        the httpx2 default. This is a client-wide limit - httpx2 accepts it only
        at construction - so it applies to every request which follows
        redirects, including one which opted in per request.
    **extra : Any
        Additional keyword arguments passed directly to ``httpx2.AsyncClient``.

    Attributes
    ----------
    base_url : URL
        The configured base URL as an httpx2 URL object.

    Examples
    --------
    >>> # Basic usage with context manager
    >>> async with ctx.scope(
    ...     "api",
    ...     disposables=(HTTPXClient(base_url="https://api.example.com"),),
    ... ):
    ...         response = await HTTPClient.get(url="/users")
    ...         payload = await response.body()
    ...
    >>> # With custom configuration
    >>> async with ctx.scope(
    ...     "api",
    ...     disposables=(
    ...         HTTPXClient(
    ...             base_url="https://api.example.com",
    ...             headers={"Authorization": "Bearer token"},
    ...             timeout=30.0,
    ...             max_redirects=5,
    ...         ),
    ...     ),
    ... ):
    ...     response = await HTTPClient.post(
    ...         url="/data",
    ...         body=json.dumps({"key": "value"}).encode(),
    ...     )

    Notes
    -----
    - Cookies are disabled by default for security and predictability.
    - Requests are recorded as observability events by the ``HTTPClient``
      facade, above this backend - see its notes for what is recorded, and for
      the trace context a request can ask to propagate.
    - Redirect following defaults to ``False`` at the client level and can be
      overridden per request. How far a redirect chain is followed is set once
      per client through ``max_redirects``, not per request.
    - The client must be used as an async context manager to ensure proper
      resource cleanup.
    - Response bodies are read before returning unless ``stream=True`` is
      requested. Consuming a streamed body is the caller's responsibility: it
      keeps its connection checked out until the body is read or closed, and
      one that is never read holds it until the pool is closed on scope exit at
      the latest.
    - A request ``body`` given as an async byte generator is streamed to the
      server with chunked transfer encoding rather than buffered. httpx2
      cannot replay it, so a redirect which preserves the method - and any
      retry - fails with ``HTTPClientError``. Buffered payloads are ``bytes``;
      text has to be encoded by the caller.
    - A single instance supports one active scope at a time. Re-entering a
      previously closed instance creates a fresh internal
      ``httpx2.AsyncClient`` with the same configuration.
    """

    _client: AsyncClient
    _prepare_client: Callable[[], AsyncClient]

    def __init__(
        self,
        base_url: str = "",
        headers: HTTPHeaders | None = None,
        timeout: float = 5,
        follow_redirects: bool = False,
        max_redirects: int = 20,
        **extra: Any,
    ) -> None:
        def prepare_client() -> AsyncClient:
            return AsyncClient(
                base_url=base_url,
                headers=headers,
                cookies=CookieJar(  # disable cookies
                    policy=DefaultCookiePolicy(allowed_domains=()),
                ),
                follow_redirects=follow_redirects,
                max_redirects=max_redirects,
                timeout=timeout,
                **extra,
            )

        object.__setattr__(
            self,
            "_prepare_client",
            prepare_client,
        )
        object.__setattr__(
            self,
            "_client",
            prepare_client(),
        )

    @property
    def base_url(self) -> URL:
        """The configured base URL against which relative URLs are resolved."""
        return self._client.base_url

    async def __aenter__(self) -> HTTPClient:
        """Enter the async context manager and return an HTTPClient.

        Opens the internal HTTPX client and returns an `HTTPClient` state
        bound to this instance's request method.

        Returns
        -------
        HTTPClient
            An `HTTPClient` state instance bound to this HTTPX client.

        Raises
        ------
        RuntimeError
            If this instance is already entered - one instance owns a single
            connection pool, so concurrent scopes need separate instances.

        Notes
        -----
        If the internal client was previously closed, a new one is created
        with the same configuration before entering the context.
        """
        if self._client.is_closed:
            object.__setattr__(
                self,
                "_client",
                self._prepare_client(),
            )

        await self._client.__aenter__()

        return HTTPClient(requesting=self.request)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit the async context manager and cleanup resources.

        Ensures the HTTPX client is properly closed and all connections
        are released.

        Parameters
        ----------
        exc_type : type[BaseException] | None
            Exception type if an exception occurred.
        exc_val : BaseException | None
            Exception instance if an exception occurred.
        exc_tb : TracebackType | None
            Exception traceback if an exception occurred.
        """
        await self._client.__aexit__(
            exc_type,
            exc_val,
            exc_tb,
        )

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
        stream: bool = False,
    ) -> HTTPResponse:
        """Execute an HTTP request using the HTTPX client.

        Implements the `HTTPRequesting` protocol and is invoked by the
        `HTTPClient` facade methods resolved from the current context.

        Parameters
        ----------
        method : str
            HTTP method (e.g., "GET", "POST").
        url : str
            Target URL. Can be relative if base_url is configured.
        query : HTTPQueryParams | None, optional
            Query parameters to append to the URL.
        headers : HTTPHeaders | None, optional
            Request headers. Merged with default headers.
        body : HTTPBody | None, optional
            Request body content. ``bytes`` are sent with a ``Content-Length``;
            an async byte generator is streamed with chunked transfer encoding
            instead of being buffered. It stays owned by the caller and is
            never closed here.
        timeout : float | None, optional
            Request timeout. Overrides default timeout if specified.
        follow_redirects : bool | None, optional
            Whether to follow redirects. Overrides the client default if
            specified. The chain length stays bounded by the client's
            ``max_redirects``.
        stream : bool, optional
            Whether to leave the body as a stream instead of reading it before
            returning. Defaults to ``False``.

        Returns
        -------
        HTTPResponse
            The HTTP response with status, headers, and either a buffered or a
            lazily-consumed body.

        Raises
        ------
        HTTPTimeoutError
            If the request exceeds its timeout.
        HTTPConnectionError
            If the connection cannot be established or is lost.
        HTTPClientError
            If the request fails for any other reason.

        Notes
        -----
        - Without ``stream=True`` the body is read before returning, which
          releases the connection back to the pool right away.
        - A streamed body keeps the connection checked out until the caller
          consumes it. Failures raised while streaming are translated the same
          way. A consumed body cannot be read again - ``HTTPResponse`` fails
          that with ``HTTPBodyConsumedError`` before it reaches httpx2 - and its
          connection returns to the pool as soon as the stream is read to its
          end, or closed early.
        - A streamed request ``body`` is sent with chunked transfer encoding
          and is read while the request is in flight, so failures raised by it
          surface here.
        - Response headers are exposed as the backend ``httpx2.Headers``
          mapping: lookups are case-insensitive and repeated headers are
          joined with ``", "``.
        - ``timeout=None`` and ``follow_redirects=None`` defer to the client
          defaults via ``httpx2.USE_CLIENT_DEFAULT``.
        """
        try:
            response: Response = await self._client.send(
                self._client.build_request(
                    method,
                    url,
                    params=query,
                    headers=headers,
                    content=body,
                    timeout=timeout if timeout is not None else USE_CLIENT_DEFAULT,
                ),
                stream=stream,
                follow_redirects=follow_redirects
                if follow_redirects is not None
                else USE_CLIENT_DEFAULT,
            )

            if stream:
                return HTTPResponse(
                    status_code=response.status_code,
                    headers=response.headers,
                    body=_ResponseStream(response),
                )

            else:
                return HTTPResponse(
                    status_code=response.status_code,
                    headers=response.headers,
                    body=response.content,
                )

        except TimeoutException as exc:
            raise HTTPTimeoutError(
                message="HTTP request timed out",
                method=method,
                url=url,
            ) from exc

        except NetworkError as exc:
            raise HTTPConnectionError(
                message="HTTP connection failed",
                method=method,
                url=url,
            ) from exc

        except Exception as exc:
            raise HTTPClientError(
                message="HTTP request failed",
                method=method,
                url=url,
            ) from exc


def _streaming_error(
    response: Response,
    exc: Exception,
) -> HTTPClientError:
    if isinstance(exc, TimeoutException):
        return HTTPTimeoutError(
            message="HTTP request timed out",
            method=response.request.method,
            url=str(response.request.url),
        )

    elif isinstance(exc, NetworkError):
        return HTTPConnectionError(
            message="HTTP connection failed",
            method=response.request.method,
            url=str(response.request.url),
        )

    else:
        return HTTPClientError(
            message="HTTP request failed",
            method=response.request.method,
            url=str(response.request.url),
        )


@final
class _ResponseStream(AsyncGenerator[bytes]):
    """Streams a response body, owning the connection behind it.

    Holding the response here, instead of only within the generator frame
    reading it, is what keeps ``aclose`` releasing the connection before the
    first chunk was requested: a generator frame which never started does not
    run on close, so the release within it would not happen either.
    """

    __slots__ = ("_generator", "_response")

    def __init__(
        self,
        response: Response,
    ) -> None:
        self._response: Response = response
        self._generator: AsyncGenerator[bytes] = self._chunks()

    async def _chunks(self) -> AsyncGenerator[bytes]:
        chunks: AsyncGenerator[bytes] = self._response.aiter_bytes()
        try:
            while True:
                chunk: bytes
                try:
                    chunk = await anext(chunks)

                except StopAsyncIteration:
                    break

                except Exception as exc:  # a failed read is a transport failure
                    raise _streaming_error(self._response, exc) from exc

                # handed over outside of the translation above on purpose: an
                # exception thrown in at this point comes from the consumer, and
                # reporting it as a transport failure would misattribute it
                yield chunk

        finally:  # ensure the connection is released in all cases
            try:
                try:
                    # releases the connection through httpx2's own path when the
                    # read was left part way, and is a no-op once it ran to its end
                    await chunks.aclose()

                finally:
                    # ...and directly when the read never started, where the frame
                    # holding that release never runs. already closed by the above
                    # otherwise, which httpx2 makes a no-op
                    await self._response.aclose()

            except Exception as exc:
                # a backend failing to release the connection is still a
                # connection error, so closing is translated as well
                raise _streaming_error(self._response, exc) from exc

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> bytes:
        return await self.asend(None)

    async def asend(
        self,
        value: None = None,
        /,
    ) -> bytes:
        return await self._generator.asend(value)

    async def athrow(
        self,
        typ: type[BaseException] | BaseException,
        val: object = None,
        tb: TracebackType | None = None,
        /,
    ) -> bytes:
        return await self._generator.athrow(thrown_exception(typ, val, tb))

    async def aclose(self) -> None:
        await self._generator.aclose()
        if self._response.is_closed:
            return  # the stream was read, releasing the connection on its way

        # closing a stream which was never read does not run the frame above,
        # leaving the connection checked out - release it here instead
        try:
            await self._response.aclose()

        except Exception as exc:
            raise _streaming_error(self._response, exc) from exc
