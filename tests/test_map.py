import json

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
