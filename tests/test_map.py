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
