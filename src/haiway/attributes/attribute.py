from typing import Any, final

from haiway._native import nstruct
from haiway.attributes.annotations import AttributeAnnotation
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
        # an attribute can be left out when it has a default or when its spec accepts
        # `Missing` - it is what both the schema and the signature derive from
        return not self.default.available and not nstruct.accepts_missing(self.spec)

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
