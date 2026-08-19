from collections.abc import Mapping
from typing import Any

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
    # text rendered instead of the value when the attribute is marked sensitive,
    # resolved once from the annotation - the renderers read it on each rendering
    redaction: str | None = None

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
        if value is MISSING:
            return self._validated_default()

        else:
            return self.annotation.validate(value)

    def validate_from(
        self,
        mapping: Mapping[str, Any],
        /,
    ) -> Any:
        if self.alias is not None and self.alias in mapping:
            return self.annotation.validate(mapping[self.alias])

        elif self.name in mapping:
            return self.annotation.validate(mapping[self.name])

        else:
            return self._validated_default()

    def _validated_default(self) -> Any:
        value: Any = self.default()
        try:
            return self.annotation.validate(value)

        except Exception as exc:
            # an environment backed default resolves to MISSING when the variable
            # is not set - reporting the type mismatch would name the annotation
            # instead of the variable which actually has to be provided
            if value is MISSING and self.default.env is not None:
                raise ValueError(
                    f"Required environment value `{self.default.env}` is missing!"
                ) from exc

            raise
