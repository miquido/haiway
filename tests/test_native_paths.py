import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import reduce
from pathlib import Path
from typing import Any, Literal

import pytest

from haiway import Map, State, ValidationError
from haiway._native import nstruct

# The paths of the native extension the rest of the suite does not reach - found with
# `make coverage-native`. Allocation failures are left out, they can't be caused here.


class Point(State):
    x: int
    y: int


class Points(State):
    values: Sequence[int]
    points: tuple[Point, ...] = ()


@pytest.mark.parametrize("count", (31, 32, 33, 64, 65, 1000))
def test_json_arrays_past_the_inline_buffer(count: int) -> None:
    # the decoder keeps a short array inline and grows onto the heap past it, more than once
    given = Points(
        values=tuple(range(count)),
        points=tuple(Point(x=i, y=-i) for i in range(count)),
    )
    decoded: Points = Points.from_json(given.to_json())
    assert decoded == given
    assert Point.from_json_array(json.dumps([{"x": i, "y": i} for i in range(count)])) == tuple(
        Point(x=i, y=i) for i in range(count)
    )


@pytest.mark.parametrize(
    "text",
    (
        '[{"x": 1, "y": 2} {"x": 3, "y": 4}]',  # no separator
        '[{"x": 1, "y": 2},',  # unterminated
    ),
)
def test_json_array_malformed(text: str) -> None:
    with pytest.raises(ValueError):
        Point.from_json_array(text)


def test_json_from_bytes_not_utf8() -> None:
    with pytest.raises(ValueError):
        Point.from_json(b'{"x": 1, "y": "\xff"}')


class Holder[Value](State):
    value: Value


# a union over more alternatives than fit the bits of a word - built at runtime
def _either(left: Any, right: Any) -> Any:
    return left | right


WIDE: Any = reduce(_either, (Literal[f"value-{i}"] for i in range(70)))
Wide: type[Holder[Any]] = Holder[WIDE]


@pytest.mark.parametrize("index", (0, 63, 64, 69))
def test_union_past_a_word_of_alternatives(index: int) -> None:
    assert Wide(value=f"value-{index}").value == f"value-{index}"


def test_union_past_a_word_of_alternatives_refused() -> None:
    with pytest.raises(ValidationError):
        Wide(value="value-70")


class FailingPathLike:
    def __fspath__(self) -> str:
        raise ValueError("no path")


class Located(State):
    location: Path


def test_path_failing_to_convert() -> None:
    with pytest.raises(ValidationError, match="path format"):
        Located(location=FailingPathLike())  # pyright: ignore[reportArgumentType]


class Counted(State):
    counts: Mapping[str, int]


def test_frozendict_with_nothing_to_convert_becomes_a_map() -> None:
    given: Mapping[str, int] = frozendict.fromkeys(("a",), 1)
    counted = Counted(counts=given)
    assert isinstance(counted.counts, Map)
    assert counted.counts == given


def test_deep_copy_refuses_a_memo_which_is_not_a_dict() -> None:
    with pytest.raises(TypeError, match="memo"):
        Point(x=1, y=2).__deepcopy__([])  # pyright: ignore[reportArgumentType]

    assert deepcopy(Point(x=1, y=2)) == Point(x=1, y=2)


def test_configure_only_once() -> None:
    with pytest.raises(RuntimeError, match="configured already"):
        nstruct.configure(object(), object(), object())


@pytest.mark.parametrize(
    "call",
    (
        lambda: nstruct.to_json(object()),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.basic_object(object(), True, ()),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.to_mapping(object(), False),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.from_json(int, "{}", False),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.compile_plan(int, (), (), False),
        lambda: nstruct.validate(object(), 1),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.check(object(), 1),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.compile_spec(()),
    ),
)
def test_internal_entry_points_refuse_what_they_are_not_given(call: Any) -> None:
    with pytest.raises(TypeError):
        call()
