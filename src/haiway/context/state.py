from asyncio import iscoroutinefunction
from collections.abc import Callable, Collection, Coroutine, Iterable, MutableMapping
from contextvars import ContextVar, Token
from threading import Lock
from types import TracebackType
from typing import Any, ClassVar, Self, cast, overload

from haiway.context.types import MissingContext, MissingState
from haiway.state import Immutable, State
from haiway.utils.mimic import mimic_function

__all__ = (
    "ScopeState",
    "StateContext",
)


class ScopeState(Immutable):
    """
    Container for state objects within a scope.

    Stores state objects by their type, allowing retrieval by type.
    Only one state of a given type can be stored at a time.
    This class is immutable after initialization.
    """

    _state: MutableMapping[type[State], State]
    _lock: Lock

    def __init__(
        self,
        state: Iterable[State],
    ) -> None:
        object.__setattr__(
            self,
            "_state",
            {type(element): element for element in state},
        )
        object.__setattr__(
            self,
            "_lock",
            Lock(),
        )

    def check_state[StateType: State](
        self,
        state: type[StateType],
        /,
        *,
        instantiate_defaults: bool = False,
    ) -> bool:
        """
        Check state object availability by its type.

        If the state type is not found, attempts to instantiate a new instance of\
         the type if possible.

        Parameters
        ----------
        state: type[StateType]
            The type of state to check

        instantiate_defaults: bool = False
            Control if default value should be instantiated during check.

        Returns
        -------
        bool
            True if state is available, otherwise False.
        """
        if state in self._state:
            return True

        elif instantiate_defaults:
            with self._lock:
                if state in self._state:
                    return True

                try:
                    initialized: StateType = state()
                    self._state[state] = initialized
                    return True

                except BaseException:
                    return False  # unavailable, we don't care the exception

        else:
            return False

    def state[StateType: State](
        self,
        state: type[StateType],
        /,
        default: StateType | None = None,
    ) -> StateType:
        """
        Get a state object by its type.

        If the state type is not found, attempts to use a provided default
        or instantiate a new instance of the type. Raises MissingState
        if neither is possible.

        Parameters
        ----------
        state: type[StateType]
            The type of state to retrieve
        default: StateType | None
            Optional default value to use if state not found

        Returns
        -------
        StateType
            The requested state object

        Raises
        ------
        MissingState
            If state not found and default not provided or instantiation fails
        """
        if state in self._state:
            return cast(StateType, self._state[state])

        elif default is not None:
            return default

        else:
            with self._lock:
                if state in self._state:
                    return cast(StateType, self._state[state])

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
        """
        Create a new ScopeState with updated state objects.

        Combines the current state with new state objects, with new state
        objects overriding existing ones of the same type.

        Parameters
        ----------
        state: Iterable[State]
            New state objects to add or replace

        Returns
        -------
        Self
            A new ScopeState with the combined state
        """
        # Fast path: if no new state, return self
        state_list = list(state)
        if not state_list:
            return self

        # Optimize: pre-allocate with known size and use dict comprehension
        combined_state = {**self._state, **{type(s): s for s in state_list}}
        return self.__class__(combined_state.values())


