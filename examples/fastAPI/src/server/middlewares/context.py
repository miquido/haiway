from logging import getLogger

from haiway import MetricsLogger, ctx
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException, WebSocketException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

__all__ = [
    "ContextMiddleware",
]


class ContextMiddleware:
    def __init__(
        self,
        app: ASGIApp,
    ) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        match scope["type"]:
            case "websocket":
                return await self._handle_websocket(scope, receive, send)

            case "http":
                return await self._handle_http(scope, receive, send)

            case _:
                return await self.app(scope, receive, send)

    async def _handle_websocket(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        async with ctx.scope(
            f"ws {scope["path"]}",
            *scope["app"].extra.get("state", ()),
            logger=getLogger("server"),
            metrics=MetricsLogger.handler(),
        ):
            try:
                return await self.app(
                    scope,
                    receive,
                    send,
                )

            except WebSocketException as exc:
                raise exc

            except BaseException as exc:
                error_type: type[BaseException] = type(exc)
                error_message: str = (
                    f"{error_type.__name__} [{error_type.__module__}] - that is an error!"
                )

                if __debug__:
                    import traceback

                    error_message = error_message + f"\n{traceback.format_exc()}"

                ctx.log_error(error_message)

                raise WebSocketException(
                    code=1011,
                    reason=error_message,
                ) from exc

    async def _handle_http(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        async with ctx.scope(
            f"{scope.get("method", "")} {scope["path"]}",
            *scope["app"].extra.get("state", ()),
            logger=getLogger("server"),
            metrics=MetricsLogger.handler(),
        ) as trace_id:

            async def traced_send(message: Message) -> None:
                match message["type"]:
                    case "http.response.start":
                        headers = MutableHeaders(scope=message)
                        headers["trace_id"] = f"{trace_id}"

                    case _:
                        pass

                await send(message)

            try:
                return await self.app(
                    scope,
                    receive,
                    traced_send,
                )

            except HTTPException as exc:
                if exc.headers is None:
                    exc.headers = {"trace_id": f"{trace_id}"}

                elif isinstance(exc.headers, dict):
                    exc.headers["trace_id"] = f"{trace_id}"

                raise exc  # do not change behavior for HTTPException

            except BaseException as exc:
                error_type: type[BaseException] = type(exc)
                error_message: str = (
                    f"{error_type.__name__} [{error_type.__module__}] - that is an error!"
                )

                if __debug__:
                    import traceback

                    error_message = error_message + f"\n{traceback.format_exc()}"

                ctx.log_error(error_message)

                raise HTTPException(
                    status_code=500,
                    headers={"trace_id": f"{trace_id}"},
                    detail=error_message,
                ) from exc
