from collections.abc import AsyncGenerator, Mapping
from typing import final

from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from starlette.responses import StreamingResponse
from starlette.types import Message, Send

from haiway.context import ctx

__all__ = ("StreamResponse",)


@final
class StreamResponse(StreamingResponse):
    """Response streaming the elements of an async generator.

    The Haiway counterpart of ``starlette.responses.StreamingResponse``: it
    streams the same way and adds closing the generator where the streaming
    ends - when it ran out of chunks and when the consumer went away - which is
    what a generator holding a context scope requires.

    The scope of the request stays entered for the whole response, so the
    generator resolves state, records observability and reports the trace of the
    request it belongs to - the middleware returns only once the last chunk was
    sent.

    Parameters
    ----------
    content : AsyncGenerator[bytes | str]
        Source of the response body. A full generator is required, not any
        async iterable, because an abandoned stream has to be closable - wrap an
        iterator in a generator to stream it.
    status_code : int
        Status code of the response.
    headers : Mapping[str, str] | None
        Headers of the response.
    media_type : str | None
        Media type of the response body.
    background : BackgroundTask | None
        Task to run once the body was streamed, still within the scope of the
        request.

    Examples
    --------
    >>> async def endpoint(request: Request) -> Response:
    ...     async def content() -> AsyncGenerator[bytes]:
    ...         async for row in Postgres.fetch_rows(QUERY):  # request state
    ...             yield row.get_str("payload").encode()
    ...
    ...     return StreamResponse(content(), media_type="application/x-ndjson")

    Raises
    ------
    AssertionError
        When ``content`` is not an async generator - a response of this kind is
        returned from a handler rather than declared as its response class,
        which would be handed a serialized value instead. Checked in debug
        builds only, where a wiring mistake is worth reporting rather than
        paying for on every response.

    Notes
    -----
    A server advertising ASGI spec version 2.4 or newer is required, which is
    the one reporting a gone consumer by failing the send. Below that version
    the framework ends a streamed response by cancelling it, which can not close
    a body whose cleanup awaits anything - the cancellation is delivered again
    at the first await of that cleanup, leaving the body suspended halfway
    through it.

    A generator opening a scope of its own has to keep it inside itself, which
    is what ``ctx.stream`` provides - a scope entered around building the
    generator is already released by the time the streaming starts.
    """

    def __init__(
        self,
        content: AsyncGenerator[bytes | str],
        status_code: int = 200,
        headers: Mapping[str, str] | None = None,
        media_type: str | None = None,
        background: BackgroundTask | None = None,
    ) -> None:
        assert isinstance(content, AsyncGenerator)  # nosec: B101
        super().__init__(
            content=content,
            status_code=status_code,
            headers=headers,
            media_type=media_type,
            background=background,
        )
        # kept typed, unlike the `body_iterator` of the framework, which is
        # allowed to be an async iterable with nothing to close
        self._stream: AsyncGenerator[bytes | str] = content

    async def stream_response(
        self,
        send: Send,
    ) -> None:
        # a gone consumer is reported by the send failing, which is what tells it
        # apart from a body failing with an `OSError` of its own - the framework
        # collapses the two into a `ClientDisconnect` above this, where the
        # failure of a body would be lost with nothing recording it
        disconnected: bool = False

        async def tracked_send(message: Message) -> None:
            nonlocal disconnected
            try:
                await send(message)

            except OSError:
                disconnected = True
                raise

        try:
            await super().stream_response(tracked_send)

        except Exception as exc:
            if disconnected or isinstance(exc, ClientDisconnect):
                # a consumer which went away is not a failure of the response it
                # ended - recorded as what happened rather than as an error
                ctx.log_debug("Response streaming ended by a disconnected consumer")

            else:
                # recorded here, where the failure actually is. A response which
                # already started can not be answered with an error, so this one
                # travels out through the exception handling of the framework,
                # which replaces it with a `RuntimeError` about a response
                # already started whenever a handler matches its type - what the
                # scope of the request would then record instead of the failure
                # which happened
                ctx.log_error(
                    "Response streaming failed",
                    exception=exc,
                )

            raise  # the transport still has to end the response as incomplete

        finally:
            # closed where the streaming ended, whether the body ran out or the
            # connection went away. Leaving it to the garbage collector would
            # finalize it in a fresh context, where a scope it opened - what
            # `ctx.stream` provides - can no longer be released
            try:
                await self._stream.aclose()

            except Exception as exc:
                # a failure to close can not fix the response and must not
                # replace what is already on its way out - cancellation is not
                # caught here, it has to keep unwinding
                ctx.log_warning(
                    "Response stream failed to close",
                    exception=exc,
                )
