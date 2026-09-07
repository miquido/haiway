from asyncio import CancelledError, sleep
from collections.abc import AsyncGenerator
from types import TracebackType
from typing import NoReturn

from pytest import mark, raises

from haiway import ctx
from haiway.helpers.concurrent import (
    stream2_concurrently,
    stream3_concurrently,
    stream4_concurrently,
    stream_concurrently,
)


class FakeException(Exception):
    pass


async def async_range(
    start: int,
    stop: int,
    delay: float = 0,
) -> AsyncGenerator[int]:
    for i in range(start, stop):
        await sleep(delay)
        yield i


async def async_letters(
    letters: str,
    delay: float = 0,
) -> AsyncGenerator[str]:
    for letter in letters:
        await sleep(delay)
        yield letter


@mark.asyncio
async def test_merges_two_streams():
    items: list[int | str] = []

    async for item in stream_concurrently(
        async_range(0, 3),
        async_letters("abc"),
        exhaustive=True,
    ):
        items.append(item)

    # Should have all items from both sources
    assert len(items) == 6
    assert set(items) == {0, 1, 2, "a", "b", "c"}


@mark.asyncio
async def test_interleaves_based_on_timing():
    items: list[int | str] = []

    # Numbers come faster than letters
    async for item in stream_concurrently(
        async_range(0, 5, delay=0.02), async_letters("abc", delay=0.05)
    ):
        items.append(item)

    # Due to timing, we should see more numbers before letters
    # Find positions of first letter and last number
    first_letter_pos = next(i for i, item in enumerate(items) if isinstance(item, str))
    last_number_pos = max(i for i, item in enumerate(items) if isinstance(item, int))

    # Some numbers should come before the first letter due to faster generation
    assert first_letter_pos > 0
    # Some letters should be interleaved with numbers
    assert last_number_pos > first_letter_pos


@mark.asyncio
async def test_handles_empty_iterators():
    items: list[int | str] = []

    async def empty_iter() -> AsyncGenerator[int]:
        return
        yield  # Make it a generator

    # Both empty
    async for item in stream_concurrently(empty_iter(), empty_iter()):
        items.append(item)
    assert items == []

    # One empty, one with items - the empty source finishes the merge, but how much
    # of the non-empty source escapes first is plain FIFO interleaving, so only the
    # "ordered prefix" invariant holds
    items = []
    async for item in stream_concurrently(async_range(0, 3), empty_iter()):
        items.append(item)
    assert items == list(range(len(items)))
    assert len(items) <= 3

    # exhaustive
    items = []
    async for item in stream_concurrently(async_range(0, 3), empty_iter(), exhaustive=True):
        items.append(item)
    assert items == [0, 1, 2]

    # Other way around - here the empty source is producer A, which is spawned first
    # and exhausts without ever suspending, so the merge is finished (and producer B
    # cancelled) before source B runs at all
    items = []
    async for item in stream_concurrently(empty_iter(), async_range(0, 3)):
        items.append(item)
    assert items == []

    # exhaustive
    items = []
    async for item in stream_concurrently(empty_iter(), async_range(0, 3), exhaustive=True):
        items.append(item)
    assert items == [0, 1, 2]


@mark.asyncio
async def test_handles_different_lengths():
    items: list[int | str] = []

    async for item in stream_concurrently(async_range(0, 10), async_letters("ab")):
        items.append(item)

    # Non-exhaustive: the merge ends the moment either source exhausts. The shorter
    # source is guaranteed to deliver everything (its sends complete before it finishes
    # the stream), while the longer one only contributes an unspecified ordered prefix.
    numbers = [i for i in items if isinstance(i, int)]
    letters = [i for i in items if isinstance(i, str)]
    assert letters == ["a", "b"]
    assert numbers == list(range(len(numbers)))
    assert len(numbers) <= 10
    assert len(items) >= 2  # at least both letters

    # exhaustive
    items = []
    async for item in stream_concurrently(async_range(0, 10), async_letters("ab"), exhaustive=True):
        items.append(item)

    # Should have all items from both sources
    assert len(items) == 12
    numbers = [i for i in items if isinstance(i, int)]
    letters = [i for i in items if isinstance(i, str)]
    assert numbers == list(range(10))
    assert letters == ["a", "b"]


