import builtins
import datetime
import enum
import os
import pathlib
import sys
import types
import typing
import uuid
from collections import abc as collections_abc
from collections.abc import (
    Generator,
    Hashable,
    Iterable,
    Mapping,
    MutableMapping,
    MutableSequence,
    Sequence,
    Set,
)
from types import GenericAlias
from typing import (
    Any,
    ClassVar,
    Final,
    ForwardRef,
    Literal,
    Protocol,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

import typing_extensions
from typing_extensions import is_typeddict as is_typeddict_ext  # keep this particular import

from haiway import types as haiway_types
from haiway.attributes.validation import (
    Validating,
    ValidationContext,
    Validator,
    Verifier,
    Verifying,
)
from haiway.types import (
    META_EMPTY,
    MISSING,
    Alias,
    Description,
    Immutable,
    Map,
    Meta,
    Specification,
    TypeSpecification,
)

__all__ = (
    "AliasAttribute",
    "AnyAttribute",
    "AttributeAnnotation",
    "BoolAttribute",
    "BytesAttribute",
    "CustomAttribute",
    "DateAttribute",
    "DatetimeAttribute",
    "FloatAttribute",
    "FunctionAttribute",
    "IntEnumAttribute",
    "IntegerAttribute",
    "LiteralAttribute",
    "MappingAttribute",
    "MetaAttribute",
    "MissingAttribute",
    "NoneAttribute",
    "NotRequired",
    "ObjectAttribute",
    "PathAttribute",
    "ProtocolAttribute",
    "SequenceAttribute",
    "SetAttribute",
    "StrEnumAttribute",
    "StringAttribute",
    "TimeAttribute",
    "TupleAttribute",
    "TypedDictAttribute",
    "UUIDAttribute",
    "UnionAttribute",
    "ValidableAttribute",
    "resolve_attribute",
    "resolve_self_attribute",
)


class NotRequired(Immutable):
    pass


NOT_REQUIRED: Final[NotRequired] = NotRequired()

Annotation = (
    Alias | Description | Specification | Validator[Any] | Verifier[Any] | Meta | NotRequired | str
)


class AttributeAnnotation(Protocol):
    @property
    def type_name(self) -> str: ...

    @property
    def base(self) -> Any: ...

    @property
    def alias(self) -> str | None: ...

    @property
    def description(self) -> str | None: ...

    @property
    def specification(self) -> TypeSpecification | None: ...

    @property
    def meta(self) -> Meta: ...

    @property
    def required(self) -> bool: ...

    def annotated(
        self,
        annotations: Sequence[Any],
    ) -> "AttributeAnnotation": ...

    def validate(
        self,
        value: Any,
    ) -> Any: ...

    def check(
        self,
        value: Any,
    ) -> bool: ...


def _no_verify[Type](value: Type) -> Type:
    return value


class AnyAttribute(Immutable):
    type_name: Final[Literal["Any"]] = "Any"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> Any:
        return typing.Any

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        return self.verifying(value)  # Any is always valid

    def check(
        self,
        value: Any,
    ) -> bool:
        return True  # Any is always valid


class AliasAttribute(Immutable):
    type_alias: str
    module: str
    annotations: Sequence[Annotation] = ()
    _resolved: AttributeAnnotation | None = None

    @property
    def type_name(self) -> str:
        return f"{self.module}.{self.type_alias}"

    @property
    def base(self) -> Any:
        assert self._resolved is not None  # nosec: B101
        return self._resolved.base

    @property
    def alias(self) -> str | None:
        if self._resolved is None:
            for annotation in self.annotations:
                if isinstance(annotation, Alias):
                    return annotation.alias

            return self.type_alias

        alias: str | None = self._resolved.alias
        if alias is not None:
            return alias

        return self.type_alias

    @property
    def description(self) -> str | None:
        if self._resolved is None:
            for annotation in self.annotations:
                if isinstance(annotation, str):
                    return annotation

                elif isinstance(annotation, Description):
                    return annotation.description

            return None

        return self._resolved.description

    @property
    def specification(self) -> TypeSpecification | None:
        if self._resolved is None:
            for annotation in self.annotations:
                if isinstance(annotation, Specification):
                    return annotation.specification

            return None

        return self._resolved.specification

    @property
    def meta(self) -> Meta:
        if self._resolved is None:
            for annotation in self.annotations:
                if isinstance(annotation, Meta):
                    return annotation

            return META_EMPTY

        return self._resolved.meta

    @property
    def required(self) -> bool:
        if self._resolved is None:
            return not any(isinstance(annotation, NotRequired) for annotation in self.annotations)

        return self._resolved.required

    def annotated(
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        return self.__class__(
            type_alias=self.type_alias,
            module=self.module,
            annotations=annotations,
            _resolved=self._resolved.annotated(annotations) if self._resolved is not None else None,
        )

    def resolve(
        self,
        target: AttributeAnnotation,
    ) -> None:
        assert self._resolved is None  # nosec: B101
        if self.annotations:
            object.__setattr__(
                self,
                "_resolved",
                target.annotated(self.annotations),
            )

        else:
            object.__setattr__(
                self,
                "_resolved",
                target,
            )

    @property
    def resolved(self) -> AttributeAnnotation:
        if self._resolved is None:
            raise RuntimeError(f"Alias '{self.module}.{self.type_alias}' used before resolution")

        return self._resolved

    def validate(
        self,
        value: Any,
    ) -> Any:
        return self.resolved.validate(value)

    def check(
        self,
        value: Any,
    ) -> bool:
        return self.resolved.check(value)


class MissingAttribute(Immutable):
    type_name: Final[Literal["Missing"]] = "Missing"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = False
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[haiway_types.Missing]:
        return haiway_types.Missing

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if value is MISSING:
            return value

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'Missing'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return value is MISSING


class NoneAttribute(Immutable):
    type_name: Final[Literal["None"]] = "None"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> None:
        return None

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if value is None:
            return self.verifying(value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'None'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return value is None


class LiteralAttribute(Immutable):
    base: Any
    values: Sequence[Any]
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return f"Literal[{', '.join(repr(value) for value in self.values)}]"

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if value in self.values:
            return self.verifying(value)

        raise ValueError(
            f"'{value}' is not matching any of expected literal values"
            f" [{', '.join(repr(literal) for literal in self.values)}]"
        )

    def check(
        self,
        value: Any,
    ) -> bool:
        return value in self.values


class BoolAttribute(Immutable):
    type_name: Final[Literal["bool"]] = "bool"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[bool]:
        return bool

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, bool):
            return self.verifying(value)

        elif isinstance(value, int):
            return self.verifying(value != 0)

        elif isinstance(value, str):
            if value.lower() == "true":
                return self.verifying(True)

            if value.lower() == "false":
                return self.verifying(False)

            raise ValueError(f"'{value}' is not matching any of expected values [True, False]")

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'bool'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, bool)


class IntegerAttribute(Immutable):
    type_name: Final[Literal["int"]] = "int"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[int]:
        return int

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, int):
            return self.verifying(value)

        elif isinstance(value, float) and value.is_integer():
            return self.verifying(int(value))

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'int'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, int)


