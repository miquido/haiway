from asyncio import CancelledError, get_running_loop, sleep
from collections.abc import AsyncGenerator, AsyncIterator

from pytest import mark, raises

from haiway import ctx
from haiway.context.metrics import ScopeMetrics
from haiway.state.structure import State


class FakeException(Exception):
    pass


@mark.asyncio
async def test_fails_when_generator_fails():
    async def generator(value: int) -> AsyncGenerator[int, None]:
        yield value
        raise FakeException()

    elements: int = 0
    with raises(FakeException):
        async for _ in ctx.stream(generator, 42):
            elements += 1

    assert elements == 1


@mark.asyncio
async def test_cancels_when_iteration_cancels():
    async def generator(value: int) -> AsyncGenerator[int, None]:
        await sleep(0)
        yield value

    elements: int = 0
    with raises(CancelledError):
        ctx.cancel()
        async for _ in ctx.stream(generator, 42):
            elements += 1

    assert elements == 0


@mark.asyncio
async def test_ends_when_generator_ends():
    async def generator(value: int) -> AsyncGenerator[int, None]:
        yield value

    elements: int = 0
    async for _ in ctx.stream(generator, 42):
        elements += 1

    assert elements == 1


@mark.asyncio
async def test_delivers_updates_when_generating():
    async def generator(value: int) -> AsyncGenerator[int, None]:
        for i in range(0, value):
            yield i

    elements: list[int] = []

    async for element in ctx.stream(generator, 10):
        elements.append(element)

    assert elements == list(range(0, 10))


@mark.asyncio
async def test_streaming_context_variables_access_is_preserved():
    class TestState(State):
        value: int = 42
        other: str = "other"

    async def generator(value: int) -> AsyncGenerator[TestState, None]:
        yield ctx.state(TestState)
        with ctx.scope("nested", ctx.state(TestState).updated(value=value)):
            yield ctx.state(TestState)

    stream: AsyncIterator[TestState]
    async with ctx.scope("test", TestState(value=42)):
        elements: list[TestState] = []

        stream = ctx.stream(generator, 10)

    async for element in stream:
        elements.append(element)

    assert elements == [
        TestState(value=42),
        TestState(value=10),
    ]


@mark.asyncio
async def test_nested_streaming_streams_correctly():
    class TestState(State):
        value: int = 42
        other: str = "other"

    async def inner(value: int) -> AsyncGenerator[TestState, None]:
        yield ctx.state(TestState)
        with ctx.scope("inner", ctx.state(TestState).updated(value=value, other="inner")):
            yield ctx.state(TestState)

    async def outer(value: int) -> AsyncGenerator[TestState, None]:
        yield ctx.state(TestState)
        with ctx.scope("outer", ctx.state(TestState).updated(other="outer")):
            async for item in ctx.stream(inner, value):
                yield item

    stream: AsyncIterator[TestState]
    async with ctx.scope("test", TestState(value=42)):
        elements: list[TestState] = []

        stream = ctx.stream(outer, 10)

    async for element in stream:
        elements.append(element)

    assert elements == [
        TestState(value=42),
        TestState(value=42, other="outer"),
        TestState(value=10, other="inner"),
    ]


@mark.asyncio
async def test_streaming_context_completion_is_called_at_the_end_of_stream():
    completion_future = get_running_loop().create_future()

    class IterationMetric(State):
        value: int = 0

    metric: IterationMetric = IterationMetric()

    def completion(metrics: ScopeMetrics):
        nonlocal metric
        metric = metrics.metrics(merge=lambda current, nested: nested)[0]  # pyright: ignore[reportAssignmentType]
        completion_future.set_result(())

    async def generator(value: int) -> AsyncGenerator[int, None]:
        for i in range(0, value):
            ctx.record(
                IterationMetric(value=i),
                merge=lambda lhs, rhs: IterationMetric(value=lhs.value + rhs.value),
            )
            yield i

    stream: AsyncIterator[int]
    async with ctx.scope("test", completion=completion):
        elements: list[int] = []

        stream = ctx.stream(generator, 10)

    async for element in stream:
        elements.append(element)

    await completion_future
    assert metric.value == 45
