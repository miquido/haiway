import copy
import json
from typing import Any

import pytest

from haiway.types.map import Map


def test_json_dumps_handles_map() -> None:
    mapping = Map({"alpha": 1, "beta": 2})

    encoded = json.dumps(mapping)

    assert json.loads(encoded) == {"alpha": 1, "beta": 2}


def test_mutating_operations_raise_attribute_error() -> None:
    mapping = Map({"alpha": 1})

    with pytest.raises(AttributeError):
        mapping.update({"beta": 2})

    with pytest.raises(AttributeError):
        mapping["alpha"] = 5

    with pytest.raises(AttributeError):
        mapping.pop("alpha")


def test_map_or_returns_map() -> None:
    mapping = Map({"a": 1})

    merged = mapping | {"b": 2}

    assert isinstance(merged, Map)
    assert merged["a"] == 1
    assert merged["b"] == 2
    assert mapping is not merged


def test_map_ror_right_hand_precedence() -> None:
    mapping = Map({"a": 2, "b": 2})

    merged = {"a": 1, "c": 3} | mapping

    assert isinstance(merged, Map)
    assert merged == {"a": 2, "b": 2, "c": 3}


def test_from_json_decodes_objects() -> None:
    from_str: Map[str, Any] = Map[str, Any].from_json('{"a": 1, "b": {"c": [1, 2]}}')
    from_bytes: Map[str, Any] = Map[str, Any].from_json(b'{"a": 1}')

    assert isinstance(from_str, Map)
    assert from_str == {"a": 1, "b": {"c": [1, 2]}}
    assert isinstance(from_bytes, Map)
    assert from_bytes == {"a": 1}
    assert Map.from_json("{}") == {}


@pytest.mark.parametrize("payload", ["[1, 2]", "null", "1", '"text"', "true"])
def test_from_json_rejects_non_objects(payload: str) -> None:
    with pytest.raises(ValueError, match="expected object"):
        Map.from_json(payload)


def test_from_json_propagates_decoding_errors() -> None:
    with pytest.raises(json.JSONDecodeError):
        Map.from_json("{not json")


def test_to_json_round_trip() -> None:
    mapping = Map({"a": 1, "b": [1, 2], "c": None, "d": {"e": "f"}})

    assert Map.from_json(mapping.to_json()) == mapping
    assert Map().to_json() == "{}"


def test_to_json_rejects_unencodable_values() -> None:
    with pytest.raises(TypeError):
        Map({"a": {1, 2}}).to_json()


def test_to_str_matches_str() -> None:
    mapping = Map({"a": 1})

    assert mapping.to_str() == str(mapping) == "{'a': 1}"


def test_hash_follows_contents() -> None:
    assert hash(Map({"a": 1, "b": 2})) == hash(Map({"b": 2, "a": 1}))
    assert {Map({"a": 1}): "value"}[Map({"a": 1})] == "value"


def test_hash_fails_for_unhashable_contents() -> None:
    with pytest.raises(TypeError):
        hash(Map({"a": [1]}))


def test_remaining_mutations_raise_attribute_error() -> None:
    mapping = Map({"a": 1})

    with pytest.raises(AttributeError):
        del mapping["a"]

    with pytest.raises(AttributeError):
        mapping.clear()

    with pytest.raises(AttributeError):
        mapping.popitem()

    with pytest.raises(AttributeError):
        mapping.setdefault("b", 1)

    with pytest.raises(AttributeError):
        mapping.__ior__({"b": 2})

    with pytest.raises(AttributeError):
        mapping.attribute = 1  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError):
        del mapping.attribute  # pyright: ignore[reportAttributeAccessIssue]

    assert mapping == {"a": 1}


def test_or_with_non_mapping_is_not_supported() -> None:
    mapping = Map({"a": 1})

    with pytest.raises(TypeError):
        mapping | 1  # pyright: ignore[reportUnusedExpression, reportOperatorIssue]

    with pytest.raises(TypeError):
        1 | mapping  # pyright: ignore[reportUnusedExpression, reportOperatorIssue]


def test_or_merge_prefers_right_operand_and_keeps_originals() -> None:
    left = Map({"a": 1, "b": 1})
    right = Map({"b": 2})

    assert left | right == {"a": 1, "b": 2}
    assert left == {"a": 1, "b": 1}
    assert right == {"b": 2}


def test_copies_are_the_same_instance() -> None:
    mapping = Map({"a": [1]})

    assert mapping.copy() is mapping
    assert copy.copy(mapping) is mapping
    assert copy.deepcopy(mapping) is mapping
