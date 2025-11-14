from asyncio import ALL_COMPLETED, FIRST_COMPLETED, Task, wait
from collections.abc import (
    AsyncIterable,
    Callable,
    Collection,
    Coroutine,
    Iterable,
    Iterator,
    MutableSequence,
    MutableSet,
    Sequence,
)
from typing import Any, Literal, overload

from haiway.context import ctx
from haiway.utils.stream import AsyncStream

__all__ = (
    "concurrently",
    "execute_concurrently",
    "process_concurrently",
    "stream_concurrently",
)


async def process_concurrently[Element](  # noqa: C901, PLR0912
    source: AsyncIterable[Element] | Iterable[Element],
    /,
    handler: Callable[[Element], Coroutine[None, None, None]],
    *,
    concurrent_tasks: int = 2,
    ignore_exceptions: bool = False,
) -> None:
    """Process elements from an iterable concurrently.

    Consumes elements from an iterable and processes them using the provided
    handler function. Processing happens concurrently with a configurable maximum
    number of concurrent tasks. Elements are processed as they become available,
    maintaining the specified concurrency limit.

    The function continues until the source iterator is exhausted. If the function
    is cancelled, all running tasks are also cancelled. When ignore_exceptions is
    False, the first exception encountered will stop processing and propagate.

    Parameters
    ----------
    source : AsyncIterable[Element] | Iterable[Element]
        An iterable providing elements to process. Elements are consumed
        one at a time as processing slots become available.
    handler : Callable[[Element], Coroutine[None, None, None]]
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
    tasks: MutableSet[Task[Exception | None]] = set()

    async def process(
        element: Element,
        /,
    ) -> Exception | None:
        try:
            await handler(element)

        except Exception as exc:
            if not ignore_exceptions:
                return exc

            ctx.log_error(
                f"Concurrent processing error - {type(exc)}: {exc}",
                exception=exc,
            )

    try:
        if isinstance(source, AsyncIterable):
            async for element in source:
                tasks.add(ctx.spawn(process, element))
                if len(tasks) < concurrent_tasks:
                    continue  # keep spawning tasks

                completed, tasks = await wait(
                    tasks,
                    return_when=FIRST_COMPLETED,
                )
                for task in completed:
                    if exc := task.result():
                        raise exc

        else:
            assert isinstance(source, Iterable)  # nosec: B101
            for element in source:
                tasks.add(ctx.spawn(process, element))
                if len(tasks) < concurrent_tasks:
                    continue  # keep spawning tasks

                completed, tasks = await wait(
                    tasks,
                    return_when=FIRST_COMPLETED,
                )
                for task in completed:
                    if exc := task.result():
                        raise exc

    except BaseException as exc:
        # Cancel all running tasks
        for task in tasks:
            task.cancel()

        raise

    else:
        if tasks:
            completed, _ = await wait(
                tasks,
                return_when=ALL_COMPLETED,
            )
            for task in completed:
                if exc := task.result():
                    raise exc


@overload
async def execute_concurrently[Element, Result](
    handler: Callable[[Element], Coroutine[None, None, Result]],
    /,
    elements: AsyncIterable[Element] | Iterable[Element],
    *,
    concurrent_tasks: int = 2,
) -> Sequence[Result]: ...


@overload
async def execute_concurrently[Element, Result](
    handler: Callable[[Element], Coroutine[None, None, Result]],
    /,
    elements: AsyncIterable[Element] | Iterable[Element],
    *,
    concurrent_tasks: int = 2,
    return_exceptions: Literal[True],
) -> Sequence[Result | Exception]: ...


async def execute_concurrently[Element, Result](  # noqa: C901, PLR0912
    handler: Callable[[Element], Coroutine[None, None, Result]],
    /,
    elements: AsyncIterable[Element] | Iterable[Element],
    *,
    concurrent_tasks: int = 2,
    return_exceptions: bool = False,
) -> Sequence[Result | Exception] | Sequence[Result]:
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
    handler : Callable[[Element], Coroutine[None, None, Result]]
        A coroutine function that processes each element and returns a result.
    elements : AsyncIterable[Element] | Iterable[Element]
        A source of elements to process. The source size determines
        the result sequence length.
    concurrent_tasks : int, default=2
        Maximum number of concurrent tasks. Must be greater than 0. Higher
        values allow more parallelism but consume more resources.
    return_exceptions : bool, default=False
        If True, exceptions from handler tasks are included in the results
        as Exception instances. If False, the first exception stops
        processing and is raised.

    Returns
    -------
    Sequence[Result] or Sequence[Result | Exception]
        Results from each handler invocation, in the same order as input elements.
        If return_exceptions is True, failed tasks return Exception instances.

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
    ...     if isinstance(result, Exception):
    ...         print(f"Failed to fetch {url}: {result}")
    ...     else:
    ...         print(f"Got data from {url}")

    """
    assert concurrent_tasks > 0  # nosec: B101
    tasks: MutableSet[Task[Any]] = set()
    results: MutableSequence[Task[Any]] = []

    async def process(
        element: Element,
        /,
    ) -> Result | Exception:
        try:
            return await handler(element)

        except Exception as exc:
            if return_exceptions:
                return exc

            else:
                raise

    try:
        if isinstance(elements, AsyncIterable):
            async for element in elements:
                task: Task[Any] = ctx.spawn(process, element)
                results.append(task)
                tasks.add(task)
                if len(tasks) < concurrent_tasks:
                    continue  # keep spawning tasks

                completed, tasks = await wait(
                    tasks,
                    return_when=FIRST_COMPLETED,
                )
                for task in completed:
                    if exc := task.exception():
                        raise exc

        else:
            assert isinstance(elements, Iterable)  # nosec: B101
            for element in elements:
                task: Task[Any] = ctx.spawn(process, element)
                results.append(task)
                tasks.add(task)
                if len(tasks) < concurrent_tasks:
                    continue  # keep spawning tasks

                completed, tasks = await wait(
                    tasks,
                    return_when=FIRST_COMPLETED,
                )
                for task in completed:
                    if exc := task.exception():
                        raise exc

    except BaseException as exc:
        # Cancel all running tasks
        for task in tasks:
            task.cancel()

        raise

    else:
        if tasks:
            completed, _ = await wait(
                tasks,
                return_when=ALL_COMPLETED,
            )
            for task in completed:
                if exc := task.exception():
                    raise exc

    return [result.result() for result in results]


