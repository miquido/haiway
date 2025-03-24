from asyncio import Task, get_running_loop, iscoroutinefunction, shield
from collections import OrderedDict
from collections.abc import Callable, Coroutine, Hashable
from functools import _make_key, partial  # pyright: ignore[reportPrivateUsage]
from time import monotonic
from typing import NamedTuple, cast, overload
from weakref import ref

from haiway.utils.mimic import mimic_function

__all__ = [
    "cache",
]


@overload
def cache[**Args, Result](
    function: Callable[Args, Result],
    /,
) -> Callable[Args, Result]: ...


@overload
def cache[**Args, Result, Key: Hashable](
    *,
    limit: int = 1,
    expiration: float | None = None,
    prepare_key: Callable[Args, Key] | None = None,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]]: ...


def cache[**Args, Result, Key](
    function: Callable[Args, Result] | None = None,
    *,
    limit: int = 1,
    expiration: float | None = None,
    prepare_key: Callable[Args, Key] | None = None,
) -> Callable[[Callable[Args, Result]], Callable[Args, Result]] | Callable[Args, Result]:
    """
    Memoize the result of a function using a simple cache.

    This function wrapper caches the result of a function call based on its arguments.
    It supports both synchronous and asynchronous functions.
    The cache can be configured with a size limit and an expiration time for entries.
    Default implementation uses simple lru, in memory cache.
    It is important to note that this cache is not thread-safe and should not be used
    in multi-threaded applications without external synchronization.

    Parameters
    ----------
    function : Callable[Args, Result]
        The function to be memoized.
        When `cache` is used as a simple decorator (i.e., `@cache` without arguments),
        this is the function being decorated.
        If `cache` is called as a function with configuration arguments, this parameter
        should be omitted (use only keyword arguments in that case).
    limit : int
        The maximum number of entries to keep in the default cache.
        Defaults is 1.
    expiration : float | None
        The time in seconds after which a cache entry expires and will be re-computed.
        Defaults to `None`, meaning entries do not expire with time passing.
    prepare_key : Callable[Args, Key] | None, optional
        A function that takes the same arguments as the memoized function and returns
        a cache key. This is useful when the function arguments are not directly hashable
        or when you want to customize the cache key generation.
        Defaults to `None`, in which case the arguments themselves are used as the cache key
        (assuming they are hashable).

    Returns
    -------
    Callable[Args, Result]
        If `function` is provided as a positional argument, returns the memoized function.
    Callable[[Callable[Args, Result]], Callable[Args, Result]]
        If `function` is not provided (i.e., `cache` is called with configuration arguments),
        returns a decorator that can be applied to a function to memoize it with the given configuration.

    Examples
    --------
    Simple usage as a decorator:

    >>> @cache
    ... def my_function(x: int):
    ...     print("Function called")
    ...     return x * 2
    >>> my_function(5)
    Function called
    10
    >>> my_function(5) # Cache hit, "Function called" is not printed
    10
    """  # noqa: E501

    def _wrap(function: Callable[Args, Result]) -> Callable[Args, Result]:
        def default_key_preparation(
            *args: Args.args,
            **kwargs: Args.kwargs,
        ) -> Key:
            return cast(
                Key,
                _make_key(
                    args=args,
                    kwds=kwargs,
                    typed=True,
                ),
            )

        if iscoroutinefunction(function):
            return cast(
                Callable[Args, Result],
                _AsyncCache(
                    function,
                    limit=limit,
                    expiration=expiration,
                    prepare_key=prepare_key or default_key_preparation,
                ),
            )

        else:
            return cast(
                Callable[Args, Result],
                _SyncCache(
                    function,
                    limit=limit,
                    expiration=expiration,
                    prepare_key=prepare_key or default_key_preparation,
                ),
            )

    if function := function:
        return _wrap(function)

    else:
        return _wrap


class _CacheEntry[Entry](NamedTuple):
    value: Entry
    expire: float | None


