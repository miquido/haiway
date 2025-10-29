from asyncio import CancelledError, Task, sleep
from collections.abc import Callable, Generator

from pytest import fixture, mark, raises

from haiway import cache, cache_externally, ctx


class FakeException(Exception):
    pass


@fixture
def fake_random() -> Callable[[], Generator[int]]:
    def random_next() -> Generator[int]:
        yield from range(0, 65536)

    return random_next


async def _wait_for(condition: Callable[[], bool], attempts: int = 20) -> None:
    for _ in range(attempts):
        if condition():
            return
        await sleep(0)
    raise AssertionError("condition was not met")


@mark.asyncio
async def test_async_returns_cache_value_with_same_argument(fake_random: Callable[[], int]):
    @cache
    async def randomized(_: str, /) -> int:
        return fake_random()

    expected: int = await randomized("expected")
    assert await randomized("expected") == expected


@mark.asyncio
async def test_async_returns_fresh_value_with_different_argument(fake_random: Callable[[], int]):
    @cache
    async def randomized(_: str, /) -> int:
        return fake_random()

    expected: int = await randomized("expected")
    assert await randomized("checked") != expected


@mark.asyncio
async def test_async_returns_fresh_value_with_limit_exceed(fake_random: Callable[[], int]):
    @cache(limit=1)
    async def randomized(_: str, /) -> int:
        return fake_random()

    expected: int = await randomized("expected")
    await randomized("different")
    assert await randomized("expected") != expected


@mark.asyncio
async def test_async_returns_same_value_with_repeating_argument(fake_random: Callable[[], int]):
    @cache(limit=2)
    async def randomized(_: str, /) -> int:
        return fake_random()

    expected: int = await randomized("expected")
    await randomized("different")
    await randomized("expected")
    await randomized("more_different")
    await randomized("expected")
    await randomized("final_different")
    assert await randomized("expected") == expected


@mark.asyncio
async def test_async_returns_fresh_value_with_expiration_time_exceed(
    fake_random: Callable[[], int],
):
    @cache(expiration=0.01)
    async def randomized(_: str, /) -> int:
        return fake_random()

    expected: int = await randomized("expected")
    await sleep(0.02)
    assert await randomized("expected") != expected


@mark.asyncio
async def test_async_cancel_waiting_does_not_cancel_task():
    @cache
    async def randomized(_: str, /) -> int:
        try:
            await sleep(0.5)
            return 0
        except CancelledError:
            return 42

    expected: int = await randomized("expected")
    cancelled = Task(randomized("expected"))

    async def delayed_cancel() -> None:
        cancelled.cancel()

    Task(delayed_cancel())
    assert await randomized("expected") == expected


@mark.asyncio
async def test_async_expiration_does_not_cancel_task():
    @cache(expiration=0.01)
    async def randomized(_: str, /) -> int:
        try:
            await sleep(0.02)
            return 0
        except CancelledError:
            return 42

    assert await randomized("expected") == 0


@mark.asyncio
async def test_async_fails_with_error():
    @cache(expiration=0.02)
    async def randomized(_: str, /) -> int:
        raise FakeException()

    with raises(FakeException):
        await randomized("expected")


@mark.asyncio
async def test_async_clear_cache_returns_fresh_value(fake_random: Callable[[], int]):
    @cache
    async def randomized(_: str, /) -> int:
        return fake_random()

    expected: int = await randomized("expected")
    await randomized.clear_cache()
    assert await randomized("expected") != expected


@mark.asyncio
async def test_external_cache_persists_results_once() -> None:
    backend: dict[str, int] = {}
    call_count: int = 0

    async def read_from_store(key: str) -> int | None:
        return backend.get(key)

    async def write_to_store(key: str, value: int) -> None:
        backend[key] = value

    async def clear_from_store(key: str | None) -> None:
        if key is None:
            backend.clear()
            return
        backend.pop(key, None)

    @cache_externally(
        make_key=lambda value: f"cache:{value}",
        read=read_from_store,
        write=write_to_store,
        clear=clear_from_store,
    )
    async def compute(value: str) -> int:
        nonlocal call_count
        call_count += 1
        return call_count

    async with ctx.scope("external-cache"):
        first: int = await compute("alpha")
        await _wait_for(lambda: "cache:alpha" in backend)
        second: int = await compute("alpha")

    assert first == 1
    assert second == 1
    assert call_count == 1


@mark.asyncio
async def test_external_cache_clear_supports_key_and_global_flush() -> None:
    backend: dict[str, int] = {}
    cleared_keys: list[str | None] = []

    async def read_from_store(key: str) -> int | None:
        return backend.get(key)

    async def write_to_store(key: str, value: int) -> None:
        backend[key] = value

    async def clear_from_store(key: str | None) -> None:
        cleared_keys.append(key)
        if key is None:
            backend.clear()
            return
        backend.pop(key, None)

    @cache_externally(
        make_key=lambda value: f"cache:{value}",
        read=read_from_store,
        write=write_to_store,
        clear=clear_from_store,
    )
    async def compute(value: str) -> str:
        return value

    async with ctx.scope("external-cache-clear"):
        await compute("alpha")
        await _wait_for(lambda: "cache:alpha" in backend)
        await compute.clear_cache("cache:alpha")
        assert "cache:alpha" not in backend
        assert cleared_keys[-1] == "cache:alpha"

        await compute("alpha")
        await _wait_for(lambda: "cache:alpha" in backend)
        await compute("beta")
        await _wait_for(lambda: "cache:beta" in backend)
        await compute.clear_cache()

    assert backend == {}
    assert cleared_keys[-1] is None