@overload
async def concurrently[Result](
    coroutines: AsyncIterable[Coroutine[None, None, Result]]
    | Iterable[Coroutine[None, None, Result]],
    /,
    *,
    concurrent_tasks: int = 2,
    return_exceptions: Literal[False] = False,
) -> Sequence[Result]: ...


@overload
async def concurrently[Result](
    coroutines: AsyncIterable[Coroutine[None, None, Result]]
    | Iterable[Coroutine[None, None, Result]],
    /,
    *,
    concurrent_tasks: int = 2,
    return_exceptions: Literal[True],
) -> Sequence[Result | Exception]: ...


async def concurrently[Result](  # noqa: C901, PLR0912
    coroutines: AsyncIterable[Coroutine[None, None, Result]]
    | Iterable[Coroutine[None, None, Result]],
    /,
    *,
    concurrent_tasks: int = 2,
    return_exceptions: bool = False,
) -> Sequence[Result | Exception] | Sequence[Result]:
    """Execute multiple coroutines concurrently with controlled parallelism.

    Executes a collection of coroutines concurrently, limiting the number of
    simultaneous tasks to the specified maximum. Results are collected and
    returned in the same order as the input coroutines. This is useful for
    executing pre-created coroutines with controlled concurrency.

    Unlike `execute_concurrently`, this function works directly with coroutine
    objects rather than applying a handler function to elements. This allows
    for more flexibility when coroutines need different parameters or come
    from different sources.

    The function ensures all tasks complete before returning. If cancelled,
    all running tasks are cancelled before propagating the cancellation.

    Parameters
    ----------
    coroutines : AsyncIterable[Coroutine] | Iterable[Coroutine]
        A collection of coroutine objects to execute. Each coroutine should
        return a Result type value.
    concurrent_tasks : int, default=2
        Maximum number of concurrent tasks. Must be greater than 0. Higher
        values allow more parallelism but consume more resources.
    return_exceptions : bool, default=False
        If True, exceptions from coroutines are included in the results
        as Exception instances. If False, the first exception stops
        processing and is raised.

    Returns
    -------
    Sequence[Result] or Sequence[Result | Exception]
        Results from each coroutine execution, in the same order as input.
        If return_exceptions is True, failed tasks return Exception instances.

    Raises
    ------
    CancelledError
        If the function is cancelled, propagated after cancelling all running tasks.
    Exception
        Any exception raised by coroutines when return_exceptions is False.

    Examples
    --------
    >>> async def fetch_with_timeout(url: str, timeout: float) -> dict:
    ...     return await asyncio.wait_for(http_client.get(url), timeout)
    ...
    >>> # Create coroutines with different parameters
    >>> coroutines = [
    ...     fetch_with_timeout("http://api.example.com/1", 5.0),
    ...     fetch_with_timeout("http://api.example.com/2", 10.0),
    ...     fetch_with_timeout("http://api.example.com/3", 3.0),
    ... ]
    >>> results = await concurrently(
    ...     coroutines,
    ...     concurrent_tasks=2
    ... )
    >>> # results[0] from first coroutine, results[1] from second, etc.

    >>> # With exception handling
    >>> results = await concurrently(
    ...     coroutines,
    ...     concurrent_tasks=2,
    ...     return_exceptions=True
    ... )
    >>> for i, result in enumerate(results):
    ...     if isinstance(result, Exception):
    ...         print(f"Coroutine {i} failed: {result}")
    ...     else:
    ...         print(f"Coroutine {i} succeeded")

    """
    assert concurrent_tasks > 0  # nosec: B101
    tasks: MutableSet[Task[Any]] = set()
    results: MutableSequence[Task[Any]] = []

    async def process(
        coroutine: Coroutine[None, None, Result],
        /,
    ) -> Result | Exception:
        try:
            return await coroutine

        except Exception as exc:
            if return_exceptions:
                return exc

            else:
                raise

    try:
        if isinstance(coroutines, AsyncIterable):
            async for element in coroutines:
                task: Task[Any] = ctx.spawn(process, element)
                results.append(task)
                tasks.add(task)
                if len(tasks) < concurrent_tasks:
                    continue  # keep spawning tasks

                completed, tasks = await wait(
                    tasks,
                    return_when=FIRST_COMPLETED,
                )
                for task in completed:
                    if exc := task.exception():
                        raise exc

        else:
            assert isinstance(coroutines, Iterable)  # nosec: B101
            iterator: Iterator[Coroutine[None, None, Result]] = iter(coroutines)
            try:
                for element in iterator:
                    task: Task[Any] = ctx.spawn(process, element)
                    results.append(task)
                    tasks.add(task)
                    if len(tasks) < concurrent_tasks:
                        continue  # keep spawning tasks

                    completed, tasks = await wait(
                        tasks,
                        return_when=FIRST_COMPLETED,
                    )
                    for task in completed:
                        if exc := task.exception():
                            raise exc

            finally:
                # cleanup already created coros
                if isinstance(coroutines, Collection):
                    for coro in iterator:
                        coro.close()

    except BaseException as exc:
        # Cancel all running tasks
        for task in tasks:
            task.cancel()

        raise

    else:
        if tasks:
            completed, _ = await wait(
                tasks,
                return_when=ALL_COMPLETED,
            )
            for task in completed:
                if exc := task.exception():
                    raise exc

    return [result.result() for result in results]


async def stream_concurrently[ElementA, ElementB](  # noqa: C901
    source_a: AsyncIterable[ElementA],
    source_b: AsyncIterable[ElementB],
    /,
    exhaustive: bool = False,
) -> AsyncIterable[ElementA | ElementB]:
    """Merge streams from two async iterators processed concurrently.

    Concurrently consumes elements from two async iterators and yields them
    as they become available. Elements from both sources are interleaved based
    on which iterator produces them first. By default, streaming stops when
    either iterator is exhausted; when `exhaustive=True`, it continues until
    both iterators are exhausted.

    This is useful for combining multiple async data sources into a single
    stream while maintaining concurrency. Each iterator is polled independently,
    and whichever has data available first will have its element yielded.

    Parameters
    ----------
    source_a : AsyncIterable[ElementA]
        First async iterable to consume from.
    source_b : AsyncIterable[ElementB]
        Second async iterable to consume from.
    exhaustive: bool = False
        If False (default, recommended), streaming continues until either source becomes exhausted.
        If True, streaming ends when both sources become completed.

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

    task_a: Task[None]
    task_b: Task[None]
    merged_stream: AsyncStream[ElementA | ElementB] = AsyncStream()

    async def producer_a() -> None:
        try:
            async for item in source_a:
                if merged_stream.finished:
                    break  # finish when output becomes finished

                await merged_stream.send(item)

            if not exhaustive:
                merged_stream.finish()
                task_b.cancel()

            elif task_b.done():
                merged_stream.finish()

        except BaseException as exc:
            merged_stream.finish(exception=exc)

    async def producer_b() -> None:
        try:
            async for item in source_b:
                if merged_stream.finished:
                    break  # finish when output becomes finished

                await merged_stream.send(item)

            if not exhaustive:
                merged_stream.finish()
                task_a.cancel()

            elif task_a.done():
                merged_stream.finish()

        except BaseException as exc:
            merged_stream.finish(exception=exc)

    task_a = ctx.spawn(producer_a)
    task_b = ctx.spawn(producer_b)

    try:
        async for element in merged_stream:
            yield element

    finally:
        if not task_a.done():
            task_a.cancel()

        if not task_b.done():
            task_b.cancel()
