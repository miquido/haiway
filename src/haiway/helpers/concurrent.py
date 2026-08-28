from asyncio import ALL_COMPLETED, CancelledError, Semaphore, Task, current_task, wait
from collections.abc import (
    AsyncGenerator,
    Callable,
    Collection,
    Coroutine,
    Iterable,
    Iterator,
    MutableSequence,
    MutableSet,
    Sequence,
)
from functools import partial
from inspect import iscoroutine
from types import TracebackType
from typing import Any, Literal, Self, cast, final, overload

from haiway.context import ctx
from haiway.context.tasks import ContextTaskGroup
from haiway.utils.exceptions import thrown_exception
from haiway.utils.stream import AsyncStream

__all__ = (
    "concurrently",
    "execute_concurrently",
    "process_concurrently",
    "stream_concurrently",
)


@final
class _ConcurrentTasks[Result]:
    """
    Task spawning bounded by a concurrency limit.

    Keeps at most the requested number of tasks running at once and preserves the
    error of a failed task. The enclosing task group aborts on a task failure by
    cancelling whoever spawned that task, which would otherwise surface as a
    cancellation or an exception group instead of the error breaking processing.
    """

    __slots__ = (
        "_failed",
        "_running",
        "_slots",
    )

    def __init__(
        self,
        limit: int,
        /,
    ) -> None:
        assert limit > 1  # nosec: B101

        # the slot of the task being spawned is not counted, so waiting for a free
        # one happens right after a spawn instead of ahead of it - which keeps the
        # source from being consumed any further than the running tasks allow
        self._slots: Semaphore = Semaphore(limit - 1)
        self._running: MutableSet[Task[Result]] = set()
        # a failed task leaves `_running` as soon as its completion is handled, which
        # happens before its error is examined - keep it available until it is
        self._failed: MutableSequence[Task[Result]] = []

    def _handle_completion(
        self,
        task: Task[Result],
        /,
    ) -> None:
        self._running.discard(task)
        if not task.cancelled() and task.exception() is not None:
            self._failed.append(task)

        self._slots.release()  # free the slot for the next task

    @overload
    async def spawn(
        self,
        coro: Coroutine[None, None, Result],
        /,
    ) -> Task[Result]: ...

    @overload
    async def spawn[**Arguments](
        self,
        coro: Callable[Arguments, Coroutine[None, None, Result]],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]: ...

    async def spawn[**Arguments](
        self,
        coro: Callable[Arguments, Coroutine[None, None, Result]] | Coroutine[None, None, Result],
        /,
        *args: Arguments.args,
        **kwargs: Arguments.kwargs,
    ) -> Task[Result]:
        """
        Spawn a task for the coroutine, then wait for room for the next one.

        Accepts either a coroutine or a function to call with the given arguments,
        which is then called within the spawned task.

        Returns without suspending while the limit is not reached yet.
        """
        task: Task[Result] = ctx.spawn(
            cast(Any, coro),
            *args,
            **kwargs,
        )
        self._running.add(task)
        task.add_done_callback(self._handle_completion)
        await self._slots.acquire()
        return task

    async def join(self) -> None:
        """Wait for all spawned tasks to complete, raising the first task error."""
        if self._running:
            await wait(
                self._running,
                return_when=ALL_COMPLETED,
            )

        self.raise_error()

    def raise_error(self) -> None:
        """Raise the error of the first failed task, when any task has failed."""
        for task in (*self._failed, *self._running):
            if not task.done() or task.cancelled():
                continue  # examine only completed tasks

            error: BaseException | None = task.exception()
            if error is not None:
                raise error from None  # raise task error and break processing


@overload
async def _processing(
    coro: Coroutine[None, None, None],
    /,
    *,
    ignore_exceptions: bool,
) -> None: ...


@overload
async def _processing[Element](
    coro: Callable[[Element], Coroutine[None, None, None]],
    element: Element,
    /,
    *,
    ignore_exceptions: bool,
) -> None: ...


async def _processing[Element](
    coro: Callable[[Element], Coroutine[None, None, None]] | Coroutine[None, None, None],
    /,
    *arguments: Element,
    ignore_exceptions: bool,
) -> None:
    """Process the coroutine, logging and optionally suppressing its errors."""
    try:
        if iscoroutine(coro):
            await coro

        else:
            # the coroutine of a handler is created within the task running it
            await cast(Callable[..., Coroutine[None, None, None]], coro)(*arguments)

    except Exception as exc:
        ctx.log_error(
            f"Concurrent processing error - {type(exc)}: {exc}",
            exception=exc,
        )
        if not ignore_exceptions:
            raise  # reraise exception


