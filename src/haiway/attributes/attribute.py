from typing import Any, final

from haiway._native import nstruct
from haiway.attributes.annotations import AttributeAnnotation
from haiway.attributes.specification import type_specification
from haiway.types import (
    DefaultValue,
    Missing,
    TypeSpecification,
)
from haiway.types.frozen import same_copy, same_deepcopy

__all__ = ("Attribute",)


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
        self.name = name
        self.annotation = annotation
        self.default = default
        self.spec = spec
        self.redaction = redaction

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
        return self.annotation.required and not self.default.available

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
