from asyncio import ALL_COMPLETED, FIRST_COMPLETED, CancelledError, Task, wait
from collections.abc import (
    AsyncIterable,
    AsyncIterator,
    Callable,
    Collection,
    Coroutine,
    MutableSequence,
    Sequence,
)
from typing import Any, Literal, overload

from haiway.context import ctx

__all__ = (
    "execute_concurrently",
    "process_concurrently",
    "stream_concurrently",
)


async def process_concurrently[Element](  # noqa: C901, PLR0912
    source: AsyncIterator[Element],
    /,
    handler: Callable[[Element], Coroutine[Any, Any, None]],
    *,
    concurrent_tasks: int = 2,
    ignore_exceptions: bool = False,
) -> None:
    """Process elements from an async iterator concurrently.

    Consumes elements from an async iterator and processes them using the provided
    handler function. Processing happens concurrently with a configurable maximum
    number of concurrent tasks. Elements are processed as they become available,
    maintaining the specified concurrency limit.

    The function continues until the source iterator is exhausted. If the function
    is cancelled, all running tasks are also cancelled. When ignore_exceptions is
    False, the first exception encountered will stop processing and propagate.

    Parameters
    ----------
    source : AsyncIterator[Element]
        An async iterator providing elements to process. Elements are consumed
        one at a time as processing slots become available.
    handler : Callable[[Element], Coroutine[Any, Any, None]]
        A coroutine function that processes each element. The handler should
        not return a value (returns None).
    concurrent_tasks : int, default=2
        Maximum number of concurrent tasks. Must be greater than 0. Higher
        values allow more parallelism but consume more resources.
    ignore_exceptions : bool, default=False
        If True, exceptions from handler tasks will be logged but not propagated,
        allowing processing to continue. If False, the first exception stops
        all processing.

    Raises
    ------
    CancelledError
        If the function is cancelled, propagated after cancelling all running tasks.
    Exception
        Any exception raised by handler tasks when ignore_exceptions is False.

    Examples
    --------
    >>> async def process_item(item: str) -> None:
    ...     await some_async_operation(item)
    ...
    >>> async def items() -> AsyncIterator[str]:
    ...     for i in range(10):
    ...         yield f"item_{i}"
    ...
    >>> await process_concurrently(
    ...     items(),
    ...     process_item,
    ...     concurrent_tasks=5
    ... )

    """
    assert concurrent_tasks > 0  # nosec: B101
    running: set[Task[None]] = set()
    try:
        while True:
            element: Element = await anext(source)
            running.add(ctx.spawn(handler, element))
            if len(running) < concurrent_tasks:
                continue  # keep spawning tasks

            completed, running = await wait(
                running,
                return_when=FIRST_COMPLETED,
            )

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

    except StopAsyncIteration:
        pass  # just stop and proceed to finally

    finally:
        if running:
            completed, _ = await wait(
                running,
                return_when=ALL_COMPLETED,
            )
            for task in completed:
                if exc := task.exception():
                    if not ignore_exceptions:
                        raise exc

                    ctx.log_error(
                        f"Concurrent processing error - {type(exc)}: {exc}",
                        exception=exc,
                    )


@overload
async def execute_concurrently[Element, Result](
    handler: Callable[[Element], Coroutine[Any, Any, Result]],
    /,
    elements: Collection[Element],
    *,
    concurrent_tasks: int = 2,
) -> Sequence[Result]: ...


@overload
async def execute_concurrently[Element, Result](
    handler: Callable[[Element], Coroutine[Any, Any, Result]],
    /,
    elements: Collection[Element],
    *,
    concurrent_tasks: int = 2,
    return_exceptions: Literal[True],
) -> Sequence[Result | BaseException]: ...