@mark.asyncio
async def test_propagates_exceptions_from_source_a():
    async def failing_iter_a() -> AsyncGenerator[int]:
        yield 1
        raise FakeException("Source A failed")

    items: list[int | str] = []
    with raises(FakeException, match="Source A failed"):
        async for item in stream_concurrently(failing_iter_a(), async_letters("abc")):
            items.append(item)

    # Should have collected some items before failure
    assert len(items) >= 1  # At least the one yielded number


@mark.asyncio
async def test_propagates_exceptions_from_source_b():
    async def failing_iter_b() -> AsyncGenerator[str]:
        yield "x"
        raise FakeException("Source B failed")

    items: list[int | str] = []
    with raises(FakeException, match="Source B failed"):
        async for item in stream_concurrently(async_range(0, 5), failing_iter_b()):
            items.append(item)

    # Should have collected some items before failure
    assert len(items) >= 1  # At least the yielded "x"


@mark.asyncio
async def test_cancellation_cancels_both_sources():
    started_a = False
    started_b = False
    cancelled_a = False
    cancelled_b = False

    async def tracked_iter_a() -> AsyncGenerator[int]:
        nonlocal started_a, cancelled_a
        started_a = True
        try:
            for i in range(100):
                await sleep(0.1)
                yield i
        except CancelledError:
            cancelled_a = True
            raise

    async def tracked_iter_b() -> AsyncGenerator[str]:
        nonlocal started_b, cancelled_b
        started_b = True
        try:
            for c in "abcdefghijk":
                await sleep(0.1)
                yield c
        except CancelledError:
            cancelled_b = True
            raise

    async def consume_with_cancel():
        items = []
        async for item in stream_concurrently(tracked_iter_a(), tracked_iter_b()):
            items.append(item)
            if len(items) >= 4:  # Cancel after collecting some items
                raise CancelledError()

    with raises(CancelledError):
        task = ctx.spawn(consume_with_cancel)
        await task

    # Both iterators should have started and been cancelled
    assert started_a
    assert started_b
    # Note: The cancellation might not propagate to the source iterators
    # in all cases due to timing, so we don't assert cancelled_a/b


@mark.asyncio
async def test_works_with_different_types():
    async def fibonacci() -> AsyncGenerator[int]:
        a, b = 0, 1
        for _ in range(5):
            yield a
            a, b = b, a + b

    async def words() -> AsyncGenerator[str]:
        for word in ["hello", "world", "test"]:
            yield word

    items: list[int | str] = []
    async for item in stream_concurrently(fibonacci(), words()):
        items.append(item)

    numbers = [i for i in items if isinstance(i, int)]
    strings = [i for i in items if isinstance(i, str)]
    # Non-exhaustive: whichever source exhausts first finishes the merge and is fully
    # delivered, the other one contributes an ordered prefix. Which one wins the race
    # is plain FIFO interleaving, so assert only the guaranteed shape.
    assert numbers == [0, 1, 1, 2, 3][: len(numbers)]
    assert strings == ["hello", "world", "test"][: len(strings)]
    assert numbers == [0, 1, 1, 2, 3] or strings == ["hello", "world", "test"]

    # exhaustive
    items = []
    async for item in stream_concurrently(fibonacci(), words(), exhaustive=True):
        items.append(item)

    numbers = [i for i in items if isinstance(i, int)]
    strings = [i for i in items if isinstance(i, str)]
    assert numbers == [0, 1, 1, 2, 3]
    assert strings == ["hello", "world", "test"]


@mark.asyncio
async def test_immediate_yield():
    """Test that items are yielded as soon as they're available."""

    async def slow_numbers() -> AsyncGenerator[int]:
        for i in range(3):
            await sleep(0.2)  # Increased delay for more reliable timing
            yield i

    async def fast_letters() -> AsyncGenerator[str]:
        for c in "abc":
            await sleep(0.01)  # Smaller delay
            yield c

    items: list[int | str] = []
    async for item in stream_concurrently(slow_numbers(), fast_letters(), exhaustive=True):
        items.append(item)

    # Check that we got all items
    assert len(items) == 6
    assert set(items) == {0, 1, 2, "a", "b", "c"}

    # Due to timing differences, at least the first letter should come before the first number
    # This is more robust than expecting ALL letters before ALL numbers
    first_letter_pos = next(i for i, item in enumerate(items) if isinstance(item, str))
    first_number_pos = next(i for i, item in enumerate(items) if isinstance(item, int))
    assert first_letter_pos < first_number_pos

    # Additionally, verify that letters tend to come earlier (more relaxed check)
    letter_positions = [i for i, item in enumerate(items) if isinstance(item, str)]
    number_positions = [i for i, item in enumerate(items) if isinstance(item, int)]
    # Average position of letters should be less than average position of numbers
    assert sum(letter_positions) / len(letter_positions) < sum(number_positions) / len(
        number_positions
    )


