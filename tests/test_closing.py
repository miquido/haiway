from collections.abc import AsyncGenerator
from uuid import UUID, uuid4

from pytest import mark, raises

from haiway import (
    ContextIdentifier,
    Observability,
    ObservabilityLevel,
    ctx,
)


class FakeException(Exception):
    pass


@mark.asyncio
async def test_closing_releases_the_generator_on_early_exit():
    released: list[str] = []

    async def generator() -> AsyncGenerator[int]:
        try:
            for element in range(10):
                yield element

        finally:
            released.append("source")

    async with ctx.scope("test"):
        async with ctx.closing(generator()) as stream:
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
        async with ctx.closing(generator()) as stream:
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
            async with ctx.closing(generator()) as stream:
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
        # a generator keeping a scope of its own inside itself, which is what
        # spanning the whole iteration requires
        async with ctx.scope("generator"):
            for element in range(10):
                yield element

    async with ctx.scope("test", observability=observability):
        async with ctx.closing(generator()) as stream:
            async for _ in stream:
                break

    # leaving the iteration early is how such a scope is meant to end - the
    # `GeneratorExit` unwinding it is not a failure of the scope or its task group
    assert failures == []
