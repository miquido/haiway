import json
from collections.abc import Mapping, Sequence
from copy import deepcopy
from functools import reduce
from pathlib import Path
from typing import Any, Literal

import pytest

from haiway import Map, State, ValidationError
from haiway._native import nstruct
from haiway.attributes.native import Kind

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


class OpenAlternative(State):
    value: tuple[Point, *tuple[int, ...]] | int | str


@pytest.mark.parametrize(
    ("given", "expected"),
    (
        ((Point(x=1, y=2),), (Point(x=1, y=2),)),
        ([Point(x=1, y=2), 3, 4], (Point(x=1, y=2), 3, 4)),  # accepted as it is
        (({"x": 1, "y": 2}, 3), (Point(x=1, y=2), 3)),  # no alternative accepts it as it is
        (3, 3),  # not a sequence
        ("ab", "ab"),  # text, which is no tuple
    ),
)
def test_open_tuple_among_the_alternatives_of_a_union(given: Any, expected: Any) -> None:
    assert OpenAlternative(value=given).value == expected


@pytest.mark.parametrize("given", ((), (Point(x=1, y=2), "x"), [{"x": 1}]))
def test_open_tuple_among_the_alternatives_of_a_union_refused(given: Any) -> None:
    with pytest.raises(ValidationError):
        OpenAlternative(value=given)


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


_UNTYPED: Any = nstruct  # the entry points called with what their stubs refuse


@pytest.mark.parametrize(
    "call",
    (
        lambda: nstruct.to_json(object()),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.basic_object(object(), True, ()),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.to_mapping(object(), False),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.from_json(int, "{}", False, False),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.compile_plan(int, (), ()),
        lambda: nstruct.validate(object(), 1),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.check(object(), 1),  # pyright: ignore[reportArgumentType]
        lambda: nstruct.compile_spec(()),
        lambda: _UNTYPED.accepts_missing(object()),
        lambda: _UNTYPED.spelled_once(set()),
    ),
)
def test_internal_entry_points_refuse_what_they_are_not_given(call: Any) -> None:
    with pytest.raises(TypeError):
        call()


def test_validating_ignoring_extra_refuses_what_it_is_not_given() -> None:
    not_a_class: Any = int
    missing: tuple[Any, ...] = (Point,)
    with pytest.raises(TypeError, match="Struct class"):
        nstruct.validate_instance(not_a_class, {}, True)

    with pytest.raises(TypeError, match="expects a class, a value and ignore_extra"):
        nstruct.validate_instance(*missing)


def test_validate_instance_leaves_out_keys_which_are_not_attributes_when_told_to() -> None:
    assert nstruct.validate_instance(Point, {"x": 1, "y": 2, "z": 3}, True) == Point(x=1, y=2)
    assert nstruct.validate_instance(Point, {"x": 1, "y": 2}, False) == Point(x=1, y=2)
    with pytest.raises(TypeError, match="Unexpected attribute 'z' for Point"):
        nstruct.validate_instance(Point, {"x": 1, "y": 2, "z": 3}, False)


class ValidatingItself(State):
    @classmethod
    def validate(cls, value: Any, /) -> ValidatingItself:
        return cls()


class InheritingValidation(ValidatingItself):
    pass


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        (nstruct.Struct, False),
        (State, False),
        (Point, False),
        (ValidatingItself, True),
        (InheritingValidation, True),
    ],
)
def test_validates_itself_tells_a_validate_declared_before_struct(
    cls: type[nstruct.Struct],
    expected: bool,
) -> None:
    assert nstruct.validates_itself(cls) is expected


def test_validates_itself_refuses_what_is_not_a_struct_class() -> None:
    not_a_struct: Any = int
    with pytest.raises(TypeError, match="Struct class"):
        nstruct.validates_itself(not_a_struct)


def test_from_json_refuses_wrong_arguments() -> None:
    missing: tuple[Any, ...] = (Point, "{}", False)
    with pytest.raises(TypeError, match="expects a class, a text, array and ignore_extra"):
        nstruct.from_json(*missing)

    not_a_class: Any = int
    with pytest.raises(TypeError, match="Struct class"):
        nstruct.from_json(not_a_class, "{}", False, False)


class Undecidable:
    def __bool__(self) -> bool:
        raise RuntimeError("undecidable")


@pytest.mark.parametrize("position", [2, 3])
def test_from_json_passes_on_an_error_of_a_flag(position: int) -> None:
    arguments: list[Any] = [Point, '{"x": 1, "y": 2}', False, False]
    arguments[position] = Undecidable()
    with pytest.raises(RuntimeError, match="undecidable"):
        nstruct.from_json(*arguments)


def test_kind_names_every_kind_the_extension_exports() -> None:
    exported: dict[str, int] = {
        name.removeprefix("SPEC_"): getattr(nstruct, name)
        for name in dir(nstruct)
        if name.startswith("SPEC_")
    }

    assert exported == {kind.name: kind.value for kind in Kind}


def test_spelled_once_takes_a_spelling_in_once() -> None:
    spelled: set[Any] = set()
    nstruct.spelled_once(spelled, "a")
    nstruct.spelled_once(spelled, "b")

    with pytest.raises(ValueError, match="same once spelled"):
        nstruct.spelled_once(spelled, "a")

    assert spelled == {"a", "b"}


@pytest.mark.parametrize("container", (frozenset({"a"}), ["a"], {"a": 1}, None))
def test_spelled_once_refuses_what_is_not_a_set(container: Any) -> None:
    with pytest.raises(TypeError, match="spelled_once expects a set"):
        _UNTYPED.spelled_once(container, "b")


@pytest.mark.parametrize(
    ("key", "expected"),
    ((None, "null"), (True, "true"), (7, "7"), (float("-inf"), "-inf"), ("a", None), (b"a", None)),
    ids=repr,
)
def test_key_spelling_covers_the_keys_without_a_text_of_their_own(
    key: Any,
    expected: str | None,
) -> None:
    assert nstruct.key_spelling(key) == expected
