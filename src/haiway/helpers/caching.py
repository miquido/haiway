from asyncio import iscoroutinefunction
from collections import OrderedDict
from collections.abc import Callable, Coroutine, Hashable
from functools import _make_key  # pyright: ignore[reportPrivateUsage]
from time import monotonic
from typing import Any, NamedTuple, Protocol, cast, overload

from haiway.context.access import ctx
from haiway.utils.mimic import mimic_function

__all__ = (
    "CacheClear",
    "CacheMakeKey",
    "CacheRead",
    "CacheWrite",
    "cache",
)


class CacheMakeKey[**Args, Key](Protocol):
    """
    Protocol for generating cache keys from function arguments.

    Implementations of this protocol are responsible for creating a unique key
    based on the arguments passed to a function, which can then be used for
    cache lookups.

    The key must be consistent for the same set of arguments, and different
    for different sets of arguments that should be cached separately.
    """

    def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Key: ...


class CacheRead[Key, Value](Protocol):
    """
    Protocol for reading values from a cache.

    Implementations of this protocol are responsible for retrieving cached values
    based on a key. If the key is not present in the cache, None should be returned.

    This is designed as an asynchronous operation to support remote caches where
    retrieval might involve network operations.
    """

    async def __call__(
        self,
        key: Key,
    ) -> Value | None: ...


class CacheWrite[Key, Value](Protocol):
    """
    Protocol for writing values to a cache.

    Implementations of this protocol are responsible for storing values in a cache
    using the specified key. Any existing value with the same key should be overwritten.

    This is designed as an asynchronous operation to support remote caches where
    writing might involve network operations.
    """

    async def __call__(
        self,
        key: Key,
        value: Value,
    ) -> None: ...


class CacheClear[Key](Protocol):
    """
    Protocol for clearing values from a cache.

    Implementations of this protocol are responsible for clearing cached values
    based on a key. If the key is not provided clear the whole cache.

    This is designed as an asynchronous operation to support remote caches where
    retrieval might involve network operations.
    """

    async def __call__(
        self,
        key: Key | None,
    ) -> None: ...


class Cached[**Args, Result, Key: Hashable](Protocol):
    async def clear_cache(
        self,
        key: Key | None = None,
    ) -> None: ...

    async def clear_call_cache(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> None: ...

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result: ...


@overload
def cache[**Args, Result](
    function: Callable[Args, Coroutine[Any, Any, Result]],
    /,
) -> Cached[Args, Result, Hashable]: ...


@overload
def cache[**Args, Result, Key: Hashable](
    *,
    limit: int | None = None,
    expiration: float | None = None,
    make_key: CacheMakeKey[Args, Key] | None = None,
) -> Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Cached[Args, Result, Key]]: ...


@overload
def cache[**Args, Result, Key: Hashable](
    *,
    make_key: CacheMakeKey[Args, Key],
    read: CacheRead[Key, Result],
    write: CacheWrite[Key, Result],
    clear: CacheClear[Key] | None = None,
) -> Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Cached[Args, Result, Key]]: ...


def cache[**Args, Result, Key: Hashable](
    function: Callable[Args, Coroutine[Any, Any, Result]] | None = None,
    *,
    limit: int | None = None,
    expiration: float | None = None,
    make_key: CacheMakeKey[Args, Key] | None = None,
    read: CacheRead[Key, Result] | None = None,
    write: CacheWrite[Key, Result] | None = None,
    clear: CacheClear[Key] | None = None,
) -> (
    Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Cached[Args, Result, Key]]
    | Cached[Args, Result, Key]
):
    """
    Memoize coroutine results using an in-memory LRU cache or a custom backend.

    The decorator must wrap an `async def` function. When used without arguments
    (``@cache``) the wrapped coroutine is cached in-process with an LRU cache whose
    default size is one entry. Providing configuration arguments returns a decorator
    that can be applied later and enables custom cache behaviour.

    Parameters
    ----------
    function : Callable[Args, Coroutine[Any, Any, Result]] | None
        Coroutine function to memoize when ``cache`` is used directly as ``@cache``.
        Omit this parameter when supplying configuration options so that ``cache``
        returns a decorator.
    limit : int | None
        Maximum number of entries kept by the default in-memory cache. Defaults to ``1``.
        Ignored when a custom ``read``/``write`` pair is supplied.
    expiration : float | None
        Monotonic seconds after which an in-memory entry is considered stale and recomputed.
        ``None`` (the default) disables time-based eviction. Ignored for custom caches.
    make_key : CacheMakeKey[Args, Key] | None
        Callable that converts invocation arguments into a cache key. A default that mirrors
        ``functools.lru_cache`` behaviour is used when omitted. Required when ``read`` and
        ``write`` are provided.
    read : CacheRead[Key, Result] | None
        Async callable used to fetch values from an external cache. Must be provided together
        with ``write`` and ``make_key``.
    write : CacheWrite[Key, Result] | None
        Async callable used to persist values to an external cache. Must be provided together
        with ``read`` and ``make_key``. Writes are scheduled via ``ctx.spawn`` so the call
        returns without awaiting the persistence step.
    clear : CacheClear[Key] | None
        Async callable used to evict entries from a custom cache. Provide this if you plan to
        call ``clear_cache``/``clear_call_cache`` on the returned wrapper.

    Returns
    -------
    Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Cached[Args, Result, Key]]
        When used with configuration arguments, returns a decorator that will memoize the
        target coroutine. When ``function`` is supplied positionally, the memoized coroutine is
        returned immediately.

    Notes
    -----
    - Only coroutine functions are supported; synchronous functions will trigger an assertion.
    - The default cache keeps state per decorated function and is not thread-safe.
    - Custom backends are responsible for persistence semantics; ``write`` is invoked in the
      background via ``ctx.spawn``.

    Examples
    --------
    Simple usage as a decorator:

    >>> @cache
    ... async def my_function(x: int) -> int:
    ...     print("Function called")
    ...     return x * 2
    >>> await my_function(5)
    Function called
    10
    >>> await my_function(5)  # Cache hit, function body not executed
    10

    With configuration parameters:

    >>> @cache(limit=10, expiration=60.0)
    ... async def my_function(x: int) -> int:
    ...     return x * 2

    With custom cache for async functions:

    >>> @cache(make_key=custom_key_maker, read=redis_read, write=redis_write)
    ... async def fetch_data(user_id: str) -> dict:
    ...     return await api_call(user_id)
    """

    def _wrap(
        function: Callable[Args, Coroutine[Any, Any, Result]],
    ) -> Cached[Args, Result, Key]:
        assert iscoroutinefunction(function)  # nosec: B101
        if read is not None and write is not None and make_key is not None:
            assert limit is None and expiration is None  # nosec: B101
            return _CustomCache(
                function,
                make_key=make_key,
                read=read,
                write=write,
                clear=clear,
            )

        else:
            assert read is None and write is None  # nosec: B101
            return _AsyncCache(
                function,
                limit=limit if limit is not None else 1,
                expiration=expiration,
                make_key=cast(
                    CacheMakeKey[Args, Hashable],
                    make_key if make_key is not None else _default_make_key,
                ),
            )

    if function is not None:
        return _wrap(function)

    else:
        return _wrap


