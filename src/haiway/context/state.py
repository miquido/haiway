from collections.abc import Iterable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Self, cast, final

from haiway.context.types import MissingContext, MissingState
from haiway.state import State
from haiway.utils import freeze

__all__ = [
    "ScopeState",
    "StateContext",
]


@final
class ScopeState:
    def __init__(
        self,
        state: Iterable[State],
    ) -> None:
        self._state: dict[type[State], State] = {type(element): element for element in state}
        freeze(self)

    def state[StateType: State](
        self,
        state: type[StateType],
        /,
        default: StateType | None = None,
    ) -> StateType:
        if state in self._state:
            return cast(StateType, self._state[state])

        elif default is not None:
            return default

        else:
            try:
                initialized: StateType = state()
                self._state[state] = initialized
                return initialized

            except Exception as exc:
                raise MissingState(
                    f"{state.__qualname__} is not defined in current scope"
                    " and failed to provide a default value"
                ) from exc

    def updated(
        self,
        state: Iterable[State],
    ) -> Self:
        if state:
            return self.__class__(
                [
                    *self._state.values(),
                    *state,
                ]
            )

        else:
            return self


@final
class StateContext:
    _context = ContextVar[ScopeState]("StateContext")

    @classmethod
    def current[StateType: State](
        cls,
        state: type[StateType],
        /,
        default: StateType | None = None,
    ) -> StateType:
        try:
            return cls._context.get().state(state, default=default)

        except LookupError as exc:
            raise MissingContext("StateContext requested but not defined!") from exc

    @classmethod
    def updated(
        cls,
        state: Iterable[State],
        /,
    ) -> Self:
        try:
            # update current scope context
            return cls(state=cls._context.get().updated(state=state))

        except LookupError:  # create root scope when missing
            return cls(state=ScopeState(state))

    def __init__(
        self,
        state: ScopeState,
    ) -> None:
        self._state: ScopeState = state

    def __enter__(self) -> None:
        assert not hasattr(self, "_token"), "Context reentrance is not allowed"  # nosec: B101
        self._token: Token[ScopeState] = StateContext._context.set(self._state)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert hasattr(self, "_token"), "Unbalanced context enter/exit"  # nosec: B101
        StateContext._context.reset(self._token)
        del self._token
