import pytest

from haiway.utils.context import NoopAsyncContext, NoopContext


def test_noop_context_instance_is_shared() -> None:
    assert isinstance(NoopContext.instance, NoopContext)
    assert NoopContext.instance is NoopContext.instance


def test_noop_context_yields_nothing() -> None:
    with NoopContext.instance as value:
        assert value is None

    with NoopContext() as value:
        assert value is None


def test_noop_context_does_not_suppress_exceptions() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        with NoopContext.instance:
            raise RuntimeError("boom")


def test_noop_context_is_reentrant() -> None:
    with NoopContext.instance:
        with NoopContext.instance:
            pass


@pytest.mark.asyncio
async def test_noop_async_context_instance_is_shared() -> None:
    assert isinstance(NoopAsyncContext.instance, NoopAsyncContext)
    assert NoopAsyncContext.instance is NoopAsyncContext.instance


@pytest.mark.asyncio
async def test_noop_async_context_yields_nothing() -> None:
    async with NoopAsyncContext.instance as value:
        assert value is None


@pytest.mark.asyncio
async def test_noop_async_context_does_not_suppress_exceptions() -> None:
    with pytest.raises(RuntimeError, match="boom"):
        async with NoopAsyncContext.instance:
            raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_noop_async_context_is_reentrant() -> None:
    async with NoopAsyncContext.instance:
        async with NoopAsyncContext.instance:
            pass
