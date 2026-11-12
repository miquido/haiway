from asyncio import AbstractEventLoop, get_running_loop
from collections.abc import Callable
from functools import partial, wraps
from typing import Concatenate, Protocol

__all__ = (
    "LoopBound",
    "foreign_loop",
    "loop_safe",
)


class LoopBound(Protocol):
    _loop: AbstractEventLoop


def foreign_loop(
    loop: AbstractEventLoop,
    /,
) -> bool:
    # a loop which is not running has nothing to race with - acting in place is safe
    try:
        return get_running_loop() is not loop and loop.is_running()

    except RuntimeError:  # no loop running within this thread at all
        return loop.is_running()


def loop_safe[Owner: LoopBound, **Args]() -> Callable[
    [Callable[Concatenate[Owner, Args], None]],
    Callable[Concatenate[Owner, Args], None],
]:
    # a call made outside of the owner's loop is handed over to it, the wrapped
    # method then runs there later - so it can't return anything to the caller

    def decorator(
        method: Callable[Concatenate[Owner, Args], None],
        /,
    ) -> Callable[Concatenate[Owner, Args], None]:
        @wraps(method)
        def wrapped(
            owner: Owner,
            /,
            *args: Args.args,
            **kwargs: Args.kwargs,
        ) -> None:
            loop: AbstractEventLoop = owner._loop  # pyright: ignore[reportPrivateUsage]
            if foreign_loop(loop):
                loop.call_soon_threadsafe(partial(wrapped, owner, *args, **kwargs))

            else:
                method(owner, *args, **kwargs)

        return wrapped

    return decorator
