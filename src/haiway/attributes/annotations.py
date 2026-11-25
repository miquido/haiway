import annotationlib
import builtins
import datetime
import enum
import inspect
import pathlib
import types
import typing
import uuid
from collections import abc as collections_abc
from collections.abc import (
    Callable,
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
    Final,
    ForwardRef,
    Literal,
    Protocol,
    TypeVar,
    cast,
    final,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)

import typing_extensions
from typing_extensions import is_typeddict as is_typeddict_ext  # keep this particular import

from haiway import types as haiway_types
from haiway.attributes.generics import (
    is_open_type_argument,
    refused_self_specialization,
    type_parameters_of,
)
from haiway.attributes.validation import (
    Validating,
    Validator,
    Verifier,
    Verifying,
)
from haiway.types import (
    Alias,
    Description,
    Meta,
    Sensitive,
    Specification,
    TypeSpecification,
)
from haiway.types.frozen import refuse_assignment, refuse_deletion, same_copy, same_deepcopy

__all__ = (
    "AliasAttribute",
    "AnyAttribute",
    "AttributeAnnotation",
    "AttributeMetadata",
    "BoolAttribute",
    "BytesAttribute",
    "CallableSignature",
    "CustomAttribute",
    "DateAttribute",
    "DatetimeAttribute",
    "FloatAttribute",
    "FunctionAttribute",
    "FunctionSignature",
    "IntEnumAttribute",
    "IntegerAttribute",
    "LiteralAttribute",
    "MappingAttribute",
    "MetaAttribute",
    "MissingAttribute",
    "NoneAttribute",
    "NotRequired",
    "ObjectAttribute",
    "OpenTupleAttribute",
    "PathAttribute",
    "ProtocolSignature",
    "SequenceAttribute",
    "SetAttribute",
    "StrEnumAttribute",
    "StringAttribute",
    "TimeAttribute",
    "TimedeltaAttribute",
    "TupleAttribute",
    "TypedDictAttribute",
    "UUIDAttribute",
    "UnionAttribute",
    "ValidableAttribute",
    "resolve_attribute",
    "resolve_self_attribute",
)


def _slot_names(
    self: object,
    /,
) -> tuple[str, ...]:
    return getattr(type(self), "__slots__", ())


def _record_text(
    self: object,
) -> str:
    # built from the values a record holds rather than its address - recursion
    # keys are made of these, so equal records have to read the same
    fields: str = ", ".join(
        f"{name}: {getattr(self, name)}" for name in _slot_names(self) if not name.startswith("_")
    )
    return f"{type(self).__name__}({fields})"


@final
class NotRequired:
    __slots__ = ()

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __repr__ = _record_text
    __str__ = _record_text
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(
        self,
    ) -> None:
        pass


NOT_REQUIRED: Final[NotRequired] = NotRequired()

Annotation = (
    Alias
    | Description
    | Specification
    | Sensitive
    # `Sensitive` needs no argument, so the class itself marks an attribute the
    # same way an instance of it does - see `_annotation_markers`
    | type[Sensitive]
    | Validator[Any]
    | Verifier[Any]
    | Meta
    | NotRequired
    | str
)


def _annotation_markers(
    annotations: Sequence[Any],
    /,
) -> tuple[Any, ...]:
    """Markers of an ``Annotated``, with the ones written as a class instantiated.

    Parameters
    ----------
    annotations : Sequence[Any]
        Metadata of an ``Annotated``, as declared.

    Returns
    -------
    tuple[Any, ...]
        The same markers, with every one requiring no argument replaced by an
        instance of it.

    Notes
    -----
    ``Sensitive`` carries a default redaction, which makes ``Annotated[str,
    Sensitive]`` as meaningful to write as ``Annotated[str, Sensitive()]`` - and
    an easy thing to write by accident. Left as the class it would be no marker
    at all, and the attribute it was meant to withhold would be rendered in
    full, which is the one mistake this marker exists to prevent.
    """
    if not any(annotation is Sensitive for annotation in annotations):
        return tuple(annotations)  # nothing to instantiate, by far the common case

    return tuple(
        Sensitive() if annotation is Sensitive else annotation for annotation in annotations
    )


