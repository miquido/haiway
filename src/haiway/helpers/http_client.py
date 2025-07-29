from collections.abc import Mapping, Sequence
from typing import Protocol, final, runtime_checkable

from haiway.context import ctx
from haiway.state import State

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


class HTTPResponse(State):
    """Immutable HTTP response container.

    Encapsulates the response from an HTTP request including status code,
    headers, and body content.

    Attributes
    ----------
    status_code : int
        HTTP status code (e.g., 200, 404, 500).
    headers : Mapping[str, str]
        Response headers as an immutable mapping.
    body : bytes
        Raw response body content.

    Examples
    --------
    >>> response = HTTPResponse(
    ...     status_code=200,
    ...     headers={"Content-Type": "application/json"},
    ...     body=b'{"status": "ok"}'
    ... )
    >>> data = json.loads(response.body)
    """

    status_code: HTTPStatusCode
    headers: HTTPHeaders
    body: bytes


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
    pass


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
    ...         data = json.loads(response.body)
    ...
    >>> # Making a POST request
    >>> response = await HTTPClient.post(
    ...     url="https://api.example.com/users",
    ...     body=json.dumps({"name": "Alice"}),
    ...     headers={"Content-Type": "application/json"}
    ... )
    """

    @classmethod
    async def get(
        cls,
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
        return await cls.request(
            "GET",
            url=url,
            query=query,
            headers=headers,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

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
        return await cls.request(
            "PUT",
            url=url,
            query=query,
            headers=headers,
            body=body,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

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
        return await cls.request(
            "POST",
            url=url,
            query=query,
            headers=headers,
            body=body,
            timeout=timeout,
            follow_redirects=follow_redirects,
        )

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
            return await ctx.state(cls).requesting(
                method,
                url=url,
                query=query,
                headers=headers,
                body=body,
                timeout=timeout,
                follow_redirects=follow_redirects,
            )

        except HTTPClientError as exc:
            raise exc

        except Exception as exc:
            raise HTTPClientError(
                f"HTTP request failed due to an error: {exc or type(exc).__name__}"
            ) from exc

    requesting: HTTPRequesting