@overload
async def _executing[Result](
    coro: Coroutine[None, None, Result],
    /,
    *,
    return_exceptions: bool,
) -> Result | Exception: ...


@overload
async def _executing[Element, Result](
    coro: Callable[[Element], Coroutine[None, None, Result]],
    element: Element,
    /,
    *,
    return_exceptions: bool,
) -> Result | Exception: ...


async def _executing[Element, Result](
    coro: Callable[[Element], Coroutine[None, None, Result]] | Coroutine[None, None, Result],
    /,
    *arguments: Element,
    return_exceptions: bool,
) -> Result | Exception:
    """Execute the coroutine, returning or logging and raising its errors."""
    try:
        if iscoroutine(coro):
            return await coro

        # the coroutine of a handler is created within the task running it
        return await cast(Callable[..., Coroutine[None, None, Result]], coro)(*arguments)

    except Exception as exc:
        if return_exceptions:
            return exc  # return exception as result

        ctx.log_error(
            f"Concurrent execution error - {type(exc)}: {exc}",
            exception=exc,
        )
        raise  # reraise exception


async def process_concurrently[Element](
    source: AsyncGenerator[Element] | Iterable[Element],
    /,
    handler: Callable[[Element], Coroutine[None, None, None]],
    *,
    concurrent_tasks: int = 2,
    ignore_exceptions: bool = False,
) -> None:
    """Process elements from a source concurrently.

    Consumes elements from a source and processes them using the provided
    handler function. Processing happens concurrently with a configurable maximum
    number of concurrent tasks. Elements are processed as they become available,
    maintaining the specified concurrency limit.

    The function continues until the source is exhausted. If the function
    is cancelled, all running tasks are also cancelled. When ignore_exceptions is
    False, the first exception encountered will stop processing and propagate.

    Parameters
    ----------
    source : AsyncGenerator[Element] | Iterable[Element]
        A generator providing elements to process. Elements are consumed
        one at a time as processing slots become available.
    handler : Callable[[Element], Coroutine[None, None, None]]
        A coroutine function that processes each element. The handler should
        not return a value (returns None).
    concurrent_tasks : int, default=2
        Maximum number of concurrent tasks. Must be greater than 1. Higher
        values allow more parallelism but consume more resources.
    ignore_exceptions : bool, default=False
        If True, exceptions from handler tasks will be logged but not propagated,
        allowing processing to continue. If False, the first exception stops
        all processing.

    Raises
    ------
    TypeError
        If the source is neither an AsyncGenerator nor an Iterable.
    CancelledError
        If the function is cancelled, propagated after cancelling all running tasks.
    Exception
        Any exception raised by handler tasks when ignore_exceptions is False.

    Examples
    --------
    >>> async def process_item(item: str) -> None:
    ...     await some_async_operation(item)
    ...
    >>> async def items() -> AsyncGenerator[str]:
    ...     for i in range(10):
    ...         yield f"item_{i}"
    ...
    >>> await process_concurrently(
    ...     items(),
    ...     process_item,
    ...     concurrent_tasks=5
    ... )

    """
    tasks: _ConcurrentTasks[None] = _ConcurrentTasks(concurrent_tasks)
    process = partial(  # keeps both call forms, an annotation would drop one
        _processing,
        ignore_exceptions=ignore_exceptions,
    )

    async with ContextTaskGroup():  # local task group for more granular management
        try:
            if isinstance(source, AsyncGenerator):
                generator: AsyncGenerator[Element] = source
                try:
                    async for element in generator:
                        await tasks.spawn(process(handler, element))

                finally:
                    await generator.aclose()

            else:
                # an async iterable which is not a generator has no `aclose`, so the
                # source could not be released when processing ends - hence not accepted
                # the type checker knows this holds - the guard is for callers
                # reaching the runtime without it
                if not isinstance(source, Iterable):  # pyright: ignore[reportUnnecessaryIsInstance]
                    raise TypeError(
                        "process_concurrently requires an AsyncGenerator or an Iterable source,"
                        f" received {type(source).__name__}"
                    )

                for element in source:
                    await tasks.spawn(process(handler, element))

            await tasks.join()

        except CancelledError:
            # a failed task aborts the enclosing task group, which cancels us -
            # surface the error which broke processing instead of that cancellation
            tasks.raise_error()
            raise  # raise cancellation


