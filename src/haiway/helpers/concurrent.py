from asyncio import (
    CancelledError,
    Semaphore,
    Task,
)
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
from types import TracebackType
from typing import Literal, NoReturn, Self, final, overload

from haiway.context import ctx
from haiway.context.tasks import ContextTaskGroup
from haiway.utils.exceptions import thrown_exception
from haiway.utils.stream import AsyncStream

__all__ = (
    "concurrently",
    "execute_concurrently",
    "process_concurrently",
    "stream2_concurrently",
    "stream3_concurrently",
    "stream4_concurrently",
    "stream_concurrently",
)


async def process_concurrently[Element](  # noqa: C901
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
        Multiple handlers failing together raise their errors as an `ExceptionGroup`.

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
    # local task group for more granular management
    async with ContextTaskGroup() as group:
        assert concurrent_tasks > 0  # nosec: B101
        # the slot of the task being spawned is not counted, so waiting for a free
        # one happens right after a spawn instead of ahead of it - which keeps the
        # source from being consumed any further than the running tasks allow
        slots: Semaphore = Semaphore(concurrent_tasks - 1)
        running: MutableSet[Task[None]] = set()

        def complete(
            task: Task[None],
            /,
        ) -> None:
            slots.release()  # free the slot for the next task

        async def spawn[**Arguments](
            coro: Callable[Arguments, Coroutine[None, None, None]],
            /,
            *args: Arguments.args,
            **kwargs: Arguments.kwargs,
        ) -> Task[None]:
            task: Task[None] = group.run(coro, *args, **kwargs)
            running.add(task)
            task.add_done_callback(complete)
            await slots.acquire()
            return task

        async def ignoring_errors[**Arguments](
            coro: Callable[Arguments, Coroutine[None, None, None]],
            /,
            *args: Arguments.args,
            **kwargs: Arguments.kwargs,
        ) -> None:
            try:
                return await coro(*args, **kwargs)

            except Exception as exc:
                ctx.log_error(
                    f"Concurrent processing error - {type(exc)}: {exc}",
                    exception=exc,
                )

        if isinstance(source, AsyncGenerator):
            try:
                if ignore_exceptions:
                    async for element in source:
                        await spawn(ignoring_errors, handler, element)

                else:
                    async for element in source:
                        await spawn(handler, element)

            finally:
                await source.aclose()

        else:
            # an async iterable which is not a generator has no `aclose`, so the
            # source could not be released when processing ends - hence not accepted,
            # which the type of the argument already ensures
            assert isinstance(source, Iterable)  # nosec: B101

            if ignore_exceptions:
                for element in source:
                    await spawn(ignoring_errors, handler, element)

            else:
                for element in source:
                    await spawn(handler, element)


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


async def execute_concurrently[Element, Result](  # noqa: C901
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
        Multiple handlers failing together raise their errors as an `ExceptionGroup`.

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
    # local task group for more granular management
    async with ContextTaskGroup() as group:
        assert concurrent_tasks > 0  # nosec: B101
        # the slot of the task being spawned is not counted, so waiting for a free
        # one happens right after a spawn instead of ahead of it - which keeps the
        # source from being consumed any further than the running tasks allow
        slots: Semaphore = Semaphore(concurrent_tasks - 1)
        results: MutableSequence[Task[Result | Exception]] = []  # ordered results collection

        def complete(
            task: Task[Result | Exception],
            /,
        ) -> None:
            slots.release()  # free the slot for the next task

        async def spawn[**Arguments](
            coro: Callable[Arguments, Coroutine[None, None, Result | Exception]],
            /,
            *args: Arguments.args,
            **kwargs: Arguments.kwargs,
        ) -> Task[Result | Exception]:
            task: Task[Result | Exception] = group.run(coro, *args, **kwargs)
            task.add_done_callback(complete)
            await slots.acquire()
            return task

        async def returning_errors[**Arguments](
            coro: Callable[Arguments, Coroutine[None, None, Result]],
            /,
            *args: Arguments.args,
            **kwargs: Arguments.kwargs,
        ) -> Result | Exception:
            try:
                return await coro(*args, **kwargs)

            except Exception as exc:
                return exc

        if isinstance(elements, AsyncGenerator):
            try:
                if return_exceptions:
                    async for element in elements:
                        results.append(await spawn(returning_errors, handler, element))

                else:
                    async for element in elements:
                        results.append(await spawn(handler, element))

            finally:
                await elements.aclose()

        else:
            # an async iterable which is not a generator has no `aclose`, so the
            # source could not be released when execution ends - hence not accepted,
            # which the type of the argument already ensures
            assert isinstance(elements, Iterable)  # nosec: B101

            if return_exceptions:
                for element in elements:
                    results.append(await spawn(returning_errors, handler, element))

            else:
                for element in elements:
                    results.append(await spawn(handler, element))

    # task group joins all tasks so at this point it will be all completed
    return tuple(result.result() for result in results)


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


async def concurrently[Result](  # noqa: C901, PLR0912
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
        Multiple coroutines failing together raise their errors as an `ExceptionGroup`.

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
    # local task group for more granular management
    async with ContextTaskGroup() as group:
        assert concurrent_tasks > 0  # nosec: B101
        # the slot of the task being spawned is not counted, so waiting for a free
        # one happens right after a spawn instead of ahead of it - which keeps the
        # source from being consumed any further than the running tasks allow
        slots: Semaphore = Semaphore(concurrent_tasks - 1)
        results: MutableSequence[Task[Result | Exception]] = []  # ordered results collection

        def complete(
            task: Task[Result | Exception],
            /,
        ) -> None:
            slots.release()  # free the slot for the next task

        async def spawn(
            coro: Callable[[], Coroutine[None, None, Result | Exception]]
            | Coroutine[None, None, Result | Exception],
            /,
        ) -> Task[Result | Exception]:
            task: Task[Result | Exception] = group.run(coro)
            task.add_done_callback(complete)
            await slots.acquire()
            return task

        async def returning_errors(
            coro: Coroutine[None, None, Result],
            /,
        ) -> Result | Exception:
            try:
                return await coro

            except Exception as exc:
                return exc

        if isinstance(coroutines, AsyncGenerator):
            try:
                if return_exceptions:
                    async for coro in coroutines:
                        results.append(await spawn(returning_errors(coro)))

                else:
                    async for coro in coroutines:
                        results.append(await spawn(coro))

            finally:
                await coroutines.aclose()

        else:
            assert isinstance(coroutines, Iterable)  # nosec: B101

            remaining: Iterator[Coroutine[None, None, Result]] = iter(coroutines)
            try:
                if return_exceptions:
                    for coro in remaining:
                        results.append(await spawn(returning_errors(coro)))

                else:
                    for coro in remaining:
                        results.append(await spawn(coro))

            finally:
                if isinstance(coroutines, Collection):
                    # a collection holds all of its coroutines already, so the ones
                    # left when execution ends early are released here - a lazy
                    # source has none left over, draining it could never end
                    for coro in remaining:
                        coro.close()

    # task group joins all tasks so at this point it will be all completed
    return tuple(result.result() for result in results)


@final
class _DummyGenerator[Element](AsyncGenerator[Element]):
    __slots__ = ()

    def __aiter__(self) -> Self:
        return self

    async def __anext__(self) -> NoReturn:
        raise StopAsyncIteration

    async def asend(
        self,
        value: None = None,
        /,
    ) -> NoReturn:
        raise StopAsyncIteration

    async def athrow(
        self,
        typ: type[BaseException] | BaseException,
        val: object = None,
        tb: TracebackType | None = None,
        /,
    ) -> NoReturn:
        raise thrown_exception(typ, val, tb)

    async def aclose(self) -> None:
        pass  # there is nothing left to release


def stream_concurrently[Element](  # noqa: C901
    *sources: AsyncGenerator[Element],
    exhaustive: bool = False,
) -> AsyncGenerator[Element]:
    """Merge streams from multiple async generators consumed concurrently.

    Concurrently consumes elements from all of the provided async generators and
    yields them as they become available. Elements from the sources are interleaved
    based on which generator produces them first. By default, streaming stops when
    any of the generators is exhausted; when `exhaustive=True`, it continues until
    all of the generators are exhausted.

    This is useful for combining multiple async data sources into a single stream
    while maintaining concurrency. Each generator is drained independently, and
    whichever has data available first will have its element yielded. Delivery is
    flow-controlled - a source producing faster than the merged stream is consumed
    is suspended instead of buffering, so a fast source can't outrun the consumer.

    Parameters
    ----------
    *sources : AsyncGenerator[Element]
        Generators to consume from. Merging no sources produces an empty stream,
        while a single source is returned as is - there is nothing to merge it with.
    exhaustive : bool = False
        If False (default, recommended), streaming continues until any source becomes
        exhausted. If True, streaming ends when all sources become exhausted.

    Yields
    ------
    Element
        Elements from the sources as they become available. The order depends on
        which generator produces elements first.

    Raises
    ------
    CancelledError
        If the merged stream is cancelled, all source producers are cancelled
        before propagating the cancellation.
    Exception
        Any exception raised by any of the source generators. The first error ends
        the merged stream, cancelling the remaining producers regardless of
        `exhaustive`.
        A producer failing after the merged stream already ended without an error
        reaching the consumer - exhausted, or stopped by the first source ending -
        typically while being released, has no delivered error to raise in its place,
        so the task group holding the producers raises what it collected - a single
        error on its own, or a `BaseExceptionGroup` of many failing together.
        An error which did reach the consumer is raised on its own instead, even
        when other producers failed alongside it.

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
    Element types are unified under a single `Element` type variable - use
    ``stream2_concurrently``, ``stream3_concurrently`` or ``stream4_concurrently``
    to merge a fixed number of differently typed sources into their union.

    Each source is drained by exactly one producer task, so the merge keeps a single
    pending element per source at all times, ensuring efficient resource usage while
    maximizing throughput from all sources.

    Each source is closed by the producer draining it, however the merged stream
    ends - exhausted, failed, closed or thrown into - so a source failing to close
    does not strand the others. Its error is raised by the task group holding the
    producers only while nothing else is in flight - a source failing to release a
    merged stream which is being closed is logged and chained as the cause of the
    closing rather than raised, the closing taking precedence.
    A merged stream which was never started is the exception: closing it does not
    run its frame, so it never reaches its sources - start it, or close the sources
    directly. This holds for an actual merge only; the single source returned as is
    has no frame of its own to start, so closing it always releases it. The merged
    stream itself is the caller's to close: the task group
    holding its producers lives inside the generator frame, so only closing the
    stream releases them, while an abandoned generator leaves that to the garbage
    collector, finalizing it outside the task which started it. Wrap it in
    ``ctx.closing`` whenever the iteration may be left early.
    """
    match len(sources):
        case 0:
            return _DummyGenerator()

        case 1:
            return sources[0]

        case _:
            pass  # continue with actual merge

    async def merged() -> AsyncGenerator[Element]:  # noqa: C901
        merged_stream: AsyncStream[Element] = AsyncStream()

        async with ContextTaskGroup() as group:
            running: MutableSet[Task[None]] = set()

            def complete_source(
                task: Task[None],
                /,
            ) -> None:
                running.remove(task)
                # a cancelled producer has no error of its own to report - it was
                # cancelled by the merge ending, and asking it for one raises
                exception: BaseException | None = task.exception()
                if not running:  # finish when no more running
                    return merged_stream.finish(exception)

                # cancel remaining if not exhaustive or failed
                if not exhaustive or exception is not None:
                    for run in running:
                        run.cancel()

            def run_source(
                source: AsyncGenerator[Element],
                /,
            ) -> Task[None]:
                async def drain() -> None:
                    try:
                        try:
                            async for element in source:
                                if merged_stream.finished:
                                    break  # the output ended - sending to it is rejected

                                await merged_stream.send(element)

                        except CancelledError:
                            pass  # cancellation of producer is not an error

                        except BaseException as exc:
                            merged_stream.finish(exc)

                    finally:
                        await source.aclose()

                task: Task[None] = group.run(drain)
                task.add_done_callback(complete_source)
                return task

            for source in sources:
                running.add(run_source(source))

            try:
                async for element in merged_stream:
                    yield element

            finally:
                await merged_stream.aclose()

    return merged()


def stream2_concurrently[ElementA, ElementB](
    source_a: AsyncGenerator[ElementA],
    source_b: AsyncGenerator[ElementB],
    /,
    *,
    exhaustive: bool = False,
) -> AsyncGenerator[ElementA | ElementB]:
    """Merge streams from two differently typed async generators consumed concurrently.

    Typed variant of ``stream_concurrently`` preserving the type of each source in
    the union of the merged stream. See ``stream_concurrently`` for the merging,
    closing and failure semantics.

    Parameters
    ----------
    source_a : AsyncGenerator[ElementA]
        First generator to consume from.
    source_b : AsyncGenerator[ElementB]
        Second generator to consume from.
    exhaustive : bool = False
        If False (default, recommended), streaming continues until any source becomes
        exhausted. If True, streaming ends when all sources become exhausted.

    Yields
    ------
    ElementA | ElementB
        Elements from either source as they become available.
    """
    return stream_concurrently(
        source_a,
        source_b,
        exhaustive=exhaustive,
    )


def stream3_concurrently[ElementA, ElementB, ElementC](
    source_a: AsyncGenerator[ElementA],
    source_b: AsyncGenerator[ElementB],
    source_c: AsyncGenerator[ElementC],
    /,
    *,
    exhaustive: bool = False,
) -> AsyncGenerator[ElementA | ElementB | ElementC]:
    """Merge streams from three differently typed async generators consumed concurrently.

    Typed variant of ``stream_concurrently`` preserving the type of each source in
    the union of the merged stream. See ``stream_concurrently`` for the merging,
    closing and failure semantics.

    Parameters
    ----------
    source_a : AsyncGenerator[ElementA]
        First generator to consume from.
    source_b : AsyncGenerator[ElementB]
        Second generator to consume from.
    source_c : AsyncGenerator[ElementC]
        Third generator to consume from.
    exhaustive : bool = False
        If False (default, recommended), streaming continues until any source becomes
        exhausted. If True, streaming ends when all sources become exhausted.

    Yields
    ------
    ElementA | ElementB | ElementC
        Elements from any of the sources as they become available.
    """
    return stream_concurrently(
        source_a,
        source_b,
        source_c,
        exhaustive=exhaustive,
    )


def stream4_concurrently[ElementA, ElementB, ElementC, ElementD](
    source_a: AsyncGenerator[ElementA],
    source_b: AsyncGenerator[ElementB],
    source_c: AsyncGenerator[ElementC],
    source_d: AsyncGenerator[ElementD],
    /,
    *,
    exhaustive: bool = False,
) -> AsyncGenerator[ElementA | ElementB | ElementC | ElementD]:
    """Merge streams from four differently typed async generators consumed concurrently.

    Typed variant of ``stream_concurrently`` preserving the type of each source in
    the union of the merged stream. See ``stream_concurrently`` for the merging,
    closing and failure semantics.

    Parameters
    ----------
    source_a : AsyncGenerator[ElementA]
        First generator to consume from.
    source_b : AsyncGenerator[ElementB]
        Second generator to consume from.
    source_c : AsyncGenerator[ElementC]
        Third generator to consume from.
    source_d : AsyncGenerator[ElementD]
        Fourth generator to consume from.
    exhaustive : bool = False
        If False (default, recommended), streaming continues until any source becomes
        exhausted. If True, streaming ends when all sources become exhausted.

    Yields
    ------
    ElementA | ElementB | ElementC | ElementD
        Elements from any of the sources as they become available.
    """
    return stream_concurrently(
        source_a,
        source_b,
        source_c,
        source_d,
        exhaustive=exhaustive,
    )
