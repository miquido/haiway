from asyncio import iscoroutinefunction
from collections import OrderedDict
from collections.abc import Callable, Coroutine, Hashable
from functools import _make_key  # pyright: ignore[reportPrivateUsage]
from time import monotonic
from typing import Any, NamedTuple, Protocol, cast, overload

from haiway.context.access import ctx
from haiway.utils.mimic import mimic_function

__all__ = (
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


@overload
def cache[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Result]: ...


@overload
def cache[**Args, Result, Key: Hashable](
    *,
    limit: int | None = None,
    expiration: float | None = None,
    make_key: CacheMakeKey[Args, Key] | None = None,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]]: ...


@overload
def cache[**Args, Result, Key](
    *,
    make_key: CacheMakeKey[Args, Key],
    read: CacheRead[Key, Result],
    write: CacheWrite[Key, Result],
) -> Callable[
    [Callable[Args, Coroutine[Any, Any, Result]]], Callable[Args, Coroutine[Any, Any, Result]]
]: ...


def cache[**Args, Result, Key](
    function: Callable[Args, Result] | None = None,
    *,
    limit: int | None = None,
    expiration: float | None = None,
    make_key: CacheMakeKey[Args, Key] | None = None,
    read: CacheRead[Key, Result] | None = None,
    write: CacheWrite[Key, Result] | None = None,
) -> (
    Callable[
        [Callable[Args, Coroutine[Any, Any, Result]]],
        Callable[Args, Coroutine[Any, Any, Result]],
    ]
    | Callable[[Callable[Args, Result]], Callable[Args, Result]]
    | Callable[Args, Result]
):
    """
    Memoize the result of a function using a configurable cache.

    Parameters
    ----------
    function : Callable[Args, Result] | None
        The function to be memoized.
        When used as a simple decorator (i.e., `@cache`), this is the decorated function.
        Should be omitted when cache is called with configuration arguments.
    limit : int | None
        The maximum number of entries to keep in the cache.
        Defaults to 1 if not specified.
        Ignored when using custom cache implementations (read/write).
    expiration : float | None
        Time in seconds after which a cache entry expires and will be recomputed.
        Defaults to None, meaning entries don't expire based on time.
        Ignored when using custom cache implementations (read/write).
    make_key : CacheMakeKey[Args, Key] | None
        Function to generate a cache key from function arguments.
        If None, uses a default implementation that handles most cases.
        Required when using custom cache implementations (read/write).
    read : CacheRead[Key, Result] | None
        Custom asynchronous function to read values from cache.
        Must be provided together with `write` and `make_key`.
        Only available for async functions.
    write : CacheWrite[Key, Result] | None
        Custom asynchronous function to write values to cache.
        Must be provided together with `read` and `make_key`.
        Only available for async functions.

    Returns
    -------
    Callable
        If `function` is provided as a positional argument, returns the memoized function.
        Otherwise returns a decorator that can be applied to a function to memoize it
        with the given configuration.

    Notes
    -----
    This decorator supports both synchronous and asynchronous functions.
    The default implementation uses a simple in-memory LRU cache.
    For asynchronous functions, you can provide custom cache implementations
    via the `read` and `write` parameters.

    The default cache is not thread-safe and should not be used in multi-threaded
    applications without external synchronization.

    Examples
    --------
    Simple usage as a decorator:

    >>> @cache
    ... def my_function(x: int) -> int:
    ...     print("Function called")
    ...     return x * 2
    >>> my_function(5)
    Function called
    10
    >>> my_function(5)  # Cache hit, function body not executed
    10

    With configuration parameters:

    >>> @cache(limit=10, expiration=60.0)
    ... def my_function(x: int) -> int:
    ...     return x * 2

    With custom cache for async functions:

    >>> @cache(make_key=custom_key_maker, read=redis_read, write=redis_write)
    ... async def fetch_data(user_id: str) -> dict:
    ...     return await api_call(user_id)
    """

    def _wrap(function: Callable[Args, Result]) -> Callable[Args, Result]:
        if iscoroutinefunction(function):
            if read is not None and write is not None and make_key is not None:
                assert limit is None and expiration is None  # nosec: B101
                return cast(
                    Callable[Args, Result],
                    _CustomCache(
                        function,
                        make_key=make_key,
                        read=read,
                        write=write,
                    ),
                )

            else:
                assert read is None and write is None  # nosec: B101
                return cast(
                    Callable[Args, Result],
                    _AsyncCache(
                        function,
                        limit=limit if limit is not None else 1,
                        expiration=expiration,
                        make_key=cast(
                            CacheMakeKey[Args, Hashable],
                            make_key if make_key is not None else _default_make_key,
                        ),
                    ),
                )

        else:
            assert read is None and write is None, "Custom sync cache is not supported"  # nosec: B101
            return cast(
                Callable[Args, Result],
                _SyncCache(
                    function,
                    limit=limit if limit is not None else 1,
                    expiration=expiration,
                    make_key=cast(
                        CacheMakeKey[Args, Hashable],
                        make_key if make_key is not None else _default_make_key,
                    ),
                ),
            )

    if function := function:
        return _wrap(function)

    else:
        return _wrap


class _CacheEntry[Entry](NamedTuple):
    value: Entry
    expire: float | None


class _SyncCache[**Args, Result]:
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
        function: Callable[Args, Result],
        /,
        limit: int,
        expiration: float | None,
        make_key: CacheMakeKey[Args, Hashable],
    ) -> None:
        self._function: Callable[Args, Result] = function
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
    ) -> Callable[Args, Result]:
        assert instance is None, "cache does not work for classes"  # nosec: B101
        return self

    def __call__(
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

        result: Result = self._function(*args, **kwargs)
        self._cached[key] = _CacheEntry(
            value=result,
            expire=self._next_expire_time(),
        )

        if len(self._cached) > self._limit:
            # keep the size limit
            self._cached.popitem(last=False)

        return result


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
    ) -> None:
        self._function: Callable[Args, Coroutine[Any, Any, Result]] = function
        self._make_key: CacheMakeKey[Args, Key] = make_key
        self._read: CacheRead[Key, Result] = read
        self._write: CacheWrite[Key, Result] = write

        # mimic function attributes if able
        mimic_function(function, within=self)

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
