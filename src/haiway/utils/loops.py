from asyncio import AbstractEventLoop, get_running_loop
from collections.abc import Callable
from functools import partial, wraps
from typing import Concatenate, Protocol

__all__ = (
    "LoopBound",
    "loop_safe",
)


class LoopBound(Protocol):
    _loop: AbstractEventLoop


def loop_safe[Owner: LoopBound, **Args](
    method: Callable[Concatenate[Owner, Args], None],
) -> Callable[Concatenate[Owner, Args], None]:

    @wraps(method)
    def wrapped(
        owner: Owner,
        /,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> None:
        required: AbstractEventLoop = owner._loop  # pyright: ignore[reportPrivateUsage]
        running: AbstractEventLoop | None
        try:
            running = get_running_loop()

        except RuntimeError:
            # no loop running within this thread at all
            running = None

        if running is not required:
            required.call_soon_threadsafe(
                partial(
                    wrapped,
                    owner,
                    *args,
                    **kwargs,
                )
            )

        else:
            method(owner, *args, **kwargs)

    return wrapped
