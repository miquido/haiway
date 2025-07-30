from collections.abc import MutableMapping
from contextvars import ContextVar, Token
from types import TracebackType
from typing import ClassVar, Self, cast, final

from haiway.context.types import MissingContext
from haiway.state import Immutable, State
from haiway.types import Default

__all__ = ("VariablesContext",)


@final
class Variables(Immutable):
    _parent: Self | None
    _values: MutableMapping[type[State], State] = Default(factory=dict)

    def get[Variable: State](
        self,
        variable: type[Variable],
        /,
    ) -> Variable | None:
        return cast(Variable | None, self._values.get(variable))

    def set(
        self,
        variable: State,
        /,
    ) -> None:
        self._values[type(variable)] = variable

    def propagate(self) -> None:
        if self._parent is None:
            return  # nothing to do

        self._parent._values.update(self._values)


@final
class VariablesContext(Immutable):
    _context: ClassVar[ContextVar[Variables]] = ContextVar[Variables]("VariablesContext")

    @classmethod
    def get[Variable: State](
        cls,
        variable: type[Variable],
        /,
    ) -> Variable | None:
        try:
            return cls._context.get().get(variable)

        except LookupError as exc:
            raise MissingContext("VariablesContext requested but not defined!") from exc

    @classmethod
    def set(
        cls,
        variable: State,
        /,
    ) -> None:
        try:
            cls._context.get().set(variable)

        except LookupError as exc:
            raise MissingContext("VariablesContext requested but not defined!") from exc

    isolated: bool
    _variables: Variables | None = None
    _token: Token[Variables] | None = None

    def __enter__(self) -> None:
        assert self._token is None, "Context reentrance is not allowed"  # nosec: B101
        try:
            object.__setattr__(
                self,
                "_variables",
                Variables(_parent=VariablesContext._context.get() if not self.isolated else None),
            )

        except LookupError:
            object.__setattr__(
                self,
                "_variables",
                Variables(_parent=None),
            )

        assert self._variables is not None  # nosec: B101
        object.__setattr__(
            self,
            "_token",
            VariablesContext._context.set(self._variables),
        )

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        assert self._token is not None, "Unbalanced context enter/exit"  # nosec: B101
        assert self._variables is not None  # nosec: B101

        VariablesContext._context.reset(self._token)
        object.__setattr__(
            self,
            "_token",
            None,
        )
        if not self.isolated:
            self._variables.propagate()

        object.__setattr__(
            self,
            "_variables",
            None,
        )