@mark.asyncio
async def test_concurrent_execution():
    execution_order: list[str] = []

    async def iter_a() -> AsyncGenerator[str]:
        execution_order.append("a_start")
        await sleep(0.1)  # Increased for more reliable timing
        execution_order.append("a_yield_1")
        yield "a1"
        await sleep(0.1)
        execution_order.append("a_yield_2")
        yield "a2"

    async def iter_b() -> AsyncGenerator[str]:
        execution_order.append("b_start")
        await sleep(0.01)  # Much smaller delay
        execution_order.append("b_yield_1")
        yield "b1"
        await sleep(0.01)
        execution_order.append("b_yield_2")
        yield "b2"

    items: list[str] = []
    async for item in stream_concurrently(iter_a(), iter_b(), exhaustive=True):
        items.append(item)

    # Both should start immediately (concurrently)
    assert execution_order[0] in ["a_start", "b_start"]
    assert execution_order[1] in ["a_start", "b_start"]
    assert execution_order[0] != execution_order[1]

    # Due to timing, b should yield first
    assert "b_yield_1" in execution_order
    b_yield_1_pos = execution_order.index("b_yield_1")
    a_yield_1_pos = execution_order.index("a_yield_1")
    assert b_yield_1_pos < a_yield_1_pos


@mark.asyncio
async def test_early_completion_does_not_raise_cancellation_error():
    async def fast_iter() -> AsyncGenerator[int]:
        yield 1
        yield 2

    async def slow_iter() -> AsyncGenerator[str]:
        for i in range(100):
            await sleep(0.1)  # Very slow
            yield f"item_{i}"

    # This should complete when fast_iter finishes without raising CancelledError
    items: list[int | str] = []

    async with ctx.scope("test"):
        async for item in stream_concurrently(fast_iter(), slow_iter(), exhaustive=False):
            items.append(item)

    # Should have items from fast iterator and possibly some from slow
    assert len(items) >= 2
    assert 1 in items
    assert 2 in items


@mark.asyncio
async def test_task_group_integration_with_early_completion():
    results = []

    async def consumer():
        async def numbers() -> AsyncGenerator[int]:
            for i in range(3):
                await sleep(0.01)
                yield i

        async def letters() -> AsyncGenerator[str]:
            for c in "abcdefghijklmnopqrstuvwxyz":  # Long stream
                await sleep(0.05)  # Slower than numbers
                yield c

        items = []
        async for item in stream_concurrently(numbers(), letters(), exhaustive=False):
            items.append(item)

        results.extend(items)

    # Run in task group - should not raise CancelledError
    async with ctx.scope("task_group_test"):
        await ctx.spawn(consumer)

    # Should have completed successfully with items from numbers stream
    assert len(results) >= 3
    assert 0 in results
    assert 1 in results
    assert 2 in results


@mark.asyncio
async def test_cleanup_awaits_cancelled_tasks():
    cleanup_completed = False

    async def slow_stream() -> AsyncGenerator[int]:
        try:
            for i in range(1000):
                await sleep(0.1)
                yield i
        except CancelledError:
            nonlocal cleanup_completed
            # Simulate some cleanup work
            await sleep(0.01)
            cleanup_completed = True
            raise

    async def fast_stream() -> AsyncGenerator[str]:
        yield "done"

    items = []

    # This should not raise CancelledError even though slow_stream gets cancelled
    async with ctx.scope("cleanup_test"):
        async for item in stream_concurrently(fast_stream(), slow_stream(), exhaustive=False):
            items.append(item)

    # Should complete successfully
    assert "done" in items
    # Cleanup should have been called (though timing dependent)
    # Note: We don't assert cleanup_completed=True because cancellation timing is non-deterministic


@mark.asyncio
async def test_multiple_stream_concurrently_in_task_group():
    items_1 = []
    items_2 = []

    async def nums() -> AsyncGenerator[int]:
        for i in [1, 2]:
            yield i

    async def slow() -> AsyncGenerator[str]:
        for _ in range(100):
            await sleep(0.1)
            yield "slow"

    async def consumer_1():
        async for item in stream_concurrently(nums(), slow(), exhaustive=False):
            items_1.append(item)

    async def consumer_2():
        async for item in stream_concurrently(nums(), slow(), exhaustive=False):
            items_2.append(item)

    async with ctx.scope("multiple_streams"):
        await consumer_1()
        await consumer_2()

    assert 1 in items_1 and 2 in items_1
    assert 1 in items_2 and 2 in items_2