class StateContext(Immutable):
    """
    Context manager for state within a scope.

    Manages state propagation and access within a context. Provides
    methods to retrieve state by type and create updated state contexts.
    This class is immutable after initialization.
    """

    _context: ClassVar[ContextVar[ScopeState]] = ContextVar[ScopeState]("StateContext")

    @classmethod
    def current_state(cls) -> Collection[State]:
        """
        Return an immutable snapshot of the current state.

        Returns
        -------
        Collection[State]
            State objects present in the current context,
            or an empty tuple if no context is active.
        """
        try:
            scope_state: ScopeState = cls._context.get()
            with scope_state._lock:
                return tuple(scope_state._state.values())

        except LookupError:
            return ()  # return empty as default

    @classmethod
    def check_state[StateType: State](
        cls,
        state: type[StateType],
        /,
        instantiate_defaults: bool = False,
    ) -> bool:
        """
        Check if state object is available in the current context.

        Verifies if state object of the specified type is available the current context.

        Parameters
        ----------
        state: type[StateType]
            The type of state to check

        instantiate_defaults: bool = False
            Control if default value should be instantiated during check.

        Returns
        -------
        bool
            True if state is available, otherwise False.
        """
        try:
            return cls._context.get().check_state(
                state,
                instantiate_defaults=instantiate_defaults,
            )

        except LookupError:
            return False  # no context no state

    @classmethod
    def state[StateType: State](
        cls,
        state: type[StateType],
        /,
        default: StateType | None = None,
    ) -> StateType:
        """
        Get a state object by type from the current context.

        Retrieves a state object of the specified type from the current context.
        If not found, uses the provided default or attempts to create a new instance.

        Parameters
        ----------
        state: type[StateType]
            The type of state to retrieve
        default: StateType | None
            Optional default value to use if state not found

        Returns
        -------
        StateType
            The requested state object

        Raises
        ------
        MissingContext
            If called outside of a state context
        MissingState
            If state not found and default not provided or instantiation fails
        """
        try:
            return cls._context.get().state(
                state,
                default=default,
            )

        except LookupError as exc:
            raise MissingContext("StateContext requested but not defined!") from exc

    @classmethod
    def updated(
        cls,
        state: Iterable[State],
        /,
    ) -> Self:
        """
        Create a new StateContext with updated state.

        If called within an existing context, inherits and updates that context's state.
        If called outside any context, creates a new root context.

        Parameters
        ----------
        state: Iterable[State]
            New state objects to add or replace

        Returns
        -------
        Self
            A new StateContext with the combined state
        """
        try:
            # update current scope context
            return cls(_state=cls._context.get().updated(state=state))

        except LookupError:  # create root scope when missing
            return cls(_state=ScopeState(state))

    _state: ScopeState
    _token: Token[ScopeState] | None = None

    def __enter__(self) -> None:
        """
        Enter this state context.

        Sets this context's state as the current state in the context.

        Raises
        ------
        AssertionError
            If attempting to re-enter an already active context
        """
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        object.__setattr__(
            self,
            "_token",
            StateContext._context.set(self._state),
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """
        Exit this state context.

        Restores the previous state context.

        Parameters
        ----------
        exc_type: type[BaseException] | None
            Type of exception that caused the exit
        exc_val: BaseException | None
            Exception instance that caused the exit
        exc_tb: TracebackType | None
            Traceback for the exception

        Raises
        ------
        AssertionError
            If the context is not active
        """
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        StateContext._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )

    @overload
    def __call__[Result, **Arguments](
        self,
        function: Callable[Arguments, Coroutine[Any, Any, Result]],
    ) -> Callable[Arguments, Coroutine[Any, Any, Result]]: ...

    @overload
    def __call__[Result, **Arguments](
        self,
        function: Callable[Arguments, Result],
    ) -> Callable[Arguments, Result]: ...

    def __call__[Result, **Arguments](
        self,
        function: Callable[Arguments, Coroutine[Any, Any, Result]] | Callable[Arguments, Result],
    ) -> Callable[Arguments, Coroutine[Any, Any, Result]] | Callable[Arguments, Result]:
        if iscoroutinefunction(function):

            async def async_context(
                *args: Arguments.args,
                **kwargs: Arguments.kwargs,
            ) -> Result:
                with self:
                    return await function(*args, **kwargs)

            return mimic_function(function, within=async_context)

        else:

            def sync_context(
                *args: Arguments.args,
                **kwargs: Arguments.kwargs,
            ) -> Result:
                with self:
                    return function(*args, **kwargs)  # pyright: ignore[reportReturnType]

            return mimic_function(function, within=sync_context)  # pyright: ignore[reportReturnType]
