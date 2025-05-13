from asyncio import iscoroutinefunction
from collections.abc import Callable, Coroutine
from typing import Any, cast, overload

from haiway.context import ctx
from haiway.context.observability import ObservabilityLevel
from haiway.types import MISSING
from haiway.utils import mimic_function
from haiway.utils.formatting import format_str

__all__ = ("traced",)


@overload
def traced[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Result]: ...


@overload
def traced[**Args, Result](
    *,
    level: ObservabilityLevel = ObservabilityLevel.DEBUG,
    label: str,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]]: ...


def traced[**Args, Result](
    function: Callable[Args, Result] | None = None,
    /,
    *,
    level: ObservabilityLevel = ObservabilityLevel.DEBUG,
    label: str | None = None,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]] | Callable[Args, Result]:
    def wrap(
        wrapped: Callable[Args, Result],
    ) -> Callable[Args, Result]:
        if __debug__:
            if iscoroutinefunction(wrapped):
                return cast(
                    Callable[Args, Result],
                    _traced_async(
                        wrapped,
                        label=label or wrapped.__name__,
                        level=level,
                    ),
                )

            else:
                return _traced_sync(
                    wrapped,
                    label=label or wrapped.__name__,
                    level=level,
                )

        else:  # do not trace on non debug runs
            return wrapped

    if function := function:
        return wrap(wrapped=function)

    else:
        return wrap


def _traced_sync[**Args, Result](
    function: Callable[Args, Result],
    /,
    label: str,
    level: ObservabilityLevel,
) -> Callable[Args, Result]:
    def traced(
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        with ctx.scope(label):
            ctx.record(
                level,
                attributes={
                    f"[{idx}]": f"{arg}" for idx, arg in enumerate(args) if arg is not MISSING
                },
            )
            ctx.record(
                level,
                attributes={key: f"{arg}" for key, arg in kwargs.items() if arg is not MISSING},
            )

            try:
                result: Result = function(*args, **kwargs)
                ctx.record(
                    level,
                    event="result",
                    attributes={"value": format_str(result)},
                )
                return result

            except BaseException as exc:
                ctx.record(
                    level,
                    event="result",
                    attributes={"error": f"{type(exc)}: {exc}"},
                )
                raise exc

    return mimic_function(
        function,
        within=traced,
    )


def _traced_async[**Args, Result](
    function: Callable[Args, Coroutine[Any, Any, Result]],
    /,
    label: str,
    level: ObservabilityLevel,
) -> Callable[Args, Coroutine[Any, Any, Result]]:
    async def traced(
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        with ctx.scope(label):
            ctx.record(
                level,
                attributes={
                    f"[{idx}]": f"{arg}" for idx, arg in enumerate(args) if arg is not MISSING
                },
            )
            ctx.record(
                level,
                attributes={key: f"{arg}" for key, arg in kwargs.items() if arg is not MISSING},
            )

            try:
                result: Result = await function(*args, **kwargs)
                ctx.record(
                    level,
                    event="result",
                    attributes={"value": format_str(result)},
                )
                return result

            except BaseException as exc:
                ctx.record(
                    level,
                    event="result",
                    attributes={"error": f"{type(exc)}: {exc}"},
                )
                raise exc

    return mimic_function(
        function,
        within=traced,
    )
