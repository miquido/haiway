from typing import Any

import pytest

from haiway import State


class Holder(State):
    value: Any


def _cycle() -> list[Any]:
    cycle: list[Any] = []
    cycle.append(cycle)
    return cycle


def _mapping_cycle() -> dict[str, Any]:
    cycle: dict[str, Any] = {}
    cycle["self"] = cycle
    return cycle


@pytest.mark.parametrize("make", [_cycle, _mapping_cycle])
def test_conversion_of_a_cyclic_value_fails_instead_of_overflowing_the_stack(make: Any) -> None:
    holder = Holder(value=make())

    with pytest.raises(RecursionError):
        holder.to_mapping()

    with pytest.raises(RecursionError):
        holder.to_basic_object()

    with pytest.raises(ValueError, match="Failed to encode") as raised:
        holder.to_json()

    assert isinstance(raised.value.__cause__, RecursionError)


def test_deeply_nested_but_finite_values_still_convert() -> None:
    value: Any = 1
    for _ in range(50):
        value = [value]

    assert Holder(value=value).to_mapping()["value"][0][0] is not None
