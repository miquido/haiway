from __future__ import annotations

from collections.abc import Callable
from typing import Any, Concatenate

from haiway.context.access import ctx
from haiway.state import State

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
        "_method",
        "_name",
    )

    def __init__(
        self,
        method: Callable[Concatenate[Subject, Arguments], Result],
    ) -> None:
        self._method: Callable[Concatenate[Subject, Arguments], Result] = method
        self._name: str | None = None

    def __set_name__(
        self,
        owner: type[Any],
        name: str,
    ) -> None:
        self._name = name

    def __get__(
        self,
        obj: Subject | None,
        owner: type[Subject] | None = None,
    ) -> Callable[Arguments, Result]:
        # Instance access: bind to provided instance
        if obj is not None:

            def bound(
                *args: Arguments.args,
                **kwargs: Arguments.kwargs,
            ) -> Result:
                return self._method(obj, *args, **kwargs)

            # Preserve useful metadata without copying signature
            bound.__name__ = getattr(
                self._method,
                "__name__",
                "bound",
            )
            bound.__doc__ = getattr(
                self._method,
                "__doc__",
                None,
            )
            if hasattr(self._method, "__module__") and self._method.__module__:
                bound.__module__ = self._method.__module__

            bound.__wrapped__ = self._method  # type: ignore[attr-defined]

            return bound

        # Class access: resolve instance from current ctx
        if owner is None:
            name: str = self._name if self._name is not None else "<unknown>"
            raise AttributeError(f"Unbound statemethod access to '{name}' without owner class")

        def bound(
            *args: Arguments.args,
            **kwargs: Arguments.kwargs,
        ) -> Result:
            instance: Subject = ctx.state(owner)
            return self._method(instance, *args, **kwargs)

        # Preserve useful metadata without copying signature
        bound.__name__ = getattr(
            self._method,
            "__name__",
            "bound",
        )
        bound.__doc__ = getattr(
            self._method,
            "__doc__",
            None,
        )
        if hasattr(self._method, "__module__") and self._method.__module__:
            bound.__module__ = self._method.__module__

        bound.__wrapped__ = self._method  # type: ignore[attr-defined]

        return bound
