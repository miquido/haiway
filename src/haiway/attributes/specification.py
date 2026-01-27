from collections.abc import Callable, Mapping, MutableMapping, MutableSequence
from typing import (
    Literal,
    cast,
)

from haiway.attributes.annotations import (
    AliasAttribute,
    AnyAttribute,
    AttributeAnnotation,
    BoolAttribute,
    CustomAttribute,
    DateAttribute,
    DatetimeAttribute,
    FloatAttribute,
    IntegerAttribute,
    IntEnumAttribute,
    LiteralAttribute,
    MappingAttribute,
    MetaAttribute,
    MissingAttribute,
    NoneAttribute,
    SequenceAttribute,
    StrEnumAttribute,
    StringAttribute,
    TimeAttribute,
    TupleAttribute,
    TypedDictAttribute,
    UnionAttribute,
    UUIDAttribute,
    ValidableAttribute,
)
from haiway.types import TypeSpecification

__all__ = ("type_specification",)


def type_specification(
    annotation: AttributeAnnotation,
    /,
) -> TypeSpecification | None:
    specification: TypeSpecification | None = _specification(
        annotation,
        recursion_guard={},
    )

    if specification is None:
        return None

    return _with_description(
        specification,
        description=annotation.description,
    )


class _RecursionGuard:
    __slots__ = (
        "annotation",
        "referenced",
    )

    def __init__(
        self,
        annotation: AttributeAnnotation,
    ) -> None:
        self.annotation: AttributeAnnotation = annotation
        self.referenced: bool = False

    @property
    def name(self) -> str:
        return self.annotation.type_name


