from asyncio import CancelledError, run, sleep
from collections.abc import AsyncIterator, Callable, Coroutine, Generator, Hashable
from contextlib import asynccontextmanager
from inspect import iscoroutinefunction
from threading import Thread
from typing import Any

from pytest import fixture, mark, raises

from haiway import cache, cache_externally, ctx, retry, throttle


class FakeException(Exception):
    pass


@fixture
def fake_random() -> Callable[[], int]:
    def random_next() -> Generator[int]:
        yield from range(0, 65536)

    iterator: Generator[int] = random_next()

    def next_value() -> int:
        return next(iterator)

    return next_value


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
    # generous margin over the 0.01s expiration checked against the monotonic clock
    await sleep(0.05)
    assert await randomized("expected") != expected


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


@mark.asyncio
async def test_external_cache_clear_without_backend_is_a_noop() -> None:
    backend: dict[str, int] = {}

    def make_key(value: str) -> str:
        return f"cache:{value}"

    async def read_from_store(key: str) -> int | None:
        return backend.get(key)

    async def write_to_store(key: str, value: int) -> None:
        backend[key] = value

    @cache_externally(
        make_key=make_key,
        read=read_from_store,
        write=write_to_store,
    )
    async def compute(value: str) -> int:
        return len(value)

    async with ctx.scope("external-cache-no-clear"):
        await compute("alpha")
        await _wait_for(lambda: "cache:alpha" in backend)

        await compute.clear_cache("cache:alpha")
        await compute.clear_cache()

    assert backend == {"cache:alpha": 5}


def test_local_cache_rejects_method_binding() -> None:
    class Holder:
        @cache
        async def method(self) -> int:
            return 1

    assert callable(Holder.method)

    with raises(AssertionError, match="does not work for classes"):
        Holder().method  # noqa: B018


@mark.asyncio
async def test_external_cache_writes_while_disposables_are_prepared() -> None:
    backend: dict[str, int] = {}

    async def read_from_store(key: str) -> int | None:
        return backend.get(key)

    async def write_to_store(key: str, value: int) -> None:
        backend[key] = value

    @cache_externally(
        make_key=lambda value: f"cache:{value}",
        read=read_from_store,
        write=write_to_store,
    )
    async def compute(value: str) -> int:
        return len(value)

    computed: list[int] = []

    @asynccontextmanager
    async def preparing() -> AsyncIterator[tuple[()]]:
        # spawning scoped tasks is refused while disposables are prepared
        computed.append(await compute("alpha"))
        yield ()

    async with ctx.scope("external-cache", disposables=(preparing(),)):
        pass

    # the computed value is returned and cached instead of lost to a failed spawn
    assert computed == [5]
    assert backend == {"cache:alpha": 5}


def test_local_cache_is_consistent_across_threads():
    @cache(limit=4, expiration=0.0005)
    async def cached(value: int) -> int:
        return value

    errors: list[BaseException] = []

    def worker(seed: int) -> None:
        async def work() -> None:
            for index in range(3000):
                try:
                    assert await cached((index + seed) % 8) == (index + seed) % 8

                except BaseException as exc:
                    errors.append(exc)

        run(work())

    threads = [Thread(target=worker, args=(seed,)) for seed in range(6)]
    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert errors == []


def _externally_cached[**Args, Result](
    function: Callable[Args, Coroutine[None, None, Result]],
) -> Callable[Args, Coroutine[None, None, Result]]:
    stored: dict[Hashable, Result] = {}

    async def read(key: Hashable) -> Result | None:
        return stored.get(key)

    async def write(key: Hashable, value: Result) -> None:
        stored[key] = value

    return cache_externally(
        make_key=lambda *args, **kwargs: (args, tuple(kwargs.items())),
        read=read,
        write=write,
    )(function)


_CACHING: tuple[Callable[[Callable[..., Coroutine[None, None, int]]], Any], ...] = (
    cache,
    cache(limit=4),
    _externally_cached,
)


@mark.parametrize("caching", _CACHING)
def test_cached_function_is_a_coroutine_function(caching: Callable[..., Any]) -> None:
    async def function(value: int) -> int:
        return value

    assert iscoroutinefunction(caching(function))


@mark.asyncio
@mark.parametrize("caching", _CACHING)
async def test_retry_over_cached_function_retries(caching: Callable[..., Any]) -> None:
    calls: list[int] = []

    async def function(value: int) -> int:
        calls.append(value)
        if len(calls) < 2:
            raise FakeException()

        return value

    retried = retry(limit=2)(caching(function))
    async with ctx.scope("test"):
        assert await retried(1) == 1

    assert calls == [1, 1]


@mark.asyncio
@mark.parametrize("caching", _CACHING)
async def test_throttle_over_cached_function_runs(caching: Callable[..., Any]) -> None:
    async def function(value: int) -> int:
        return value

    throttled = throttle(limit=1)(caching(function))
    async with ctx.scope("test"):
        assert await throttled(1) == 1


@mark.asyncio
async def test_cache_key_tells_apart_types_and_keywords() -> None:
    calls: list[object] = []

    @cache(limit=8)
    async def function(value: object, *, flag: bool = False) -> object:
        calls.append(value)
        return value

    await function(1)
    await function(1)
    await function(1.0)
    await function(1, flag=True)
    await function(1, flag=True)
    await function(True)

    assert calls == [1, 1.0, 1, True]
