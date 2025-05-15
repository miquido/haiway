from asyncio import FIRST_COMPLETED, CancelledError, Task, wait
from collections.abc import AsyncIterator, Callable, Coroutine
from concurrent.futures import ALL_COMPLETED
from typing import Any

from haiway.context import ctx

__all__ = ("process_concurrently",)


async def process_concurrently[Element](  # noqa: C901
    source: AsyncIterator[Element],
    /,
    handler: Callable[[Element], Coroutine[Any, Any, None]],
    *,
    concurrent_tasks: int = 2,
    ignore_exceptions: bool = False,
) -> None:
    """Process elements from an async iterator concurrently.

    Parameters
    ----------
    source: AsyncIterator[Element]
        An async iterator providing elements to process.

    handler: Callable[[Element], Coroutine[Any, Any, None]]
        A coroutine function that processes each element.

    concurrent_tasks: int
        Maximum number of concurrent tasks (must be > 0), default is 2.

    ignore_exceptions: bool
        If True, exceptions from tasks will be logged but not propagated,
         default is False.

    """
    assert concurrent_tasks > 0  # nosec: B101
    running: set[Task[None]] = set()
    try:
        while element := await anext(source, None):
            if len(running) < concurrent_tasks:
                running.add(ctx.spawn(handler, element))
                continue  # keep spawning tasks

            completed, running = await wait(running, return_when=FIRST_COMPLETED)

            for task in completed:
                if exc := task.exception():
                    if not ignore_exceptions:
                        raise exc

                    ctx.log_error(
                        f"Concurrent processing error - {type(exc)}: {exc}",
                        exception=exc,
                    )

    except CancelledError as exc:
        # Cancel all running tasks
        for task in running:
            task.cancel()

        raise exc

    finally:
        completed, _ = await wait(running, return_when=ALL_COMPLETED)
        for task in completed:
            if exc := task.exception():
                if not ignore_exceptions:
                    raise exc

                ctx.log_error(
                    f"Concurrent processing error - {type(exc)}: {exc}",
                    exception=exc,
                )
