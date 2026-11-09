from collections.abc import Iterator, Mapping
from types import MappingProxyType
from typing import Any

import pytest

from haiway import Map, Missing, as_dict, as_list, as_map, as_set, as_tuple, without_missing


def test_as_list() -> None:
    original = [1, 2]
    assert as_list(original) is original
    assert as_list((1, 2)) == [1, 2]
    assert as_list({1}) == [1]
    assert as_list(iter(range(3))) == [0, 1, 2]
    assert as_list([]) == []
    assert as_list(None) is None


def test_as_list_copies_non_list() -> None:
    source: tuple[int, ...] = (1, 2)
    result: list[int] = as_list(source)
    result.append(3)
    assert source == (1, 2)


def test_as_tuple() -> None:
    original = (1, 2)
    assert as_tuple(original) is original
    assert as_tuple([1, 2]) == (1, 2)
    assert as_tuple(x for x in "ab") == ("a", "b")
    assert as_tuple(()) == ()
    assert as_tuple(None) is None


def test_as_set() -> None:
    original = {1, 2}
    assert as_set(original) is original
    assert as_set([1, 1, 2]) == {1, 2}
    assert as_set("aab") == {"a", "b"}
    assert as_set(None) is None


def test_as_set_does_not_treat_frozenset_as_set_instance() -> None:
    frozen = frozenset({1})
    result = as_set(frozen)
    assert isinstance(result, set)
    assert result == {1}


def test_as_dict() -> None:
    original = {"a": 1}
    assert as_dict(original) is original
    result = as_dict(MappingProxyType[str, int]({"a": 1}))
    assert type(result) is dict
    assert result == {"a": 1}
    assert as_dict(None) is None


def test_as_map() -> None:
    original = Map({"a": 1})
    assert as_map(original) is original

    converted = as_map({"a": 1})
    assert isinstance(converted, Map)
    assert converted == {"a": 1}
    assert as_map(None) is None


def test_as_map_accepts_custom_mappings() -> None:
    class Custom(Mapping[str, int]):
        def __getitem__(self, key: str) -> int:
            return {"x": 1}[key]

        def __iter__(self) -> Iterator[str]:
            return iter(("x",))

        def __len__(self) -> int:
            return 1

    result = as_map(Custom())
    assert isinstance(result, Map)
    assert result["x"] == 1


def test_without_missing_filters_only_missing() -> None:
    result = without_missing(
        {"a": 1, "b": Missing, "c": None, "d": 0, "e": "", "f": False},
    )
    assert result == {"a": 1, "c": None, "d": 0, "e": "", "f": False}


def test_without_missing_returns_new_mapping() -> None:
    source = {"a": 1, "b": Missing}
    result = without_missing(source)
    assert result is not source
    assert source == {"a": 1, "b": Missing}


def test_without_missing_empty_and_all_missing() -> None:
    assert without_missing({}) == {}
    assert without_missing({"a": Missing}) == {}


def test_without_missing_typed_returns_same_content() -> None:
    result = without_missing({"a": 1, "b": Missing}, typed=dict[str, Any])
    assert result == {"a": 1}


@pytest.mark.parametrize("converter", [as_list, as_tuple, as_set, as_dict, as_map])
def test_none_passthrough(converter: object) -> None:
    assert converter(None) is None  # pyright: ignore[reportCallIssue]