@overload
async def execute_concurrently[Element, Result](
    handler: Callable[[Element], Coroutine[None, None, Result]],
    /,
    elements: AsyncGenerator[Element] | Iterable[Element],
    *,
    concurrent_tasks: int = 2,
    return_exceptions: Literal[False] = False,
) -> Sequence[Result]: ...


@overload
async def execute_concurrently[Element, Result](
    handler: Callable[[Element], Coroutine[None, None, Result]],
    /,
    elements: AsyncGenerator[Element] | Iterable[Element],
    *,
    concurrent_tasks: int = 2,
    return_exceptions: Literal[True],
) -> Sequence[Result | Exception]: ...


async def execute_concurrently[Element, Result](
    handler: Callable[[Element], Coroutine[None, None, Result]],
    /,
    elements: AsyncGenerator[Element] | Iterable[Element],
    *,
    concurrent_tasks: int = 2,
    return_exceptions: bool = False,
) -> Sequence[Result | Exception] | Sequence[Result]:
    """Execute handler for each element from a collection concurrently.

    Processes all elements from a collection using the provided handler function,
    executing multiple handlers concurrently up to the specified limit. Results
    are collected and returned in the same order as the input elements.

    Unlike `process_concurrently`, this function:
    - Works with collections (known size) rather than async generators
    - Returns results from each handler invocation
    - Preserves the order of results to match input order

    The function ensures all tasks complete before returning. If cancelled,
    all running tasks are cancelled before propagating the cancellation.

    Parameters
    ----------
    handler : Callable[[Element], Coroutine[None, None, Result]]
        A coroutine function that processes each element and returns a result.
    elements : AsyncGenerator[Element] | Iterable[Element]
        A source of elements to process. The source size determines
        the result sequence length.
    concurrent_tasks : int, default=2
        Maximum number of concurrent tasks. Must be greater than 1. Higher
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
    TypeError
        If the elements source is neither an AsyncGenerator nor an Iterable.
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
    tasks: _ConcurrentTasks[Result | Exception] = _ConcurrentTasks(concurrent_tasks)
    results: MutableSequence[Task[Result | Exception]] = []  # ordered results collection
    process = partial(  # keeps both call forms, an annotation would drop one
        _executing,
        return_exceptions=return_exceptions,
    )

    async with ContextTaskGroup():  # local task group for more granular management
        try:
            if isinstance(elements, AsyncGenerator):
                generator: AsyncGenerator[Element] = elements
                try:
                    async for element in generator:
                        results.append(await tasks.spawn(process(handler, element)))

                finally:
                    await generator.aclose()

            else:
                # the type checker knows this holds - the guard is for callers
                # reaching the runtime without it
                if not isinstance(elements, Iterable):  # pyright: ignore[reportUnnecessaryIsInstance]
                    raise TypeError(
                        "execute_concurrently requires an AsyncGenerator or an Iterable of"
                        f" elements, received {type(elements).__name__}"
                    )

                for element in elements:
                    results.append(await tasks.spawn(process(handler, element)))

            await tasks.join()

        except CancelledError:
            # a failed task aborts the enclosing task group, which cancels us -
            # surface the error which broke processing instead of that cancellation
            tasks.raise_error()
            raise  # raise cancellation

    return [result.result() for result in results]


@overload
async def concurrently[Result](
    coroutines: AsyncGenerator[Coroutine[None, None, Result]]
    | Iterable[Coroutine[None, None, Result]],
    /,
    *,
    concurrent_tasks: int = 2,
    return_exceptions: Literal[False] = False,
) -> Sequence[Result]: ...


@overload
async def concurrently[Result](
    coroutines: AsyncGenerator[Coroutine[None, None, Result]]
    | Iterable[Coroutine[None, None, Result]],
    /,
    *,
    concurrent_tasks: int = 2,
    return_exceptions: Literal[True],
) -> Sequence[Result | Exception]: ...


async def concurrently[Result](
    coroutines: AsyncGenerator[Coroutine[None, None, Result]]
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
    coroutines : AsyncGenerator[Coroutine] | Iterable[Coroutine]
        A collection of coroutine objects to execute. Each coroutine should
        return a Result type value.
    concurrent_tasks : int, default=2
        Maximum number of concurrent tasks. Must be greater than 1. Higher
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
    TypeError
        If the coroutines source is neither an AsyncGenerator nor an Iterable.
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

    Notes
    -----
    When execution ends early, the coroutines which were never executed are closed -
    but only when the source is a collection, the sole shape where they are known to
    exist already. A lazy source creates its coroutines on demand, so it has none
    left over, while draining it to find out could never end. This leaves one shape
    unclaimed: an iterator over already created coroutines, like ``iter([...])``,
    which is neither collection nor lazy - pass the collection itself instead of an
    iterator over it, otherwise its leftovers are only reclaimed by the garbage
    collector, warning about coroutines which were never awaited.
    """
    tasks: _ConcurrentTasks[Result | Exception] = _ConcurrentTasks(concurrent_tasks)
    results: MutableSequence[Task[Result | Exception]] = []  # ordered results collection
    process = partial(  # keeps both call forms, an annotation would drop one
        _executing,
        return_exceptions=return_exceptions,
    )

    async with ContextTaskGroup():  # local task group for more granular management
        try:
            if isinstance(coroutines, AsyncGenerator):
                generator: AsyncGenerator[Coroutine[None, None, Result]] = coroutines
                try:
                    async for coroutine in generator:
                        results.append(await tasks.spawn(process(coroutine)))

                finally:
                    await generator.aclose()

            else:
                # the type checker knows this holds - the guard is for callers
                # reaching the runtime without it
                if not isinstance(coroutines, Iterable):  # pyright: ignore[reportUnnecessaryIsInstance]
                    raise TypeError(
                        "concurrently requires an AsyncGenerator or an Iterable of coroutines,"
                        f" received {type(coroutines).__name__}"
                    )

                iterator: Iterator[Coroutine[None, None, Result]] = iter(coroutines)
                try:
                    for coroutine in iterator:
                        results.append(await tasks.spawn(process(coroutine)))

                finally:
                    # a lazy iterable creates its coroutines on demand - only the ones
                    # a collection has already created require an explicit cleanup
                    if isinstance(coroutines, Collection):
                        for pending in iterator:
                            pending.close()

            await tasks.join()

        except CancelledError:
            # a failed task aborts the enclosing task group, which cancels us -
            # surface the error which broke processing instead of that cancellation
            tasks.raise_error()
            raise  # raise cancellation

    return [result.result() for result in results]


