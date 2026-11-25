from typing import Any, final

from haiway._native import nstruct
from haiway.attributes.annotations import (
    AliasAttribute,
    AnyAttribute,
    AttributeAnnotation,
    CustomAttribute,
    MissingAttribute,
    UnionAttribute,
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


# whether the annotation validates `Missing`, which is what omitting a value yields. a
# validator of the annotation is not asked - it receives the raw input and may refuse
# `Missing` or replace it, which is why such an attribute is not considered omittable
def _accepts_missing(
    annotation: AttributeAnnotation,
    /,
    visited: set[int],
) -> bool:
    if id(annotation) in visited:
        return False  # a recursive alias, accepting it only through itself

    visited.add(id(annotation))
    match annotation:
        case AnyAttribute() | MissingAttribute():
            return True

        case UnionAttribute():
            return any(
                _accepts_missing(alternative, visited) for alternative in annotation.alternatives
            )

        case AliasAttribute():
            return _accepts_missing(annotation.resolved, visited)

        case CustomAttribute():
            # `object` and the like - a class validating on its own is asked to
            return not hasattr(annotation.base, "validate") and _is_instance(
                Missing, annotation.base
            )

        case _:
            return False


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
    __slots__ = (
        "annotation",
        "default",
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

    @property
    def alias(self) -> str | None:
        return self.annotation.alias

    @property
    def key(self) -> str:
        # the name this attribute is keyed by when rendered or serialized - the
        # alias when one was declared, the declared name otherwise
        alias: str | None = self.annotation.alias
        return alias if alias is not None else self.name

    @property
    def description(self) -> str | None:
        return self.annotation.description

    @property
    def required(self) -> bool:
        # an attribute can be left out when it has a default or when it accepts
        # `Missing` - it is what both the schema and the signature derive from
        return not self.default.available and not _accepts_missing(self.annotation, set())

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