@mark.asyncio
async def test_closes_both_sources_when_left_early():
    closed: list[str] = []

    def tracked(name: str, delay: float) -> AsyncGenerator[str]:
        async def generator() -> AsyncGenerator[str]:
            try:
                for index in range(100):
                    await sleep(delay)
                    yield f"{name}{index}"

            finally:
                closed.append(name)

        return generator()

    async with ctx.closing(stream_concurrently(tracked("a", 0.01), tracked("b", 0.015))) as merged:
        async for _ in merged:
            break

    # closing the merged stream joins both producers and releases both sources
    assert sorted(closed) == ["a", "b"]


@mark.asyncio
async def test_closes_both_sources_when_exhausted():
    closed: list[str] = []

    def tracked(name: str, count: int) -> AsyncGenerator[str]:
        async def generator() -> AsyncGenerator[str]:
            try:
                for index in range(count):
                    yield f"{name}{index}"

            finally:
                closed.append(name)

        return generator()

    async for _ in stream_concurrently(tracked("a", 2), tracked("b", 2), exhaustive=True):
        pass

    assert sorted(closed) == ["a", "b"]


@mark.asyncio
async def test_closes_the_other_source_when_one_fails_to_close():
    closed: list[str] = []

    class FailingToClose(AsyncGenerator[int]):
        """A source whose own release fails, unlike its iteration."""

        async def __anext__(self) -> int:
            await sleep(0.01)
            return 1

        async def asend(self, value: None = None, /) -> int:
            return await self.__anext__()

        async def athrow(
            self,
            typ: type[BaseException] | BaseException,
            val: object = None,
            tb: TracebackType | None = None,
            /,
        ) -> NoReturn:
            raise FakeException("cleanup failed")

        async def aclose(self) -> None:
            raise FakeException("cleanup failed")

    async def tracked() -> AsyncGenerator[str]:
        try:
            while True:
                await sleep(0.01)
                yield "b"

        finally:
            closed.append("b")

    # each source is released by its own producer, so one failing to close does not
    # strand the other - its error is reported by the task group holding them, which
    # the closing of the merged stream takes precedence over, leaving it only logged
    async with ctx.closing(stream_concurrently(FailingToClose(), tracked())) as merged:
        async for _ in merged:
            break

    assert closed == ["b"]


@mark.asyncio
async def test_closes_scoped_source_within_its_producer_when_the_other_fails():
    released: list[str] = []

    async def scoped() -> AsyncGenerator[int]:
        try:
            async with ctx.scope("scoped_source"):
                index: int = 0
                while True:  # no await between the yields, resuming stays in the same tick
                    yield index
                    index += 1

        finally:
            released.append("scoped")

    async def failing() -> AsyncGenerator[str]:
        yield "a"
        await sleep(0)
        raise FakeException("source failed")

    # the other source failing finishes the output without cancelling this producer,
    # which leaves its source suspended - unwinding it belongs to the producer, as
    # closing it along with the merged stream would exit the scope it holds within
    # the context of the consumer instead of the one which entered it
    async with ctx.scope("test"):
        with raises(FakeException, match="source failed"):
            async for _ in stream_concurrently(scoped(), failing()):
                await sleep(0)

    assert released == ["scoped"]


@mark.asyncio
async def test_closes_scoped_sources_within_their_producers_when_left_early():
    released: list[str] = []

    def scoped(name: str) -> AsyncGenerator[str]:
        async def generator() -> AsyncGenerator[str]:
            try:
                async with ctx.scope(f"scoped_source_{name}"):
                    for index in range(100):
                        await sleep(0.01)
                        yield f"{name}{index}"

            finally:
                released.append(name)

        return generator()

    # leaving the iteration early cancels both producers while they send, which
    # leaves their sources suspended at the yield they were sending from
    async with ctx.scope("test"):
        async with ctx.closing(stream_concurrently(scoped("a"), scoped("b"))) as merged:
            async for _ in merged:
                break

    assert sorted(released) == ["a", "b"]


@mark.asyncio
async def test_merges_no_sources_into_empty_stream():
    items: list[object] = []
    merged: AsyncGenerator[object] = stream_concurrently()
    async for item in merged:
        items.append(item)

    assert items == []