async def _merge_source[Element](
    source: AsyncGenerator[Element],
    /,
    output: AsyncStream[Element],
    producers: Sequence[Task[None]],
    exhaustive: bool,
) -> None:
    """Consume a source into the merged output, ending it when merging is over."""
    try:
        async for item in source:
            if output.finished:
                break  # finish when output becomes finished

            await output.send(item)

        # every producer is spawned before any of them runs, so the other ones
        # are always there to be examined by the time this is reached
        others: Sequence[Task[None]] = [
            producer for producer in producers if producer is not current_task()
        ]
        if not exhaustive:
            output.finish()
            for other in others:
                other.cancel()

        elif all(other.done() for other in others):
            output.finish()

    except CancelledError:
        output.finish()  # release the consumer, it gets nothing more
        raise  # a swallowed cancellation would report this task as completed

    except BaseException as exc:
        output.finish(exception=exc)


@final
class _MergedStream[ElementA, ElementB](AsyncGenerator[ElementA | ElementB]):
    __slots__ = (
        "_generator",
        "_source_a",
        "_source_b",
        "_started",
    )

    def __init__(
        self,
        source_a: AsyncGenerator[ElementA],
        source_b: AsyncGenerator[ElementB],
        exhaustive: bool,
    ) -> None:
        self._source_a: AsyncGenerator[ElementA] = source_a
        self._source_b: AsyncGenerator[ElementB] = source_b
        self._started: bool = False
        self._generator: AsyncGenerator[ElementA | ElementB] = self._merged(exhaustive)

    async def _merged(
        self,
        exhaustive: bool,
    ) -> AsyncGenerator[ElementA | ElementB]:
        self._started = True  # the frame runs only when the stream is actually started
        merged_stream: AsyncStream[ElementA | ElementB] = AsyncStream()
        producers: MutableSequence[Task[None]] = []

        try:
            async with ContextTaskGroup():  # local task group for more granular management
                for source in (self._source_a, self._source_b):
                    producers.append(
                        ctx.spawn(
                            _merge_source,
                            source,
                            output=merged_stream,
                            producers=producers,
                            exhaustive=exhaustive,
                        )
                    )

                try:
                    async for element in merged_stream:
                        yield element

                finally:
                    for producer in producers:
                        if not producer.done():
                            producer.cancel()

        finally:
            # the task group above has joined both producers, so neither source is
            # being iterated anymore and both can be closed
            await self._close_sources()

    async def _close_sources(self) -> None:
        # nested, so a source failing to close still leaves the other one released
        try:
            await self._source_a.aclose()

        finally:
            await self._source_b.aclose()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> ElementA | ElementB:
        return await self._generator.__anext__()

    async def asend(
        self,
        value: None = None,
        /,
    ) -> ElementA | ElementB:
        return await self._generator.asend(value)

    async def athrow(
        self,
        typ: type[BaseException] | BaseException,
        val: object = None,
        tb: TracebackType | None = None,
        /,
    ) -> ElementA | ElementB:
        try:
            return await self._generator.athrow(thrown_exception(typ, val, tb))

        finally:
            if not self._started:
                # the frame of a generator function which never started does not run
                # on throw, leaving both sources open - release them here instead
                await self._close_sources()

    async def aclose(self) -> None:
        try:
            await self._generator.aclose()

        finally:
            if not self._started:
                # the frame of a generator function which never started does not run
                # on close, leaving both sources open - release them here instead
                await self._close_sources()


