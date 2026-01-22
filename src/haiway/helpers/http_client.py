from collections.abc import AsyncIterable, Mapping, MutableSequence, Sequence
from typing import Protocol, cast, final, overload, runtime_checkable

from haiway.attributes import State
from haiway.helpers.statemethods import statemethod
from haiway.types import Immutable

__all__ = (
    "HTTPClient",
    "HTTPClientError",
    "HTTPHeaders",
    "HTTPQueryParams",
    "HTTPRequesting",
    "HTTPResponse",
    "HTTPStatusCode",
)


type HTTPStatusCode = int
type HTTPHeaders = Mapping[str, str]
type HTTPQueryParams = Mapping[
    str,
    Sequence[str] | Sequence[float] | Sequence[int] | Sequence[bool] | str | float | int | bool,
]


class HTTPResponse(Immutable):
    """Immutable HTTP response container.

    Encapsulates the response from an HTTP request including status code,
    headers, and body content.

    Attributes
    ----------
    status_code : int
        HTTP status code (e.g., 200, 404, 500).
    headers : Mapping[str, str]
        Response headers as an immutable mapping.

    Methods
    -------
    body() -> bytes
        Asynchronously read and cache the raw response body content.

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
    _body: AsyncIterable[bytes] | bytes

    def __init__(
        self,
        status_code: HTTPStatusCode,
        headers: HTTPHeaders,
        body: AsyncIterable[bytes] | bytes,
    ) -> None:
        super().__init__(
            status_code=status_code,
            headers=headers,
            _body=body,
        )

    async def body(self) -> bytes:
        if isinstance(self._body, bytes):
            return self._body

        parts: MutableSequence[bytes] = []
        async for part in self.iter_bytes():
            parts.append(part)

        object.__setattr__(self, "_body", b"".join(parts))

        return cast(bytes, self._body)

    async def iter_bytes(self) -> AsyncIterable[bytes]:
        if isinstance(self._body, bytes):
            yield self._body
            return

        parts: MutableSequence[bytes] = []
        body_iter = self._body
        completed = False
        try:
            async for part in body_iter:
                parts.append(part)
                yield part
            completed = True
        finally:
            if completed:
                object.__setattr__(self, "_body", b"".join(parts))
            else:
                await self._close_body(body_iter)

    async def stream_body(self) -> AsyncIterable[bytes]:
        async for part in self.iter_bytes():
            yield part

    async def _close_body(self, body: AsyncIterable[bytes]) -> None:
        closer = getattr(body, "aclose", None)
        if closer is None:
            return

        await closer()


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
    body : str | bytes | None
        Request body content.
    timeout : float | None
        Request timeout in seconds. None uses client default.
    follow_redirects : bool | None
        Whether to follow redirects. None uses client default.

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
        body: str | bytes | None,
        timeout: float | None,
        follow_redirects: bool | None,
    ) -> HTTPResponse: ...


class HTTPClientError(Exception):
    def __init__(
        self,
        message: str,
        *,
        method: str | None = None,
        url: str | None = None,
        status_code: int | None = None,
        cause: Exception | None = None,
    ) -> None:
        context_parts: MutableSequence[str] = []
        if method:
            context_parts.append(f"method={method}")

        if url:
            context_parts.append(f"url={url}")

        if status_code is not None:
            context_parts.append(f"status={status_code}")

        context = f" ({', '.join(context_parts)})" if context_parts else ""
        super().__init__(f"{message}{context}")
        self.method = method
        self.url = url
        self.status_code = status_code
        self.__cause__ = cause


@final
class HTTPClient(State):
    """Context-aware HTTP client for making HTTP requests.

    Provides a functional interface for HTTP operations using the context
    system for dependency injection. The actual HTTP implementation is
    provided through the `requesting` protocol.

    This class serves as the main interface for HTTP operations in Haiway,
    offering convenience methods for common HTTP verbs while maintaining
    flexibility through the general `request` method.

    Attributes
    ----------
    requesting : HTTPRequesting
        The protocol implementation that performs actual HTTP requests.

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
    ...     body=json.dumps({"name": "Alice"}),
    ...     headers={"Content-Type": "application/json"}
    ... )
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

        Returns
        -------
        HTTPResponse
            The response from the GET request.

        Raises
        ------
        HTTPClientError
            If the request fails.
        """
        try:
            return await self.requesting(
                "GET",
                url=url,
                query=query,
                headers=headers,
                body=None,
                timeout=timeout,
                follow_redirects=follow_redirects,
            )

        except HTTPClientError:
            raise

        except Exception as exc:
            raise HTTPClientError(
                f"HTTP request failed due to an error: {exc or type(exc).__name__}",
                method="GET",
                url=url,
                cause=exc,
            ) from exc

    @overload
    @classmethod
    async def put(
        cls,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
    ) -> HTTPResponse: ...

    @overload
    async def put(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
    ) -> HTTPResponse: ...

    @statemethod
    async def put(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
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
        body : str | bytes | None, optional
            Request body content.
        timeout : float | None, optional
            Request timeout in seconds.
        follow_redirects : bool | None, optional
            Whether to follow redirects.

        Returns
        -------
        HTTPResponse
            The response from the PUT request.

        Raises
        ------
        HTTPClientError
            If the request fails.
        """
        try:
            return await self.requesting(
                "PUT",
                url=url,
                query=query,
                headers=headers,
                body=body,
                timeout=timeout,
                follow_redirects=follow_redirects,
            )

        except HTTPClientError:
            raise

        except Exception as exc:
            raise HTTPClientError(
                f"HTTP request failed due to an error: {exc or type(exc).__name__}",
                method="PUT",
                url=url,
                cause=exc,
            ) from exc

    @overload
    @classmethod
    async def post(
        cls,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
    ) -> HTTPResponse: ...

    @overload
    async def post(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
    ) -> HTTPResponse: ...

    @statemethod
    async def post(
        self,
        *,
        url: str,
        query: HTTPQueryParams | None = None,
        headers: HTTPHeaders | None = None,
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
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
        body : str | bytes | None, optional
            Request body content.
        timeout : float | None, optional
            Request timeout in seconds.
        follow_redirects : bool | None, optional
            Whether to follow redirects.

        Returns
        -------
        HTTPResponse
            The response from the POST request.

        Raises
        ------
        HTTPClientError
            If the request fails.
        """
        try:
            return await self.requesting(
                "POST",
                url=url,
                query=query,
                headers=headers,
                body=body,
                timeout=timeout,
                follow_redirects=follow_redirects,
            )

        except HTTPClientError:
            raise

        except Exception as exc:
            raise HTTPClientError(
                f"HTTP request failed due to an error: {exc or type(exc).__name__}",
                method="POST",
                url=url,
                cause=exc,
            ) from exc

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
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
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
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
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
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
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
        body : str | bytes | None, optional
            Request body content.
        timeout : float | None, optional
            Request timeout in seconds. None uses client default.
        follow_redirects : bool | None, optional
            Whether to follow redirects. None uses client default.

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
        ...     body=json.dumps({"status": "active"}),
        ...     headers={"Content-Type": "application/json"}
        ... )
        """
        try:
            return await self.requesting(
                method,
                url=url,
                query=query,
                headers=headers,
                body=body,
                timeout=timeout,
                follow_redirects=follow_redirects,
            )

        except HTTPClientError:
            raise

        except Exception as exc:
            raise HTTPClientError(
                f"HTTP request failed due to an error: {exc or type(exc).__name__}"
            ) from exc

    requesting: HTTPRequesting
