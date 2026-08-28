from asyncio import CancelledError, sleep
from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

from pytest import mark, raises

from haiway import (
    ContextIdentifier,
    Observability,
    ObservabilityLevel,
    State,
    ctx,
)


class FakeException(Exception):
    pass


@mark.asyncio
async def test_fails_when_generator_fails():
    async def generator(value: int) -> AsyncGenerator[int]:
        yield value
        raise FakeException()

    elements: int = 0
    with raises(FakeException):
        async for _ in ctx.stream(generator, 42):
            elements += 1

    assert elements == 1


@mark.asyncio
async def test_cancels_when_iteration_cancels():
    async def generator(value: int) -> AsyncGenerator[int]:
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
    async def generator(value: int) -> AsyncGenerator[int]:
        yield value

    elements: int = 0
    async for _ in ctx.stream(generator, 42):
        elements += 1

    assert elements == 1


@mark.asyncio
async def test_delivers_updates_when_generating():
    async def generator(value: int) -> AsyncGenerator[int]:
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

    async def generator(value: int) -> AsyncGenerator[TestState]:
        yield ctx.state(TestState)
        async with ctx.scope("nested", ctx.state(TestState).updating(value=value)):
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

    async def inner(value: int) -> AsyncGenerator[TestState]:
        yield ctx.state(TestState)
        async with ctx.scope("inner", ctx.state(TestState).updating(value=value, other="inner")):
            yield ctx.state(TestState)

    async def outer(value: int) -> AsyncGenerator[TestState]:
        yield ctx.state(TestState)
        async with ctx.scope("outer", ctx.state(TestState).updating(other="outer")):
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


@mark.asyncio
async def test_closing_releases_the_stream_scope_on_early_exit():
    released: list[str] = []

    async def generator() -> AsyncGenerator[int]:
        try:
            for element in range(10):
                yield element

        finally:
            released.append("source")

    async with ctx.scope("test"):
        async with ctx.closing(ctx.stream(generator)) as stream:
            async for _ in stream:
                break

        # closing runs the generator cleanup where the iteration ended,
        # instead of leaving it to the garbage collector
        assert released == ["source"]


@mark.asyncio
async def test_closing_provides_the_wrapped_generator():
    async def generator() -> AsyncGenerator[int]:
        yield 1
        yield 2

    async with ctx.scope("test"):
        async with ctx.closing(ctx.stream(generator)) as stream:
            assert [element async for element in stream] == [1, 2]


@mark.asyncio
async def test_closing_closes_on_error():
    released: list[str] = []

    async def generator() -> AsyncGenerator[int]:
        try:
            for element in range(10):
                yield element

        finally:
            released.append("source")

    async with ctx.scope("test"):
        with raises(FakeException):
            async with ctx.closing(ctx.stream(generator)) as stream:
                async for _ in stream:
                    raise FakeException()

        assert released == ["source"]


@mark.asyncio
async def test_closing_records_no_failure() -> None:
    failures: list[str] = []

    def log_recording(
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: object,
        exception: BaseException | None,
    ) -> None:
        if level >= ObservabilityLevel.ERROR:
            failures.append(message)

    def trace_identifying(
        scope: ContextIdentifier,
        /,
    ) -> UUID:
        return uuid4()

    observability = Observability(
        trace_identifying=trace_identifying,
        log_recording=log_recording,
        metric_recording=lambda *args, **kwargs: None,
        event_recording=lambda *args, **kwargs: None,
        attributes_recording=lambda *args, **kwargs: None,
        scope_entering=lambda scope, /: "trace",
        scope_exiting=lambda scope, /, *, exception: None,
    )

    async def generator() -> AsyncGenerator[int]:
        for element in range(10):
            yield element

    async with ctx.scope("test", observability=observability):
        async with ctx.closing(ctx.stream(generator)) as stream:
            async for _ in stream:
                break

    # leaving the iteration early is how a stream scope is meant to end - the
    # `GeneratorExit` unwinding it is not a failure of the scope or its task group
    assert failures == []
