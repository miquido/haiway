from collections.abc import (
    Collection,
    Iterable,
    Mapping,
    Sequence,
)
from contextvars import ContextVar, Token
from types import TracebackType
from typing import (
    Any,
    ClassVar,
    NoReturn,
    Protocol,
    Self,
    final,
    runtime_checkable,
)

from haiway.attributes import State
from haiway.context.disposables import Disposable, Disposables, DisposableState

__all__ = (
    "ContextPresets",
    "ContextPresetsRegistry",
)


@runtime_checkable
class ContextPresetsStatePreparing(Protocol):
    async def __call__(self) -> Iterable[State] | State: ...


@runtime_checkable
class ContextPresetsDisposablePreparing(Protocol):
    def __call__(self) -> Disposable: ...


@final  # immutable
class ContextPresets:
    """
    Bundle named context disposables into an immutable preset.

    Presets are composable collections of disposable factories that can be resolved
    and then wired into a running context. Immutability is enforced via `@final`
    and attribute guards, so instances are safe to share between scopes and cannot
    be mutated after creation. Resolution happens per scope entry: disposable
    factories are called each time ``resolve()`` is used. State provided via
    ``ContextPresets.of(..., *state)`` is kept as given when it is plain ``State``,
    so a preset carrying only state needs no preparation when a scope is entered
    with it. State produced by an async factory is wrapped once in a
    ``DisposableState`` and prepared on every resolution.

    Examples
    --------
    >>> class ExampleState(State):
    ...     ...
    >>> async def prepare_state() -> ExampleState:
    ...     return ExampleState()
    >>> preset = ContextPresets.of("example", prepare_state)
    >>> disposable = DisposableState.of(prepare_state)
    >>> async with ctx.scope(preset, disposables=(disposable,)):
    ...     _ = ctx.state(ExampleState)
    """

    @classmethod
    def of(
        cls,
        name: str,
        *state: ContextPresetsStatePreparing | State,
        disposables: Collection[ContextPresetsDisposablePreparing] = (),
    ) -> Self:
        """
        Create a preset from state builders and disposable factories.

        Parameters
        ----------
        name:
            Preset name used for registry lookup and identification.
        state:
            State instances or async state factories to be wrapped into a
            `DisposableState` when provided.
        disposables:
            Additional disposable factories to include in the preset.

        Returns
        -------
        Self
            A new immutable `ContextPresets` instance.

        Notes
        -----
        Plain `State` given without any `disposables` is retained as is - such a
        preset has nothing to prepare, so entering a scope with it stays
        synchronous. Otherwise `state` is composed into a `DisposableState` and
        wrapped as a callable factory, so the preset behaves consistently with
        other disposable factories, and async state factories inside `state` are
        run when the preset is resolved for a scope entry. Either way the state
        resolves after the preset disposables, so it keeps precedence over them.
        """
        if state:
            # a preset built only out of plain state has nothing to prepare - keep
            # it aside so entering a scope with it stays synchronous instead of
            # going through the disposables machinery for a no-op
            if not disposables and all(isinstance(element, State) for element in state):
                return cls(
                    name=name,
                    static_state=tuple(element for element in state),  # pyright: ignore
                )

            disposable_state: DisposableState = DisposableState.of(*state)
            return cls(
                name=name,
                # state goes last to take precedence over preset disposables,
                # matching `with_state` and the scope level resolution order
                disposables=(*disposables, lambda: disposable_state),
            )

        else:
            return cls(
                name=name,
                disposables=disposables,
            )

    __slots__ = (
        "_disposables",
        "_static_state",
        "name",
    )

    def __init__(
        self,
        name: str,
        disposables: Collection[ContextPresetsDisposablePreparing] = (),
        static_state: Sequence[State] = (),
    ) -> None:
        self.name: str
        object.__setattr__(
            self,
            "name",
            name,
        )
        self._disposables: Collection[ContextPresetsDisposablePreparing]
        object.__setattr__(
            self,
            "_disposables",
            disposables,
        )
        self._static_state: Sequence[State]
        object.__setattr__(
            self,
            "_static_state",
            static_state,
        )

    def extended(
        self,
        other: Self,
    ) -> Self:
        if not self._disposables and not other._disposables:
            return self.__class__(
                name=self.name,
                static_state=(*self._static_state, *other._static_state),
            )

        return self.__class__(
            name=self.name,
            disposables=(*self._disposables, *self._state_disposables(), *other._disposables),
            static_state=other._static_state,
        )

    def with_state(
        self,
        *state: ContextPresetsStatePreparing | State,
    ) -> Self:
        if not state:
            return self

        if not self._disposables and all(isinstance(element, State) for element in state):
            return self.__class__(
                name=self.name,
                static_state=(*self._static_state, *state),  # pyright: ignore
            )

        disposable_state: DisposableState = DisposableState.of(*state)
        return self.__class__(
            name=self.name,
            disposables=(*self._disposables, *self._state_disposables(), lambda: disposable_state),
        )

    def with_disposables(
        self,
        *disposables: ContextPresetsDisposablePreparing,
    ) -> Self:
        if not disposables:
            return self

        return self.__class__(
            name=self.name,
            disposables=(*self._disposables, *self._state_disposables(), *disposables),
        )

    def _state_disposables(self) -> tuple[ContextPresetsDisposablePreparing, ...]:
        # fold the static state back into the disposables order it would have
        # held, so priority stays the same once a factory is added after it
        if not self._static_state:
            return ()

        disposable_state: DisposableState = DisposableState.of(*self._static_state)
        return (lambda: disposable_state,)

    @property
    def static_state(self) -> Sequence[State]:
        """State this preset carries which needs no preparation."""
        return self._static_state

    def resolve(self) -> Disposables:
        """
        Prepare every element of this preset as disposables.

        State which needs no preparation is wrapped back into a `DisposableState`
        here, so the resulting disposables hold the whole preset. Use
        ``resolve_disposables()`` together with ``static_state`` to skip that
        wrapping when entering a scope, where plain state can be applied directly.
        """
        return Disposables(
            factory() for factory in (*self._disposables, *self._state_disposables())
        )

    def resolve_disposables(self) -> Disposables:
        """
        Prepare only the elements of this preset which have to be prepared.

        The state reported by ``static_state`` is not included - it resolves after
        these disposables, so applying it right after them keeps the priority it
        holds within ``resolve()``.
        """
        return Disposables(factory() for factory in self._disposables)

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__}"
            f" attribute - '{name}' cannot be deleted"
        )


@final  # consider immutable
class ContextPresetsRegistry:
    @classmethod
    def select(
        cls,
        name: str,
        /,
    ) -> ContextPresets | None:
        try:
            return cls._context.get().preset(name)

        except LookupError:
            return None  # no presets

    _context: ClassVar[ContextVar[Self]] = ContextVar("ContextPresetsRegistry")
    __slots__ = (
        "_registry",
        "_token",
    )

    def __init__(
        self,
        presets: Iterable[ContextPresets],
    ) -> None:
        self._registry: Mapping[str, ContextPresets] = {preset.name: preset for preset in presets}
        self._token: Token[ContextPresetsRegistry] | None = None

    def preset(
        self,
        name: str,
        /,
    ) -> ContextPresets | None:
        return self._registry.get(name)

    def __enter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        self._token = ContextPresetsRegistry._context.set(self)

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        ContextPresetsRegistry._context.reset(self._token)
        self._token = None