class AttributeAnnotation(Protocol):
    @property
    def type_name(self) -> str: ...

    @property
    def metadata(self) -> AttributeMetadata: ...

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

    # redaction is only ever added by annotating, never annotated away
    @property
    def redaction(self) -> str | None: ...

    @property
    def required(self) -> bool: ...

    def annotated(
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation: ...


@final
class _Composition:
    # several validators, or verifiers, of one attribute - run one after another, each
    # given what the previous one returned. a failure is not caught here, it reaches
    # the validation which called the composition as the one of the callable it is
    __slots__ = ("steps",)

    steps: tuple[Validating[Any], ...]

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __repr__ = _record_text
    __str__ = _record_text
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(
        self,
        steps: tuple[Validating[Any], ...],
        /,
    ) -> None:
        object.__setattr__(self, "steps", steps)

    def __call__(
        self,
        value: Any,
    ) -> Any:
        for step in self.steps:
            value = step(value)

        return value


def _composed(
    steps: Sequence[Validating[Any]],
    /,
) -> Validating[Any] | None:
    # a single step is kept as it is, it needs no composition to run
    match len(steps):
        case 0:
            return None

        case 1:
            return steps[0]

        case _:
            return _Composition(tuple(steps))


@final
class AttributeMetadata:
    """
    What an attribute is declared with besides its type.

    The one place the metadata of an ``Annotated`` is read: every attribute annotation
    holds one and is annotated through it.

    Attributes
    ----------
    alias : str | None
        Key the attribute is provided and rendered under instead of its name.
    description : str | None
        Description of the attribute.
    verifying : Verifying[Any] | None
        What runs on the value once it is validated - all of the declared verifiers,
        in the order they were declared as one.
    required : bool
        Whether the attribute has to be provided.
    specification : TypeSpecification | None
        Declared specification overriding the generated one.
    meta : Meta
        Metadata attached to the attribute.
    redaction : str | None
        Text to render instead of the value of a sensitive attribute.
    """

    alias: str | None
    description: str | None
    verifying: Verifying[Any] | None
    required: bool
    specification: TypeSpecification | None
    meta: Meta
    redaction: str | None
    __slots__ = (
        "alias",
        "description",
        "meta",
        "redaction",
        "required",
        "specification",
        "verifying",
    )

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __repr__ = _record_text
    __str__ = _record_text
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(
        self,
        *,
        alias: str | None = None,
        description: str | None = None,
        verifying: Verifying[Any] | None = None,
        required: bool = True,
        specification: TypeSpecification | None = None,
        meta: Meta = Meta.empty,
        redaction: str | None = None,
    ) -> None:
        object.__setattr__(self, "alias", alias)
        object.__setattr__(self, "description", description)
        object.__setattr__(self, "verifying", verifying)
        object.__setattr__(self, "required", required)
        object.__setattr__(self, "specification", specification)
        object.__setattr__(self, "meta", meta)
        object.__setattr__(self, "redaction", redaction)

    def annotated(  # noqa: C901
        self,
        annotations: Sequence[Any],
    ) -> tuple[AttributeMetadata, Validating[Any] | None]:
        """
        The metadata with the given annotations applied, in the order they are given.

        The last of an alias, description, specification, meta or sensitivity wins.
        Validators and verifiers do not replace one another - the ones already held
        run first, the given ones after them, in order they are given.

        Parameters
        ----------
        annotations : Sequence[Any]
            Metadata of an ``Annotated``.

        Returns
        -------
        tuple[AttributeMetadata, Validating[Any] | None]
            The resulting metadata, and the validators given, composed - those run on
            the value before it is validated, which is not a part of the metadata
            of the attribute but of the annotation wrapping it.
        """
        alias: str | None = self.alias
        description: str | None = self.description
        required: bool = self.required
        specification: TypeSpecification | None = self.specification
        meta: Meta = self.meta
        redaction: str | None = self.redaction
        validators: list[Validating[Any]] = []
        verifiers: list[Verifying[Any]] = [] if self.verifying is None else [self.verifying]

        for annotation in annotations:
            if isinstance(annotation, str):
                description = annotation

            elif isinstance(annotation, Description):
                description = annotation.description

            elif isinstance(annotation, Alias):
                alias = annotation.name

            elif isinstance(annotation, Specification):
                specification = annotation.specification

            elif isinstance(annotation, NotRequired):
                required = False

            elif isinstance(annotation, Verifier):
                verifiers.append(cast(Verifier[Any], annotation).verifier)

            elif isinstance(annotation, Validator):
                validators.append(cast(Validator[Any], annotation).validator)

            elif isinstance(annotation, Meta):
                meta = annotation

            elif isinstance(annotation, Sensitive):
                redaction = annotation.redaction

        return (
            AttributeMetadata(
                alias=alias,
                description=description,
                verifying=_composed(verifiers),
                required=required,
                specification=specification,
                meta=meta,
                redaction=redaction,
            ),
            _composed(validators),
        )


_DEFAULT_METADATA: Final[AttributeMetadata] = AttributeMetadata()
_MISSING_METADATA: Final[AttributeMetadata] = AttributeMetadata(required=False)


class _MetadataAnnotation:
    # what every annotation which holds its metadata shares - the way it is read, and
    # annotated. the annotation itself can't change, annotating makes another one
    __slots__ = ()

    metadata: AttributeMetadata

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __repr__ = _record_text
    __str__ = _record_text
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    @property
    def alias(self) -> str | None:
        return self.metadata.alias

    @property
    def description(self) -> str | None:
        return self.metadata.description

    @property
    def specification(self) -> TypeSpecification | None:
        return self.metadata.specification

    @property
    def meta(self) -> Meta:
        return self.metadata.meta

    @property
    def redaction(self) -> str | None:
        return self.metadata.redaction

    @property
    def required(self) -> bool:
        return self.metadata.required

    def annotated(
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        if not annotations:
            # every class holding metadata is a complete annotation, this one is not
            return cast(AttributeAnnotation, self)  # nothing to apply

        metadata: AttributeMetadata
        validating: Validating[Any] | None
        metadata, validating = self.metadata.annotated(annotations)
        attribute: AttributeAnnotation = self._with_metadata(metadata)
        if validating is None:
            return attribute

        return ValidableAttribute(
            validating=validating,
            attribute=attribute,
        )

    def _with_metadata(
        self,
        metadata: AttributeMetadata,
    ) -> Any:
        # the same annotation holding other metadata
        cls: type[Any] = type(self)
        annotation: Any = object.__new__(cls)
        for slot in _slot_names(self):
            object.__setattr__(
                annotation,
                slot,
                metadata if slot == "metadata" else getattr(self, slot),
            )

        return annotation


def _iterable_snapshot(value: Any) -> Any:
    # only unordered or single use iterables need normalization, anything else
    # is passed through to be validated (or rejected) as a sequence
    if isinstance(value, Iterable) and not isinstance(
        value, Sequence | str | bytes | bytearray | memoryview
    ):
        return tuple(cast(Iterable[Any], value))

    return cast(Any, value)


@final
class AnyAttribute(_MetadataAnnotation):
    type_name: Literal["Any"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["Any"] = "Any",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> Any:
        return typing.Any


class _AliasTarget:
    # what an alias stands for, bound once the target is resolved - a cell the alias
    # holds, since the alias itself can't change after it is made
    __slots__ = ("annotation",)

    def __init__(
        self,
        annotation: AttributeAnnotation | None = None,
    ) -> None:
        self.annotation: AttributeAnnotation | None = annotation


@final
class AliasAttribute:
    type_alias: str
    module: str
    annotations: Sequence[Annotation]
    _target: _AliasTarget
    __slots__ = ("_target", "annotations", "module", "type_alias")

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __repr__ = _record_text
    __str__ = _record_text
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(
        self,
        *,
        type_alias: str,
        module: str,
        annotations: Sequence[Annotation] = (),
        _target: _AliasTarget | None = None,
    ) -> None:
        object.__setattr__(self, "type_alias", type_alias)
        object.__setattr__(self, "module", module)
        object.__setattr__(self, "annotations", annotations)
        object.__setattr__(self, "_target", _AliasTarget() if _target is None else _target)

    @property
    def _resolved(self) -> AttributeAnnotation | None:
        return self._target.annotation

    @property
    def type_name(self) -> str:
        return f"{self.module}.{self.type_alias}"

    @property
    def base(self) -> Any:
        assert self._resolved is not None  # nosec: B101
        return self._resolved.base

    @property
    def metadata(self) -> AttributeMetadata:
        if self._resolved is None:
            # not known yet - what is declared on the alias itself
            return _DEFAULT_METADATA.annotated(self.annotations)[0]

        return self._resolved.metadata

    @property
    def alias(self) -> str | None:
        alias: str | None = self.metadata.alias
        if alias is not None:
            return alias

        return self.type_alias

    @property
    def description(self) -> str | None:
        return self.metadata.description

    @property
    def specification(self) -> TypeSpecification | None:
        return self.metadata.specification

    @property
    def meta(self) -> Meta:
        return self.metadata.meta

    @property
    def redaction(self) -> str | None:
        return self.metadata.redaction

    @property
    def required(self) -> bool:
        return self.metadata.required

    def annotated(
        self,
        annotations: Sequence[Any],
    ) -> AttributeAnnotation:
        return self.__class__(
            type_alias=self.type_alias,
            module=self.module,
            annotations=annotations,
            _target=_AliasTarget(
                self._resolved.annotated(annotations) if self._resolved is not None else None
            ),
        )

    def resolve(
        self,
        target: AttributeAnnotation,
    ) -> None:
        assert self._resolved is None  # nosec: B101
        if self.annotations:
            self._target.annotation = target.annotated(self.annotations)

        else:
            self._target.annotation = target

    @property
    def is_resolved(self) -> bool:
        return self._resolved is not None

    @property
    def resolved(self) -> AttributeAnnotation:
        if self._resolved is None:
            raise RuntimeError(f"Alias '{self.module}.{self.type_alias}' used before resolution")

        return self._resolved


@final
class MissingAttribute(_MetadataAnnotation):
    type_name: Literal["Missing"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["Missing"] = "Missing",
        metadata: AttributeMetadata = _MISSING_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> Any:  # the sentinel - it is its own type
        return haiway_types.Missing


@final
class NoneAttribute(_MetadataAnnotation):
    type_name: Literal["None"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["None"] = "None",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> None:
        return None


@final
class LiteralAttribute(_MetadataAnnotation):
    base: Any
    values: Sequence[Any]
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "metadata",
        "values",
    )

    def __init__(
        self,
        *,
        base: Any,
        values: Sequence[Any],
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "metadata", metadata)

    @property
    def type_name(self) -> str:
        return f"Literal[{', '.join(repr(value) for value in self.values)}]"


@final
class BoolAttribute(_MetadataAnnotation):
    type_name: Literal["bool"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["bool"] = "bool",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[bool]:
        return bool


@final
class IntegerAttribute(_MetadataAnnotation):
    type_name: Literal["int"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["int"] = "int",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[int]:
        return int


@final
class FloatAttribute(_MetadataAnnotation):
    type_name: Literal["float"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["float"] = "float",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[float]:
        return float


@final
class BytesAttribute(_MetadataAnnotation):
    type_name: Literal["bytes"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["bytes"] = "bytes",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[bytes]:
        return bytes


@final
class UUIDAttribute(_MetadataAnnotation):
    type_name: Literal["UUID"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["UUID"] = "UUID",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[uuid.UUID]:
        return uuid.UUID


@final
class StringAttribute(_MetadataAnnotation):
    type_name: Literal["str"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["str"] = "str",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[str]:
        return str


@final
class DatetimeAttribute(_MetadataAnnotation):
    type_name: Literal["datetime"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["datetime"] = "datetime",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[datetime.datetime]:
        return datetime.datetime


@final
class DateAttribute(_MetadataAnnotation):
    type_name: Literal["date"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["date"] = "date",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[datetime.date]:
        return datetime.date


@final
class TimedeltaAttribute(_MetadataAnnotation):
    type_name: Literal["timedelta"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["timedelta"] = "timedelta",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[datetime.timedelta]:
        return datetime.timedelta


@final
class TimeAttribute(_MetadataAnnotation):
    type_name: Literal["time"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["time"] = "time",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[datetime.time]:
        return datetime.time


@final
class PathAttribute(_MetadataAnnotation):
    type_name: Literal["Path"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["Path"] = "Path",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[pathlib.Path]:
        return pathlib.Path


@final
class TupleAttribute(_MetadataAnnotation):
    type_name: Literal["tuple"]
    base: type[Sequence[Any]]
    values: Sequence[AttributeAnnotation]
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "metadata",
        "type_name",
        "values",
    )

    def __init__(
        self,
        *,
        type_name: Literal["tuple"] = "tuple",
        base: type[Sequence[Any]],
        values: Sequence[AttributeAnnotation],
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "metadata", metadata)


@final
class OpenTupleAttribute(_MetadataAnnotation):
    type_name: Literal["tuple"]
    base: type[Sequence[Any]]
    leading: Sequence[AttributeAnnotation]
    variadic: AttributeAnnotation
    trailing: Sequence[AttributeAnnotation]
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "leading",
        "metadata",
        "trailing",
        "type_name",
        "variadic",
    )

    def __init__(
        self,
        *,
        type_name: Literal["tuple"] = "tuple",
        base: type[Sequence[Any]],
        leading: Sequence[AttributeAnnotation],
        variadic: AttributeAnnotation,
        trailing: Sequence[AttributeAnnotation],
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "leading", leading)
        object.__setattr__(self, "variadic", variadic)
        object.__setattr__(self, "trailing", trailing)
        object.__setattr__(self, "metadata", metadata)


@final
class SequenceAttribute(_MetadataAnnotation):
    type_name: Literal["Sequence"]
    base: type[Sequence[Any]]
    values: AttributeAnnotation
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "metadata",
        "type_name",
        "values",
    )

    def __init__(
        self,
        *,
        type_name: Literal["Sequence"] = "Sequence",
        base: type[Sequence[Any]],
        values: AttributeAnnotation,
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "metadata", metadata)


@final
class SetAttribute(_MetadataAnnotation):
    type_name: Literal["Set"]
    base: type[Set[Any]]
    values: AttributeAnnotation
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "metadata",
        "type_name",
        "values",
    )

    def __init__(
        self,
        *,
        type_name: Literal["Set"] = "Set",
        base: type[Set[Any]],
        values: AttributeAnnotation,
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "metadata", metadata)


@final
class MappingAttribute(_MetadataAnnotation):
    type_name: Literal["Mapping"]
    base: type[Mapping[Any, Any]]
    keys: AttributeAnnotation
    values: AttributeAnnotation
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "keys",
        "metadata",
        "type_name",
        "values",
    )

    def __init__(
        self,
        *,
        type_name: Literal["Mapping"] = "Mapping",
        base: type[Mapping[Any, Any]],
        keys: AttributeAnnotation,
        values: AttributeAnnotation,
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "keys", keys)
        object.__setattr__(self, "values", values)
        object.__setattr__(self, "metadata", metadata)


@final
class MetaAttribute(_MetadataAnnotation):
    type_name: Literal["Meta"]
    metadata: AttributeMetadata
    __slots__ = (
        "metadata",
        "type_name",
    )

    def __init__(
        self,
        *,
        type_name: Literal["Meta"] = "Meta",
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "type_name", type_name)
        object.__setattr__(self, "metadata", metadata)

    @property
    def base(self) -> type[haiway_types.Meta]:
        return haiway_types.Meta


@final
class ValidableAttribute:
    attribute: AttributeAnnotation
    validating: Validating[Any]
    __slots__ = ("attribute", "validating")

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __repr__ = _record_text
    __str__ = _record_text
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(
        self,
        *,
        attribute: AttributeAnnotation,
        validating: Validating[Any],
    ) -> None:
        object.__setattr__(self, "attribute", attribute)
        object.__setattr__(self, "validating", validating)

    @property
    def type_name(self) -> str:
        return self.attribute.type_name

    @property
    def base(self) -> Any:
        return self.attribute.base

    @property
    def metadata(self) -> AttributeMetadata:
        return self.attribute.metadata

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
    def redaction(self) -> str | None:
        return self.attribute.redaction

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


def attribute_redaction(
    annotation: AttributeAnnotation,
    /,
) -> str | None:
    """Resolve the redaction of an attribute annotation.

    Parameters
    ----------
    annotation : AttributeAnnotation
        Annotation to inspect, including through wrapping annotations.

    Returns
    -------
    str | None
        Text to render instead of the value when the annotation was marked with
        ``Sensitive``, otherwise ``None``.

    Notes
    -----
    A marker nested within a union or a container makes the whole value
    sensitive - the concrete runtime value is not known here, so the redaction
    replaces the entire attribute instead of the sensitive part of it. Redacting
    more than strictly required keeps a secret out of the output either way,
    while resolving to ``None`` would leak it.
    """
    return _attribute_redaction(annotation, set())


def _attribute_redaction(  # noqa: PLR0911
    annotation: AttributeAnnotation,
    /,
    visited_aliases: set[tuple[str, str]],
) -> str | None:
    # the marker on the attribute itself, including the one delegated by wrapping annotations
    if annotation.redaction is not None:
        return annotation.redaction

    # otherwise it can still be nested within the structure of the attribute
    match annotation:
        case ValidableAttribute() as validable:
            return _attribute_redaction(validable.attribute, visited_aliases)

        case AliasAttribute() as aliased:
            if not aliased.is_resolved:
                return None  # an unresolved alias can't be inspected further

            identity: tuple[str, str] = (aliased.module, aliased.type_alias)
            if identity in visited_aliases:
                return None  # a recursive alias is already being inspected

            visited_aliases.add(identity)
            return _attribute_redaction(aliased.resolved, visited_aliases)

        case UnionAttribute() as union:
            return _any_redaction(union.alternatives, visited_aliases)

        case TupleAttribute() | OpenTupleAttribute() as elements:
            return _any_redaction(_tuple_elements(elements), visited_aliases)

        case SequenceAttribute() | SetAttribute() as collection:
            return _attribute_redaction(collection.values, visited_aliases)

        case MappingAttribute() as mapping:
            return _any_redaction((mapping.keys, mapping.values), visited_aliases)

        case _:
            return None


def _tuple_elements(
    attribute: TupleAttribute | OpenTupleAttribute,
    /,
) -> Sequence[AttributeAnnotation]:
    if isinstance(attribute, TupleAttribute):
        return attribute.values

    return (*attribute.leading, attribute.variadic, *attribute.trailing)


def _any_redaction(
    annotations: Iterable[AttributeAnnotation],
    /,
    visited_aliases: set[tuple[str, str]],
) -> str | None:
    return next(
        (
            redaction
            for annotation in annotations
            if (redaction := _attribute_redaction(annotation, visited_aliases)) is not None
        ),
        None,
    )


@final
class ObjectAttribute(_MetadataAnnotation):
    base: Any
    parameters: Sequence[AttributeAnnotation]
    attributes: Mapping[str, AttributeAnnotation]
    metadata: AttributeMetadata
    __slots__ = (
        "attributes",
        "base",
        "metadata",
        "parameters",
    )

    def __init__(
        self,
        *,
        base: Any,
        parameters: Sequence[AttributeAnnotation] = (),
        attributes: Mapping[str, AttributeAnnotation],
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "attributes", attributes)
        object.__setattr__(self, "metadata", metadata)

    @property
    def type_name(self) -> str:
        return self.base.__qualname__


@final
class TypedDictAttribute(_MetadataAnnotation):
    base: Any
    parameters: Sequence[AttributeAnnotation]
    attributes: Mapping[str, AttributeAnnotation]
    metadata: AttributeMetadata
    __slots__ = (
        "attributes",
        "base",
        "metadata",
        "parameters",
    )

    def __init__(
        self,
        *,
        base: Any,
        parameters: Sequence[AttributeAnnotation] = (),
        attributes: Mapping[str, AttributeAnnotation],
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "attributes", attributes)
        object.__setattr__(self, "metadata", metadata)

    @property
    def type_name(self) -> str:
        return self.base.__qualname__


@final
class FunctionAttribute(_MetadataAnnotation):
    base: Any
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "metadata",
    )

    def __init__(
        self,
        *,
        base: Any,
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "metadata", metadata)

    @property
    def type_name(self) -> str:
        return self.base.__name__

    @property
    def signature(self) -> FunctionSignature | None:
        """
        The calls a value of this annotation has to accept.

        Returns
        -------
        FunctionSignature | None
            The arguments of a ``Callable`` or the ``__call__`` of a callback protocol,
            ``None`` when the annotation accepts any call.
        """
        return _function_signature(self.base)


@final
class CallableSignature:
    """
    The arguments of a ``Callable`` annotation, all passed by position.

    Attributes
    ----------
    arguments : int
        How many arguments are passed.
    extensible : bool
        Whether more arguments may follow - after ``Concatenate`` or an unpacked
        ``TypeVarTuple``.
    """

    arguments: int
    extensible: bool
    __slots__ = ("arguments", "extensible")

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __repr__ = _record_text
    __str__ = _record_text
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(
        self,
        *,
        arguments: int,
        extensible: bool,
    ) -> None:
        object.__setattr__(self, "arguments", arguments)
        object.__setattr__(self, "extensible", extensible)


@final
class ProtocolSignature:
    """
    The parameters of the ``__call__`` of a callback protocol.

    Attributes
    ----------
    protocol : type[Any]
        The protocol declaring ``__call__``.
    parameters : Sequence[inspect.Parameter]
        The parameters of ``__call__`` without the instance it is called on.
    extensible : bool
        Whether more arguments may follow - those of a ``ParamSpec``.
    """

    protocol: type[Any]
    parameters: Sequence[inspect.Parameter]
    extensible: bool
    __slots__ = ("extensible", "parameters", "protocol")

    __copy__ = same_copy
    __deepcopy__ = same_deepcopy
    __repr__ = _record_text
    __str__ = _record_text
    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(
        self,
        *,
        protocol: type[Any],
        parameters: Sequence[inspect.Parameter],
        extensible: bool,
    ) -> None:
        object.__setattr__(self, "protocol", protocol)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "extensible", extensible)


type FunctionSignature = CallableSignature | ProtocolSignature


def _unpacked(argument: Any, /) -> bool:
    return get_origin(argument) in (typing.Unpack, typing_extensions.Unpack) or getattr(
        argument, "__unpacked__", False
    )


def _function_signature(
    annotation: Any,
    /,
) -> FunctionSignature | None:
    # only the shape of the signature is checked, argument types stay unresolved
    origin: Any = get_origin(annotation) or annotation
    if is_callback_protocol(origin):
        return _protocol_signature(origin)

    arguments: tuple[Any, ...] = get_args(annotation)
    if not arguments:
        return None  # a bare Callable

    signature: Any = arguments[0]
    if isinstance(signature, list | tuple):
        listed: Sequence[Any] = cast(Sequence[Any], signature)
        # an unpacked TypeVarTuple stands for any number of further arguments
        fixed: int = sum(1 for argument in listed if not _unpacked(argument))
        return _callable_signature(fixed, extensible=fixed < len(listed))

    if get_origin(signature) in (typing.Concatenate, typing_extensions.Concatenate):
        # the last argument of Concatenate is the ParamSpec
        return _callable_signature(len(get_args(signature)) - 1, extensible=True)

    return None  # `...` or a ParamSpec


def _callable_signature(
    arguments: int,
    /,
    *,
    extensible: bool,
) -> CallableSignature | None:
    if extensible and not arguments:
        return None  # accepts any call

    return CallableSignature(arguments=arguments, extensible=extensible)


def is_callback_protocol(
    annotation: Any,
    /,
) -> bool:
    """
    Tell whether an annotation is a protocol describing a function.

    Parameters
    ----------
    annotation : Any
        A class, or anything else an annotation may be.

    Returns
    -------
    bool
        ``True`` for a protocol whose only member is ``__call__``.
    """
    return typing.is_protocol(annotation) and typing.get_protocol_members(annotation) == frozenset(
        ("__call__",)
    )


def _protocol_signature(
    protocol: type[Any],
    /,
) -> ProtocolSignature | None:
    # read statically, keeping annotations as written - they may refer to names declared later
    call: Any = inspect.getattr_static(protocol, "__call__")
    function: Any = call
    if isinstance(call, staticmethod | classmethod):
        function = cast(Any, call).__func__
    parameters: tuple[inspect.Parameter, ...] = tuple(
        inspect.signature(
            function,
            annotation_format=annotationlib.Format.FORWARDREF,
        ).parameters.values()
    )
    if not isinstance(call, staticmethod):
        parameters = parameters[1:]  # drop self or cls

    # a ParamSpec accepts any further arguments - only the parameters before it are checked
    if any(
        isinstance(parameter.annotation, typing.ParamSpecArgs | typing.ParamSpecKwargs)
        for parameter in parameters
    ):
        fixed: tuple[inspect.Parameter, ...] = tuple(
            parameter
            for parameter in parameters
            if parameter.kind not in (parameter.VAR_POSITIONAL, parameter.VAR_KEYWORD)
        )
        if not fixed:
            return None  # accepts any call

        return ProtocolSignature(protocol=protocol, parameters=fixed, extensible=True)

    return ProtocolSignature(protocol=protocol, parameters=parameters, extensible=False)


@final
class UnionAttribute(_MetadataAnnotation):
    base: Any
    alternatives: Sequence[AttributeAnnotation]
    metadata: AttributeMetadata
    __slots__ = (
        "alternatives",
        "base",
        "metadata",
    )

    def __init__(
        self,
        *,
        base: Any,
        alternatives: Sequence[AttributeAnnotation] = (),
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "alternatives", alternatives)
        object.__setattr__(self, "metadata", metadata)

    @property
    def type_name(self) -> str:
        return "|".join(alt.type_name for alt in self.alternatives)


@final
class CustomAttribute(_MetadataAnnotation):
    base: Any
    parameters: Sequence[AttributeAnnotation]
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "metadata",
        "parameters",
    )

    def __init__(
        self,
        *,
        base: Any,
        parameters: Sequence[AttributeAnnotation] = (),
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "metadata", metadata)

    @property
    def type_name(self) -> str:
        return self.base.__qualname__


@final
class StrEnumAttribute(_MetadataAnnotation):
    base: type[enum.StrEnum]
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "metadata",
    )

    def __init__(
        self,
        *,
        base: type[enum.StrEnum],
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "metadata", metadata)

    @property
    def type_name(self) -> str:
        return self.base.__qualname__


@final
class IntEnumAttribute(_MetadataAnnotation):
    base: type[enum.IntEnum]
    metadata: AttributeMetadata
    __slots__ = (
        "base",
        "metadata",
    )

    def __init__(
        self,
        *,
        base: type[enum.IntEnum],
        metadata: AttributeMetadata = _DEFAULT_METADATA,
    ) -> None:
        object.__setattr__(self, "base", base)
        object.__setattr__(self, "metadata", metadata)

    @property
    def type_name(self) -> str:
        return self.base.__qualname__


@final
class _OwnReference:
    # a class being resolved, with the parameters which stand for each of the ones it
    # declares - a reference to it made of those, in the order of the declared ones, is a
    # reference to the class itself. it is one each where the class declares them, several
    # where a generic alias is given a parameter of the class more than once, and none
    # where its type parameters are shadowed, as within an alias, which only says the
    # class is being resolved
    __slots__ = ("origin", "standing_for")

    def __init__(
        self,
        origin: type[Any],
        standing_for: tuple[frozenset[Any], ...],
    ) -> None:
        self.origin: type[Any] = origin
        self.standing_for: tuple[frozenset[Any], ...] = standing_for

    def refers_to(self, arguments: Sequence[Any], /) -> bool:
        if not self.standing_for or len(arguments) != len(self.standing_for):
            return False

        return all(
            _declared_parameter(argument) in parameters
            for argument, parameters in zip(arguments, self.standing_for, strict=True)
        )

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, _OwnReference)
            and self.origin is other.origin
            and self.standing_for == other.standing_for
        )

    def __hash__(self) -> int:
        return hash((id(self.origin), self.standing_for))


@final
class _AliasUse:
    # a generic alias given its arguments, as seen from what the class being resolved
    # is referred to by - the same alias resolves to something else for another of them
    __slots__ = ("alias", "arguments", "references")

    def __init__(
        self,
        alias: Any,
        arguments: tuple[Any, ...],
        references: frozenset[_OwnReference],
    ) -> None:
        self.alias: Any = alias
        self.arguments: tuple[Any, ...] = arguments
        self.references: frozenset[_OwnReference] = references

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, _AliasUse)
            and self.alias is other.alias
            and self.arguments == other.arguments
            and self.references == other.references
        )

    def __hash__(self) -> int:
        return hash((id(self.alias), self.arguments, self.references))


def _generic_origin(cls: type[Any], /) -> type[Any]:
    # a specialization is resolved for the generic it specializes
    return cls.__dict__.get("__origin__", cls)


def _declared_parameter(argument: Any, /) -> Any | None:
    # the parameter an argument stands for, `*Ts` for the variadic one - nothing if it
    # is an argument of any other kind
    if get_origin(argument) is typing.Unpack:
        argument = get_args(argument)[0]

    if isinstance(argument, TypeVar | typing.ParamSpec | typing.TypeVarTuple):
        return argument

    return None


def resolve_self_attribute(
    cls: type[Any],
    /,
    parameters: Mapping[str, Any],
    # annotations of the attributes of the class, the inherited ones included, read
    # by the caller before the class existed - its namespace would otherwise shadow
    # a type used as an annotation with the descriptor of the same name
    annotations: Mapping[str, Any],
    # names the class provides a default for - the class itself can't be asked,
    # since a descriptor stands in for every attribute, default or not
    defaults: Set[str] = frozenset(),
) -> ObjectAttribute:
    recursion_guard: MutableMapping[Any, AttributeAnnotation] = {}
    generic: type[Any] = _generic_origin(cls)
    declared_parameters: tuple[Any, ...] = tuple(type_parameters_of(generic))
    # what a string annotation sees: the generic itself, and its type parameters - as
    # the class body would, were they not evaluated before the class exists
    localns: Mapping[str, Any] = {
        **{parameter.__name__: parameter for parameter in declared_parameters},
        generic.__name__: generic,
    }
    resolved_parameters: Mapping[str, AttributeAnnotation] = {
        key: resolve_attribute(
            value,
            localns=localns,
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
    # and so is the generic given its own parameters, `Tree[V]` within `Tree[V]`
    if declared_parameters:
        recursion_guard[
            _OwnReference(
                generic,
                tuple(frozenset((parameter,)) for parameter in declared_parameters),
            )
        ] = self_attribute

    recursion_guard[
        _recursion_key(
            origin=cls,
            parameters=tuple(resolved_parameters.values()),
        )
    ] = self_attribute

    for key, annotation in annotations.items():
        attribute: AttributeAnnotation = resolve_attribute(
            annotation,
            localns=localns,
            module=self_attribute.base.__module__,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )
        if key in defaults and attribute.required:
            attribute = attribute.annotated((NOT_REQUIRED,))

        attributes[key] = attribute

    return self_attribute


def _declared_self_attribute(
    annotation: Any,
    /,
) -> ObjectAttribute | None:
    """The resolved attribute of a class, when it is the one that class declared.

    Parameters
    ----------
    annotation : Any
        Annotation to inspect - a class above all, anything else resolving to
        nothing.

    Returns
    -------
    ObjectAttribute | None
        Attribute the class resolved for itself, or ``None`` when it has not
        resolved one yet.

    Notes
    -----
    A class being created has no attribute of its own yet - it is resolved to be
    assigned, and looking the name up on it reaches the one of its base instead,
    describing that class rather than the one the annotation refers to. The base
    of the attribute is what tells the two apart, so a class referring to itself
    within its own body resolves through the recursion guard, which holds the
    attribute being built for exactly that.
    """
    self_attribute: Any = getattr(annotation, "__SELF_ATTRIBUTE__", None)
    if self_attribute is None:
        return None

    assert isinstance(self_attribute, ObjectAttribute)  # nosec: B101
    if isinstance(annotation, type) and self_attribute.base is not annotation:
        return None  # inherited from a base - this class has not resolved its own

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


def _variadic_parameter(argument: Any, /) -> str | None:
    # the name of the variadic parameter an unpacked argument stands for, `*Ts`
    if get_origin(argument) is typing.Unpack:
        unpacked: Any = get_args(argument)[0]
        if isinstance(unpacked, typing.TypeVarTuple):
            return unpacked.__name__

    return None


def _unpacked_tuple(argument: Any, /) -> Sequence[Any] | None:
    # the arguments of an unpacked tuple, `*tuple[int, str]` or `*tuple[int, ...]` - also
    # as a type alias of one, `*IntTuple`
    unpacked: Any = argument
    if get_origin(argument) is typing.Unpack:
        unpacked = get_args(argument)[0]
        visited: set[int] = set()
        while isinstance(unpacked, typing.TypeAliasType | typing_extensions.TypeAliasType):
            if id(unpacked) in visited:
                return None  # an alias of itself stands for nothing

            visited.add(id(unpacked))
            unpacked = unpacked.__value__

    elif not (isinstance(argument, GenericAlias) and argument.__unpacked__):
        return None

    if unpacked is builtins.tuple or unpacked is typing.Tuple:  # noqa: UP006
        # not parametrized at all, which is any number of anything - `tuple[()]` is the empty one
        return (Any, builtins.Ellipsis)

    if get_origin(unpacked) is builtins.tuple:
        return get_args(unpacked)

    return None


def _resolve_tuple(  # noqa: C901
    annotation: Any,
    *,
    localns: Mapping[str, Any] | None,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    leading: MutableSequence[AttributeAnnotation] = []
    variadic: AttributeAnnotation | None = None
    variadic_base: Callable[[], Any] | None = None
    trailing: MutableSequence[AttributeAnnotation] = []

    def append(attribute: AttributeAnnotation) -> None:
        # what follows the unbounded part is counted from the end
        if variadic is None:
            leading.append(attribute)

        else:
            trailing.append(attribute)

    def open_at(attribute: AttributeAnnotation, base: Callable[[], Any]) -> None:
        # the base is asked for only when it is needed - an alias which stands for
        # the tuple itself is not bound until the tuple is resolved
        nonlocal variadic, variadic_base
        if variadic is not None:
            raise TypeError(
                f"'{annotation}' has more than one unbounded part, a tuple can have only one"
            )

        variadic = attribute
        variadic_base = base

    def splice_parameter(name: str) -> None:
        # a variadic parameter stands for the arguments it was given, which is
        # a tuple of them - and for any number of anything when it was not
        resolved: AttributeAnnotation | None = resolved_parameters.get(name)
        if isinstance(resolved, TupleAttribute):
            for value in resolved.values:
                append(value)

        elif isinstance(resolved, OpenTupleAttribute):
            for value in resolved.leading:
                append(value)

            open_variadic: AttributeAnnotation = resolved.variadic
            open_at(open_variadic, lambda: open_variadic.base)
            trailing.extend(resolved.trailing)

        elif isinstance(resolved, SequenceAttribute):
            open_values: AttributeAnnotation = resolved.values
            open_at(open_values, lambda: open_values.base)

        elif resolved is None:
            open_at(ANY_ATTRIBUTE, lambda: Any)

        else:
            raise TypeError(
                f"'{name}' of '{annotation}' can't stand for '{resolved}',"
                " only for the types of a tuple"
            )

    def splice(arguments: Sequence[Any]) -> None:
        match arguments:
            case [element, builtins.Ellipsis]:
                open_at(
                    resolve_attribute(
                        element,
                        localns=localns,
                        module=module,
                        resolved_parameters=resolved_parameters,
                        recursion_guard=recursion_guard,
                    ),
                    lambda: element,
                )
                return

            case _:
                pass

        for argument in arguments:
            if (name := _variadic_parameter(argument)) is not None:
                splice_parameter(name)

            elif (unpacked := _unpacked_tuple(argument)) is not None:
                splice(unpacked)

            else:
                append(
                    resolve_attribute(
                        argument,
                        localns=localns,
                        module=module,
                        resolved_parameters=resolved_parameters,
                        recursion_guard=recursion_guard,
                    )
                )

    splice(get_args(annotation))
    if variadic is None:
        return TupleAttribute(
            base=annotation,
            values=tuple(leading),
        )

    if not leading and not trailing:
        # any number of the one type is what `tuple[X, ...]` is
        assert variadic_base is not None  # nosec: B101
        return SequenceAttribute(
            base=Sequence[variadic_base()],
            values=variadic,
        )

    # the elements around the unbounded part are still what they are declared to be
    return OpenTupleAttribute(
        base=annotation,
        leading=tuple(leading),
        variadic=variadic,
        trailing=tuple(trailing),
    )


def _resolve_forward_reference(
    resolved: Any,
    *,
    localns: Mapping[str, Any] | None,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    hashable: bool = isinstance(resolved, Hashable)
    if hashable and (guard := recursion_guard.get(resolved)):
        return guard

    # a class being resolved is guarded by its name, which another class can have as
    # well - the guard found by it is the one of this class only when it describes it
    recursion_key: str = _recursion_key(origin=resolved)
    if (guard := recursion_guard.get(recursion_key)) and getattr(guard, "base", None) is resolved:
        return guard

    attribute: AttributeAnnotation = resolve_attribute(
        resolved,
        localns=localns,
        module=module,
        resolved_parameters=resolved_parameters,
        recursion_guard=recursion_guard,
    )
    if hashable:
        recursion_guard[resolved] = attribute

    recursion_guard.setdefault(recursion_key, attribute)

    return attribute


def _resolve_parameters(
    annotation: Any,
    *,
    localns: Mapping[str, Any] | None,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> Sequence[AttributeAnnotation]:
    return tuple(
        resolve_attribute(
            argument,
            localns=localns,
            resolved_parameters=resolved_parameters,
            module=module,
            recursion_guard=recursion_guard,
        )
        for argument in get_args(annotation)
    )


def _evaluate_forward_ref(
    annotation: ForwardRef | str,
    /,
    localns: Mapping[str, Any] | None,
    module: str,
) -> Any:
    try:
        if isinstance(annotation, ForwardRef):
            try:  # within what the reference was made in, when it knows that
                return annotation.evaluate(locals=localns)

            except NameError:
                # a reference made without its module - the module of the attribute
                annotation = annotation.__forward_arg__

        return ForwardRef(annotation, module=module).evaluate(locals=localns)

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
        # the declared alias, never the reported one - `alias` is overridden by an
        # `Alias(...)` annotation, which would leave an annotated recursive use
        # unmatched here and unresolved by the time it is validated against
        if attribute.type_alias == alias_name and attribute.module == alias_module:
            if not attribute.is_resolved:
                attribute.resolve(alias_target)

            if attribute.is_resolved:
                _finalize_alias_resolution(
                    attribute.resolved,
                    alias_name=alias_name,
                    alias_module=alias_module,
                    alias_target=alias_target,
                    visited=visited,
                )

        elif attribute.is_resolved:
            _finalize_alias_resolution(
                attribute.resolved,
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

    elif isinstance(attribute, OpenTupleAttribute):
        for value in _tuple_elements(attribute):
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


def _terminates_recursion(key: Any, value: AttributeAnnotation, /) -> bool:
    # what a resolution still going on within the scope of an alias needs to be found
    # there: an alias being resolved, and a class by its name or itself. a completed result
    # was resolved for the class being what it is referred to by outside of the alias
    if isinstance(value, AliasAttribute):
        return not value.is_resolved

    return isinstance(key, str | type) and isinstance(value, ObjectAttribute | TypedDictAttribute)


def _alias_references(
    recursion_guard: Mapping[Any, AttributeAnnotation],
    /,
    parameters: Sequence[Any],
    arguments: Sequence[Any],
) -> Mapping[_OwnReference, AttributeAnnotation]:
    # an alias is written in terms of its own parameters, none of which is the parameter
    # of a class it is given - the class being resolved is referred to by the ones
    # standing for its parameters, whichever of them is used where several stand for one
    references: dict[_OwnReference, AttributeAnnotation] = {}
    for reference, attribute in tuple(recursion_guard.items()):
        if not isinstance(reference, _OwnReference) or not reference.standing_for:
            continue

        standing_for: tuple[frozenset[Any], ...] = tuple(
            frozenset(
                parameter
                for parameter, argument in zip(parameters, arguments, strict=False)
                if _declared_parameter(argument) in declared
            )
            for declared in reference.standing_for
        )
        if all(standing_for):
            references[_OwnReference(reference.origin, standing_for)] = attribute

    return references


def _alias_scope(
    annotation: typing.TypeAliasType | typing_extensions.TypeAliasType,
    /,
    localns: Mapping[str, Any] | None,
) -> Mapping[str, Any]:
    # what the value of an alias sees is its own scope - the type parameters of the class
    # using it are not names within it
    scope: Mapping[str, Any] = localns or {}
    shadowing: Mapping[str, Any] = {
        name: value
        for name, value in scope.items()
        if isinstance(value, TypeVar | typing.ParamSpec | typing.TypeVarTuple)
    }
    if not shadowing and not annotation.__type_params__:
        return scope

    return {
        **{name: value for name, value in scope.items() if name not in shadowing},
        **{parameter.__name__: parameter for parameter in annotation.__type_params__},
    }


def _alias_value(
    annotation: typing.TypeAliasType | typing_extensions.TypeAliasType,
    /,
) -> Any:
    try:
        return annotation.__value__

    except AttributeError as error:
        raise AttributeError(
            f"{error} within the value of type alias '{annotation.__name__}'",
            name=error.name,
            obj=error.obj,
        ) from error

    except NameError:
        # the value is evaluated lazily, when the names it refers to may not exist yet - a
        # class being created is not bound in its module. what is not defined is left as a
        # reference, resolved the way any other is
        return annotationlib.call_evaluate_function(
            annotation.evaluate_value,
            annotationlib.Format.FORWARDREF,
        )


def _resolve_alias_value(
    annotation: typing.TypeAliasType | typing_extensions.TypeAliasType,
    value: Any,
    /,
    *,
    localns: Mapping[str, Any] | None,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    try:
        return resolve_attribute(
            value,
            localns=_alias_scope(annotation, localns),
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    except RuntimeError as error:
        # reported as it is when an alias can't be evaluated - naming the alias
        cause: BaseException | None = error.__cause__
        if isinstance(cause, NameError):
            raise NameError(
                f"{cause} within the value of type alias '{annotation.__name__}'",
                name=cause.name,
            ) from error

        if isinstance(cause, AttributeError):
            raise AttributeError(
                f"{cause} within the value of type alias '{annotation.__name__}'",
                name=cause.name,
                obj=cause.obj,
            ) from error

        raise


def _resolve_generic_type_alias(
    annotation: typing.TypeAliasType | typing_extensions.TypeAliasType,
    *,
    arguments: Sequence[Any],
    localns: Mapping[str, Any] | None,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    references: Mapping[_OwnReference, AttributeAnnotation] = _alias_references(
        recursion_guard,
        parameters=annotation.__type_params__,
        arguments=arguments,
    )
    use: _AliasUse | None = _AliasUse(annotation, tuple(arguments), frozenset(references))
    try:
        hash(use)

    except TypeError:
        use = None  # an argument which can't be told from another one is not cached

    if use is not None and (guard := recursion_guard.get(use)):
        return guard

    placeholder = AliasAttribute(
        type_alias=annotation.__name__,
        module=annotation.__module__ or module,
    )
    if use is not None:
        recursion_guard[use] = placeholder

    # within the alias its own parameters stand where the type parameters of the class
    # would, the class is referred to by them only - and by nothing else resolved on the
    # way is any of it to be taken for what the alias is not given - only what is still
    # being resolved is carried, the results are not
    scope: MutableMapping[Any, AttributeAnnotation] = {
        key: value
        for key, value in recursion_guard.items()
        if not isinstance(key, _OwnReference) and _terminates_recursion(key, value)
    }
    scope.update(
        {
            _OwnReference(reference.origin, ()): attribute
            for reference, attribute in recursion_guard.items()
            if isinstance(reference, _OwnReference)
        }
    )
    scope.update(references)
    resolved_attribute: AttributeAnnotation = _resolve_alias_value(
        annotation,
        _alias_value(annotation),
        localns=localns,
        module=module,
        resolved_parameters=resolved_parameters,
        recursion_guard=scope,
    )
    placeholder.resolve(resolved_attribute)
    if use is not None:
        recursion_guard[use] = resolved_attribute

    _finalize_alias_resolution(
        resolved_attribute,
        alias_name=placeholder.type_alias,
        alias_module=placeholder.module,
        alias_target=resolved_attribute,
        visited=set(),
    )

    return resolved_attribute


def _resolve_type_alias(
    annotation: typing.TypeAliasType | typing_extensions.TypeAliasType,
    *,
    arguments: Sequence[Any] = (),
    localns: Mapping[str, Any] | None,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    if annotation.__type_params__:
        return _resolve_generic_type_alias(
            annotation,
            arguments=arguments,
            localns=localns,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    if guard := recursion_guard.get(annotation):
        return guard

    value: Any = _alias_value(annotation)
    recursion_key: str = _recursion_key(
        origin=get_origin(value) or value,
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

    resolved_attribute: AttributeAnnotation = _resolve_alias_value(
        annotation,
        value,
        localns=localns,
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


def _resolved_alias_arguments(
    arguments: Sequence[Any],
    resolved_parameters: Mapping[str, AttributeAnnotation],
    /,
) -> Sequence[Any] | None:
    resolved: list[Any] = []
    for argument in arguments:
        if isinstance(argument, TypeVar):
            if parameter := resolved_parameters.get(argument.__name__):
                resolved.append(parameter.base)

            else:
                resolved.append(argument.__bound__ or Any)

        elif isinstance(argument, typing.ParamSpec):
            # the signature it stands for, `...` while the class is not specialized
            parameter = resolved_parameters.get(argument.__name__)
            resolved.append(parameter.base if parameter else Ellipsis)

        elif (name := _variadic_parameter(argument)) is not None:
            parameter = resolved_parameters.get(name)
            if parameter is None:
                return None

            resolved.extend(get_args(parameter.base))

        else:
            resolved.append(argument)

    return resolved


def _own_attribute(
    origin_type: Any,
    annotation: GenericAlias,
    recursion_guard: Mapping[Any, AttributeAnnotation],
    /,
) -> AttributeAnnotation | None:
    # the class being resolved, given its own parameters, is the class itself
    arguments: tuple[Any, ...] = get_args(annotation)
    references: Sequence[tuple[_OwnReference, AttributeAnnotation]] = tuple(
        (key, value)
        for key, value in recursion_guard.items()
        if isinstance(key, _OwnReference) and key.origin is origin_type
    )
    for reference, own in references:
        if reference.refers_to(arguments):
            return own

    # given anything else it can't be specialized, it does not exist yet
    if references:
        raise refused_self_specialization(origin_type)

    return None


def _resolve_generic_alias(  # noqa: PLR0911
    annotation: GenericAlias,
    *,
    localns: Mapping[str, Any] | None,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
) -> AttributeAnnotation:
    origin_type: Any = annotation.__origin__
    if is_callback_protocol(origin_type):
        # checked by the shape of its signature, whatever its type arguments
        return FunctionAttribute(base=annotation)

    if isinstance(origin_type, typing.TypeAliasType | typing_extensions.TypeAliasType):
        return _resolve_type_alias(
            origin_type,
            arguments=get_args(annotation),
            localns=localns,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    if not hasattr(origin_type, "__class_getitem__"):
        return resolve_attribute(
            origin_type,
            localns=localns,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    if own := _own_attribute(origin_type, annotation, recursion_guard):
        return own

    # try to resolve the alias with available types
    resolved_arguments_of_alias: Sequence[Any] | None = _resolved_alias_arguments(
        get_args(annotation),
        resolved_parameters,
    )
    if resolved_arguments_of_alias is None:
        # a variadic parameter which is not given its arguments - no number of them
        # to specialize with, the generic is taken unspecialized
        return resolve_attribute(
            origin_type,
            localns=localns,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    resolved_origin: Any = origin_type.__class_getitem__(tuple(resolved_arguments_of_alias))

    # a specialized class is resolved as itself, an alias of the generic as an alias
    if get_origin(resolved_origin) is None:
        if specialized_self := _declared_self_attribute(resolved_origin):
            return specialized_self

        return resolve_attribute(
            resolved_origin,
            localns=localns,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )

    # otherwise resolve alias as we can
    resolved_arguments: Sequence[Any] = get_args(resolved_origin)
    generic_parameters: Sequence[Any] = getattr(annotation.__origin__, "__parameters__", ())

    if generic_parameters and resolved_arguments:
        # bound as resolved attributes, as a parameter is read everywhere else
        resolved_parameters = {
            **resolved_parameters,
            **{
                parameter.__name__: resolve_attribute(
                    argument,
                    localns=localns,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                )
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
            localns=localns,
            module=resolved_module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        ),
    )
    if guard := recursion_guard.get(recursion_key):
        return guard

    resolved_attribute: AttributeAnnotation = resolve_attribute(
        resolved_origin.__origin__,
        localns=localns,
        module=resolved_module,
        resolved_parameters=resolved_parameters,
        recursion_guard=recursion_guard,
    )
    recursion_guard[recursion_key] = resolved_attribute

    return resolved_attribute


def _resolve_typeddict(
    annotation: Any,
    *,
    arguments: Sequence[Any],
    localns: Mapping[str, Any] | None,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    parameters: Sequence[AttributeAnnotation] = tuple(
        resolve_attribute(
            argument,
            localns=localns,
            module=module,
            resolved_parameters=resolved_parameters,
            recursion_guard=recursion_guard,
        )
        for argument in arguments
    )
    if parameters:
        resolved_parameters = {
            **resolved_parameters,
            **{
                parameter.__name__: resolved
                for parameter, resolved in zip(
                    getattr(annotation, "__parameters__", ()),
                    parameters,
                    strict=False,
                )
                if hasattr(parameter, "__name__")
            },
        }

    attributes: MutableMapping[str, AttributeAnnotation] = {}
    resolved_attribute: TypedDictAttribute = TypedDictAttribute(
        base=annotation,
        attributes=attributes,
    )

    recursion_key: str = _recursion_key(
        origin=annotation,
        parameters=parameters,
    )

    if guard := recursion_guard.get(recursion_key):
        return guard

    recursion_guard[recursion_key] = resolved_attribute

    # Self refers to the TypedDict within it - the reference of the enclosing
    # resolution is brought back afterwards, or removed when there was none
    self_attribute: AttributeAnnotation | None = recursion_guard.get("Self", None)
    recursion_guard["Self"] = resolved_attribute
    try:
        for key, element in get_type_hints(
            annotation,
            localns={annotation.__name__: annotation},
            include_extras=True,
        ).items():
            attribute: AttributeAnnotation = resolve_attribute(
                element,
                localns=localns,
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

    finally:
        if self_attribute is None:
            del recursion_guard["Self"]

        else:
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
TIMEDELTA_ATTRIBUTE: Final[TimedeltaAttribute] = TimedeltaAttribute()
TIME_ATTRIBUTE: Final[TimeAttribute] = TimeAttribute()
PATH_ATTRIBUTE: Final[PathAttribute] = PathAttribute()


def _resolve_type(  # noqa: C901, PLR0911, PLR0912, PLR0915
    annotation: Any,
    *,
    localns: Mapping[str, Any] | None,
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
                arguments=get_args(annotation),
                localns=localns,
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

        case datetime.timedelta:
            return TIMEDELTA_ATTRIBUTE

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
                    localns=localns,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                ),
                values=resolve_attribute(
                    values_annotation,
                    localns=localns,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                ),
            )

        case (
            builtins.set
            | builtins.frozenset
            | collections_abc.Set
            | collections_abc.MutableSet
            | typing.Set  # noqa: UP006
            | typing.MutableSet
            | typing.FrozenSet  # noqa: UP006
            | typing_extensions.Set
            | typing_extensions.MutableSet
            | typing_extensions.FrozenSet
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
                    localns=localns,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                ),
            )

        case builtins.tuple | typing.Tuple | typing_extensions.Tuple:  # noqa: UP006
            match get_args(annotation):
                case () if annotation in (builtins.tuple, typing.Tuple):  # noqa: UP006
                    # not parametrized at all, which is any tuple - `tuple[()]` is the empty one
                    return SequenceAttribute(
                        base=Sequence[Any],
                        values=ANY_ATTRIBUTE,
                    )

                case (values_annotation, builtins.Ellipsis):
                    return SequenceAttribute(
                        base=Sequence[values_annotation],
                        values=resolve_attribute(
                            values_annotation,
                            localns=localns,
                            module=module,
                            resolved_parameters=resolved_parameters,
                            recursion_guard=recursion_guard,
                        ),
                    )

                case _:
                    return _resolve_tuple(
                        annotation,
                        localns=localns,
                        module=module,
                        resolved_parameters=resolved_parameters,
                        recursion_guard=recursion_guard,
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
                    localns=localns,
                    module=module,
                    resolved_parameters=resolved_parameters,
                    recursion_guard=recursion_guard,
                ),
            )

        case (
            collections_abc.Collection
            | collections_abc.Iterable
            | typing.Collection
            | typing.Iterable
            | typing_extensions.Collection
            | typing_extensions.Iterable
        ):
            values_annotation: Any
            match get_args(annotation):
                case (values,):
                    values_annotation = values

                case _:
                    values_annotation = Any

            # unordered and single use iterables are normalized to an immutable
            # sequence first - the annotation admits them while the stored value
            # has to be an immutable snapshot of its elements
            return ValidableAttribute(
                validating=_iterable_snapshot,
                attribute=SequenceAttribute(
                    base=Sequence[values_annotation],
                    values=resolve_attribute(
                        values_annotation,
                        localns=localns,
                        module=module,
                        resolved_parameters=resolved_parameters,
                        recursion_guard=recursion_guard,
                    ),
                ),
            )

        case protocol if is_callback_protocol(protocol):
            return FunctionAttribute(base=annotation)

        case origin:
            if self_attribute := _declared_self_attribute(annotation):
                return self_attribute

            parameters: Sequence[AttributeAnnotation] = _resolve_parameters(
                annotation,
                localns=localns,
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

            return CustomAttribute(
                base=origin,
                parameters=parameters,
            )


# the special forms which only wrap their arguments - resolved by their own cases, never
# specialized as a generic is
_PARAMETER_WRAPPING_FORMS: Final[frozenset[Any]] = frozenset(
    (
        typing.Annotated,
        typing_extensions.Annotated,
        typing.Union,
        typing_extensions.Union,
        types.UnionType,
        typing.Required,
        typing_extensions.Required,
        typing.NotRequired,
        typing_extensions.NotRequired,
        typing.Final,
        typing_extensions.Final,
        typing.ReadOnly,
        typing_extensions.ReadOnly,
    )
)


def resolve_attribute(  # noqa: C901, PLR0911, PLR0912
    annotation: Any,
    /,
    module: str,
    resolved_parameters: Mapping[str, AttributeAnnotation],
    recursion_guard: MutableMapping[Any, AttributeAnnotation],
    localns: Mapping[str, Any] | None = None,
) -> AttributeAnnotation:
    origin: Any | None = get_origin(annotation)

    # a generic given a type parameter is specialized once the parameter is known - a
    # special form wrapping a parameter, `Annotated[T, ...]` among them, is resolved by
    # its own case below instead, which resolves the parameter and keeps the form. So is a
    # builtin or abstract collection, `Sequence[T]` among them - it declares no type
    # parameters to specialize, and its own case resolves the element types
    if (
        origin is not None
        and origin not in _PARAMETER_WRAPPING_FORMS
        and getattr(origin, "__parameters__", ())
        and any(
            isinstance(argument, TypeVar)
            # a variadic parameter or a parameter specification is only given to a generic
            # State - elsewhere it is part of what the annotation is made of
            or (is_open_type_argument(argument) and hasattr(origin, "__ANNOTATED__"))
            for argument in get_args(annotation)
        )
    ):
        return _resolve_generic_alias(
            annotation,
            localns=localns,
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
                        localns=localns,
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
                localns=localns,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case typing.Annotated | typing_extensions.Annotated:
            annotation_args: Sequence[Any] = get_args(annotation)
            attribute: AttributeAnnotation = resolve_attribute(
                annotation_args[0],
                localns=localns,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )
            return attribute.annotated(_annotation_markers(annotation_args[1:]))

        case typing.TypeVar | typing_extensions.TypeVar:
            if resolved := resolved_parameters.get(annotation.__name__):
                return resolved

            return resolve_attribute(
                annotation.__bound__ or Any,
                localns=localns,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case typing.Literal | typing_extensions.Literal:
            return _resolve_literal(annotation)

        case collections_abc.Callable | typing.Callable | typing_extensions.Callable:  # pyright: ignore[reportUnknownMemberType]
            return FunctionAttribute(base=annotation)

        case typing.Self | typing_extensions.Self:  # pyright: ignore[reportUnknownMemberType]
            if self_attribute := recursion_guard.get("Self"):
                return self_attribute

            else:
                raise RuntimeError(f"Unresolved Self annotation: {annotation}")

        case typing.Required | typing_extensions.Required:
            attribute: AttributeAnnotation = resolve_attribute(
                get_args(annotation)[0],
                localns=localns,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

            return attribute

        case typing.NotRequired | typing_extensions.NotRequired:
            attribute: AttributeAnnotation = resolve_attribute(
                get_args(annotation)[0],
                localns=localns,
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
                        localns=localns,
                        module=module,
                        resolved_parameters=resolved_parameters,
                        recursion_guard=recursion_guard,
                    ),
                    NONE_ATTRIBUTE,
                ),
            )

        # a qualifier of a TypedDict key which only tells a type checker the key is not
        # to be assigned, it changes nothing about the value - State attributes are
        # immutable already
        case typing.Final | typing_extensions.Final | typing.ReadOnly | typing_extensions.ReadOnly:
            return resolve_attribute(
                get_args(annotation)[0],
                localns=localns,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case typing.ForwardRef | typing_extensions.ForwardRef:
            return _resolve_forward_reference(
                _evaluate_forward_ref(
                    annotation,
                    localns=localns,
                    module=module,
                ),
                localns=localns,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        # a value pattern, not a class pattern - the subject is the type of the
        # annotation, so `builtins.str()` would ask whether the `str` class is an
        # instance of `str` and never match, leaving string annotations resolved
        # as an opaque custom type which fails on the first validation
        case builtins.str:
            return _resolve_forward_reference(
                _evaluate_forward_ref(
                    annotation,
                    localns=localns,
                    module=module,
                ),
                localns=localns,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case type():
            return _resolve_type(
                annotation,
                localns=localns,
                module=module,
                resolved_parameters=resolved_parameters,
                recursion_guard=recursion_guard,
            )

        case _:
            raise TypeError(f"Unsupported annotation of '{annotation}'")
