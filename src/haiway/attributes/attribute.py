from collections.abc import Mapping
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


@final
class Attribute:
    name: str
    annotation: AttributeAnnotation
    default: DefaultValue
    spec: Any
    redaction: str | None
    __slots__ = ("annotation", "default", "name", "redaction", "spec")

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion
    __copy__ = same_copy
    __deepcopy__ = same_deepcopy

    def __init__(
        self,
        *,
        name: str,
        annotation: AttributeAnnotation,
        default: DefaultValue,
        spec: Any,
        redaction: str | None = None,
    ) -> None:
        object.__setattr__(self, "name", name)
        object.__setattr__(self, "annotation", annotation)
        object.__setattr__(self, "default", default)
        object.__setattr__(self, "spec", spec)
        object.__setattr__(self, "redaction", redaction)

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

    def validate_from(
        self,
        mapping: Mapping[str, Any],
        /,
    ) -> Any:
        if self.alias is not None and self.alias in mapping:
            return self._validate(mapping[self.alias])

        elif self.name in mapping:
            return self._validate(mapping[self.name])

        else:
            return self._validated_default()

    def _validate(
        self,
        value: Any,
        /,
    ) -> Any:
        return nstruct.validate(self.spec, value)

    def _validated_default(self) -> Any:
        value: Any = self.default()
        try:
            return self._validate(value)

        except Exception as exc:
            # an environment backed default resolves to Missing when the variable
            # is not set - reporting the type mismatch would name the annotation
            # instead of the variable which actually has to be provided
            if value is Missing and self.default.env is not None:
                raise ValueError(
                    f"Required environment value `{self.default.env}` is missing!"
                ) from exc

            raise