def stream_concurrently[ElementA, ElementB](
    source_a: AsyncGenerator[ElementA],
    source_b: AsyncGenerator[ElementB],
    /,
    exhaustive: bool = False,
) -> AsyncGenerator[ElementA | ElementB]:
    """Merge streams from two async generators processed concurrently.

    Concurrently consumes elements from two async generators and yields them
    as they become available. Elements from both sources are interleaved based
    on which generator produces them first. By default, streaming stops when
    either generator is exhausted; when `exhaustive=True`, it continues until
    both generators are exhausted.

    This is useful for combining multiple async data sources into a single
    stream while maintaining concurrency. Each generator is polled independently,
    and whichever has data available first will have its element yielded.

    Parameters
    ----------
    source_a : AsyncGenerator[ElementA]
        First generator to consume from.
    source_b : AsyncGenerator[ElementB]
        Second generator to consume from.
    exhaustive: bool = False
        If False (default, recommended), streaming continues until either source becomes exhausted.
        If True, streaming ends when both sources become completed.

    Yields
    ------
    ElementA | ElementB
        Elements from either source as they become available. The order
        depends on which generator produces elements first.

    Raises
    ------
    CancelledError
        If the async generator is cancelled, both source tasks are cancelled
        before propagating the cancellation.
    Exception
        Any exception raised by either source generator.

    Examples
    --------
    >>> async def numbers() -> AsyncGenerator[int]:
    ...     for i in range(5):
    ...         await asyncio.sleep(0.1)
    ...         yield i
    ...
    >>> async def letters() -> AsyncGenerator[str]:
    ...     for c in "abcde":
    ...         await asyncio.sleep(0.15)
    ...         yield c
    ...
    >>> async with ctx.closing(stream_concurrently(numbers(), letters())) as merged:
    ...     async for item in merged:
    ...         print(item)  # Prints interleaved numbers and letters

    Notes
    -----
    The function maintains exactly one pending task per generator at all times,
    ensuring efficient resource usage while maximizing throughput from both
    sources.

    Both sources are closed when the merged stream ends, however it ends -
    exhausted, failed, closed or thrown into, including a stream ended before it
    was ever started. The merged stream itself is the caller's to close: it holds a task
    group inside the generator, and an abandoned generator is finalized by the
    garbage collector in a fresh context, where that group can no longer be
    released. Wrap it in ``ctx.closing`` whenever the iteration may be left
    early.
    """

    return _MergedStream(
        source_a,
        source_b,
        exhaustive,
    )
