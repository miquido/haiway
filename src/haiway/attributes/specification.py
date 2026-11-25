import json
import re
from collections.abc import Callable, Mapping, MutableMapping, MutableSequence, Sequence
from enum import Enum
from typing import (
    Any,
    cast,
)

from haiway._native import nstruct
from haiway.attributes.annotations import (
    AliasAttribute,
    AnyAttribute,
    AttributeAnnotation,
    BoolAttribute,
    BytesAttribute,
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
    OpenTupleAttribute,
    PathAttribute,
    SequenceAttribute,
    SetAttribute,
    StrEnumAttribute,
    StringAttribute,
    TimeAttribute,
    TimedeltaAttribute,
    TupleAttribute,
    TypedDictAttribute,
    UnionAttribute,
    UUIDAttribute,
    ValidableAttribute,
)
from haiway.types import Missing, TypeSpecification
from haiway.types.basic import basic_key

__all__ = (
    "object_specification",
    "type_specification",
    "unique_anchors",
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

    return unique_anchors(
        _with_description(
            specification,
            description=annotation.metadata.description,
        )
    )


def object_specification(
    annotation: AttributeAnnotation,
    /,
    attributes: Sequence[tuple[str, AttributeAnnotation, bool]],
    description: str | None = None,
) -> TypeSpecification | None:
    """Specification of an object made of the given attributes.

    Parameters
    ----------
    annotation : AttributeAnnotation
        Annotation of the object itself, as resolved for the class declaring it.
    attributes : Sequence[tuple[str, AttributeAnnotation, bool]]
        Key to render the attribute under, its annotation, and whether it is
        required - in the order they should be rendered.
    description : str | None
        Description of the object itself, rendered when given.

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
            description=element.metadata.description,
        )

        if element_required:
            required.append(key)

    specification: TypeSpecification = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }

    if description:
        specification["description"] = description

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
    if declared := annotation.metadata.specification:
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
        return _state_specification(annotation)

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
) -> TypeSpecification | None:
    # `Missing` stands for a value which was not provided, there is no JSON
    # value for it - an attribute accepting it can be left out, which is what
    # `required` of the object tells
    return None


def _prepare_specification_of_literal(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    values: list[Any] = []
    for element in cast(LiteralAttribute, annotation).values:
        if element is Missing:
            continue  # it has no JSON value, the attribute accepting it can be left out

        json_value: Any = _literal_json_value(element)
        if json_value is _NO_JSON_FORM:
            return None

        values.append(json_value)

    if not values:
        return None  # nothing but `Missing`

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


_NO_JSON_FORM: object = object()


def _literal_json_value(
    value: Any,
    /,
) -> Any:
    # a member of an enum is read as its value, which is what both the encoder
    # writes and the decoder accepts - when it equals the member, an enum which
    # does not is not read from a JSON value at all
    if isinstance(value, Enum):
        json_value: Any = value.value
        if json_value == value and isinstance(json_value, str | int):
            return json_value

        return _NO_JSON_FORM

    # `bytes` is written as base64 text, which is not read back as the literal
    if value is None or isinstance(value, str | int):
        return value

    return _NO_JSON_FORM


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


def _prepare_specification_of_set(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    items_specification: TypeSpecification | None = _specification(
        cast(SetAttribute, annotation).values,
        recursion_guard=recursion_guard,
    )

    if items_specification is None:
        return None

    # no `uniqueItems` - elements which differ may be written the same way, and a
    # document repeating one is read as a set all the same
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

    names_specification: TypeSpecification | None = _key_specification(
        cast(MappingAttribute, annotation).keys,
    )
    if names_specification is None:
        return {
            "type": "object",
            "additionalProperties": properties_specification,
        }

    return {
        "type": "object",
        "propertyNames": names_specification,
        "additionalProperties": properties_specification,
    }


# the spellings a key is read from - exactly the ones it is written as, see `basic_key` and the
# reader of keys of the extension (`spec.h`), which these patterns restate - they can't be
# derived from `nstruct.key_spelling`, which tells the spelling of a key and not what is read
_INTEGER_KEY: str = r"^(0|-?[1-9][0-9]*)$"
_NUMBER_KEY: str = r"^(-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][+-]?[0-9]+)?|-?inf|nan)$"
_BOOLEAN_KEY: str = r"^(true|false)$"
_NULL_KEY: str = r"^null$"


def _key_specification(
    keys: AttributeAnnotation,
    /,
) -> TypeSpecification | None:
    # a key is a string in a JSON object, read the way the annotation reads one - a
    # specification is made where its spelling can be told, and where it can't nothing
    # is better than one refusing what is accepted
    while isinstance(keys, AliasAttribute):
        keys = keys.resolved

    match keys:
        case IntegerAttribute():
            return {"type": "string", "pattern": _INTEGER_KEY}

        case FloatAttribute():
            return {"type": "string", "pattern": _NUMBER_KEY}

        case BoolAttribute():
            return {"type": "string", "pattern": _BOOLEAN_KEY}

        case NoneAttribute():
            return {"type": "string", "pattern": _NULL_KEY}

        case UUIDAttribute():
            return {"type": "string", "format": "uuid"}

        case _:
            return _named_key_specification(keys)


def _named_key_specification(
    keys: AttributeAnnotation,
    /,
) -> TypeSpecification | None:
    match keys:
        case StrEnumAttribute():
            # read by its value or by the name of a member
            return {
                "type": "string",
                "enum": _distinct(
                    [
                        *(member.value for member in keys.base),
                        *keys.base.__members__,
                    ]
                ),
            }

        case IntEnumAttribute():
            # the number of a member spelled as a key, or the name of one
            return {
                "type": "string",
                "enum": _distinct(
                    [
                        *(_integer_key(member.value) for member in keys.base),
                        *keys.base.__members__,
                    ]
                ),
            }

        case LiteralAttribute():
            names: list[str] = []
            for element in keys.values:
                spelled: str | None = _literal_key(_literal_json_value(element))
                if spelled is None:
                    return None  # no spelling to tell

                names.append(spelled)

            if not names:
                return None

            return {"type": "string", "enum": _distinct(names)}

        case _:
            return None


def _integer_key(
    value: int,
    /,
) -> str:
    spelled: str | None = nstruct.key_spelling(value)
    assert spelled is not None  # nosec: B101 - every int has a spelling

    return spelled


def _literal_key(
    value: Any,
    /,
) -> str | None:
    # the key a literal is written as - and read from
    spelled: Any = basic_key(value, strict=False)
    if isinstance(spelled, str):
        return spelled

    return None


def _distinct(
    values: Sequence[Any],
    /,
) -> list[Any]:
    return list(dict.fromkeys(values))


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

    if not elements_specification:  # `tuple[()]` - `prefixItems` can't be empty
        return {
            "type": "array",
            "maxItems": 0,
        }

    return {
        "type": "array",
        "prefixItems": elements_specification,
        "items": False,
        "minItems": len(elements_specification),
        "maxItems": len(elements_specification),
    }


def _prepare_specification_of_open_tuple(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification | None:
    open_tuple = cast(OpenTupleAttribute, annotation)
    leading_specification: MutableSequence[TypeSpecification] = []
    for element in open_tuple.leading:
        element_specification: TypeSpecification | None = _specification(
            element,
            recursion_guard=recursion_guard,
        )

        if element_specification is None:
            return None

        leading_specification.append(element_specification)

    # the elements at the end can't be told from the ones in the middle by a schema, so
    # everything after the leading ones is allowed to be any of them
    rest_specification: MutableSequence[TypeSpecification] = []
    for element in (open_tuple.variadic, *open_tuple.trailing):
        element_specification = _specification(
            element,
            recursion_guard=recursion_guard,
        )

        if element_specification is None:
            return None

        if element_specification not in rest_specification:
            rest_specification.append(element_specification)

    items_specification: TypeSpecification = (
        rest_specification[0] if len(rest_specification) == 1 else {"anyOf": rest_specification}
    )

    # the elements which have to be there - a schema can't tell where the ones at the
    # end are, though the number of them is known
    minimum: int = len(open_tuple.leading) + len(open_tuple.trailing)

    if not leading_specification:
        if minimum:
            return {
                "type": "array",
                "items": items_specification,
                "minItems": minimum,
            }

        return {
            "type": "array",
            "items": items_specification,
        }

    return {
        "type": "array",
        "prefixItems": leading_specification,
        "items": items_specification,
        "minItems": minimum,
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
        if isinstance(argument, MissingAttribute):
            continue  # it has no JSON value, the attribute accepting it can be left out

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
            # alternatives can name it more than once - i.e. `int | Annotated[int, ...]`
            compressed_alternatives.append(compressed_type)

    if not alternatives:
        return None  # nothing but `Missing`

    if len(alternatives) == 1:  # `Missing` left out, what remains is the union itself
        return alternatives[0]

    if compressible:
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
    # a boolean, the numbers 0 and 1 and the words in any case - which has to be spelled
    # out, as a pattern of JSON Schema has no flag to ignore it
    return {
        "anyOf": [
            {"type": "boolean"},
            {"enum": [0, 1]},
            {"type": "string", "pattern": "^([Tt][Rr][Uu][Ee]|[Ff][Aa][Ll][Ss][Ee])$"},
        ],
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
    # read by its value or by the name of a member
    members: type[Enum] = cast(StrEnumAttribute, annotation).base
    return {
        "type": "string",
        "enum": _distinct(
            [
                *(member.value for member in members),
                *members.__members__,
            ]
        ),
    }


def _prepare_specification_of_int_enum(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    # read by its value, an int, or by the name of a member - never by the number as text
    members: type[Enum] = cast(IntEnumAttribute, annotation).base
    return {
        "anyOf": [
            {"type": "integer", "enum": [int(member.value) for member in members]},
            {"type": "string", "enum": list(members.__members__)},
        ],
    }


def _prepare_specification_of_bytes(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
        "contentEncoding": "base64",
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
    }


def _prepare_specification_of_time(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
    }


def _prepare_specification_of_timedelta(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
    }


def _prepare_specification_of_path(
    annotation: AttributeAnnotation,
    recursion_guard: MutableMapping[int, _RecursionGuard],
) -> TypeSpecification:
    return {
        "type": "string",
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

        if not element.metadata.required:
            continue

        required.append(key)

    return {
        "type": "object",
        "properties": properties,
        "additionalProperties": False,
        "required": required,
    }


def _state_specification(
    annotation: AttributeAnnotation,
) -> TypeSpecification | None:
    # as the class holds it - its anchors are made unique once in the whole document
    specification = cast(TypeSpecification | None, annotation.base.__JSON_SPECIFICATION__)
    if specification is not None and annotation.description and "description" in specification:
        # the description of the attribute is more specific than the one of its type
        return cast(
            TypeSpecification,
            {**specification, "description": annotation.description},
        )

    return specification


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


def unique_anchors(
    specification: TypeSpecification,
    /,
) -> TypeSpecification:
    # an anchor is named after its type, which other types can share - two recursive
    # aliases of the same shape, two classes of one name in different modules - while
    # an anchor names one subschema of the whole document. A definition met again is
    # referred to instead of repeated, and another one under a taken name is renamed,
    # with the references within it - a reference to a definition is generated within it,
    # or to one whose name is unique in the document
    anchors: _Anchors = _Anchors()
    anchors.reserve(specification)
    return cast(TypeSpecification, anchors.renamed(specification, {}))


class _Anchors:
    __slots__ = ("emitted", "first", "reserved")

    def __init__(self) -> None:
        # the first definition under each anchor - its json with the names the references
        # out of it are bound to - and the name it got
        self.first: MutableMapping[str, tuple[str, str]] = {}
        self.emitted: set[str] = set()
        # every name the document spells - a renamed definition takes none of them, so a
        # reference to a name given once keeps reaching its definition wherever it is
        self.reserved: set[str] = set()

    def reserve(self, node: Any) -> None:
        if isinstance(node, list):
            for element in cast(list[Any], node):
                self.reserve(element)

        elif isinstance(node, dict):
            entries: dict[str, Any] = cast(dict[str, Any], node)
            if isinstance(anchor := entries.get("$anchor"), str):
                self.reserved.add(anchor)

            for key, value in entries.items():
                if key in _ANCHOR_VALUES:
                    continue

                if key in _ANCHOR_NAMED and isinstance(value, dict):
                    for element in cast(dict[str, Any], value).values():
                        self.reserve(element)

                else:
                    self.reserve(value)

    def renamed(self, node: Any, scope: Mapping[str, str]) -> Any:
        if isinstance(node, list):
            return [self.renamed(element, scope) for element in cast(list[Any], node)]

        if not isinstance(node, dict):
            return node

        entries: dict[str, Any] = cast(dict[str, Any], node)
        anchor: Any = entries.get("$anchor")
        if isinstance(anchor, str):
            # bool and int are told apart, as their json is - and a definition is the same
            # one only when the references out of it lead to the same definitions as well
            identity: str = json.dumps([entries, sorted(scope.items())], sort_keys=True)
            if (met := self.first.get(anchor)) is not None and met[0] == identity:
                return {"$ref": f"#{met[1]}"}

            scope = {**scope, anchor: self.named(anchor, identity)}

        result: dict[str, Any] = {
            key: self.renamed_entry(key, value, scope) for key, value in entries.items()
        }
        if isinstance(anchor, str):
            result["$anchor"] = scope[anchor]

        reference: Any = entries.get("$ref")
        if isinstance(reference, str) and reference.startswith("#"):
            result["$ref"] = f"#{scope.get(reference[1:], reference[1:])}"

        return result

    def named(self, anchor: str, identity: str) -> str:
        unique: str = anchor
        if unique in self.emitted:
            suffix: int = 2
            unique = f"{anchor}_{suffix}"
            while unique in self.emitted or unique in self.reserved:
                suffix += 1
                unique = f"{anchor}_{suffix}"

        self.emitted.add(unique)
        self.first.setdefault(anchor, (identity, unique))
        return unique

    def renamed_entry(self, key: str, value: Any, scope: Mapping[str, str]) -> Any:
        if key in _ANCHOR_VALUES:
            return value

        if key in _ANCHOR_NAMED and isinstance(value, dict):
            # keys are names of attributes, not keywords - each value is a schema
            return {
                name: self.renamed(element, scope)
                for name, element in cast(dict[str, Any], value).items()
            }

        return self.renamed(value, scope)


# the values of a user, not schemas - left as they are
_ANCHOR_VALUES: frozenset[str] = frozenset(("enum", "const", "default", "examples"))
_ANCHOR_NAMED: frozenset[str] = frozenset(
    ("properties", "patternProperties", "$defs", "definitions", "dependentSchemas")
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
    SetAttribute: _prepare_specification_of_set,
    TupleAttribute: _prepare_specification_of_tuple,
    OpenTupleAttribute: _prepare_specification_of_open_tuple,
    MappingAttribute: _prepare_specification_of_mapping,
    TypedDictAttribute: _prepare_specification_of_typed_dict,
    UnionAttribute: _prepare_specification_of_union,
    ValidableAttribute: _prepare_specification_of_validable,
    BytesAttribute: _prepare_specification_of_bytes,
    UUIDAttribute: _prepare_specification_of_uuid,
    DateAttribute: _prepare_specification_of_date,
    DatetimeAttribute: _prepare_specification_of_datetime,
    TimeAttribute: _prepare_specification_of_time,
    TimedeltaAttribute: _prepare_specification_of_timedelta,
    PathAttribute: _prepare_specification_of_path,
    MetaAttribute: _prepare_specification_of_meta,
    CustomAttribute: _prepare_specification_of_custom,
}