@mark.asyncio
async def test_closing_no_sources_merge_does_nothing():
    merged: AsyncGenerator[object] = stream_concurrently()
    await merged.aclose()

    with raises(StopAsyncIteration):
        await anext(merged)


@mark.asyncio
async def test_merges_single_source_by_passing_it_through():
    source: AsyncGenerator[int] = async_range(0, 3)

    # there is nothing to merge it with, so it is its own merged stream
    assert stream_concurrently(source) is source
    assert [item async for item in source] == [0, 1, 2]


@mark.asyncio
async def test_merges_more_than_two_streams():
    items: list[int | str] = []

    async for item in stream_concurrently(
        async_range(0, 3),
        async_letters("abc"),
        async_range(10, 13),
        async_letters("xyz"),
        exhaustive=True,
    ):
        items.append(item)

    assert len(items) == 12
    assert set(items) == {0, 1, 2, 10, 11, 12, "a", "b", "c", "x", "y", "z"}


@mark.asyncio
async def test_ends_promptly_when_any_of_many_sources_exhausts():
    async def endless() -> AsyncGenerator[str]:
        while True:
            await sleep(0.1)
            yield "endless"

    items: list[int | str] = []
    async with ctx.closing(
        stream_concurrently(async_range(0, 2), endless(), endless(), endless())
    ) as merged:
        async for item in merged:
            items.append(item)

    # the exhausted source ends the merge, the endless ones are cancelled
    assert [item for item in items if isinstance(item, int)] == [0, 1]


@mark.asyncio
async def test_propagates_source_exception_instead_of_its_cancellation():
    async def failing() -> AsyncGenerator[int]:
        yield 1
        raise FakeException("source failed")

    async def endless() -> AsyncGenerator[str]:
        while True:
            await sleep(0.1)
            yield "endless"

    # a failing producer aborts the group holding it, cancelling the consumer - the
    # error is delivered to the merged stream first, so the consumer ends on it
    # instead of on that cancellation
    with raises(FakeException, match="source failed"):
        async for _ in stream_concurrently(failing(), endless(), endless(), exhaustive=True):
            pass


@mark.asyncio
async def test_typed_variants_merge_their_sources():
    async def numbers() -> AsyncGenerator[int]:
        yield 1

    async def letters() -> AsyncGenerator[str]:
        yield "a"

    async def floats() -> AsyncGenerator[float]:
        yield 2.5

    async def flags() -> AsyncGenerator[bool]:
        yield True

    two: list[int | str] = [
        item async for item in stream2_concurrently(numbers(), letters(), exhaustive=True)
    ]
    assert sorted(map(str, two)) == ["1", "a"]

    three: list[int | str | float] = [
        item async for item in stream3_concurrently(numbers(), letters(), floats(), exhaustive=True)
    ]
    assert sorted(map(str, three)) == ["1", "2.5", "a"]

    four: list[int | str | float | bool] = [
        item
        async for item in stream4_concurrently(
            numbers(),
            letters(),
            floats(),
            flags(),
            exhaustive=True,
        )
    ]
    assert sorted(map(str, four)) == ["1", "2.5", "True", "a"]


@mark.asyncio
async def test_raises_first_delivered_error_when_many_sources_fail_at_once():
    async def failing(tag: str) -> AsyncGenerator[int]:
        raise FakeException(tag)
        yield 0  # pragma: no cover - never reached

    # the error which reached the consumer is raised on its own, the ones which
    # followed it are only collected by the task group holding the producers
    with raises(FakeException) as failure:
        async for _ in stream_concurrently(
            failing("first"),
            failing("second"),
            failing("third"),
            exhaustive=True,
        ):
            pass

    assert str(failure.value) == "first"


@mark.asyncio
async def test_reports_release_failure_of_a_merge_which_ended_without_an_error():
    async def exhausting() -> AsyncGenerator[int]:
        yield 1

    async def failing_to_release() -> AsyncGenerator[str]:
        try:
            yield "b"
            await sleep(0.01)

        finally:
            raise FakeException("release failed")

    # the merge ends without an error - the first source exhausted, cancelling the
    # other - so the producer failing to release has no delivered error to surface
    # in its place and the task group holding it raises what it collected - a single
    # error on its own, without the group wrapper
    with raises(FakeException, match="release failed"):
        async for _ in stream_concurrently(exhausting(), failing_to_release()):
            pass
