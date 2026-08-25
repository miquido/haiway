import re
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from typing import (
    Any,
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
    PathAttribute,
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

__all__ = (
    "object_specification",
    "type_specification",
)


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


def object_specification(
    annotation: AttributeAnnotation,
    /,
    attributes: Sequence[tuple[str, AttributeAnnotation, bool]],
) -> TypeSpecification | None:
    """Specification of an object made of the given attributes.

    Parameters
    ----------
    annotation : AttributeAnnotation
        Annotation of the object itself, as resolved for the class declaring it.
    attributes : Sequence[tuple[str, AttributeAnnotation, bool]]
        Key to render the attribute under, its annotation, and whether it is
        required - in the order they should be rendered.

    Returns
    -------
    TypeSpecification | None
        Specification of the object, carrying an anchor when one of its
        attributes refers back to it, or ``None`` when any of them can't be
        represented.

    Notes
    -----
    The object is entered into the recursion guard before its attributes are
    resolved, which is what separates this from resolving each of them on its
    own. An attribute referring back to the object resolves to a reference to it
    that way, instead of to the specification of the class declaring it - which
    is not there yet, the class being created around this very call.
    """
    recursion_guard: MutableMapping[int, _RecursionGuard] = {}
    guard: _RecursionGuard = _RecursionGuard(annotation)
    recursion_guard[id(annotation)] = guard

    required: MutableSequence[str] = []
    properties: MutableMapping[str, TypeSpecification] = {}
    for key, element, element_required in attributes:
        element_specification: TypeSpecification | None = _specification(
            element,
            recursion_guard=recursion_guard,
        )
        if element_specification is None:
            return None  # an attribute which can't be represented takes the object with it

        properties[key] = _with_description(
            element_specification,
            description=element.description,
        )

        if element_required:
            required.append(key)

    specification: TypeSpecification = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }

    if guard.referenced:
        return _with_anchor(
            specification,
            anchor=guard.anchor,
        )

    return specification


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
    def anchor(self) -> str:
        return _anchor_name(self.annotation.type_name)


