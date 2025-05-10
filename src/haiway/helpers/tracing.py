from asyncio import iscoroutinefunction
from collections.abc import Callable, Coroutine, Sequence
from typing import Any, Self, cast, overload

from haiway.context import ctx
from haiway.state import State
from haiway.types import MISSING, Missing
from haiway.utils import mimic_function

__all__ = (
    "ArgumentsTrace",
    "ResultTrace",
    "traced",
)


class ArgumentsTrace(State):
    if __debug__:

        @classmethod
        def of(
            cls,
            *args: Any,
            **kwargs: Any,
        ) -> Self:
            return cls(
                args=[f"{arg}" for arg in args] if args else MISSING,
                kwargs=[f"{key}:{arg}" for key, arg in kwargs.items()] if kwargs else MISSING,
            )

    else:  # remove tracing for non debug runs to prevent accidental secret leaks

        @classmethod
        def of(
            cls,
            *args: Any,
            **kwargs: Any,
        ) -> Self:
            return cls(
                args=MISSING,
                kwargs=MISSING,
            )

    args: Sequence[str] | Missing
    kwargs: Sequence[str] | Missing


class ResultTrace(State):
    if __debug__:

        @classmethod
        def of(
            cls,
            value: Any,
            /,
        ) -> Self:
            return cls(result=f"{value}")

    else:  # remove tracing for non debug runs to prevent accidental secret leaks

        @classmethod
        def of(
            cls,
            value: Any,
            /,
        ) -> Self:
            return cls(result=MISSING)

    result: str | Missing


@overload
def traced[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Result]: ...


@overload
def traced[**Args, Result](
    *,
    label: str,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]]: ...


def traced[**Args, Result](
    function: Callable[Args, Result] | None = None,
    /,
    *,
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
                    ),
                )

            else:
                return _traced_sync(
                    wrapped,
                    label=label or wrapped.__name__,
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
) -> Callable[Args, Result]:
    def traced(
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        with ctx.scope(label):
            ctx.event(ArgumentsTrace.of(*args, **kwargs))
            try:
                result: Result = function(*args, **kwargs)
                ctx.event(ResultTrace.of(result))
                return result

            except BaseException as exc:
                ctx.event(ResultTrace.of(f"{type(exc)}: {exc}"))
                raise exc

    return mimic_function(
        function,
        within=traced,
    )


def _traced_async[**Args, Result](
    function: Callable[Args, Coroutine[Any, Any, Result]],
    /,
    label: str,
) -> Callable[Args, Coroutine[Any, Any, Result]]:
    async def traced(
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        with ctx.scope(label):
            ctx.event(ArgumentsTrace.of(*args, **kwargs))
            try:
                result: Result = await function(*args, **kwargs)
                ctx.event(ResultTrace.of(result))
                return result

            except BaseException as exc:
                ctx.event(ResultTrace.of(f"{type(exc)}: {exc}"))
                raise exc

    return mimic_function(
        function,
        within=traced,
    )
