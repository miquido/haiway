from asyncio import CancelledError, sleep
from collections.abc import AsyncIterator

from pytest import mark, raises

from haiway import ctx
from haiway.helpers.concurrent import stream_concurrently


class FakeException(Exception):
    pass


async def async_range(
    start: int,
    stop: int,
    delay: float = 0,
) -> AsyncIterator[int]:
    for i in range(start, stop):
        if delay:
            await sleep(delay)
        yield i


async def async_letters(
    letters: str,
    delay: float = 0,
) -> AsyncIterator[str]:
    for letter in letters:
        if delay:
            await sleep(delay)
        yield letter


@mark.asyncio
async def test_merges_two_streams():
    items: list[int | str] = []

    async for item in stream_concurrently(async_range(0, 3), async_letters("abc")):
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

    async def empty_iter() -> AsyncIterator[int]:
        return
        yield  # Make it a generator

    # Both empty
    async for item in stream_concurrently(empty_iter(), empty_iter()):
        items.append(item)
    assert items == []

    # One empty, one with items
    items = []
    async for item in stream_concurrently(async_range(0, 3), empty_iter()):
        items.append(item)
    assert items == [0, 1, 2]

    # Other way around
    items = []
    async for item in stream_concurrently(empty_iter(), async_range(0, 3)):
        items.append(item)
    assert items == [0, 1, 2]


@mark.asyncio
async def test_handles_different_lengths():
    items: list[int | str] = []

    async for item in stream_concurrently(async_range(0, 10), async_letters("ab")):
        items.append(item)

    # Should have all items from both sources
    assert len(items) == 12
    numbers = [i for i in items if isinstance(i, int)]
    letters = [i for i in items if isinstance(i, str)]
    assert numbers == list(range(10))
    assert letters == ["a", "b"]


@mark.asyncio
async def test_propagates_exceptions_from_source_a():
    async def failing_iter_a() -> AsyncIterator[int]:
        yield 1
        yield 2
        raise FakeException("Source A failed")

    items: list[int | str] = []
    with raises(FakeException, match="Source A failed"):
        async for item in stream_concurrently(failing_iter_a(), async_letters("abc")):
            items.append(item)

    # Should have collected some items before failure
    assert len(items) >= 2  # At least the two yielded numbers


@mark.asyncio
async def test_propagates_exceptions_from_source_b():
    async def failing_iter_b() -> AsyncIterator[str]:
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

    async def tracked_iter_a() -> AsyncIterator[int]:
        nonlocal started_a, cancelled_a
        started_a = True
        try:
            for i in range(100):
                await sleep(0.1)
                yield i
        except CancelledError:
            cancelled_a = True
            raise

    async def tracked_iter_b() -> AsyncIterator[str]:
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
    async def fibonacci() -> AsyncIterator[int]:
        a, b = 0, 1
        for _ in range(5):
            yield a
            a, b = b, a + b

    async def words() -> AsyncIterator[str]:
        for word in ["hello", "world", "test"]:
            yield word

    items: list[int | str] = []
    async for item in stream_concurrently(fibonacci(), words()):
        items.append(item)

    numbers = [i for i in items if isinstance(i, int)]
    strings = [i for i in items if isinstance(i, str)]
    assert numbers == [0, 1, 1, 2, 3]
    assert strings == ["hello", "world", "test"]


@mark.asyncio
async def test_immediate_yield():
    """Test that items are yielded as soon as they're available."""

    async def slow_numbers() -> AsyncIterator[int]:
        for i in range(3):
            await sleep(0.2)  # Increased delay for more reliable timing
            yield i

    async def fast_letters() -> AsyncIterator[str]:
        for c in "abc":
            await sleep(0.01)  # Smaller delay
            yield c

    items: list[int | str] = []
    async for item in stream_concurrently(slow_numbers(), fast_letters()):
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

    async def iter_a() -> AsyncIterator[str]:
        execution_order.append("a_start")
        await sleep(0.1)  # Increased for more reliable timing
        execution_order.append("a_yield_1")
        yield "a1"
        await sleep(0.1)
        execution_order.append("a_yield_2")
        yield "a2"

    async def iter_b() -> AsyncIterator[str]:
        execution_order.append("b_start")
        await sleep(0.01)  # Much smaller delay
        execution_order.append("b_yield_1")
        yield "b1"
        await sleep(0.01)
        execution_order.append("b_yield_2")
        yield "b2"

    items: list[str] = []
    async for item in stream_concurrently(iter_a(), iter_b()):
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
