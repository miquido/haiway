from collections.abc import Collection, Iterable, Mapping, MutableMapping
from contextvars import ContextVar, Token
from threading import Lock
from types import TracebackType
from typing import ClassVar, Self, cast, final

from haiway.attributes import State
from haiway.context.types import ContextMissing, ContextStateMissing

__all__ = ("ContextState",)


@final  # consider immutable
class ContextState:
    @classmethod
    def snapshot(cls) -> Collection[State]:
        try:
            return cls._context.get()._state.values()

        except LookupError:
            return ()  # return empty as default

    @classmethod
    def contains[StateType: State](
        cls,
        state: type[StateType],
        /,
    ) -> bool:
        try:
            return state in cls._context.get()._state

        except LookupError:
            return False  # no context no state

    @classmethod
    def state[StateType: State](
        cls,
        state: type[StateType],
        /,
        default: StateType | None = None,
    ) -> StateType:
        try:
            current: Self = cls._context.get()
            if state in current._state:
                return cast(StateType, current._state[state])

            if default is not None:
                return default  # do not store default

            with current._lock:
                if state in current._state:
                    return cast(StateType, current._state[state])

                try:
                    initialized: StateType = state()
                    current._state[state] = initialized
                    return initialized

                except Exception as exc:
                    raise ContextStateMissing(
                        f"{state.__qualname__} is not defined in current scope"
                        " and failed to provide a default value"
                    ) from exc

        except LookupError:
            if default is not None:
                return default

            raise ContextMissing("ContextState requested but not defined!") from None

    @classmethod
    def updated(
        cls,
        state: Iterable[State | None],
        /,
    ) -> Self:
        try:  # update current scope context
            current: Self = cls._context.get()
            resolved: Mapping[type[State], State] = {
                type(element): element for element in state if element is not None
            }

            updated: Self = object.__new__(cls)  # always provide a copy
            updated._state = {**current._state, **resolved}
            updated._lock = Lock()
            updated._token = None
            return updated

        except LookupError:  # or create root scope when missing
            return cls(state=state)

    _context: ClassVar[ContextVar[Self]] = ContextVar[Self]("ContextState")
    __slots__ = (
        "_lock",
        "_state",
        "_token",
    )

    def __init__(
        self,
        state: Iterable[State | None],
    ) -> None:
        self._state: MutableMapping[type[State], State] = {
            type(element): element for element in state if element is not None
        }
        self._lock: Lock = Lock()
        self._token: Token[ContextState] | None = None

    def __enter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        self._token = ContextState._context.set(self)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ContextState._context.reset(self._token)
        self._token = None
