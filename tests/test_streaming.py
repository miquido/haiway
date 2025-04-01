from asyncio import CancelledError, sleep
from collections.abc import AsyncGenerator

from pytest import mark, raises

from haiway import ctx
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

    async with ctx.scope("test", TestState(value=42)):
        elements: list[TestState] = []

        async for element in ctx.stream(generator, 10):
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

    async with ctx.scope("test", TestState(value=42)):
        elements: list[TestState] = []

        async for element in ctx.stream(outer, 10):
            elements.append(element)

    assert elements == [
        TestState(value=42),
        TestState(value=42, other="outer"),
        TestState(value=10, other="inner"),
    ]