async def execute_concurrently[Element, Result](  # noqa: C901
    handler: Callable[[Element], Coroutine[Any, Any, Result]],
    /,
    elements: Collection[Element],
    *,
    concurrent_tasks: int = 2,
    return_exceptions: bool = False,
) -> Sequence[Result | BaseException] | Sequence[Result]:
    """Execute handler for each element from a collection concurrently.

    Processes all elements from a collection using the provided handler function,
    executing multiple handlers concurrently up to the specified limit. Results
    are collected and returned in the same order as the input elements.

    Unlike `process_concurrently`, this function:
    - Works with collections (known size) rather than async iterators
    - Returns results from each handler invocation
    - Preserves the order of results to match input order

    The function ensures all tasks complete before returning. If cancelled,
    all running tasks are cancelled before propagating the cancellation.

    Parameters
    ----------
    handler : Callable[[Element], Coroutine[Any, Any, Result]]
        A coroutine function that processes each element and returns a result.
    elements : Collection[Element]
        A collection of elements to process. The collection size determines
        the result sequence length.
    concurrent_tasks : int, default=2
        Maximum number of concurrent tasks. Must be greater than 0. Higher
        values allow more parallelism but consume more resources.
    return_exceptions : bool, default=False
        If True, exceptions from handler tasks are included in the results
        as BaseException instances. If False, the first exception stops
        processing and is raised.

    Returns
    -------
    Sequence[Result] or Sequence[Result | BaseException]
        Results from each handler invocation, in the same order as input elements.
        If return_exceptions is True, failed tasks return BaseException instances.

    Raises
    ------
    CancelledError
        If the function is cancelled, propagated after cancelling all running tasks.
    Exception
        Any exception raised by handler tasks when return_exceptions is False.

    Examples
    --------
    >>> async def fetch_data(url: str) -> dict:
    ...     return await http_client.get(url)
    ...
    >>> urls = ["http://api.example.com/1", "http://api.example.com/2"]
    >>> results = await execute_concurrently(
    ...     fetch_data,
    ...     urls,
    ...     concurrent_tasks=10
    ... )
    >>> # results[0] corresponds to urls[0], results[1] to urls[1], etc.

    >>> # With exception handling
    >>> results = await execute_concurrently(
    ...     fetch_data,
    ...     urls,
    ...     concurrent_tasks=10,
    ...     return_exceptions=True
    ... )
    >>> for url, result in zip(urls, results):
    ...     if isinstance(result, BaseException):
    ...         print(f"Failed to fetch {url}: {result}")
    ...     else:
    ...         print(f"Got data from {url}")

    """
    assert concurrent_tasks > 0  # nosec: B101
    running: set[Task[Result]] = set()
    results: MutableSequence[Task[Result]] = []
    try:
        for element in elements:
            task: Task[Result] = ctx.spawn(handler, element)
            results.append(task)
            running.add(task)
            if len(running) < concurrent_tasks:
                continue  # keep spawning tasks

            completed, running = await wait(
                running,
                return_when=FIRST_COMPLETED,
            )

            for task in completed:
                if exc := task.exception():
                    if not return_exceptions:
                        raise exc

                    ctx.log_error(
                        f"Concurrent execution error - {type(exc)}: {exc}",
                        exception=exc,
                    )

    except CancelledError as exc:
        # Cancel all running tasks
        for task in running:
            task.cancel()

        raise exc

    finally:
        if running:
            completed, _ = await wait(
                running,
                return_when=ALL_COMPLETED,
            )
            for task in completed:
                if exc := task.exception():
                    if not return_exceptions:
                        raise exc

                    ctx.log_error(
                        f"Concurrent execution error - {type(exc)}: {exc}",
                        exception=exc,
                    )

    return [result.exception() or result.result() for result in results]


async def stream_concurrently[ElementA, ElementB](  # noqa: C901
    source_a: AsyncIterator[ElementA],
    source_b: AsyncIterator[ElementB],
    /,
) -> AsyncIterable[ElementA | ElementB]:
    """Merge streams from two async iterators processed concurrently.

    Concurrently consumes elements from two async iterators and yields them
    as they become available. Elements from both sources are interleaved based
    on which iterator produces them first. The function continues until both
    iterators are exhausted.

    This is useful for combining multiple async data sources into a single
    stream while maintaining concurrency. Each iterator is polled independently,
    and whichever has data available first will have its element yielded.

    Parameters
    ----------
    source_a : AsyncIterator[ElementA]
        First async iterator to consume from.
    source_b : AsyncIterator[ElementB]
        Second async iterator to consume from.

    Yields
    ------
    ElementA | ElementB
        Elements from either source as they become available. The order
        depends on which iterator produces elements first.

    Raises
    ------
    CancelledError
        If the async generator is cancelled, both source tasks are cancelled
        before propagating the cancellation.
    Exception
        Any exception raised by either source iterator.

    Examples
    --------
    >>> async def numbers() -> AsyncIterator[int]:
    ...     for i in range(5):
    ...         await asyncio.sleep(0.1)
    ...         yield i
    ...
    >>> async def letters() -> AsyncIterator[str]:
    ...     for c in "abcde":
    ...         await asyncio.sleep(0.15)
    ...         yield c
    ...
    >>> async for item in stream_concurrently(numbers(), letters()):
    ...     print(item)  # Prints interleaved numbers and letters

    Notes
    -----
    The function maintains exactly one pending task per iterator at all times,
    ensuring efficient resource usage while maximizing throughput from both
    sources.

    """

    async def next_a() -> ElementA:
        return await anext(source_a)

    async def next_b() -> ElementB:
        return await anext(source_b)

    task_a: Task[ElementA] = ctx.spawn(next_a)
    task_b: Task[ElementB] = ctx.spawn(next_b)

    try:
        while not (  # Continue until both iterators are exhausted
            task_a.done()
            and task_b.done()
            and isinstance(task_a.exception(), StopAsyncIteration)
            and isinstance(task_b.exception(), StopAsyncIteration)
        ):
            # Wait for at least one task to complete
            done, _ = await wait(
                {task_a, task_b},
                return_when=FIRST_COMPLETED,
            )

            # Process completed tasks
            for task in done:
                if task is task_a:
                    exc: BaseException | None = task.exception()
                    if exc is None:
                        yield task.result()
                        task_a = ctx.spawn(next_a)

                    elif not isinstance(exc, StopAsyncIteration):
                        raise exc
                    # If StopAsyncIteration, don't respawn task_a

                elif task is task_b:
                    exc: BaseException | None = task.exception()
                    if exc is None:
                        yield task.result()
                        task_b = ctx.spawn(next_b)

                    elif not isinstance(exc, StopAsyncIteration):
                        raise exc
                    # If StopAsyncIteration, don't respawn task_b

    except CancelledError as exc:
        # Cancel all running tasks
        task_a.cancel()
        task_b.cancel()
        raise exc

    finally:
        # Ensure cleanup of any remaining tasks
        for task in (task_a, task_b):
            task.cancel()