def _specification(  # noqa: C901, PLR0911, PLR0912
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    key: int = id(annotation)

    if guard := recursion_guard.get(key):
        guard.referenced = True
        return {"$ref": f"#{guard.name}"}

    elif isinstance(annotation, AliasAttribute):
        recursion_guard[key] = _RecursionGuard(annotation)
        specification: TypeSpecification | None = _specification(
            annotation.resolved,
            recursion_guard,
        )

        if specification is None:
            return None

        if recursion_guard[key].referenced:
            return _with_identifier(
                specification,
                identifier=annotation.type_name,
            )

        return specification

    elif getattr(annotation.base, "__SERIALIZABLE__", False):
        recursion_guard[key] = _RecursionGuard(annotation)
        specification: TypeSpecification | None = annotation.base.__SPECIFICATION__

        if specification is None:
            return None

        if recursion_guard[key].referenced:
            return _with_identifier(
                specification,
                identifier=annotation.type_name,
            )

        return specification

    elif specification_factory := SPECIFICATIONS.get(type(annotation)):
        guard = recursion_guard.setdefault(
            key,
            _RecursionGuard(annotation),
        )

        specification: TypeSpecification | None
        try:
            specification = specification_factory(
                annotation,
                recursion_guard,
            )

        finally:
            if not guard.referenced:
                recursion_guard.pop(key, None)

        if specification is None:
            return None

        if guard.referenced:
            return _with_identifier(
                specification,
                identifier=guard.name,
            )

        return specification

    else:
        return None  # Unsupported type annotation


def _prepare_specification_of_any(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "object",
        "additionalProperties": True,
    }


def _prepare_specification_of_none(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "null",
    }


def _prepare_specification_of_missing(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "null",
    }


def _prepare_specification_of_literal(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    literal: LiteralAttribute = cast(LiteralAttribute, annotation)

    if all(isinstance(element, str) for element in literal.values):
        return {
            "type": "string",
            "enum": literal.values,
        }

    elif all(isinstance(element, int) for element in literal.values):
        return {
            "type": "integer",
            "enum": literal.values,
        }

    raise TypeError(f"Unsupported literal annotation: {annotation}")


def _prepare_specification_of_sequence(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    items_specification: TypeSpecification | None = _specification(
        cast(SequenceAttribute, annotation).values,
        recursion_guard=recursion_guard,
    )

    if items_specification is None:
        return None

    return {
        "type": "array",
        "items": items_specification,
    }


def _prepare_specification_of_mapping(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    properties_specification: TypeSpecification | None = _specification(
        cast(MappingAttribute, annotation).values,
        recursion_guard=recursion_guard,
    )

    if properties_specification is None:
        return None

    return {
        "type": "object",
        "additionalProperties": properties_specification,
    }


def _prepare_specification_of_meta(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "object",
        "additionalProperties": True,
    }


def _prepare_specification_of_tuple(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    tuple_attribute = cast(TupleAttribute, annotation)
    elements_specification: MutableSequence[TypeSpecification] = []
    for element in tuple_attribute.values:
        element_specification: TypeSpecification | None = _specification(
            element,
            recursion_guard=recursion_guard,
        )

        if element_specification is None:
            return None

        elements_specification.append(element_specification)

    return {
        "type": "array",
        "prefixItems": elements_specification,
        "items": False,
    }


def _prepare_specification_of_union(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    compressed_alternatives: list[Literal["string", "number", "integer", "boolean", "null"]] = []
    alternatives: list[TypeSpecification] = []
    for argument in cast(UnionAttribute, annotation).alternatives:
        specification: TypeSpecification | None = _specification(
            argument,
            recursion_guard=recursion_guard,
        )
        if specification is None:
            return None

        alternatives.append(specification)
        match specification:
            case {"type": "null", **tail} if not tail:
                compressed_alternatives.append("null")

            case {"type": "string", **tail} if not tail:
                compressed_alternatives.append("string")

            case {"type": "number", **tail} if not tail:
                compressed_alternatives.append("number")

            case {"type": "integer", **tail} if not tail:
                compressed_alternatives.append("integer")

            case {"type": "boolean", **tail} if not tail:
                compressed_alternatives.append("boolean")

            case _:
                pass

    if alternatives and len(compressed_alternatives) == len(alternatives):
        return cast(
            TypeSpecification,
            {
                "type": tuple(compressed_alternatives),
            },
        )

    return {
        "oneOf": alternatives,
    }


def _prepare_specification_of_bool(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "boolean",
    }


def _prepare_specification_of_int(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "integer",
    }


def _prepare_specification_of_float(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "number",
    }


def _prepare_specification_of_str(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
    }


def _prepare_specification_of_str_enum(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
        "enum": [member.value for member in cast(StrEnumAttribute, annotation).base],
    }


def _prepare_specification_of_int_enum(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "integer",
        "enum": [int(member.value) for member in cast(IntEnumAttribute, annotation).base],
    }


def _prepare_specification_of_uuid(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
        "format": "uuid",
    }


def _prepare_specification_of_date(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
        "format": "date",
    }


def _prepare_specification_of_datetime(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
        "format": "date-time",
    }


def _prepare_specification_of_time(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
        "format": "time",
    }


def _prepare_specification_of_custom(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    return None


def _prepare_specification_of_validable(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    return _specification(
        cast(ValidableAttribute, annotation).attribute,
        recursion_guard=recursion_guard,
    )


def _prepare_specification_of_typed_dict(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    typed_dict = cast(TypedDictAttribute, annotation)

    required: list[str] = []
    properties: dict[str, TypeSpecification] = {}

    for key, element in typed_dict.attributes.items():
        specification: TypeSpecification | None = _specification(
            element,
            recursion_guard=recursion_guard,
        )
        if specification is None:
            return None

        properties[key] = specification

        if not element.required:
            continue

        required.append(key)

    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "required": required,
    }


def _with_description(
    specification: TypeSpecification,
    description: str | None,
) -> TypeSpecification | None:
    if not description:
        return specification

    return cast(
        TypeSpecification,
        {
            **specification,
            "description": description,
        },
    )


def _with_identifier(
    specification: TypeSpecification,
    identifier: str,
) -> TypeSpecification:
    return cast(
        TypeSpecification,
        {
            **specification,
            "$id": f"#{identifier}",
        },
    )


SPECIFICATIONS: Mapping[
    type[AttributeAnnotation],
    Callable[
        [AttributeAnnotation, MutableMapping[int, _RecursionGuard]],
        TypeSpecification | None,
    ],
] = {
    AnyAttribute: _prepare_specification_of_any,
    NoneAttribute: _prepare_specification_of_none,
    MissingAttribute: _prepare_specification_of_missing,
    BoolAttribute: _prepare_specification_of_bool,
    IntegerAttribute: _prepare_specification_of_int,
    FloatAttribute: _prepare_specification_of_float,
    StringAttribute: _prepare_specification_of_str,
    StrEnumAttribute: _prepare_specification_of_str_enum,
    IntEnumAttribute: _prepare_specification_of_int_enum,
    LiteralAttribute: _prepare_specification_of_literal,
    SequenceAttribute: _prepare_specification_of_sequence,
    TupleAttribute: _prepare_specification_of_tuple,
    MappingAttribute: _prepare_specification_of_mapping,
    TypedDictAttribute: _prepare_specification_of_typed_dict,
    UnionAttribute: _prepare_specification_of_union,
    ValidableAttribute: _prepare_specification_of_validable,
    UUIDAttribute: _prepare_specification_of_uuid,
    DateAttribute: _prepare_specification_of_date,
    DatetimeAttribute: _prepare_specification_of_datetime,
    TimeAttribute: _prepare_specification_of_time,
    MetaAttribute: _prepare_specification_of_meta,
    CustomAttribute: _prepare_specification_of_custom,
}
