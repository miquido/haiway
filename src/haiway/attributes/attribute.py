from collections.abc import Mapping
from typing import (
    Any,
)

from haiway.attributes.annotations import (
    AttributeAnnotation,
)
from haiway.types import (
    MISSING,
    DefaultValue,
    Immutable,
    TypeSpecification,
)

__all__ = ("Attribute",)


class Attribute(Immutable):
    name: str
    alias: str | None
    annotation: AttributeAnnotation
    required: bool
    default: DefaultValue
    specification: TypeSpecification | None

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