def _specification(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    # an explicitly declared specification is used as it stands, wherever it is
    # declared - the attribute of an object and the element of a container alike
    if declared := annotation.specification:
        return declared

    key: int = id(annotation)

    if guard := recursion_guard.get(key):
        guard.referenced = True
        return {"$ref": f"#{guard.anchor}"}

    elif isinstance(annotation, AliasAttribute):
        return _guarded_specification(
            annotation,
            resolve=lambda: _specification(
                annotation.resolved,
                recursion_guard,
            ),
            recursion_guard=recursion_guard,
        )

    elif getattr(annotation.base, "__SERIALIZABLE__", False):
        # the class resolved its own specification when it was created, including
        # the anchor of a reference to itself - there is nothing to resolve here
        return cast(TypeSpecification | None, annotation.base.__SPECIFICATION__)

    elif specification_factory := SPECIFICATIONS.get(type(annotation)):
        return _guarded_specification(
            annotation,
            resolve=lambda: specification_factory(
                annotation,
                recursion_guard,
            ),
            recursion_guard=recursion_guard,
        )

    else:
        return None  # Unsupported type annotation


def _guarded_specification(
    annotation: AttributeAnnotation,
    /,
    resolve: Callable[[], TypeSpecification | None],
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    key: int = id(annotation)
    guard: _RecursionGuard = recursion_guard.setdefault(
        key,
        _RecursionGuard(annotation),
    )

    specification: TypeSpecification | None
    try:
        specification = resolve()

    finally:
        # the guard is only kept for an annotation which is actually referenced -
        # the entry would otherwise turn a later, unrelated use of the same
        # annotation into a reference to an anchor which was never rendered
        if not guard.referenced:
            recursion_guard.pop(key, None)

    if specification is None:
        return None

    if guard.referenced:
        return _with_anchor(
            specification,
            anchor=guard.anchor,
        )

    return specification


def _prepare_specification_of_any(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    # `Any` accepts every value, which has to be spelled out - a schema naming a
    # single type would refuse the values the annotation validates just fine
    return {
        "type": [
            "string",
            "number",
            "integer",
            "boolean",
            "object",
            "array",
            "null",
        ],
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
    values: Sequence[Any] = list(cast(LiteralAttribute, annotation).values)

    # `bool` is checked before `int` - it is a subclass of it, while a JSON
    # boolean is not a JSON integer, so a schema naming one refuses the other
    if all(isinstance(element, bool) for element in values):
        return {
            "type": "boolean",
            "enum": values,
        }

    elif all(isinstance(element, str) for element in values):
        return {
            "type": "string",
            "enum": values,
        }

    elif all(isinstance(element, int) and not isinstance(element, bool) for element in values):
        return {
            "type": "integer",
            "enum": values,
        }

    # values of more than one JSON type, i.e. `Literal["a", None]` - the enum
    # names them all, which is what makes naming a single type unnecessary
    return {
        "enum": values,
    }


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


_COMPRESSIBLE_TYPES: frozenset[str] = frozenset(
    (
        "null",
        "string",
        "number",
        "integer",
        "boolean",
    )
)


def _compressed_type(
    specification: TypeSpecification,
    /,
) -> str | None:
    # an alternative naming nothing but its type joins a list of types instead of
    # a list of alternatives - anything more than that has to be kept as it is
    match specification:
        case {"type": str() as type_name, **tail} if not tail and type_name in _COMPRESSIBLE_TYPES:
            return type_name

        case _:
            return None


def _prepare_specification_of_union(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    alternatives: list[TypeSpecification] = []
    compressed_alternatives: list[str] = []
    compressible: bool = True
    for argument in cast(UnionAttribute, annotation).alternatives:
        specification: TypeSpecification | None = _specification(
            argument,
            recursion_guard=recursion_guard,
        )
        if specification is None:
            return None

        alternatives.append(specification)
        compressed_type: str | None = _compressed_type(specification)
        if compressed_type is None:
            compressible = False

        elif compressed_type not in compressed_alternatives:
            # a type can be named only once within a list of types, while the
            # alternatives can name it more than once - i.e. `None | Missing`
            compressed_alternatives.append(compressed_type)

    if alternatives and compressible:
        if len(compressed_alternatives) == 1:  # alternatives of one and the same type
            return cast(
                TypeSpecification,
                {
                    "type": compressed_alternatives[0],
                },
            )

        return cast(
            TypeSpecification,
            {
                "type": compressed_alternatives,
            },
        )

    # `anyOf` rather than `oneOf` - more than one alternative can accept the same
    # value, i.e. the empty array of `Sequence[int] | Sequence[str]`, which the
    # annotation validates through the first alternative matching it
    return {
        "anyOf": alternatives,
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


def _prepare_specification_of_path(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
        "format": "path",
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
) -> TypeSpecification:
    # a declared specification describing itself keeps its own description
    if not description or "description" in specification:
        return specification

    return cast(
        TypeSpecification,
        {
            **specification,
            "description": description,
        },
    )


def _with_anchor(
    specification: TypeSpecification,
    anchor: str,
) -> TypeSpecification:
    return cast(
        TypeSpecification,
        {
            **specification,
            "$anchor": anchor,
        },
    )


_ANCHOR_DISALLOWED: re.Pattern[str] = re.compile(r"[^-A-Za-z0-9._]")
_ANCHOR_START: re.Pattern[str] = re.compile(r"[A-Za-z_]")


def _anchor_name(
    type_name: str,
    /,
) -> str:
    # an anchor is a plain name - the `<locals>` of a class declared within a
    # function and the brackets of a specialized generic are not part of one
    anchor: str = _ANCHOR_DISALLOWED.sub("_", type_name)

    if not anchor or not _ANCHOR_START.match(anchor[0]):
        return f"_{anchor}"

    return anchor


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
    PathAttribute: _prepare_specification_of_path,
    MetaAttribute: _prepare_specification_of_meta,
    CustomAttribute: _prepare_specification_of_custom,
}
