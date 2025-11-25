from collections.abc import Mapping
from typing import (
    Any,
)

from haiway.attributes.annotations import AttributeAnnotation
from haiway.attributes.specification import type_specification
from haiway.types import (
    MISSING,
    DefaultValue,
    Immutable,
    TypeSpecification,
)

__all__ = ("Attribute",)


class Attribute(Immutable):
    name: str
    annotation: AttributeAnnotation
    default: DefaultValue

    @property
    def alias(self) -> str | None:
        return self.annotation.alias

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
        if value is MISSING:
            return self.annotation.validate(self.default())

        else:
            return self.annotation.validate(value)

    def validate_from(
        self,
        mapping: Mapping[str, Any],
        /,
    ) -> Any:
        value: Any
        if self.alias is None:
            value = mapping.get(
                self.name,
                self.default(),
            )

        else:
            value = mapping.get(
                self.alias,
                mapping.get(
                    self.name,
                    self.default(),
                ),
            )

        return self.annotation.validate(value)
