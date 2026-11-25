from typing import Any, final

from haiway._native import nstruct
from haiway.attributes.annotations import (
    AliasAttribute,
    AnyAttribute,
    AttributeAnnotation,
    CustomAttribute,
    MissingAttribute,
    UnionAttribute,
    ValidableAttribute,
)
from haiway.attributes.specification import type_specification
from haiway.types import (
    DefaultValue,
    Missing,
    TypeSpecification,
)
from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = ("Attribute",)

# bound once, the attributes are made for each of the attributes of each class
_bind = object.__setattr__


# whether the annotation validates `Missing`, which is what omitting a value yields. neither
# a validator nor a verifier of the annotation is asked - a validator receives the raw input
# and a verifier the validated value, either may refuse `Missing` or replace it, which is
# why an annotation holding one, at any level, is not considered omittable
def _accepts_missing(
    annotation: AttributeAnnotation,
    /,
    visited: set[int] | None = None,
) -> bool:
    match annotation:
        case AnyAttribute() | MissingAttribute():
            return not _may_refuse_missing(annotation)

        case UnionAttribute() | AliasAttribute():
            return _accepts_missing_within(annotation, visited if visited is not None else set())

        case CustomAttribute():
            return not _may_refuse_missing(annotation) and _custom_accepts_missing(annotation)

        case _:
            return False  # what most are


def _accepts_missing_within(
    annotation: UnionAttribute | AliasAttribute,
    /,
    visited: set[int],
) -> bool:
    if id(annotation) in visited or _may_refuse_missing(annotation):
        # a recursive alias accepts it only through itself, code of the user may refuse it
        return False

    visited.add(id(annotation))
    if isinstance(annotation, UnionAttribute):
        return any(
            _accepts_missing(alternative, visited) for alternative in annotation.alternatives
        )

    return _accepts_missing(annotation.resolved, visited)


def _custom_accepts_missing(
    annotation: CustomAttribute,
    /,
) -> bool:
    # `object` and the like - a class validating on its own is asked to
    return not hasattr(annotation.base, "validate") and _is_instance(Missing, annotation.base)


def _may_refuse_missing(
    annotation: AttributeAnnotation,
    /,
) -> bool:
    return isinstance(annotation, ValidableAttribute) or annotation.metadata.verifying is not None


def _is_instance(
    value: Any,
    base: Any,
    /,
) -> bool:
    try:
        return isinstance(value, base)

    except TypeError:  # not a class instances can be checked against
        return False


@final
class Attribute:
    name: str
    annotation: AttributeAnnotation
    default: DefaultValue
    spec: Any
    redaction: str | None
    alias: str | None
    key: str
    __slots__ = (
        "alias",
        "annotation",
        "default",
        "key",
        "name",
        "redaction",
        "spec",
    )

    def __init__(
        self,
        *,
        name: str,
        annotation: AttributeAnnotation,
        default: DefaultValue,
        spec: Any,
        redaction: str | None = None,
    ) -> None:
        _bind(self, "name", name)
        _bind(self, "annotation", annotation)
        _bind(self, "default", default)
        _bind(self, "spec", spec)
        _bind(self, "redaction", redaction)
        # read for each rendering and whenever the class is worked with - held, not asked
        # of the annotation each time. the key is the name this attribute is rendered or
        # serialized under: the alias when one was declared, the declared name otherwise
        alias: str | None = annotation.alias
        _bind(self, "alias", alias)
        _bind(self, "key", alias if alias is not None else name)

    @property
    def description(self) -> str | None:
        return self.annotation.description

    @property
    def required(self) -> bool:
        # an attribute can be left out when it has a default or when it accepts
        # `Missing` - it is what both the schema and the signature derive from
        return not self.default.available and not _accepts_missing(self.annotation)

    @property
    def specification(self) -> TypeSpecification | None:
        specification: TypeSpecification | None = self.annotation.specification
        if specification is None:
            specification = type_specification(self.annotation)

        return specification

    def validate(
        self,
        value: Any,
        /,
    ) -> Any:
        if value is Missing:
            return self._validated_default()

        else:
            return self._validate(value)

    def _validate(
        self,
        value: Any,
        /,
    ) -> Any:
        return nstruct.validate(self.spec, value)

    def _validated_default(self) -> Any:
        return self._validate(self.default())

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
