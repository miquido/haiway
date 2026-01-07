from collections import OrderedDict
from collections.abc import Callable, Coroutine, Hashable
from functools import (
    _make_key,  # pyright: ignore[reportPrivateUsage]
    update_wrapper,
)
from inspect import iscoroutinefunction
from time import monotonic
from typing import Any, NamedTuple, Protocol, overload

from haiway.context.access import ctx

__all__ = (
    "CacheClear",
    "CacheMakeKey",
    "CacheRead",
    "CacheWrite",
    "cache",
    "cache_externally",
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


class Cached[**Args, Result](Protocol):
    async def clear_cache(self) -> None: ...

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result: ...


class CachedExternally[**Args, Result, Key: Hashable](Protocol):
    async def clear_cache(
        self,
        key: Key | None = None,
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
) -> Cached[Args, Result]: ...


@overload
def cache[**Args, Result](
    *,
    limit: int | None = None,
    expiration: float | None = None,
) -> Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Cached[Args, Result]]: ...


def cache[**Args, Result](
    function: Callable[Args, Coroutine[Any, Any, Result]] | None = None,
    *,
    limit: int | None = None,
    expiration: float | None = None,
) -> (
    Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Cached[Args, Result]]
    | Cached[Args, Result]
):
    """
    Memoize coroutine results using an in-memory LRU cache.

    The decorator must wrap an `async def` function. When used without arguments
    (``@cache``) the wrapped coroutine is cached in-process with an LRU store whose
    default size is one entry. Providing configuration arguments returns a decorator
    that can be applied later and enables custom cache behaviour in-process.
    For external caches that rely on custom read/write logic, use ``cache_externally``.

    Parameters
    ----------
    function : Callable[Args, Coroutine[Any, Any, Result]] | None
        Coroutine function to memoize when ``cache`` is used directly as ``@cache``.
        Omit this parameter when supplying configuration options so that ``cache``
        returns a decorator.
    limit : int | None
        Maximum number of entries kept by the in-memory cache. Defaults to ``1``.
    expiration : float | None
        Monotonic seconds after which an in-memory entry is considered stale and recomputed.
        ``None`` (the default) disables time-based eviction.

    Returns
    -------
    Callable[[Callable[Args, Coroutine[Any, Any, Result]]], Cached[Args, Result]]
        When used with configuration arguments, returns a decorator that will memoize
        the target coroutine. When ``function`` is supplied positionally, the memoized
        coroutine is returned immediately.

    Notes
    -----
    - Only coroutine functions are supported; synchronous functions will trigger an assertion.
    - The default cache keeps state per decorated function and is not thread-safe.
    - For custom cache backends (e.g., Redis), use :func:`cache_externally`.

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

    For custom external caches, see the :func:`cache_externally` example below.
    """

    def _wrap(
        function: Callable[Args, Coroutine[Any, Any, Result]],
    ) -> Cached[Args, Result]:
        assert iscoroutinefunction(function)  # nosec: B101
        cached = _LocalCache(
            function,
            limit=limit if limit is not None else 1,
            expiration=expiration,
        )
        update_wrapper(cached, function)
        return cached

    if function is not None:
        return _wrap(function)

    else:
        return _wrap


class _CacheEntry[Entry](NamedTuple):
    value: Entry
    expire: float | None


def _default_make_key[**Args](
    *args: Args.args,
    **kwargs: Args.kwargs,
) -> Hashable:
    return _make_key(
        args=args,
        kwds=kwargs,
        typed=True,
    )


class _LocalCache[**Args, Result]:
    def __init__(
        self,
        function: Callable[Args, Coroutine[Any, Any, Result]],
        /,
        limit: int,
        expiration: float | None,
    ) -> None:
        self._function: Callable[Args, Coroutine[Any, Any, Result]] = function
        self._cached: OrderedDict[Hashable, _CacheEntry[Result]] = OrderedDict()
        self._limit: int = limit

        if expiration is not None:

            def next_expire_time() -> float | None:
                return monotonic() + expiration

        else:

            def next_expire_time() -> float | None:
                return None

        self._next_expire_time: Callable[[], float | None] = next_expire_time

    def __get__(
        self,
        instance: object | None,
        owner: type | None = None,
        /,
    ) -> Callable[Args, Coroutine[Any, Any, Result]]:
        assert instance is None, "cache does not work for classes"  # nosec: B101
        return self

    async def clear_cache(self) -> None:
        self._cached.clear()

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        key: Hashable = _default_make_key(
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


def cache_externally[**Args, Result, Key: Hashable](
    *,
    make_key: CacheMakeKey[Args, Key],
    read: CacheRead[Key, Result],
    write: CacheWrite[Key, Result],
    clear: CacheClear[Key] | None = None,
) -> Callable[[Callable[Args, Coroutine[Any, Any, Result]]], CachedExternally[Args, Result, Key]]:
    """
    Memoize coroutine results using a caller-supplied cache backend.

    Provide async callables that implement your cache behaviour. The decorator returns
    a wrapper that preserves the original coroutine signature, reads through the backend
    before executing, and schedules writes via ``ctx.spawn`` to avoid blocking callers.

    Parameters
    ----------
    function : Callable[Args, Coroutine[Any, Any, Result]] | None
        Coroutine to memoize when ``cache_externally`` is used directly as ``@cache_externally``.
        Leave ``None`` to receive a decorator that can be applied later.
    make_key : CacheMakeKey[Args, Key]
        Callable that converts invocation arguments into a hashable key understood by the backend.
    read : CacheRead[Key, Result]
        Async callable that fetches cached values. Return ``None`` to signal a miss.
    write : CacheWrite[Key, Result]
        Async callable that persists values. Invoked in the background via ``ctx.spawn`` after the
        coroutine body resolves.
    clear : CacheClear[Key] | None
        Optional async callable invoked by ``clear_cache`` to evict entries. Omit to disable
        invalidation.

    Returns
    -------
    Callable[[Callable[Args, Coroutine[Any, Any, Result]]], CachedExternally[Args, Result, Key]]
        Decorator that wraps coroutine functions with external cache integration.

    Notes
    -----
    - Only coroutine functions are supported; synchronous callables raise an assertion.
    - Writes are detached tasks; ensure your context stays alive so persistence can finish.
    """

    def _wrap(
        function: Callable[Args, Coroutine[Any, Any, Result]],
    ) -> CachedExternally[Args, Result, Key]:
        assert iscoroutinefunction(function)  # nosec: B101
        cached = _ExternalCache(
            function,
            make_key=make_key,
            read=read,
            write=write,
            clear=clear,
        )
        update_wrapper(cached, function)
        return cached

    return _wrap


class _ExternalCache[**Args, Result, Key: Hashable]:
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

    async def clear_cache(
        self,
        key: Key | None = None,
    ) -> None:
        if self._clear is None:
            return

        await self._clear(key)

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
                ctx.spawn(  # write the value asynchronously
                    self._write,
                    key=key,
                    value=result,
                )

                return result

            case entry:
                return entry
