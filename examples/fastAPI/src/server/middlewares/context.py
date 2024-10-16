from uuid import uuid4

from haiway import ctx
from starlette.datastructures import MutableHeaders
from starlette.exceptions import HTTPException
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
            case "http":
                trace_id: str = uuid4().hex
                with ctx.scope(
                    scope.get("root_path", "") + scope["path"],
                    *scope["app"].extra.get("state", ()),
                    trace_id=trace_id,
                ):

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
                        if isinstance(exc.headers, dict):  # type: ignore
                            exc.headers["trace_id"] = (  # pyright: ignore[reportUnknownMemberType]
                                f"{trace_id}"
                            )

                        else:
                            exc.headers = {"trace_id": f"{trace_id}"}

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

            case _:
                return await self.app(scope, receive, send)
