from collections.abc import Mapping
from http.cookiejar import CookieJar, DefaultCookiePolicy
from types import TracebackType
from typing import Any, final

from httpx import URL, USE_CLIENT_DEFAULT, AsyncClient, Response

from haiway.helpers import (
    HTTPClient,
    HTTPClientError,
    HTTPHeaders,
    HTTPQueryParams,
    HTTPResponse,
)
from haiway.types import Immutable

__all__ = ("HTTPXClient",)


@final
class HTTPXClient(Immutable):
    """HTTPX-based implementation of the HTTP client.

    Provides an async HTTP client using the HTTPX library as the backend.
    Implements the HTTPRequesting protocol and integrates with Haiway's
    context system through an async context manager interface.

    The client is configured with sensible defaults including disabled cookies
    and explicit redirect handling. It supports connection pooling and reuse
    through the context manager pattern.

    Parameters
    ----------
    base_url : str | None, optional
        Base URL for all requests. Relative URLs will be resolved against this.
    headers : HTTPHeaders | None, optional
        Default headers to include in all requests.
    timeout : float | None, optional
        Default timeout in seconds for all requests.
    **extra : Any
        Additional keyword arguments passed directly to the HTTPX AsyncClient.

    Attributes
    ----------
    base_url : URL
        The configured base URL as an HTTPX URL object.

    Examples
    --------
    >>> # Basic usage with context manager
    >>> async with HTTPXClient(base_url="https://api.example.com") as http:
    ...     async with ctx.scope("api", http):
    ...         response = await HTTPClient.get(url="/users")
    ...
    >>> # With custom configuration
    >>> async with HTTPXClient(
    ...     base_url="https://api.example.com",
    ...     headers={"Authorization": "Bearer token"},
    ...     timeout=30.0,
    ...     max_redirects=5  # extra HTTPX option
    ... ) as http:
    ...     async with ctx.scope("api", http):
    ...         response = await HTTPClient.post(
    ...             url="/data",
    ...             body=json.dumps({"key": "value"})
    ...         )

    Notes
    -----
    - Cookies are disabled by default for security and predictability.
    - The client must be used as an async context manager to ensure proper
      resource cleanup.
    - Each context manager entry creates a fresh client connection pool.
    """

    _base_url: str
    _headers: HTTPHeaders | None
    _timeout: float | None
    _client: AsyncClient
    _extra: Mapping[str, Any]

    def __init__(
        self,
        base_url: str | None = None,
        headers: HTTPHeaders | None = None,
        timeout: float | None = None,
        **extra: Any,
    ) -> None:
        object.__setattr__(
            self,
            "_base_url",
            base_url or "",
        )
        object.__setattr__(
            self,
            "_headers",
            headers,
        )
        object.__setattr__(
            self,
            "_timeout",
            timeout,
        )
        object.__setattr__(
            self,
            "_extra",
            extra,
        )
        object.__setattr__(
            self,
            "_client",
            self._prepare_client(),
        )

    def _prepare_client(self) -> AsyncClient:
        return AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            cookies=CookieJar(  # disable cookies
                policy=DefaultCookiePolicy(allowed_domains=()),
            ),
            timeout=self._timeout,
            follow_redirects=False,
            **self._extra,
        )

    @property
    def base_url(self) -> URL:
        return self._client.base_url

    async def __aenter__(self) -> HTTPClient:
        """Enter the async context manager and return an HTTPClient.

        Creates or reuses an HTTPX AsyncClient and returns an HTTPClient
        instance configured to use this client for requests.

        Returns
        -------
        HTTPClient
            An HTTPClient instance bound to this HTTPX client.

        Notes
        -----
        If the internal client is closed, a new one will be created with
        the same configuration.
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
        body: str | bytes | None = None,
        timeout: float | None = None,
        follow_redirects: bool | None = None,
    ) -> HTTPResponse:
        """Execute an HTTP request using the HTTPX client.

        Implements the HTTPRequesting protocol to perform HTTP requests.
        This method is called by the HTTPClient interface methods.

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
        body : str | bytes | None, optional
            Request body content.
        timeout : float | None, optional
            Request timeout. Overrides default timeout if specified.
        follow_redirects : bool | None, optional
            Whether to follow redirects. Overrides default if specified.

        Returns
        -------
        HTTPResponse
            The HTTP response with status, headers, and body.

        Raises
        ------
        HTTPClientError
            Wraps any exception that occurs during the request.

        Notes
        -----
        - Response body is fully read into memory before returning.
        - Uses HTTPX's USE_CLIENT_DEFAULT for None timeout/redirect values.
        """
        try:
            response: Response = await self._client.request(
                method=method,
                url=url,
                headers=headers,
                params=query,
                content=body,
                timeout=timeout if timeout is not None else USE_CLIENT_DEFAULT,
                follow_redirects=follow_redirects
                if follow_redirects is not None
                else USE_CLIENT_DEFAULT,
            )

            return HTTPResponse(
                status_code=response.status_code,
                headers=response.headers,
                body=response.aiter_bytes(),
            )

        except Exception as exc:
            url_string: str = url
            try:
                url_string = str(URL(url))

            except Exception:  # nosec: B110
                pass  # skip and use whathever there was

            raise HTTPClientError(
                "HTTP request failed",
                method=method,
                url=url_string,
                status_code=None,
                cause=exc,
            ) from exc