class _CacheEntry[Entry](NamedTuple):
    value: Entry
    expire: float | None


class _AsyncCache[**Args, Result]:
    __slots__ = (
        "__annotations__",
        "__defaults__",
        "__doc__",
        "__globals__",
        "__kwdefaults__",
        "__name__",
        "__qualname__",
        "__wrapped__",
        "_cached",
        "_function",
        "_limit",
        "_make_key",
        "_next_expire_time",
    )

    def __init__(
        self,
        function: Callable[Args, Coroutine[Any, Any, Result]],
        /,
        limit: int,
        expiration: float | None,
        make_key: CacheMakeKey[Args, Hashable],
    ) -> None:
        self._function: Callable[Args, Coroutine[Any, Any, Result]] = function
        self._cached: OrderedDict[Hashable, _CacheEntry[Result]] = OrderedDict()
        self._limit: int = limit
        self._make_key: CacheMakeKey[Args, Hashable] = make_key

        if expiration := expiration:

            def next_expire_time() -> float | None:
                return monotonic() + expiration

        else:

            def next_expire_time() -> float | None:
                return None

        self._next_expire_time: Callable[[], float | None] = next_expire_time

        # mimic function attributes if able
        mimic_function(function, within=self)

    def __get__(
        self,
        instance: object | None,
        owner: type | None = None,
        /,
    ) -> Callable[Args, Coroutine[Any, Any, Result]]:
        assert instance is None, "cache does not work for classes"  # nosec: B101
        return self

    async def clear_cache(
        self,
        key: Hashable | None = None,
    ) -> None:
        if key is None:
            self._cached.clear()

        elif key in self._cached:
            del self._cached[key]

    async def clear_call_cache(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> None:
        key: Hashable = self._make_key(*args, **kwargs)
        if key in self._cached:
            del self._cached[key]

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        key: Hashable = self._make_key(
            *args,
            **kwargs,
        )

        match self._cached.get(key):
            case None:
                pass

            case entry:
                if (expire := entry[1]) and expire < monotonic():
                    del self._cached[key]  # continue the same way as if empty

                else:
                    self._cached.move_to_end(key)
                    return entry[0]

        result: Result = await self._function(*args, **kwargs)
        self._cached[key] = _CacheEntry(
            value=result,
            expire=self._next_expire_time(),
        )
        if len(self._cached) > self._limit:
            # keep the size limit
            self._cached.popitem(last=False)

        return result


class _CustomCache[**Args, Result, Key]:
    __slots__ = (
        "__annotations__",
        "__defaults__",
        "__doc__",
        "__globals__",
        "__kwdefaults__",
        "__name__",
        "__qualname__",
        "__wrapped__",
        "_clear",
        "_expiration",
        "_function",
        "_make_key",
        "_read",
        "_write",
    )

    def __init__(
        self,
        function: Callable[Args, Coroutine[Any, Any, Result]],
        /,
        make_key: CacheMakeKey[Args, Key],
        read: CacheRead[Key, Result],
        write: CacheWrite[Key, Result],
        clear: CacheClear[Key] | None,
    ) -> None:
        self._function: Callable[Args, Coroutine[Any, Any, Result]] = function
        self._make_key: CacheMakeKey[Args, Key] = make_key
        self._read: CacheRead[Key, Result] = read
        self._write: CacheWrite[Key, Result] = write
        self._clear: CacheClear[Key] | None = clear

        # mimic function attributes if able
        mimic_function(function, within=self)

    async def clear_cache(
        self,
        key: Key | None = None,
    ) -> None:
        assert self._clear is not None  # nosec: B101
        await self._clear(key)

    async def clear_call_cache(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> None:
        assert self._clear is not None  # nosec: B101
        await self._clear(self._make_key(*args, **kwargs))

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        key: Key = self._make_key(
            *args,
            **kwargs,
        )

        match await self._read(key):
            case None:
                result: Result = await self._function(*args, **kwargs)
                ctx.spawn(  # write the value asnychronously
                    self._write,
                    key=key,
                    value=result,
                )

                return result

            case entry:
                return entry


def _default_make_key[**Args](
    *args: Args.args,
    **kwargs: Args.kwargs,
) -> Hashable:
    return _make_key(
        args=args,
        kwds=kwargs,
        typed=True,
    )
