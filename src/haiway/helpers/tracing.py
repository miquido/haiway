from asyncio import iscoroutinefunction
from collections.abc import Callable, Coroutine, Mapping, Sequence
from typing import Any, Self, cast

from haiway.context import ctx
from haiway.state import State
from haiway.types import MISSING, Missing
from haiway.utils import mimic_function

__all__ = [
    "ArgumentsTrace",
    "ResultTrace",
    "traced",
]


class ArgumentsTrace(State):
    if __debug__:

        @classmethod
        def of(cls, *args: Any, **kwargs: Any) -> Self:
            return cls(
                args=args if args else MISSING,
                kwargs=kwargs if kwargs else MISSING,
            )

    else:  # remove tracing for non debug runs to prevent accidental secret leaks

        @classmethod
        def of(cls, *args: Any, **kwargs: Any) -> Self:
            return cls(
                args=MISSING,
                kwargs=MISSING,
            )

    args: Sequence[Any] | Missing
    kwargs: Mapping[str, Any] | Missing


class ResultTrace(State):
    if __debug__:

        @classmethod
        def of(
            cls,
            value: Any,
            /,
        ) -> Self:
            return cls(result=value)

    else:  # remove tracing for non debug runs to prevent accidental secret leaks

        @classmethod
        def of(
            cls,
            value: Any,
            /,
        ) -> Self:
            return cls(result=MISSING)

    result: Any | Missing


def traced[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Result]:
    if __debug__:
        if iscoroutinefunction(function):
            return cast(
                Callable[Args, Result],
                _traced_async(
                    function,
                    label=function.__name__,
                ),
            )
        else:
            return _traced_sync(
                function,
                label=function.__name__,
            )

    else:  # do not trace on non debug runs
        return function


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
            ctx.record(ArgumentsTrace.of(*args, **kwargs))
            try:
                result: Result = function(*args, **kwargs)
                ctx.record(ResultTrace.of(result))
                return result

            except BaseException as exc:
                ctx.record(ResultTrace.of(exc))
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
            ctx.record(ArgumentsTrace.of(*args, **kwargs))
            try:
                result: Result = await function(*args, **kwargs)
                ctx.record(ResultTrace.of(result))
                return result

            except BaseException as exc:
                ctx.record(ResultTrace.of(exc))
                raise exc

    return mimic_function(
        function,
        within=traced,
    )
