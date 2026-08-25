from collections.abc import Mapping, Sequence
from typing import (
    Any,
    Literal,
    NoReturn,
    NotRequired,
    Required,
    TypedDict,
    final,
)

__all__ = (
    "Specification",
    "TypeSpecification",
)


# a specification which recursive references point to carries the anchor naming it,
# spelled with the functional form - `$anchor` is not a valid identifier
AnchoredSpecification = TypedDict(
    "AnchoredSpecification",
    {
        "$anchor": NotRequired[str],
    },
    total=False,
)


@final
class AlternativesSpecification(AnchoredSpecification, total=False):
    type: Required[
        Sequence[Literal["string", "number", "integer", "boolean", "null", "object", "array"]]
    ]
    description: NotRequired[str]


@final
class NoneSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["null"]]
    description: NotRequired[str]


@final
class BoolSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["boolean"]]
    description: NotRequired[str]


@final
class IntegerSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["integer"]]
    description: NotRequired[str]


@final
class NumberSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["number"]]
    description: NotRequired[str]


@final
class StringSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["string"]]
    format: NotRequired[
        Literal[
            "uri",
            "path",
            "uuid",
            "date",
            "time",
            "date-time",
        ]
    ]
    description: NotRequired[str]


@final
class StringEnumSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["string"]]
    enum: Required[Sequence[str]]
    description: NotRequired[str]


@final
class IntegerEnumSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["integer"]]
    enum: Required[Sequence[int]]
    description: NotRequired[str]


@final
class NumberEnumSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["number"]]
    enum: Required[Sequence[float]]
    description: NotRequired[str]


@final
class BoolEnumSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["boolean"]]
    enum: Required[Sequence[bool]]
    description: NotRequired[str]


@final
class EnumSpecification(AnchoredSpecification, total=False):
    enum: Required[Sequence[Any]]
    description: NotRequired[str]


@final
class UnionSpecification(AnchoredSpecification, total=False):
    anyOf: Required[Sequence[TypeSpecification]]
    description: NotRequired[str]


@final
class ArraySpecification(AnchoredSpecification, total=False):
    type: Required[Literal["array"]]
    items: NotRequired[TypeSpecification]
    description: NotRequired[str]


@final
class TupleSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["array"]]
    prefixItems: Required[Sequence[TypeSpecification]]
    items: Required[Literal[False]]
    description: NotRequired[str]


@final
class DictSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["object"]]
    additionalProperties: Required[TypeSpecification]
    required: NotRequired[Sequence[str]]
    description: NotRequired[str]


@final
class ObjectSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["object"]]
    properties: Required[Mapping[str, TypeSpecification]]
    additionalProperties: Required[Literal[False]]
    required: NotRequired[Sequence[str]]
    title: NotRequired[str]
    description: NotRequired[str]


@final
class AnyObjectSpecification(AnchoredSpecification, total=False):
    type: Required[Literal["object"]]
    additionalProperties: Required[Literal[True]]
    description: NotRequired[str]


ReferenceSpecification = TypedDict(
    "ReferenceSpecification",
    {
        "$ref": Required[str],
        "description": NotRequired[str],
    },
    total=False,
)

# JSON-schema compatible
type TypeSpecification = (
    AlternativesSpecification
    | UnionSpecification
    | NoneSpecification
    | StringEnumSpecification
    | StringSpecification
    | IntegerEnumSpecification
    | IntegerSpecification
    | BoolEnumSpecification
    | NumberEnumSpecification
    | NumberSpecification
    | BoolSpecification
    | EnumSpecification
    | TupleSpecification
    | ArraySpecification
    | ObjectSpecification
    | DictSpecification
    | AnyObjectSpecification
    | ReferenceSpecification
)


@final
class Specification:
    """
    Immutable wrapper for a JSON-schema-like ``TypeSpecification`` fragment.

    Haiway consumes ``Specification`` most commonly through
    ``typing.Annotated[...]`` on ``State`` fields. The wrapper keeps the schema
    fragment immutable and typed, but it does not deeply validate every schema
    keyword beyond requiring a non-empty specification object.

    Parameters
    ----------
    specification : TypeSpecification
        Underlying schema fragment describing the accepted serialized structure.

    Raises
    ------
    AssertionError
        If an empty specification is provided.


    Examples
    --------
    >>> with_specification: Annotated[str, Specification(...)]
    """

    __slots__ = ("specification",)

    def __init__(
        self,
        specification: TypeSpecification,
        /,
    ) -> None:
        assert specification  # nosec: B101

        self.specification: TypeSpecification
        object.__setattr__(
            self,
            "specification",
            specification,
        )

    def __setattr__(
        self,
        __name: str,
        __value: Any,
    ) -> NoReturn:
        raise AttributeError("Specification can't be modified")

    def __delattr__(
        self,
        __name: str,
    ) -> NoReturn:
        raise AttributeError("Specification can't be modified")
