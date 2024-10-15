from collections.abc import Iterable
from contextvars import ContextVar, Token
from types import TracebackType
from typing import Self, cast, final

from haiway.context.types import MissingContext, MissingState
from haiway.state import Structure
from haiway.utils import freeze

__all__ = [
    "ScopeState",
    "StateContext",
]


@final
class ScopeState:
    def __init__(
        self,
        state: Iterable[Structure],
    ) -> None:
        self._state: dict[type[Structure], Structure] = {
            type(element): element for element in state
        }
        freeze(self)

    def state[State: Structure](
        self,
        state: type[State],
        /,
        default: State | None = None,
    ) -> State:
        if state in self._state:
            return cast(State, self._state[state])

        elif default is not None:
            return default

        else:
            try:
                initialized: State = state()
                self._state[state] = initialized
                return initialized

            except Exception as exc:
                raise MissingState(
                    f"{state.__qualname__} is not defined in current scope"
                    " and failed to provide a default value"
                ) from exc

    def updated(
        self,
        state: Iterable[Structure],
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
    def current[State: Structure](
        cls,
        state: type[State],
        /,
        default: State | None = None,
    ) -> State:
        try:
            return cls._context.get().state(state, default=default)

        except LookupError as exc:
            raise MissingContext("StateContext requested but not defined!") from exc

    @classmethod
    def updated(
        cls,
        state: Iterable[Structure],
        /,
    ) -> Self:
        try:
            return cls(state=cls._context.get().updated(state=state))

        except LookupError:  # create new context as a fallback
            return cls(state=ScopeState(state))

    def __init__(
        self,
        state: ScopeState,
    ) -> None:
        self._state: ScopeState = state
        self._token: Token[ScopeState] | None = None

    def __enter__(self) -> None:
        assert self._token is None, "StateContext reentrance is not allowed"  # nosec: B101
        self._token = StateContext._context.set(self._state)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced StateContext context exit"  # nosec: B101
        StateContext._context.reset(self._token)
        self._token = None
