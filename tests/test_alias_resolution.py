from __future__ import annotations

from collections.abc import Sequence
from typing import NotRequired, TypedDict

from haiway.attributes.annotations import (
    AliasAttribute,
    CustomAttribute,
    MappingAttribute,
    ObjectAttribute,
    SequenceAttribute,
    SetAttribute,
    TupleAttribute,
    TypedDictAttribute,
    UnionAttribute,
    ValidableAttribute,
    resolve_self_attribute,
)
from haiway.attributes.state import State


class ParameterArraySpecification(TypedDict, total=False):
    items: NotRequired[ParameterSpecification]


class ParameterObjectSpecification(TypedDict, total=False):
    oneOf: NotRequired[Sequence[ParameterSpecification]]


type ParameterSpecification = ParameterArraySpecification | ParameterObjectSpecification | str


class ParameterContainer(State):
    parameters: Sequence[ParameterSpecification]


def _collect_parameter_aliases(  # noqa: C901, PLR0911, PLR0912
    attribute: object,
    visited: set[int],
) -> list[AliasAttribute]:
    attribute_id = id(attribute)
    if attribute_id in visited:
        return []

    visited.add(attribute_id)

    if isinstance(attribute, AliasAttribute):
        aliases: list[AliasAttribute] = [attribute]
        resolved = getattr(attribute, "_resolved", None)
        if resolved is not None:
            aliases.extend(_collect_parameter_aliases(resolved, visited))
        return aliases

    if isinstance(attribute, SequenceAttribute | SetAttribute):
        return _collect_parameter_aliases(attribute.values, visited)

    if isinstance(attribute, UnionAttribute):
        collected: list[AliasAttribute] = []
        for alternative in attribute.alternatives:
            collected.extend(_collect_parameter_aliases(alternative, visited))
        return collected

    if isinstance(attribute, TupleAttribute):
        collected: list[AliasAttribute] = []
        for element in attribute.values:
            collected.extend(_collect_parameter_aliases(element, visited))
        return collected

    if isinstance(attribute, MappingAttribute):
        collected_keys = _collect_parameter_aliases(attribute.keys, visited)
        collected_values = _collect_parameter_aliases(attribute.values, visited)
        return [*collected_keys, *collected_values]

    if isinstance(attribute, TypedDictAttribute | ObjectAttribute):
        collected: list[AliasAttribute] = []
        for child in attribute.attributes.values():
            collected.extend(_collect_parameter_aliases(child, visited))
        for parameter in attribute.parameters:
            collected.extend(_collect_parameter_aliases(parameter, visited))
        return collected

    if isinstance(attribute, CustomAttribute):
        collected: list[AliasAttribute] = []
        for parameter in attribute.parameters:
            collected.extend(_collect_parameter_aliases(parameter, visited))
        return collected

    if isinstance(attribute, ValidableAttribute):
        return _collect_parameter_aliases(attribute.attribute, visited)

    return []


def test_recursive_aliases_are_resolved() -> None:
    state_attribute = resolve_self_attribute(ParameterContainer, parameters={})

    parameters_attribute = state_attribute.attributes["parameters"]
    assert isinstance(parameters_attribute, SequenceAttribute)

    # Exercising validation triggers alias usage deep within the annotation graph.
    parameters_attribute.validate([{"oneOf": [{"items": "value"}]}])

    aliases = _collect_parameter_aliases(parameters_attribute, set())
    unresolved = [
        alias
        for alias in aliases
        if alias.alias == "ParameterSpecification" and getattr(alias, "_resolved", None) is None
    ]

    assert not unresolved

    parameter_aliases = [alias for alias in aliases if alias.alias == "ParameterSpecification"]
    assert parameter_aliases  # sanity check: alias exists in the structure
    for alias in parameter_aliases:
        assert isinstance(alias.resolved, UnionAttribute)
