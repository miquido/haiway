from collections.abc import Mapping, Sequence
from typing import final

from starlette.background import BackgroundTask
from starlette.responses import Response

from haiway.attributes import State

__all__ = ("StateResponse",)


@final
class StateResponse(Response):
    """Response carrying a State, or a sequence of States, as a JSON body.

    The Haiway counterpart of ``starlette.responses.JSONResponse``: the body is
    written by ``State.to_json``, so the types, redaction and custom encoding
    of the State apply instead of those of ``json.dumps``. A sequence is
    written as a JSON array.

    Parameters
    ----------
    content : State | Sequence[State]
        State to serialize, or a sequence of States written as a JSON array.
    status_code : int
        Status code of the response.
    headers : Mapping[str, str] | None
        Headers of the response.
    media_type : str | None
        Media type of the response body, ``application/json`` by default.
    background : BackgroundTask | None
        Task to run once the response was sent, still within the scope of the
        request.

    Examples
    --------
    >>> async def endpoint(request: Request) -> Response:
    ...     profile: Profile = await Profiles.load(request.path_params["id"])
    ...     return StateResponse(profile)

    Raises
    ------
    ValueError
        When a State fails to encode. Raised where the response is built, so
        the handler fails with it rather than the transport, half way through
        the response.
    """

    media_type = "application/json"

    def __init__(
        self,
        content: State | Sequence[State],
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        super().__init__(
            content=content,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )

    def render(
        self,
        content: State | Sequence[State],
    ) -> bytes:
        if isinstance(content, State):
            return content.to_json().encode()

        return b"[" + b",".join(element.to_json().encode() for element in content) + b"]"