class FloatAttribute(Immutable):
    type_name: Final[Literal["float"]] = "float"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[float]:
        return float

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, float):
            return self.verifying(value)

        elif isinstance(value, int):
            return self.verifying(float(value))

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'float'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, float)


class BytesAttribute(Immutable):
    type_name: Final[Literal["bytes"]] = "bytes"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[bytes]:
        return bytes

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, bytes):
            return self.verifying(value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'bytes'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, bytes)


class UUIDAttribute(Immutable):
    type_name: Final[Literal["UUID"]] = "UUID"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[uuid.UUID]:
        return uuid.UUID

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, uuid.UUID):
            return self.verifying(value)

        elif isinstance(value, str):
            try:
                return self.verifying(uuid.UUID(value))

            except Exception as exc:
                raise ValueError(f"'{value}' is not matching expected format of 'UUID'") from exc

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'UUID'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, uuid.UUID)


class StringAttribute(Immutable):
    type_name: Final[Literal["str"]] = "str"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[str]:
        return str

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, str):
            return self.verifying(value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'str'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, str)


class DatetimeAttribute(Immutable):
    type_name: Final[Literal["datetime"]] = "datetime"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[datetime.datetime]:
        return datetime.datetime

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, datetime.datetime):
            return self.verifying(value)

        elif isinstance(value, str):
            try:
                return self.verifying(datetime.datetime.fromisoformat(value))

            except Exception as exc:
                raise ValueError(
                    f"'{value}' is not matching expected ISO format for 'datetime'"
                ) from exc

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'datetime'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, datetime.datetime)


class DateAttribute(Immutable):
    type_name: Final[Literal["date"]] = "date"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[datetime.date]:
        return datetime.date

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, datetime.date):
            return self.verifying(value)

        elif isinstance(value, str):
            try:
                return self.verifying(datetime.date.fromisoformat(value))

            except Exception as exc:
                raise ValueError(
                    f"'{value}' is not matching expected ISO format for 'date'"
                ) from exc

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'date'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, datetime.date)


class TimeAttribute(Immutable):
    type_name: Final[Literal["time"]] = "time"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[datetime.time]:
        return datetime.time

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, datetime.time):
            return self.verifying(value)

        elif isinstance(value, str):
            try:
                return self.verifying(datetime.time.fromisoformat(value))

            except Exception as exc:
                raise ValueError(
                    f"'{value}' is not matching expected ISO format for 'time'"
                ) from exc

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'time'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, datetime.time)


class PathAttribute(Immutable):
    type_name: Final[Literal["Path"]] = "Path"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[pathlib.Path]:
        return pathlib.Path

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, pathlib.Path):
            return self.verifying(value)

        elif isinstance(value, str | os.PathLike):  # pyright: ignore[reportUnknownArgumentType]
            try:
                return self.verifying(pathlib.Path(value))  # pyright: ignore[reportUnknownArgumentType]

            except Exception as exc:
                raise ValueError(f"'{value}' is not matching expected path format") from exc

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'Path'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, pathlib.Path)


class TupleAttribute(Immutable):
    type_name: Final[Literal["tuple"]] = "tuple"
    base: type[Sequence[Any]]
    values: Sequence[AttributeAnnotation]
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, str | bytes | bytearray | memoryview):
            raise TypeError(f"'{value}' is not matching expected type of 'tuple'")

        if isinstance(value, Sequence):
            if len(value) != len(self.values):  # pyright: ignore[reportUnknownArgumentType]
                raise ValueError(
                    f"'{value}' does not match expected tuple length {len(self.values)}"
                )

            def validated() -> Generator[Any]:
                for idx, element in enumerate(value):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                    with ValidationContext.scope(f"[{idx}]"):
                        yield self.values[idx].validate(element)

            return self.verifying(tuple(validated()))

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'tuple'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return (
            isinstance(value, Sequence)
            and len(value) == len(self.values)  # pyright: ignore[reportUnknownArgumentType]
            and all(element.check(value[idx]) for idx, element in enumerate(self.values))
        )


