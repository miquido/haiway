import gc
import json
import sys
from collections.abc import Callable, Generator, Mapping, Sequence
from typing import Any, NotRequired, Required, Self, TypedDict

import pytest

from haiway import Default, State, ValidationError


class _Tree(State):
    value: int = 0
    kids: Mapping[str, Self] = {}


class _Flat(State):
    kids: Mapping[str, int] = {}


class _IntKeys(State):
    kids: Mapping[int, int] = {}


class _Key:
    __slots__ = ("name",)

    def __init__(self, name: str) -> None:
        self.name: str = name

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        return isinstance(other, _Key) and other.name == self.name

    def __str__(self) -> str:
        return f"K{self.name}"


class _UnprintableKey(_Key):
    __slots__ = ()

    def __str__(self) -> str:
        raise RuntimeError("no text for this key")


class _InterruptingKey(_Key):
    __slots__ = ()

    def __str__(self) -> str:
        raise KeyboardInterrupt


class _InterruptingKeys(State):
    kids: Mapping[_InterruptingKey, int] = {}


class _ObjectKeys(State):
    kids: Mapping[_Key, int] = {}


class _UnprintableKeys(State):
    kids: Mapping[_UnprintableKey, int] = {}


class _Optional(State):
    kids: Mapping[str, int] | None = None


class _Entries(TypedDict):
    first: Required[int]
    second: NotRequired[int]


class _Typed(State):
    entries: _Entries = Default(factory=lambda: {"first": 0})


class _Sequence(State):
    value: int = 0
    children: Sequence[Self] = ()


class _Nested(State):
    value: int = 0
    inner: Self | None = None


class _Plain(State):
    value: int = 0


_CASES: Sequence[tuple[type[State], str, Any, Sequence[str]]] = (
    (_Tree, "kids", {"b0": {"value": {}}}, (".kids", "[b0]", ".value")),
    (_Flat, "kids", {"a": "x"}, (".kids", "[a]")),
    (_IntKeys, "kids", {1: "x"}, (".kids", "[1]")),
    (_ObjectKeys, "kids", {_Key("1"): "x"}, (".kids", "[K1]")),
    (_Optional, "kids", {"a": "x"}, (".kids",)),
    (_Typed, "entries", {"first": "x"}, (".entries", '["first"]')),
    (_Typed, "entries", {"second": 1}, (".entries", '["first"]')),
    (_Sequence, "children", [{"value": {}}], (".children", "[0]", ".value")),
    (_Nested, "inner", {"value": {}}, (".inner",)),
    (_Plain, "value", {}, (".value",)),
)


def _case_id(case: tuple[type[State], str, Any, Sequence[str]]) -> str:
    return f"{case[0].__name__}-{''.join(case[3])}"


def _constructed(cls: type[State], name: str, value: Any) -> None:
    cls(**{name: value})


def _validated(cls: type[State], name: str, value: Any) -> None:
    cls.validate({name: value})


def _from_mapping(cls: type[State], name: str, value: Any) -> None:
    cls.from_mapping({name: value})


def _updated(cls: type[State], name: str, value: Any) -> None:
    cls().updating(**{name: value})


def _from_json(cls: type[State], name: str, value: Any) -> None:
    try:
        cls.from_json(json.dumps({name: value}))

    except ValueError as exc:  # JSON decoding wraps the failure it reports
        assert isinstance(exc.__cause__, ValidationError)
        raise exc.__cause__ from None


_ENTRY_POINTS: Mapping[str, Callable[[type[State], str, Any], None]] = {
    "constructor": _constructed,
    "validate": _validated,
    "from_mapping": _from_mapping,
    "updating": _updated,
    "from_json": _from_json,
}


@pytest.fixture(autouse=True)
def gc_pressure() -> Generator[None]:
    thresholds = gc.get_threshold()
    gc.set_threshold(1)
    try:
        yield

    finally:
        gc.set_threshold(*thresholds)


@pytest.fixture
def unraisable() -> Generator[list[object]]:
    reported: list[object] = []
    hook = sys.unraisablehook
    sys.unraisablehook = lambda arguments: reported.append(arguments.exc_value)
    try:
        yield reported

    finally:
        sys.unraisablehook = hook


@pytest.mark.parametrize("entry", _ENTRY_POINTS)
@pytest.mark.parametrize("case", _CASES, ids=_case_id)
def test_failures_are_reported_under_gc_pressure(
    case: tuple[type[State], str, Any, Sequence[str]],
    entry: str,
    unraisable: list[object],
) -> None:
    cls, name, value, path = case
    if entry == "from_json" and cls is _ObjectKeys:
        pytest.skip("keys of the case are not JSON")

    for _ in range(25):
        with pytest.raises(ValidationError) as raised:
            _ENTRY_POINTS[entry](cls, name, value)

        assert tuple(raised.value.path) == path

    assert unraisable == []


@pytest.mark.parametrize("entry", ["constructor", "validate", "from_mapping", "updating"])
def test_key_which_cannot_be_printed_does_not_replace_the_failure(
    entry: str,
    unraisable: list[object],
) -> None:
    with pytest.raises(ValidationError) as raised:
        _ENTRY_POINTS[entry](_UnprintableKeys, "kids", {_UnprintableKey("1"): "x"})

    assert tuple(raised.value.path) == (".kids", "[<unprintable>]")
    assert isinstance(raised.value.__cause__, ValueError)
    assert unraisable == []


def test_key_interrupted_while_printed_is_not_swallowed(unraisable: list[object]) -> None:
    with pytest.raises(KeyboardInterrupt):
        _constructed(_InterruptingKeys, "kids", {_InterruptingKey("1"): "x"})

    assert unraisable == []


def test_unknown_keyword_still_replaces_a_failed_value_under_gc_pressure(
    unraisable: list[object],
) -> None:
    arguments: dict[str, Any] = {"value": "x", "unknown": 1}
    for _ in range(25):
        with pytest.raises(TypeError, match="_Plain has no attribute 'unknown'"):
            _Plain(**arguments)

    assert unraisable == []
