from asyncio import gather
from collections.abc import Collection, Iterable, Mapping
from contextvars import ContextVar, Token
from types import TracebackType
from typing import ClassVar, Protocol, Self, cast

from haiway.context.disposables import Disposable, Disposables
from haiway.context.state import StateContext
from haiway.state import Immutable, State

__all__ = (
    "ContextPreset",
    "ContextPresetRegistryContext",
)


class ContextPresetStatePreparing(Protocol):
    async def __call__(self) -> Iterable[State] | State: ...


class ContextPresetDisposablesPreparing(Protocol):
    async def __call__(self) -> Iterable[Disposable] | Disposable: ...


class ContextPreset(Immutable):
    """
    A configuration preset for context scopes.

    ContextPreset allows you to define reusable combinations of state and disposables
    that can be applied to scopes by name. This provides a convenient way to manage
    complex application configurations and resource setups.

    State Priority
    --------------
    When used with ctx.scope(), preset state has lower priority than explicit state:
    1. Explicit state (passed to ctx.scope()) - **highest priority**
    2. Explicit disposables (passed to ctx.scope()) - medium priority
    3. Preset state (from presets) - low priority
    4. Contextual state (from parent contexts) - **lowest priority**

    Examples
    --------
    Basic preset with static state:

    >>> from haiway import State
    >>> from haiway.context import ContextPreset
    >>>
    >>> class DatabaseConfig(State):
    ...     connection_string: str
    ...     pool_size: int = 10
    >>>
    >>> db_preset = ContextPreset(
    ...     name="database",
    ...     state=[DatabaseConfig(connection_string="postgresql://localhost/app")]
    ... )

    Preset with dynamic state factory:

    >>> async def load_config() -> DatabaseConfig:
    ...     # Load configuration from environment or config file
    ...     return DatabaseConfig(connection_string=os.getenv("DB_URL"))
    >>>
    >>> dynamic_preset = ContextPreset(
    ...     name="dynamic_db",
    ...     state=[load_config]
    ... )

    Preset with disposables:

    >>> from contextlib import asynccontextmanager
    >>>
    >>> @asynccontextmanager
    >>> async def database_connection():
    ...     conn = await create_connection()
    ...     try:
    ...         yield ConnectionState(connection=conn)
    ...     finally:
    ...         await conn.close()
    >>>
    >>> async def connection_factory():
    ...     return database_connection()
    >>>
    >>> db_preset = ContextPreset(
    ...     name="database",
    ...     state=[DatabaseConfig(connection_string="...")],
    ...     disposables=[connection_factory]
    ... )

    Using presets:

    >>> from haiway import ctx
    >>>
    >>> with ctx.presets(db_preset):
    ...     async with ctx.scope("database"):
    ...         config = ctx.state(DatabaseConfig)
    ...         # Use the preset configuration
    """

    name: str
    _state: Collection[ContextPresetStatePreparing | State]
    _disposables: Collection[ContextPresetDisposablesPreparing]

    def __init__(
        self,
        name: str,
        *,
        state: Collection[ContextPresetStatePreparing | State] = (),
        disposables: Collection[ContextPresetDisposablesPreparing] = (),
    ) -> None:
        object.__setattr__(
            self,
            "name",
            name,
        )
        object.__setattr__(
            self,
            "_state",
            state,
        )
        object.__setattr__(
            self,
            "_disposables",
            disposables,
        )

    def extended(
        self,
        other: Self,
    ) -> Self:
        """
        Create a new preset by extending this preset with another.

        Combines the state and disposables from both presets, keeping the name
        of the current preset. The other preset's state and disposables are
        appended to this preset's collections.

        Parameters
        ----------
        other : Self
            Another ContextPreset instance to merge with this one.

        Returns
        -------
        Self
            A new ContextPreset instance with combined state and disposables.
        """
        return self.__class__(
            name=self.name,
            state=(*self._state, *other._state),
            disposables=(*self._disposables, *other._disposables),
        )

    def with_state(
        self,
        *state: ContextPresetStatePreparing | State,
    ) -> Self:
        """
        Create a new preset with additional state.

        Returns a new ContextPreset instance with the provided state objects
        or state factories added to the existing state collection.

        Parameters
        ----------
        *state : ContextPresetStatePreparing | State
            Additional state objects or state factory functions to include.

        Returns
        -------
        Self
            A new ContextPreset instance with the additional state, or the
            same instance if no state was provided.
        """
        if not state:
            return self

        return self.__class__(
            name=self.name,
            state=(*self._state, *state),
            disposables=self._disposables,
        )

    def with_disposable(
        self,
        *disposable: ContextPresetDisposablesPreparing,
    ) -> Self:
        """
        Create a new preset with additional disposables.

        Returns a new ContextPreset instance with the provided disposable
        factory functions added to the existing disposables collection.

        Parameters
        ----------
        *disposable : ContextPresetDisposablesPreparing
            Additional disposable factory functions to include.

        Returns
        -------
        Self
            A new ContextPreset instance with the additional disposables, or the
            same instance if no disposables were provided.
        """
        if not disposable:
            return self

        return self.__class__(
            name=self.name,
            state=self._state,
            disposables=(*self._disposables, *disposable),
        )

    async def prepare(self) -> Disposables:
        """
        Prepare the preset for use by resolving all state and disposables.

        This method evaluates all state factories and disposable factories to create
        concrete instances. State objects are wrapped in a DisposableState to unify
        the handling of state and disposable resources.

        The method ensures concurrent safety by creating fresh instances each time
        it's called, making it safe to use the same preset across multiple concurrent
        scopes.

        Returns
        -------
        Disposables
            A Disposables container holding all resolved state (wrapped in DisposableState)
            and disposable resources from this preset.

        Note
        ----
        This method is called automatically when using presets with ctx.scope(),
        so you typically don't need to call it directly.
        """
        collected_state: Collection[State] = await self._collect_state()

        collected_disposables: Collection[Disposable]
        if collected_state:
            # use available state immediately when preparing disposables
            with StateContext.updated(collected_state):
                collected_disposables = (
                    DisposableState(_state=collected_state),
                    *await self._collect_disposables(),
                )

        else:
            collected_disposables = await self._collect_disposables()

        return Disposables(*collected_disposables)

    async def _collect_state(self) -> Collection[State]:
        collected_state: list[State] = []
        for state in self._state:
            if isinstance(state, State):
                collected_state.append(state)

            else:
                resolved_state: Iterable[State] | State = await state()
                if isinstance(resolved_state, State):
                    collected_state.append(resolved_state)

                else:
                    collected_state.extend(resolved_state)

        return collected_state

    async def _collect_disposables(self) -> Collection[Disposable]:
        collected_disposables: list[Disposable] = []
        for disposable in await gather(*(factory() for factory in self._disposables)):
            if hasattr(disposable, "__aenter__") and hasattr(disposable, "__aexit__"):
                collected_disposables.append(cast(Disposable, disposable))

            else:
                collected_disposables.extend(cast(Iterable[Disposable], disposable))

        return collected_disposables


class DisposableState(Immutable):
    _state: Iterable[State]

    async def __aenter__(self) -> Iterable[State]:
        return self._state

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass


class ContextPresetRegistryContext(Immutable):
    _context: ClassVar[ContextVar[Self]] = ContextVar[Self]("ContextPresetRegistryContext")

    @classmethod
    def select(
        cls,
        name: str,
        /,
    ) -> ContextPreset | None:
        try:
            return cls._context.get().preset(name)

        except LookupError:
            return None  # no presets

    _registry: Mapping[str, ContextPreset]
    _token: Token[Self] | None = None

    def __init__(
        self,
        presets: Iterable[ContextPreset],
    ) -> None:
        object.__setattr__(
            self,
            "_registry",
            {preset.name: preset for preset in presets},
        )
        object.__setattr__(
            self,
            "_token",
            None,
        )

    def preset(
        self,
        name: str,
        /,
    ) -> ContextPreset | None:
        return self._registry.get(name)

    def __enter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        object.__setattr__(
            self,
            "_token",
            ContextPresetRegistryContext._context.set(self),
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ContextPresetRegistryContext._context.reset(self._token)  # pyright: ignore[reportArgumentType]
        object.__setattr__(
            self,
            "_token",
            None,
        )