class SequenceAttribute(Immutable):
    type_name: Final[Literal["Sequence"]] = "Sequence"
    base: type[Sequence[Any]]
    values: AttributeAnnotation
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, str | bytes | bytearray | memoryview):
            raise TypeError(f"'{value}' is not matching expected type of 'Sequence'")

        if isinstance(value, Sequence):

            def validated() -> Generator[Any]:
                for idx, element in enumerate(value):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                    with ValidationContext.scope(f"[{idx}]"):
                        yield self.values.validate(element)

            return self.verifying(tuple(validated()))

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'Sequence'")

    def check(
        self,
        value: Any,
    ) -> bool:
        if isinstance(value, str | bytes | bytearray | memoryview):
            return False

        return isinstance(value, Sequence) and all(self.values.check(element) for element in value)  # pyright: ignore[reportUnknownVariableType]


class SetAttribute(Immutable):
    type_name: Final[Literal["Set"]] = "Set"
    base: type[Set[Any]]
    values: AttributeAnnotation
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, str | bytes | bytearray | memoryview):
            raise TypeError(f"'{value}' is not matching expected type of 'Set'")

        if isinstance(value, Iterable):

            def validated() -> Generator[Any]:
                for idx, element in enumerate(value):  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
                    with ValidationContext.scope(f"[{idx}]"):
                        yield self.values.validate(element)

            return self.verifying(frozenset(validated()))

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'Set'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, Set) and all(self.values.check(element) for element in value)  # pyright: ignore[reportUnknownVariableType]


class MappingAttribute(Immutable):
    type_name: Final[Literal["Mapping"]] = "Mapping"
    base: type[Mapping[Any, Any]]
    keys: AttributeAnnotation
    values: AttributeAnnotation
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    keys=self.keys,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    keys=self.keys,
                    values=self.values,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, collections_abc.Mapping | typing.Mapping | typing_extensions.Mapping):

            def validated() -> Generator[Any]:
                for key, element in value.items():  # pyright: ignore[reportUnknownVariableType]
                    with ValidationContext.scope(f"[{key}]"):
                        yield (self.keys.validate(key), self.values.validate(element))

            return self.verifying(Map(validated()))

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'Mapping'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, Mapping) and all(
            self.keys.check(key) and self.values.check(element)
            for key, element in value.items()  # pyright: ignore[reportUnknownVariableType]
        )


class MetaAttribute(Immutable):
    type_name: Final[Literal["Meta"]] = "Meta"
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def base(self) -> type[haiway_types.Meta]:
        return haiway_types.Meta

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, Meta):
            return self.verifying(value)

        elif isinstance(
            value, collections_abc.Mapping | typing.Mapping | typing_extensions.Mapping
        ):
            return self.verifying(Meta.validate(value))

        else:
            raise TypeError(f"'{value}' is not matching expected type of 'Meta'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, Meta)


class ValidableAttribute(Immutable):
    attribute: AttributeAnnotation
    validating: Validating[Any]

    @property
    def type_name(self) -> str:
        return self.attribute.type_name

    @property
    def base(self) -> Any:
        return self.attribute.base

    @property
    def alias(self) -> str | None:
        return self.attribute.alias

    @property
    def description(self) -> str | None:
        return self.attribute.description

    @property
    def specification(self) -> TypeSpecification | None:
        return self.attribute.specification

    @property
    def meta(self) -> Meta:
        return self.attribute.meta

    @property
    def required(self) -> bool:
        return self.attribute.required

    def annotated(
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            return self.__class__(
                attribute=self.attribute.annotated(annotations),
                validating=self.validating,
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        return self.attribute.validate(self.validating(value))

    def check(
        self,
        value: Any,
    ) -> bool:
        return self.attribute.check(value)


class ObjectAttribute(Immutable):
    base: Any
    parameters: Sequence[AttributeAnnotation] = ()
    attributes: Mapping[str, AttributeAnnotation]
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return self.base.__qualname__

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    parameters=self.parameters,
                    attributes=self.attributes,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    parameters=self.parameters,
                    attributes=self.attributes,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, self.base):
            return self.verifying(value)

        elif isinstance(
            value, collections_abc.Mapping | typing.Mapping | typing_extensions.Mapping
        ):
            return self.verifying(self.base(**value))

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{self.base}'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, self.base)


class TypedDictAttribute(Immutable):
    base: Any
    parameters: Sequence[AttributeAnnotation] = ()
    attributes: Mapping[str, AttributeAnnotation]
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return self.base.__qualname__

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    parameters=self.parameters,
                    attributes=self.attributes,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    parameters=self.parameters,
                    attributes=self.attributes,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, collections_abc.Mapping | typing.Mapping | typing_extensions.Mapping):

            def validated() -> Generator[Any]:
                for key, attribute in self.attributes.items():
                    with ValidationContext.scope(f'["{key}"]'):
                        if key in value:
                            yield (key, attribute.validate(value[key]))

                        elif attribute.required:
                            raise KeyError(f"Value for '{key}' is required")

            return self.verifying(Map(validated()))

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{self.base}'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, Mapping) and all(
            (key in value and attribute.check(value[key])) or not attribute.required
            for key, attribute in self.attributes.items()
        )


