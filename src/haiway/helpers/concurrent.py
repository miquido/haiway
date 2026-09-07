from asyncio import (
    ALL_COMPLETED,
    CancelledError,
    Semaphore,
    Task,
    get_running_loop,
    wait,
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
from contextvars import copy_context
from typing import Literal, overload

from haiway.context import ctx
from haiway.context.tasks import ContextTaskGroup
from haiway.utils.stream import AsyncStream

__all__ = (
    "concurrently",
    "execute_concurrently",
    "process_concurrently",
    "stream_concurrently",
)


async def process_concurrently[Element](  # noqa: C901, PLR0912
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
        error: BaseException | None = None  # error of the first failed task

        def complete(
            task: Task[None],
            /,
        ) -> None:
            nonlocal error
            running.discard(task)
            if error is None and not task.cancelled():
                error = task.exception()  # keep the error which breaks processing

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

        try:
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

            # join within the group, so a task failure cancels us here and its error
            # surfaces instead of the exception group of the task group exit
            if running:
                await wait(running, return_when=ALL_COMPLETED)

            if error is not None:
                raise error from None  # raise task error and break processing

        except CancelledError:
            # a failed task aborts the enclosing task group, which cancels us -
            # surface the error which broke processing instead of that cancellation
            if error is not None:
                raise error from None

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


async def execute_concurrently[Element, Result](  # noqa: C901, PLR0912
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
        error: BaseException | None = None  # error of the first failed task

        def complete(
            task: Task[Result | Exception],
            /,
        ) -> None:
            nonlocal error
            if error is None and not task.cancelled():
                error = task.exception()  # keep the error which breaks execution

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

        try:
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

            # join within the group, so a task failure cancels us here and its error
            # surfaces instead of the exception group of the task group exit
            if results:
                await wait(results, return_when=ALL_COMPLETED)

            if error is not None:
                raise error from None  # raise task error and break execution

        except CancelledError:
            # a failed task aborts the enclosing task group, which cancels us -
            # surface the error which broke execution instead of that cancellation
            if error is not None:
                raise error from None

            raise  # raise cancellation

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
        error: BaseException | None = None  # error of the first failed task

        def complete(
            task: Task[Result | Exception],
            /,
        ) -> None:
            nonlocal error
            if error is None and not task.cancelled():
                error = task.exception()  # keep the error which breaks execution

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

        try:
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
                # an async iterable which is not a generator has no `aclose`, so the
                # source could not be released when execution ends - hence not accepted,
                # which the type of the argument already ensures
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

            # join within the group, so a task failure cancels us here and its error
            # surfaces instead of the exception group of the task group exit
            if results:
                await wait(results, return_when=ALL_COMPLETED)

            if error is not None:
                raise error from None  # raise task error and break execution

        except CancelledError:
            # a failed task aborts the enclosing task group, which cancels us -
            # surface the error which broke execution instead of that cancellation
            if error is not None:
                raise error from None

            raise  # raise cancellation

    # task group joins all tasks so at this point it will be all completed
    return tuple(result.result() for result in results)


def stream_concurrently[ElementA, ElementB](  # noqa: C901, PLR0915
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

    Both sources are closed by the producer draining them, however the merged
    stream ends - exhausted, failed, closed or thrown into - and a source failing
    to close does not strand the other one, its error surfacing once both are
    released. A merged stream which was never started is the exception: closing it
    does not run its frame, so it never reaches its sources - start it, or close
    the sources directly. The merged stream itself is the caller's to close: the
    task group holding its producers lives inside the generator frame, so only
    closing the stream releases them, while an abandoned generator leaves that to
    the garbage collector, finalizing it outside the task which started it. Wrap it
    in ``ctx.closing`` whenever the iteration may be left early.
    """

    async def merged() -> AsyncGenerator[ElementA | ElementB]:  # noqa: C901, PLR0915
        merged_stream: AsyncStream[ElementA | ElementB] = AsyncStream()
        # error of a source failing to close, raised when the merged stream ends
        close_error: BaseException | None = None

        async def produce() -> None:  # noqa: C901
            nonlocal close_error
            # local task group for more granular management, entered within this
            # task so it stays in its context - an async generator frame shares the
            # context of its consumer, where a group would adopt the tasks spawned
            # while iterating and wrap the errors passing through the frame
            async with ContextTaskGroup() as group:
                sources: MutableSet[Task[None]] = set()

                def complete_source(
                    task: Task[None],
                    /,
                ) -> None:
                    sources.remove(task)
                    if not exhaustive or not sources:
                        merged_stream.finish()

                def drain_source(
                    source: AsyncGenerator[ElementA] | AsyncGenerator[ElementB],
                    /,
                ) -> Task[None]:
                    def report(
                        exception: Exception,
                        /,
                    ) -> None:
                        nonlocal close_error
                        # a stream which is still running delivers the error to its
                        # consumer, ending it - once it finished there is no one left
                        # to deliver to, so the error is kept to be raised when the
                        # merged stream ends, instead of failing this task and
                        # stranding the other source
                        if not merged_stream.finished:
                            merged_stream.finish(exception)

                        elif close_error is None:
                            close_error = exception

                    async def drain() -> None:
                        try:
                            async for element in source:
                                if merged_stream.finished:
                                    # the output ended - sending to it is rejected
                                    break

                                await merged_stream.send(element)

                        except Exception as exc:
                            report(exc)

                        finally:
                            try:
                                await source.aclose()

                            except Exception as exc:
                                report(exc)

                    task: Task[None] = group.run(drain)
                    task.add_done_callback(complete_source)
                    return task

                for source in (source_a, source_b):
                    sources.add(drain_source(source))

        # the context is copied like a context task group would, so the producers
        # keep the state and observability of the consumer starting them
        producer: Task[None] = get_running_loop().create_task(
            produce(),
            context=copy_context(),
        )

        # set when the merged stream ends by delivering a failure or by the consumer
        # being cancelled - that error is the one to report, so a source failing to
        # release afterwards can't take its place
        delivered_error: bool = False
        try:
            async for element in merged_stream:
                yield element

        except GeneratorExit:
            raise  # closing is not a failure which was delivered

        except BaseException:
            delivered_error = True
            raise

        finally:
            # nothing reads the merged stream from here on, so it is finished before
            # the producers are cancelled - whatever they hit while unwinding is then
            # kept to be raised below, instead of being handed to a stream which no
            # one will ever read again
            merged_stream.finish()
            producer.cancel()  # cancelling it cancels the producers of its group
            await wait((producer,), return_when=ALL_COMPLETED)
            if close_error is None and not producer.cancelled():
                # nothing else reports it, and leaving it unretrieved warns
                close_error = producer.exception()

            await merged_stream.aclose()
            # the group of the producer has joined both of them, so a source which
            # failed to release is known by now
            if close_error is not None and not delivered_error:
                raise close_error from None

    return merged()
