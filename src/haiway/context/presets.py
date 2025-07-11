from collections.abc import Collection, Iterable, Mapping
from contextvars import ContextVar, Token
from types import TracebackType
from typing import ClassVar, Protocol, Self, cast

from haiway.context.disposables import Disposable, Disposables
from haiway.state import Immutable, State
from haiway.types.default import Default

__all__ = (
    "ContextPresets",
    "ContextPresetsRegistry",
    "ContextPresetsRegistryContext",
)


class ContextPresetsStatePreparing(Protocol):
    async def __call__(self) -> Iterable[State] | State: ...


class ContextPresetsDisposablesPreparing(Protocol):
    async def __call__(self) -> Iterable[Disposable] | Disposable: ...


class ContextPresets(Immutable):
    """
    A configuration preset for context scopes.

    ContextPresets allow you to define reusable combinations of state and disposables
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
    >>> from haiway.context import ContextPresets
    >>>
    >>> class DatabaseConfig(State):
    ...     connection_string: str
    ...     pool_size: int = 10
    >>>
    >>> db_preset = ContextPresets(
    ...     name="database",
    ...     _state=[DatabaseConfig(connection_string="postgresql://localhost/app")]
    ... )

    Preset with dynamic state factory:

    >>> async def load_config() -> DatabaseConfig:
    ...     # Load configuration from environment or config file
    ...     return DatabaseConfig(connection_string=os.getenv("DB_URL"))
    >>>
    >>> dynamic_preset = ContextPresets(
    ...     name="dynamic_db",
    ...     _state=[load_config]
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
    >>> db_preset = ContextPresets(
    ...     name="database",
    ...     _state=[DatabaseConfig(connection_string="...")],
    ...     _disposables=[connection_factory]
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
    _state: Collection[ContextPresetsStatePreparing | State] = Default(())
    _disposables: Collection[ContextPresetsDisposablesPreparing] = Default(())

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
            Another ContextPresets instance to merge with this one.

        Returns
        -------
        Self
            A new ContextPresets instance with combined state and disposables.
        """
        return self.__class__(
            name=self.name,
            _state=(*self._state, *other._state),
            _disposables=(*self._disposables, *other._disposables),
        )

    def with_state(
        self,
        *state: ContextPresetsStatePreparing | State,
    ) -> Self:
        """
        Create a new preset with additional state.

        Returns a new ContextPresets instance with the provided state objects
        or state factories added to the existing state collection.

        Parameters
        ----------
        *state : ContextPresetsStatePreparing | State
            Additional state objects or state factory functions to include.

        Returns
        -------
        Self
            A new ContextPresets instance with the additional state, or the
            same instance if no state was provided.
        """
        if not state:
            return self

        return self.__class__(
            name=self.name,
            _state=(*self._state, *state),
            _disposables=self._disposables,
        )

    def with_disposable(
        self,
        *disposable: ContextPresetsDisposablesPreparing,
    ) -> Self:
        """
        Create a new preset with additional disposables.

        Returns a new ContextPresets instance with the provided disposable
        factory functions added to the existing disposables collection.

        Parameters
        ----------
        *disposable : ContextPresetsDisposablesPreparing
            Additional disposable factory functions to include.

        Returns
        -------
        Self
            A new ContextPresets instance with the additional disposables, or the
            same instance if no disposables were provided.
        """
        if not disposable:
            return self

        return self.__class__(
            name=self.name,
            _state=self._state,
            _disposables=(*self._disposables, *disposable),
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
        # Collect states directly
        collected_states: list[State] = []
        for state in self._state:
            if isinstance(state, State):
                collected_states.append(state)
            else:
                resolved_state: Iterable[State] | State = await state()
                if isinstance(resolved_state, State):
                    collected_states.append(resolved_state)

                else:
                    collected_states.extend(resolved_state)

        collected_disposables: list[Disposable]
        if collected_states:
            collected_disposables = [DisposableState(_state=collected_states)]

        else:
            collected_disposables = []

        for disposable in self._disposables:
            resolved_disposable: Iterable[Disposable] | Disposable = await disposable()
            if hasattr(resolved_disposable, "__aenter__") and hasattr(
                resolved_disposable, "__aexit__"
            ):
                collected_disposables.append(cast(Disposable, resolved_disposable))

            else:
                collected_disposables.extend(cast(Iterable[Disposable], resolved_disposable))

        return Disposables(*collected_disposables)


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


class ContextPresetsRegistry(Immutable):
    _presets: Mapping[str, ContextPresets]

    def __init__(
        self,
        presets: Collection[ContextPresets],
    ) -> None:
        object.__setattr__(
            self,
            "_presets",
            {preset.name: preset for preset in presets},
        )

    def select(
        self,
        name: str,
        /,
    ) -> ContextPresets | None:
        return self._presets.get(name)


class ContextPresetsRegistryContext(Immutable):
    _context: ClassVar[ContextVar[ContextPresetsRegistry]] = ContextVar[ContextPresetsRegistry](
        "ContextPresetsRegistryContext"
    )

    @classmethod
    def select(
        cls,
        name: str,
        /,
    ) -> ContextPresets | None:
        try:
            return cls._context.get().select(name)

        except LookupError:
            return None  # no presets

    _registry: ContextPresetsRegistry
    _token: Token[ContextPresetsRegistry] | None = None

    def __init__(
        self,
        registry: ContextPresetsRegistry,
    ) -> None:
        object.__setattr__(
            self,
            "_registry",
            registry,
        )
        object.__setattr__(
            self,
            "_token",
            None,
        )

    def __enter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        object.__setattr__(
            self,
            "_token",
            ContextPresetsRegistryContext._context.set(self._registry),
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ContextPresetsRegistryContext._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )
