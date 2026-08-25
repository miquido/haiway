from collections.abc import Sequence
from typing import Annotated, NotRequired, TypedDict

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
    resolve_attribute,
    resolve_self_attribute,
)
from haiway.attributes.state import State
from haiway.types import Alias


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


type AnnotatedTree = Sequence[Annotated["AnnotatedTree", Alias("children")]] | int


def test_recursive_alias_annotated_with_alias_is_resolved() -> None:
    # `Alias(...)` overrides what `AliasAttribute.alias` reports, so resolution
    # has to match on the declared type alias rather than the exposed name
    attribute = resolve_attribute(
        AnnotatedTree,
        module=__name__,
        resolved_parameters={},
        recursion_guard={},
    )

    assert attribute.validate([[1], 2]) == ((1,), 2)


def test_recursive_alias_annotated_with_alias_exposes_alias() -> None:
    attribute = resolve_attribute(
        AnnotatedTree,
        module=__name__,
        resolved_parameters={},
        recursion_guard={},
    )
    assert isinstance(attribute, UnionAttribute)
    sequence = next(
        alternative
        for alternative in attribute.alternatives
        if isinstance(alternative, SequenceAttribute)
    )
    # the annotation still renames the nested element
    assert sequence.values.alias == "children"
