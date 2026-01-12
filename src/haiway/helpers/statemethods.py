from collections.abc import Callable, MutableMapping
from functools import update_wrapper
from typing import Any, Concatenate

from haiway.attributes import State
from haiway.context.access import ctx

__all__ = ("statemethod",)


class statemethod[Subject: State, **Arguments, Result]:
    """
    Descriptor that always calls the wrapped function with an instance of the State.

    - When accessed on an instance, it passes that instance directly.
    - When accessed on the class, it resolves an instance from the current context
      via `ctx.state(cls)` and passes it.

    The wrapped function must be defined with the instance as the first parameter:

    >>> from haiway import ctx, statemethod, State
    >>>
    >>> class Example(State):
    ...     do: Callable[[], str]
    ...
    ...     @statemethod
    ...     def do_stuff(self) -> str:  # self is always an Example instance
    ...         return self.do()
    >>>
    >>> # Called on the class: resolves instance from context
    ... async with ctx.scope("ex", Example(do=lambda: "ok")):
    ...     _ = Example.do_stuff()
    >>>
    >>> # Called on an instance: uses that instance
    >>> inst = Example(do=lambda: "ok")
    >>> _ = inst.do_stuff()
    """

    __slots__ = (
        "_class_cache",
        "_method",
        "_name",
    )

    def __init__(
        self,
        method: Callable[Concatenate[Subject, Arguments], Result],
    ) -> None:
        self._name: str | None = None
        self._method: Callable[Concatenate[Subject, Arguments], Result] = method
        self._class_cache: MutableMapping[type[State], Callable[Arguments, Result]] = {}

    def _bind_class_method(
        self,
        owner: type[Subject],
    ) -> Callable[Arguments, Result]:
        assert issubclass(owner, State)  # nosec: B101

        def class_method(
            *args: Arguments.args,
            **kwargs: Arguments.kwargs,
        ) -> Result:
            return self._method(ctx.state(owner), *args, **kwargs)

        update_wrapper(class_method, self._method)
        return class_method

    def __set_name__(
        self,
        owner: type[Any],
        name: str,
    ) -> None:
        self._name = name
        self._class_cache[owner] = self._bind_class_method(owner)

    def __get__(
        self,
        obj: Subject | None,
        owner: type[Subject] | None = None,
    ) -> Callable[Arguments, Result]:
        if obj is not None:
            # Instance access
            return self._method.__get__(obj, owner)

        assert owner is not None  # nosec: B101

        # Class access: resolve instance from current ctx
        cached: Callable[Arguments, Result] | None = self._class_cache.get(owner)
        if cached is not None:
            return cached

        # Bind classmethod and cache
        class_method = self._bind_class_method(owner)
        self._class_cache[owner] = class_method
        return class_method