class FunctionAttribute(Immutable):
    base: Any
    arguments: Sequence[AttributeAnnotation]
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return self.base.__name__

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    arguments=self.arguments,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    arguments=self.arguments,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if callable(value):
            # TODO: Verify signature using inspect?
            return self.verifying(value)

        else:
            raise TypeError(f"'{value}' is not matching expected function type")

    def check(
        self,
        value: Any,
    ) -> bool:
        return callable(value)


class ProtocolAttribute(Immutable):
    base: Any
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return self.base.__qualname__

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, self.base):
            return self.verifying(value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{self.base}'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, self.base)


class UnionAttribute(Immutable):
    base: Any
    alternatives: Sequence[AttributeAnnotation] = ()
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return "|".join(alt.type_name for alt in self.alternatives)

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    alternatives=self.alternatives,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    alternatives=self.alternatives,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        errors: MutableSequence[Exception] = []
        for alternative in self.alternatives:
            try:
                return self.verifying(alternative.validate(value))

            except Exception as exc:
                errors.append(exc)

        raise ExceptionGroup(
            f"'{value}' is not matching any of the allowed alternatives:",
            errors,
        )

    def check(
        self,
        value: Any,
    ) -> bool:
        return any(alternative.check(value) for alternative in self.alternatives)


class CustomAttribute(Immutable):
    base: Any
    parameters: Sequence[AttributeAnnotation] = ()
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return self.base.__qualname__

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    parameters=self.parameters,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    parameters=self.parameters,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, self.base):
            return self.verifying(value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{self.base}'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, self.base)


class StrEnumAttribute(Immutable):
    base: type[enum.StrEnum]
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return self.base.__qualname__

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, self.base):
            return self.verifying(value)

        elif isinstance(value, str):
            try:
                return self.verifying(self.base(value))

            except Exception:
                try:
                    return self.verifying(self.base[value])

                except KeyError as exc:
                    allowed_values: str = ", ".join(member.value for member in self.base)
                    raise ValueError(
                        f"'{value}' is not matching any of expected"
                        f" {self.base.__name__} values [{allowed_values}]"
                    ) from exc

        raise TypeError(f"'{value}' is not matching expected type of '{self.base}'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, self.base)


class IntEnumAttribute(Immutable):
    base: type[enum.IntEnum]
    alias: str | None = None
    description: str | None = None
    verifying: Verifying[Any] = _no_verify
    required: bool = True
    specification: TypeSpecification | None = None
    meta: Meta = META_EMPTY

    @property
    def type_name(self) -> str:
        return self.base.__qualname__

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if annotations:
            alias: str | None = self.alias
            description: str | None = self.description
            verifying: Verifying[Any] = self.verifying
            required: bool = self.required
            specification: TypeSpecification | None = self.specification
            validating: Validating[Any] | None = None
            meta: Meta = self.meta

            for annotation in annotations:
                if isinstance(annotation, str):
                    description = annotation

                elif isinstance(annotation, Description):
                    description = annotation.description

                elif isinstance(annotation, Alias):
                    alias = annotation.alias

                elif isinstance(annotation, Specification):
                    specification = annotation.specification

                elif isinstance(annotation, NotRequired):
                    required = False

                elif isinstance(annotation, Verifier):
                    verifying = cast(Verifier[Any], annotation).verifier

                elif isinstance(annotation, Validator):
                    validating = cast(Validator[Any], annotation).validator

                elif isinstance(annotation, Meta):
                    meta = annotation

            if validating is None:
                return self.__class__(
                    base=self.base,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                )

            return ValidableAttribute(
                validating=validating,
                attribute=self.__class__(
                    base=self.base,
                    alias=alias,
                    description=description,
                    verifying=verifying,
                    required=required,
                    specification=specification,
                    meta=meta,
                ),
            )

        return self

    def validate(
        self,
        value: Any,
    ) -> Any:
        if isinstance(value, self.base):
            return self.verifying(value)

        elif isinstance(value, int):
            try:
                return self.verifying(self.base(value))

            except Exception as exc:
                allowed_values: str = ", ".join(str(member.value) for member in self.base)
                raise ValueError(
                    f"'{value}' is not matching any of expected"
                    f" {self.base.__name__} values [{allowed_values}]"
                ) from exc

        elif isinstance(value, str):
            try:
                return self.verifying(self.base[value])

            except KeyError as exc:
                try:
                    return self.verifying(self.base(int(value)))

                except Exception:
                    allowed_names: str = ", ".join(member.name for member in self.base)
                    raise ValueError(
                        f"'{value}' is not matching any of expected"
                        f" {self.base.__name__} members [{allowed_names}]"
                    ) from exc

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{self.base}'")

    def check(
        self,
        value: Any,
    ) -> bool:
        return isinstance(value, self.base)


def resolve_self_attribute(
    cls: type[Any],
    /,
    parameters: Mapping[str, Any],
) -> ObjectAttribute:
    recursion_guard: MutableMapping[Any, AttributeAnnotation] = {}
    resolved_parameters: Mapping[str, AttributeAnnotation] = {
        key: resolve_attribute(
            value,
            module=cls.__module__,
            resolved_parameters=parameters,
            recursion_guard=recursion_guard,
        )
        for key, value in parameters.items()
    }
    attributes: MutableMapping[Any, AttributeAnnotation] = {}
    self_attribute: ObjectAttribute = ObjectAttribute(
        base=cls,
        attributes=attributes,
        parameters=tuple(resolved_parameters.values()),
    )

    # Use current annotation as reference to Self
    recursion_guard["Self"] = self_attribute
    recursion_guard[
        _recursion_key(
            origin=cls,
            parameters=tuple(resolved_parameters.values()),
        )
    ] = self_attribute

    for key, annotation in get_type_hints(
        self_attribute.base,
        localns={
            self_attribute.base.__name__: self_attribute.base,
        },
        include_extras=True,
    ).items():
        if key.startswith("__"):
            continue  # do not include special items

        if get_origin(annotation) is ClassVar:
            continue  # do not include class variables

        attribute: AttributeAnnotation = resolve_attribute(
            annotation,
            module=self_attribute.base.__module__,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )
        if hasattr(cls, key) and attribute.required:
            attribute = attribute.annotated((NOT_REQUIRED,))

        attributes[key] = attribute

    return self_attribute


def _recursion_key(
    *,
    origin: Any,
    parameters: Sequence[AttributeAnnotation] | None = None,
    alias: str | None = None,
    alias_module: str | None = None,
) -> str:
    recursion_key: str
    if alias:
        if module := alias_module:
            recursion_key = f"{module}.{alias}"

        else:
            return alias

    else:
        parameters_str: str
        if parameters:
            parameters_str = "[" + ", ".join(str(param) for param in parameters) + "]"

        else:
            parameters_str = ""

        if qualname := getattr(origin, "__qualname__", None):
            recursion_key = f"{qualname}{parameters_str}"

        elif module := getattr(origin, "__module__", None):
            recursion_key = f"{module}.{getattr(origin, '__name__', str(origin))}{parameters_str}"

        else:
            recursion_key = f"{getattr(origin, '__name__', str(origin))}{parameters_str}"

    return recursion_key


def _resolve_parameters(
    annotation: Any,
    *,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> Sequence[AttributeAnnotation]:
    return tuple(
        resolve_attribute(
            argument,
            resolved_parameters=resolved_parameters,
            module=module,
            recursion_guard=recursion_guard,
        )
        for argument in get_args(annotation)
    )


def _evaluate_forward_ref(
    annotation: ForwardRef | str,
    /,
    module: str,
) -> Any:
    # ForwardRef._evaluate is deprecated; evaluate compiled expression directly.
    if isinstance(annotation, str):
        forward_ref = ForwardRef(
            annotation,
            module=module,
        )
    else:
        forward_ref = annotation

    if getattr(forward_ref, "__forward_evaluated__", False):
        return forward_ref.__forward_value__

    module_name = forward_ref.__forward_module__ or module
    namespace: dict[str, Any]
    if module_name and module_name in sys.modules:
        namespace = dict(sys.modules[module_name].__dict__)
    else:
        namespace = {}

    namespace.setdefault("__builtins__", builtins.__dict__)

    try:
        return eval(forward_ref.__forward_code__, namespace, namespace)  # nosec: B307
    except (NameError, AttributeError) as error:
        raise RuntimeError(f"Cannot resolve annotation of {annotation}") from error


def _resolve_literal(
    annotation: Any,
    /,
) -> AttributeAnnotation:
    return LiteralAttribute(
        base=annotation,
        values=get_args(annotation),
    )


def _finalize_alias_resolution(  # noqa: C901, PLR0912
    attribute: AttributeAnnotation,
    *,
    alias_name: str,
    alias_module: str,
    alias_target: AttributeAnnotation,
    visited: set[int],
) -> None:
    attribute_id = id(attribute)
    if attribute_id in visited:
        return

    visited.add(attribute_id)

    if isinstance(attribute, AliasAttribute):
        if attribute.alias == alias_name and attribute.module == alias_module:
            if attribute._resolved is None:  # pyright: ignore[reportPrivateUsage]
                attribute.resolve(alias_target)

            resolved: AttributeAnnotation | None = attribute._resolved  # pyright: ignore[reportPrivateUsage]
            if resolved is not None:
                _finalize_alias_resolution(
                    resolved,
                    alias_name=alias_name,
                    alias_module=alias_module,
                    alias_target=alias_target,
                    visited=visited,
                )

        elif attribute._resolved is not None:  # pyright: ignore[reportPrivateUsage]
            _finalize_alias_resolution(
                attribute._resolved,  # pyright: ignore[reportPrivateUsage]
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )

    elif isinstance(attribute, UnionAttribute):
        for alternative in attribute.alternatives:
            _finalize_alias_resolution(
                alternative,
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )

    elif isinstance(attribute, TypedDictAttribute):
        for child in attribute.attributes.values():
            _finalize_alias_resolution(
                child,
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )

        for parameter in attribute.parameters:
            _finalize_alias_resolution(
                parameter,
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )

    elif isinstance(attribute, ObjectAttribute):
        for child in attribute.attributes.values():
            _finalize_alias_resolution(
                child,
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )

        for parameter in attribute.parameters:
            _finalize_alias_resolution(
                parameter,
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )

    elif isinstance(attribute, SequenceAttribute | SetAttribute):
        _finalize_alias_resolution(
            attribute.values,
            alias_name=alias_name,
            alias_module=alias_module,
            alias_target=alias_target,
            visited=visited,
        )

    elif isinstance(attribute, TupleAttribute):
        for value in attribute.values:
            _finalize_alias_resolution(
                value,
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )

    elif isinstance(attribute, MappingAttribute):
        _finalize_alias_resolution(
            attribute.keys,
            alias_name=alias_name,
            alias_module=alias_module,
            alias_target=alias_target,
            visited=visited,
        )
        _finalize_alias_resolution(
            attribute.values,
            alias_name=alias_name,
            alias_module=alias_module,
            alias_target=alias_target,
            visited=visited,
        )

    elif isinstance(attribute, CustomAttribute):
        for parameter in attribute.parameters:
            _finalize_alias_resolution(
                parameter,
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )

    elif isinstance(attribute, ValidableAttribute):
        _finalize_alias_resolution(
            attribute.attribute,
            alias_name=alias_name,
            alias_module=alias_module,
            alias_target=alias_target,
            visited=visited,
        )

    elif isinstance(attribute, FunctionAttribute):
        for argument in attribute.arguments:
            _finalize_alias_resolution(
                argument,
                alias_name=alias_name,
                alias_module=alias_module,
                alias_target=alias_target,
                visited=visited,
            )


def _resolve_type_alias(
    annotation: typing.TypeAliasType | typing_extensions.TypeAliasType,
    *,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    if guard := recursion_guard.get(annotation):
        return guard

    recursion_key: str = _recursion_key(
        origin=get_origin(annotation.__value__) or annotation.__value__,
        alias=annotation.__name__,
        alias_module=annotation.__module__,
    )
    if guard := recursion_guard.get(recursion_key):
        return guard

    alias_name: str = annotation.__name__
    if guard := recursion_guard.get(alias_name):
        return guard

    placeholder = AliasAttribute(
        type_alias=annotation.__name__,
        module=annotation.__module__ or module,
    )
    recursion_guard[annotation] = placeholder
    recursion_guard[recursion_key] = placeholder
    recursion_guard[alias_name] = placeholder

    resolved_attribute: AttributeAnnotation = resolve_attribute(
        annotation.__value__,
        module=module,
        resolved_parameters=resolved_parameters,
        recursion_guard=recursion_guard,
    )
    placeholder.resolve(resolved_attribute)
    recursion_guard[annotation] = resolved_attribute
    recursion_guard[recursion_key] = resolved_attribute
    recursion_guard[alias_name] = resolved_attribute

    _finalize_alias_resolution(
        resolved_attribute,
        alias_name=placeholder.type_alias,
        alias_module=placeholder.module,
        alias_target=resolved_attribute,
        visited=set(),
    )

    return resolved_attribute


def _resolve_generic_alias(
    annotation: GenericAlias,
    *,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    origin_type: Any = annotation.__origin__
    if not hasattr(origin_type, "__class_getitem__"):
        return resolve_attribute(
            origin_type,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    # try to resolve the alias with available types
    def _resolve_type_argument(argument: Any) -> Any:
        if not isinstance(argument, TypeVar):
            return argument

        resolved: Any
        if parameter := resolved_parameters.get(
            argument.__name__,
        ):
            resolved = parameter.base

        else:
            resolved = argument.__bound__ or Any

        return resolved

    resolved_origin: Any = origin_type.__class_getitem__(  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        tuple(_resolve_type_argument(arg) for arg in get_args(annotation))
    )

    # if we have resolved it use what we got
    if not isinstance(resolved_origin, types.GenericAlias | typing._GenericAlias):  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        if specialized_self := getattr(resolved_origin, "__SELF_ATTRIBUTE__", None):
            assert isinstance(specialized_self, ObjectAttribute)  # nosec: B101
            return specialized_self

        return resolve_attribute(
            resolved_origin,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    # otherwise resolve alias as we can
    resolved_arguments: Sequence[Any] = get_args(resolved_origin)
    generic_parameters: Sequence[Any] = getattr(annotation.__origin__, "__parameters__", ())

    if generic_parameters and resolved_arguments:
        resolved_parameters = {
            **resolved_parameters,
            **{
                parameter.__name__: argument
                for parameter, argument in zip(
                    generic_parameters,
                    resolved_arguments,
                    strict=False,
                )
                if hasattr(parameter, "__name__")
            },
        }

    resolved_module: str = getattr(resolved_origin, "__module__", module)
    recursion_key: str = _recursion_key(
        origin=resolved_origin.__origin__,
        parameters=_resolve_parameters(
            resolved_origin.__origin__,
            module=resolved_module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        ),
    )
    if guard := recursion_guard.get(recursion_key):
        return guard

    resolved_attribute: AttributeAnnotation = resolve_attribute(
        resolved_origin.__origin__,
        module=resolved_module,
        resolved_parameters=resolved_parameters,
        recursion_guard=recursion_guard,
    )
    recursion_guard[recursion_key] = resolved_attribute

    return resolved_attribute


def _resolve_typeddict(
    annotation: Any,
    *,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    attributes: MutableMapping[str, AttributeAnnotation] = {}
    resolved_attribute: TypedDictAttribute = TypedDictAttribute(
        base=annotation,
        attributes=attributes,
    )

    recursion_key: str = _recursion_key(
        origin=annotation,
        # TODO: parameters?
    )

    if guard := recursion_guard.get(recursion_key):
        return guard

    recursion_guard[recursion_key] = resolved_attribute

    # preserve current Self reference
    self_attribute: AttributeAnnotation | None = recursion_guard.get("Self", None)
    # temporarily update Self reference to contextual
    recursion_guard["Self"] = resolved_attribute

    for key, element in get_type_hints(
        annotation,
        localns={annotation.__name__: annotation},
        include_extras=True,
    ).items():
        attribute: AttributeAnnotation = resolve_attribute(
            element,
            module=getattr(annotation, "__module__", module),
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

        if not attribute.required:
            attributes[key] = attribute
            continue  # already annotated

        if key not in annotation.__required_keys__:
            attribute = attribute.annotated((NOT_REQUIRED,))

        attributes[key] = attribute

    if self_attribute is not None:  # bring Self back to previous attribute
        recursion_guard["Self"] = self_attribute

    return resolved_attribute


ANY_ATTRIBUTE: Final[AnyAttribute] = AnyAttribute()
MISSING_ATTRIBUTE: Final[MissingAttribute] = MissingAttribute()
NONE_ATTRIBUTE: Final[NoneAttribute] = NoneAttribute()
STRING_ATTRIBUTE: Final[StringAttribute] = StringAttribute()
INTEGER_ATTRIBUTE: Final[IntegerAttribute] = IntegerAttribute()
FLOAT_ATTRIBUTE: Final[FloatAttribute] = FloatAttribute()
BOOL_ATTRIBUTE: Final[BoolAttribute] = BoolAttribute()
BYTES_ATTRIBUTE: Final[BytesAttribute] = BytesAttribute()
UUID_ATTRIBUTE: Final[UUIDAttribute] = UUIDAttribute()
DATETIME_ATTRIBUTE: Final[DatetimeAttribute] = DatetimeAttribute()
DATE_ATTRIBUTE: Final[DateAttribute] = DateAttribute()
TIME_ATTRIBUTE: Final[TimeAttribute] = TimeAttribute()
PATH_ATTRIBUTE: Final[PathAttribute] = PathAttribute()


def _resolve_type(  # noqa: C901, PLR0911, PLR0912, PLR0915
    annotation: Any,
    *,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    match get_origin(annotation) or annotation:
        case types.NoneType | None:
            return NONE_ATTRIBUTE

        case typeddict if is_typeddict(typeddict) or is_typeddict_ext(typeddict):
            return _resolve_typeddict(
                typeddict,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case haiway_types.Missing:
            return MISSING_ATTRIBUTE

        case typing.Any | typing_extensions.Any:
            return ANY_ATTRIBUTE

        case haiway_types.Meta:
            return MetaAttribute()

        case builtins.str:
            return STRING_ATTRIBUTE

        case builtins.int:
            return INTEGER_ATTRIBUTE

        case builtins.float:
            return FLOAT_ATTRIBUTE

        case builtins.bool:
            return BOOL_ATTRIBUTE

        case builtins.bytes:
            return BYTES_ATTRIBUTE

        case uuid.UUID:
            return UUID_ATTRIBUTE

        case datetime.datetime:
            return DATETIME_ATTRIBUTE

        case datetime.date:
            return DATE_ATTRIBUTE

        case datetime.time:
            return TIME_ATTRIBUTE

        case pathlib.Path:
            return PATH_ATTRIBUTE

        case type() as str_enum if issubclass(str_enum, enum.StrEnum):
            return StrEnumAttribute(base=str_enum)

        case type() as int_enum if issubclass(int_enum, enum.IntEnum):
            return IntEnumAttribute(base=int_enum)

        case (
            builtins.dict
            | collections_abc.Mapping
            | collections_abc.MutableMapping
            | typing.Mapping
            | typing.MutableMapping
            | typing_extensions.Mapping
            | typing_extensions.MutableMapping
            | typing.Dict  # noqa: UP006
            | typing_extensions.Dict
        ):
            keys_annotation: Any
            values_annotation: Any
            match get_args(annotation):
                case (keys, values):
                    keys_annotation = keys
                    values_annotation = values

                case _:
                    keys_annotation = Any
                    values_annotation = Any

            return MappingAttribute(
                base=Mapping[keys_annotation, values_annotation],
                keys=resolve_attribute(
                    keys_annotation,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                ),
                values=resolve_attribute(
                    values_annotation,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                ),
            )

        case (
            builtins.set
            | collections_abc.Set
            | collections_abc.MutableSet
            | typing.Set  # noqa: UP006
            | typing.MutableSet
            | typing_extensions.Set
            | typing_extensions.MutableSet
        ):
            values_annotation: Any
            match get_args(annotation):
                case (values,):
                    values_annotation = values

                case _:
                    values_annotation = Any

            return SetAttribute(
                base=Set[values_annotation],
                values=resolve_attribute(
                    values_annotation,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                ),
            )

        case builtins.tuple | typing.Tuple | typing_extensions.Tuple:  # noqa: UP006
            match get_args(annotation):
                case (values_annotation, builtins.Ellipsis):
                    return SequenceAttribute(
                        base=Sequence[values_annotation],
                        values=resolve_attribute(
                            values_annotation,
                            module=module,
                            resolved_parameters=resolved_parameters,
                            recursion_guard=recursion_guard,
                        ),
                    )

                case _:
                    return TupleAttribute(
                        base=annotation,
                        values=_resolve_parameters(
                            annotation,
                            module=module,
                            resolved_parameters=resolved_parameters,
                            recursion_guard=recursion_guard,
                        ),
                    )

        case (
            builtins.list
            | collections_abc.Sequence
            | collections_abc.MutableSequence
            | typing.Sequence
            | typing.MutableSequence
            | typing_extensions.Sequence
            | typing_extensions.MutableSequence
            | typing.List  # noqa: UP006
            | typing_extensions.List
        ):
            values_annotation: Any
            match get_args(annotation):
                case (values,):
                    values_annotation = values

                case _:
                    values_annotation = Any

            return SequenceAttribute(
                base=Sequence[values_annotation],
                values=resolve_attribute(
                    values_annotation,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                ),
            )

        case origin:
            if self_attribute := getattr(annotation, "__SELF_ATTRIBUTE__", None):
                assert isinstance(self_attribute, ObjectAttribute)  # nosec: B101
                if validate := getattr(origin, "validate", None):
                    return ValidableAttribute(
                        validating=validate,
                        attribute=self_attribute,
                    )

                else:
                    return self_attribute

            parameters: Sequence[AttributeAnnotation] = _resolve_parameters(
                annotation,
                module=getattr(annotation, "__module__", module),
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )
            recursion_key: str = _recursion_key(
                origin=origin,
                parameters=parameters,
            )
            if guard := recursion_guard.get(recursion_key):
                return guard

            if validate := getattr(origin, "validate", None):
                assert isinstance(validate, Validating)  # nosec: B101
                return ValidableAttribute(
                    validating=validate,  # pyright: ignore[reportUnknownArgumentType]
                    attribute=CustomAttribute(
                        base=origin,
                        parameters=parameters,
                    ),
                )

            return CustomAttribute(
                base=origin,
                parameters=parameters,
            )


def resolve_attribute(  # noqa: C901, PLR0911, PLR0912
    annotation: Any,
    /,
    module: str,
    resolved_parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    origin: Any | None = get_origin(annotation)

    if isinstance(annotation, types.GenericAlias | typing._GenericAlias) and any(  # pyright: ignore[reportAttributeAccessIssue, reportUnknownMemberType]
        isinstance(argument, TypeVar) for argument in get_args(annotation)
    ):
        return _resolve_generic_alias(
            annotation,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    match origin or type(annotation):
        case None:
            return NONE_ATTRIBUTE

        case typing.Union | types.UnionType | typing_extensions.Union:
            return UnionAttribute(
                base=annotation,
                alternatives=tuple(
                    resolve_attribute(
                        alternative,
                        module=module,
                        resolved_parameters=resolved_parameters,
                        recursion_guard=recursion_guard,
                    )
                    for alternative in get_args(annotation) or getattr(annotation, "__args__", ())
                ),
            )

        case typing.TypeAliasType | typing_extensions.TypeAliasType:
            return _resolve_type_alias(
                annotation,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case typing.Annotated | typing_extensions.Annotated:
            annotation_args: Sequence[Any] = get_args(annotation)
            attribute: AttributeAnnotation = resolve_attribute(
                annotation_args[0],
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

            return attribute.annotated(tuple(annotation_args[1:]))

        case typing.TypeVar | typing_extensions.TypeVar:
            if resolved := resolved_parameters.get(annotation.__name__):
                return resolved

            return resolve_attribute(
                annotation.__bound__ or Any,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case typing.Literal | typing_extensions.Literal:
            return _resolve_literal(annotation)

        case collections_abc.Callable | typing.Callable | typing_extensions.Callable:  # pyright: ignore[reportUnknownMemberType]
            return FunctionAttribute(
                base=annotation,
                arguments=(),  # TODO: use function with arguments
            )

        case typing.Self | typing_extensions.Self:  # pyright: ignore[reportUnknownMemberType]
            if self_attribute := recursion_guard.get("Self"):
                return self_attribute

            else:
                raise RuntimeError(f"Unresolved Self annotation: {annotation}")

        case typing.Required | typing_extensions.Required:
            attribute: AttributeAnnotation = resolve_attribute(
                get_args(annotation)[0],
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

            return attribute

        case typing.NotRequired | typing_extensions.NotRequired:
            attribute: AttributeAnnotation = resolve_attribute(
                get_args(annotation)[0],
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

            if attribute.required:
                return attribute.annotated((NOT_REQUIRED,))

            return attribute

        case typing.Optional | typing_extensions.Optional:
            return UnionAttribute(
                base=annotation,
                alternatives=(
                    resolve_attribute(
                        get_args(annotation)[0],
                        module=module,
                        resolved_parameters=resolved_parameters,
                        recursion_guard=recursion_guard,
                    ),
                    NONE_ATTRIBUTE,
                ),
            )

        case typing.Final | typing_extensions.Final:
            return resolve_attribute(
                get_args(annotation)[0],
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case typing.ForwardRef | typing_extensions.ForwardRef:
            resolved: Any = _evaluate_forward_ref(
                annotation,
                module=module,
            )
            if isinstance(resolved, Hashable):
                if guard := recursion_guard.get(resolved):
                    return guard
            recursion_key: str = _recursion_key(origin=resolved)
            if guard := recursion_guard.get(recursion_key):
                return guard

            attrbute: AttributeAnnotation = resolve_attribute(
                resolved,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )
            recursion_guard[recursion_key] = attrbute

            return attrbute

        case builtins.str():
            resolved: Any = _evaluate_forward_ref(
                annotation,
                module=module,
            )
            if isinstance(resolved, Hashable):
                if guard := recursion_guard.get(resolved):
                    return guard
            recursion_key: str = _recursion_key(origin=resolved)
            if guard := recursion_guard.get(recursion_key):
                return guard

            attrbute: AttributeAnnotation = resolve_attribute(
                resolved,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )
            recursion_guard[recursion_key] = attrbute

            return attrbute

        case type():
            return _resolve_type(
                annotation,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case _:
            raise TypeError(f"Unsupported annotation of '{annotation}'")