class _SyncCache[**Args, Result, Key: Hashable]:
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
        "_next_expire_time",
        "_prepare_key",
    )

    def __init__(
        self,
        function: Callable[Args, Result],
        /,
        limit: int,
        expiration: float | None,
        prepare_key: Callable[Args, Key],
    ) -> None:
        self._function: Callable[Args, Result] = function
        self._cached: OrderedDict[Hashable, _CacheEntry[Result]] = OrderedDict()
        self._limit: int = limit
        self._prepare_key: Callable[Args, Key] = prepare_key

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
        if owner is None or instance is None:
            return self

        else:
            return mimic_function(
                self._function,
                within=partial(
                    self.__method_call__,
                    instance,
                ),
            )

    def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        key: Hashable = self._prepare_key(
            *args,
            **kwargs,
        )

        match self._cached.get(key):
            case None:
                pass

            case entry:
                if (expire := entry[1]) and expire < monotonic():
                    # if still running let it complete if able
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
            # if still running let it complete if able
            self._cached.popitem(last=False)

        return result

    def __method_call__(
        self,
        __method_self: object,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        key: Hashable = self._prepare_key(
            *(ref(__method_self), *args),  # pyright: ignore[reportCallIssue]
            **kwargs,
        )

        match self._cached.get(key):
            case None:
                pass

            case entry:
                if (expire := entry[1]) and expire < monotonic():
                    # if still running let it complete if able
                    del self._cached[key]  # continue the same way as if empty

                else:
                    self._cached.move_to_end(key)
                    return entry[0]

        result: Result = self._function(__method_self, *args, **kwargs)  # pyright: ignore[reportUnknownVariableType, reportCallIssue]
        self._cached[key] = _CacheEntry(
            value=result,  # pyright: ignore[reportUnknownArgumentType]
            expire=self._next_expire_time(),
        )
        if len(self._cached) > self._limit:
            # if still running let it complete if able
            self._cached.popitem(last=False)

        return result  # pyright: ignore[reportUnknownArgumentType, reportUnknownVariableType]


class _AsyncCache[**Args, Result, Key]:
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
        "_next_expire_time",
        "_prepare_key",
    )

    def __init__(
        self,
        function: Callable[Args, Coroutine[None, None, Result]],
        /,
        limit: int,
        expiration: float | None,
        prepare_key: Callable[Args, Key],
    ) -> None:
        self._function: Callable[Args, Coroutine[None, None, Result]] = function
        self._cached: OrderedDict[Hashable, _CacheEntry[Task[Result]]] = OrderedDict()
        self._limit: int = limit
        self._prepare_key: Callable[Args, Key] = prepare_key

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
    ) -> Callable[Args, Coroutine[None, None, Result]]:
        if owner is None or instance is None:
            return self

        else:
            return mimic_function(
                self._function,
                within=partial(
                    self.__method_call__,
                    instance,
                ),
            )

    async def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        key: Hashable = self._prepare_key(
            *args,
            **kwargs,
        )

        match self._cached.get(key):
            case None:
                pass

            case entry:
                if (expire := entry[1]) and expire < monotonic():
                    # if still running let it complete if able
                    del self._cached[key]  # continue the same way as if empty

                else:
                    self._cached.move_to_end(key)
                    return await shield(entry[0])

        task: Task[Result] = get_running_loop().create_task(self._function(*args, **kwargs))  # pyright: ignore[reportCallIssue]
        self._cached[key] = _CacheEntry(
            value=task,
            expire=self._next_expire_time(),
        )
        if len(self._cached) > self._limit:
            # if still running let it complete if able
            self._cached.popitem(last=False)

        return await shield(task)

    async def __method_call__(
        self,
        __method_self: object,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        key: Hashable = self._prepare_key(
            *(ref(__method_self), *args),  # pyright: ignore[reportCallIssue]
            **kwargs,
        )

        match self._cached.get(key):
            case None:
                pass

            case entry:
                if (expire := entry[1]) and expire < monotonic():
                    # if still running let it complete if able
                    del self._cached[key]  # continue the same way as if empty

                else:
                    self._cached.move_to_end(key)
                    return await shield(entry[0])

        task: Task[Result] = get_running_loop().create_task(
            self._function(__method_self, *args, **kwargs),  # pyright: ignore[reportCallIssue, reportUnknownArgumentType]
        )
        self._cached[key] = _CacheEntry(
            value=task,
            expire=self._next_expire_time(),
        )

        if len(self._cached) > self._limit:
            # if still running let it complete if able
            self._cached.popitem(last=False)

        return await shield(task)
