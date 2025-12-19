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


@final
class AlternativesSpecification(TypedDict, total=False):
    type: Required[
        Sequence[Literal["string", "number", "integer", "boolean", "null", "object", "array"]]
    ]
    description: NotRequired[str]


@final
class NoneSpecification(TypedDict, total=False):
    type: Required[Literal["null"]]
    description: NotRequired[str]


@final
class BoolSpecification(TypedDict, total=False):
    type: Required[Literal["boolean"]]
    description: NotRequired[str]


@final
class IntegerSpecification(TypedDict, total=False):
    type: Required[Literal["integer"]]
    description: NotRequired[str]


@final
class NumberSpecification(TypedDict, total=False):
    type: Required[Literal["number"]]
    description: NotRequired[str]


@final
class StringSpecification(TypedDict, total=False):
    type: Required[Literal["string"]]
    format: NotRequired[
        Literal[
            "uri",
            "uuid",
            "date",
            "time",
            "date-time",
        ]
    ]
    description: NotRequired[str]


@final
class StringEnumSpecification(TypedDict, total=False):
    type: Required[Literal["string"]]
    enum: Required[Sequence[str]]
    description: NotRequired[str]


@final
class IntegerEnumSpecification(TypedDict, total=False):
    type: Required[Literal["integer"]]
    enum: Required[Sequence[int]]
    description: NotRequired[str]


@final
class NumberEnumSpecification(TypedDict, total=False):
    type: Required[Literal["number"]]
    enum: Required[Sequence[float]]
    description: NotRequired[str]


@final
class UnionSpecification(TypedDict, total=False):
    oneOf: Required[Sequence["TypeSpecification"]]
    description: NotRequired[str]


@final
class ArraySpecification(TypedDict, total=False):
    type: Required[Literal["array"]]
    items: NotRequired["TypeSpecification"]
    description: NotRequired[str]


@final
class TupleSpecification(TypedDict, total=False):
    type: Required[Literal["array"]]
    prefixItems: Required[Sequence["TypeSpecification"]]
    items: Required[Literal[False]]
    description: NotRequired[str]


@final
class DictSpecification(TypedDict, total=False):
    type: Required[Literal["object"]]
    additionalProperties: Required["TypeSpecification"]
    required: NotRequired[Sequence[str]]
    description: NotRequired[str]


@final
class ObjectSpecification(TypedDict, total=False):
    type: Required[Literal["object"]]
    properties: Required[Mapping[str, "TypeSpecification"]]
    additionalProperties: Required[Literal[False]]
    required: NotRequired[Sequence[str]]
    title: NotRequired[str]
    description: NotRequired[str]


@final
class AnyObjectSpecification(TypedDict, total=False):
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
    | NumberEnumSpecification
    | NumberSpecification
    | BoolSpecification
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
    Immutable wrapper ensuring a `TypeSpecification` instance remains intact.

    Parameters
    ----------
    specification : TypeSpecification
        Underlying type specification describing the accepted structure.


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
