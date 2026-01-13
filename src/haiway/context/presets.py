from collections.abc import (
    Collection,
    Iterable,
    Mapping,
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
    be mutated after creation.

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
        When `state` is provided, it is composed into a `DisposableState` and
        wrapped as a callable factory, so the preset behaves consistently with
        other disposable factories.
        """
        if state:
            disposable_state: DisposableState = DisposableState.of(*state)
            return cls(
                name=name,
                disposables=(lambda: disposable_state, *disposables),
            )

        else:
            return cls(
                name=name,
                disposables=disposables,
            )

    __slots__ = (
        "_disposables",
        "name",
    )

    def __init__(
        self,
        name: str,
        disposables: Collection[ContextPresetsDisposablePreparing] = (),
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

    def extended(
        self,
        other: Self,
    ) -> Self:
        return self.__class__(
            name=self.name,
            disposables=(*self._disposables, *other._disposables),
        )

    def with_state(
        self,
        *state: ContextPresetsStatePreparing | State,
    ) -> Self:
        if not state:
            return self

        disposable_state: DisposableState = DisposableState.of(*state)
        return self.__class__(
            name=self.name,
            disposables=(*self._disposables, lambda: disposable_state),
        )

    def with_disposables(
        self,
        *disposables: ContextPresetsDisposablePreparing,
    ) -> Self:
        if not disposables:
            return self

        return self.__class__(
            name=self.name,
            disposables=(*self._disposables, *disposables),
        )

    def resolve(self) -> Disposables:
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
