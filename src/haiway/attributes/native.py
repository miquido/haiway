import sys
from collections.abc import Callable, Mapping, Sequence
from datetime import date, datetime, time
from enum import IntEnum
from pathlib import Path
from types import ModuleType
from typing import TYPE_CHECKING, Any, Final
from uuid import UUID

from haiway._native import nstruct
from haiway.attributes.annotations import (
    AnyAttribute,
    BoolAttribute,
    BytesAttribute,
    DateAttribute,
    DatetimeAttribute,
    FloatAttribute,
    IntegerAttribute,
    IntEnumAttribute,
    LiteralAttribute,
    MappingAttribute,
    MissingAttribute,
    NoneAttribute,
    ObjectAttribute,
    PathAttribute,
    SequenceAttribute,
    SetAttribute,
    StrEnumAttribute,
    StringAttribute,
    TimeAttribute,
    TimedeltaAttribute,
    TupleAttribute,
    UnionAttribute,
    UUIDAttribute,
    _no_verify,  # pyright: ignore[reportPrivateUsage]
)
from haiway.attributes.attribute import Attribute
from haiway.types import MISSING, Missing
from haiway.types.immutable import Immutable

__all__ = (
    "ConversionHooks",
    "SpellingHooks",
    "ValidationHooks",
    "configure",
    "install",
)

# What the native extension is told about a class, and everything Python hands it to
# call back. This is the one place which knows how the two are spelled to each other -
# the numbers of a kind come from the extension itself, nothing here repeats them.


def _extension() -> ModuleType:
    if nstruct is None:
        raise RuntimeError("The native implementation is not enabled - set HAIWAY_NATIVE=1")

    return nstruct


def _constants(prefix: str) -> Mapping[str, int]:
    extension: ModuleType | None = nstruct
    if extension is None:
        return {}

    return {
        name.removeprefix(prefix): getattr(extension, name)
        for name in dir(extension)
        if name.startswith(prefix)
    }


if TYPE_CHECKING:
    # what a type checker sees - the values at runtime are the ones of the extension

    class Kind(IntEnum):
        ANY = 0
        INT = 1
        STR = 2
        FLOAT = 3
        BOOL = 4
        CALL = 5
        INSTANCE = 6
        OPTIONAL = 7
        SEQUENCE = 8
        ISINSTANCE = 9
        MAPPING = 10
        TUPLE = 11
        LITERAL = 12
        SET = 13
        UNION = 14
        FLOAT_EXACT = 15

    class Source(IntEnum):
        NONE = 0
        STR = 1
        INT = 2

else:
    Kind = IntEnum("Kind", dict(_constants("K_")))
    Source = IntEnum("Source", dict(_constants("S_")))


# The description of what an annotation accepts as it is, so that no call of Python is
# needed to validate it. A description is one of these and nothing else - what the
# native code can't take over is a `CallSpec`, rather than the absence of a description.
# Each of them knows the kind and the argument the extension reads it as.


class LeafSpec(Immutable):
    kind: Kind  # a type which is accepted when it is exactly the one, or anything

    def encode(self) -> tuple[int, Any]:
        return (self.kind, None)


class StateSpec(Immutable):
    base: type[Any]  # an instance of this very State

    def encode(self) -> tuple[int, Any]:
        return (Kind.INSTANCE, self.base)


class InstanceSpec(Immutable):
    base: type[Any]
    # converts the spelling of one the annotation accepts - a str, or an int - to it
    converter: Callable[[Any], Any] | None
    source: Source

    def encode(self) -> tuple[int, Any]:
        return (Kind.ISINSTANCE, (self.base, self.converter, int(self.source)))


class LiteralSpec(Immutable):
    values: tuple[Any, ...]

    def encode(self) -> tuple[int, Any]:
        return (Kind.LITERAL, self.values)


class CallSpec(Immutable):
    # the annotation of Python code validates

    def encode(self) -> tuple[int, Any]:
        return (Kind.CALL, None)


class OptionalSpec(Immutable):
    singletons: tuple[Any, ...]  # None and Missing, told by identity
    inner: Spec

    def encode(self) -> tuple[int, Any]:
        kind, argument = self.inner.encode()
        return (Kind.OPTIONAL, (self.singletons, kind, argument))


class SequenceSpec(Immutable):
    element: Spec

    def encode(self) -> tuple[int, Any]:
        return (Kind.SEQUENCE, self.element.encode())


class TupleSpec(Immutable):
    elements: tuple[Spec, ...]

    def encode(self) -> tuple[int, Any]:
        return (Kind.TUPLE, tuple(element.encode() for element in self.elements))


class MappingSpec(Immutable):
    key: Spec
    value: Spec

    def encode(self) -> tuple[int, Any]:
        return (Kind.MAPPING, (*self.key.encode(), *self.value.encode()))


class SetSpec(Immutable):
    element: Spec

    def encode(self) -> tuple[int, Any]:
        return (Kind.SET, self.element.encode())


class UnionSpec(Immutable):
    singletons: tuple[Any, ...]
    # only what accepts a value as it is, and converts nothing - the first of them to
    # accept it is the one which does, so a conversion would decide between them
    alternatives: tuple[ExactSpec, ...]

    def encode(self) -> tuple[int, Any]:
        return (
            Kind.UNION,
            (self.singletons, tuple(alternative.encode() for alternative in self.alternatives)),
        )


type ExactSpec = LeafSpec | StateSpec | InstanceSpec | LiteralSpec
type Spec = (
    ExactSpec
    | CallSpec
    | OptionalSpec
    | SequenceSpec
    | TupleSpec
    | MappingSpec
    | SetSpec
    | UnionSpec
)

_EXACT: Final[tuple[type[Any], ...]] = (LeafSpec, StateSpec, InstanceSpec, LiteralSpec)

type IsState = Callable[[Any], bool]
type Describer = Callable[[Any, IsState], Spec]

# the tables are made of the kinds of the extension, which exist when it is used
_NATIVE: Final[bool] = nstruct is not None

# annotations which are exactly a type, or which any value of it passes
_LEAVES: Final[Mapping[type[Any], Kind]] = (
    {
        AnyAttribute: Kind.ANY,
        IntegerAttribute: Kind.INT,
        StringAttribute: Kind.STR,
        FloatAttribute: Kind.FLOAT,
        BoolAttribute: Kind.BOOL,
    }
    if _NATIVE
    else {}
)

# annotations which return an instance of their type as it is, and what converts the
# spellings of it they accept - a str, or an int for an enum of ints. an enum converts
# through the enum itself
_INSTANCES: Final[Mapping[type[Any], tuple[Callable[[Any], Any] | None, Source]]] = (
    {
        UUIDAttribute: (UUID, Source.STR),
        DatetimeAttribute: (datetime.fromisoformat, Source.STR),
        DateAttribute: (date.fromisoformat, Source.STR),
        TimeAttribute: (time.fromisoformat, Source.STR),
        TimedeltaAttribute: (None, Source.NONE),
        PathAttribute: (Path, Source.STR),
        BytesAttribute: (None, Source.NONE),
        StrEnumAttribute: (None, Source.STR),
        IntEnumAttribute: (None, Source.INT),
    }
    if _NATIVE
    else {}
)

# defaults of these types are validated once, as nothing can change them after
_IMMUTABLE_DEFAULTS: Final[frozenset[type[Any]]] = frozenset(
    (type(None), bool, int, float, str, bytes, Missing)
)


def describe(
    annotation: Any,
    /,
    is_state: IsState,
    convert: bool = True,
) -> Spec:
    """
    What the native code accepts as it is for an annotation.

    Only an annotation which does nothing but check the type of what it is given, and
    carries no verifier of its own, is described - anything else is left to it.

    Parameters
    ----------
    annotation : Any
        Annotation of an attribute.
    is_state : Callable[[Any], bool]
        Tells a State class which validates the usual way from any other.
    convert : bool, default=True
        Whether the spellings an annotation converts are part of the description. A
        union leaves them out.

    Returns
    -------
    Spec
        The description, a ``CallSpec`` when there is none.
    """
    if getattr(annotation, "verifying", None) is not _no_verify:
        return CallSpec()

    annotation_type: type[Any] = type(annotation)  # pyright: ignore[reportUnknownVariableType]
    if (leaf := _LEAVES.get(annotation_type)) is not None:  # pyright: ignore[reportUnknownArgumentType]
        # a float is also read from an int - which a union does not do, it keeps the int
        return LeafSpec(kind=Kind.FLOAT_EXACT if leaf == Kind.FLOAT and not convert else leaf)

    if annotation_type in _INSTANCES:  # pyright: ignore[reportUnknownArgumentType]
        return _instance(annotation, convert)

    for attribute_type, describer in _COMPOUND:
        if isinstance(annotation, attribute_type):
            return describer(annotation, is_state)

    return CallSpec()


def _instance(annotation: Any, convert: bool) -> InstanceSpec:
    converter, source = _INSTANCES[type(annotation)]  # pyright: ignore[reportUnknownArgumentType]
    if not convert:
        return InstanceSpec(base=annotation.base, converter=None, source=Source.NONE)

    if isinstance(annotation, StrEnumAttribute | IntEnumAttribute):
        converter = annotation.base

    return InstanceSpec(base=annotation.base, converter=converter, source=source)


def _literal(annotation: LiteralAttribute, is_state: IsState) -> Spec:
    return LiteralSpec(values=tuple(annotation.values))  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]


def _object(annotation: ObjectAttribute, is_state: IsState) -> Spec:
    base: Any = annotation.base
    return StateSpec(base=base) if is_state(base) else CallSpec()


def _sequence(annotation: SequenceAttribute, is_state: IsState) -> Spec:
    element: Spec = describe(annotation.values, is_state)
    return CallSpec() if isinstance(element, CallSpec) else SequenceSpec(element=element)


def _set(annotation: SetAttribute, is_state: IsState) -> Spec:
    element: Spec = describe(annotation.values, is_state)
    return CallSpec() if isinstance(element, CallSpec) else SetSpec(element=element)


def _mapping(annotation: MappingAttribute, is_state: IsState) -> Spec:
    key: Spec = describe(annotation.keys, is_state)
    value: Spec = describe(annotation.values, is_state)
    if isinstance(key, CallSpec) or isinstance(value, CallSpec):
        return CallSpec()

    return MappingSpec(key=key, value=value)


def _tuple(annotation: TupleAttribute, is_state: IsState) -> Spec:
    elements: tuple[Spec, ...] = tuple(
        describe(item, is_state)
        for item in annotation.values  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
    )
    if any(isinstance(item, CallSpec) for item in elements):
        return CallSpec()

    return TupleSpec(elements=elements)


def _union(annotation: UnionAttribute, is_state: IsState) -> Spec:
    singletons: list[Any] = []
    others: list[Any] = []
    for alternative in annotation.alternatives:
        if isinstance(alternative, NoneAttribute | MissingAttribute):
            if getattr(alternative, "verifying", None) is not _no_verify:
                return CallSpec()

            singletons.append(None if isinstance(alternative, NoneAttribute) else MISSING)

        else:
            others.append(alternative)

    if len(others) == 1:
        # one alternative next to the ones which can only be told by their identity
        inner: Spec = describe(others[0], is_state)
        if isinstance(inner, CallSpec):
            return CallSpec()

        return OptionalSpec(singletons=tuple(singletons), inner=inner)

    exact: list[ExactSpec] = []
    for alternative in others:
        spec: Spec = describe(alternative, is_state, convert=False)
        if not isinstance(spec, _EXACT):
            return CallSpec()

        exact.append(spec)

    return UnionSpec(singletons=tuple(singletons), alternatives=tuple(exact))


_COMPOUND: Final[tuple[tuple[type[Any], Describer], ...]] = (
    (LiteralAttribute, _literal),
    (ObjectAttribute, _object),
    (UnionAttribute, _union),
    (SequenceAttribute, _sequence),
    (SetAttribute, _set),
    (MappingAttribute, _mapping),
    (TupleAttribute, _tuple),
)


def _cached_default(field: Attribute, /) -> Any:
    extension: ModuleType = _extension()
    try:
        value: Any = field._validated_default()  # pyright: ignore[reportPrivateUsage]

    except Exception:
        return extension.NOCACHE  # left to fail where the instance is created

    return value if type(value) in _IMMUTABLE_DEFAULTS else extension.NOCACHE


def _entry(field: Attribute, is_state: IsState) -> tuple[Any, ...]:
    spec: Spec = describe(field.annotation, is_state)
    kind, argument = spec.encode()
    # a constant default of an immutable type is validated once, not on each use. an
    # annotation which is not described may carry a validator or a verifier of the
    # user, which is not to run before an instance asks for it
    cached: Any = (
        _cached_default(field)
        if not isinstance(spec, CallSpec) and field.default.constant
        else _extension().NOCACHE
    )

    # the entry is what `compile_plan` of the extension reads, field by field. the names
    # are interned, as the names of the keyword arguments of a call are - which is what
    # lets the native code tell one from another by identity
    return (
        sys.intern(field.name),
        sys.intern(field.alias) if field.alias is not None else None,
        kind,
        field.annotation.validate,
        field._validated_default,  # pyright: ignore[reportPrivateUsage]
        cached,
        argument,
    )


def install(
    cls: Any,
    /,
    fields: Sequence[Attribute],
    owned: Sequence[str],
    is_state: IsState,
) -> None:
    """
    Give a State class its native storage and its plan.

    Parameters
    ----------
    cls : Any
        Class which was just created.
    fields : Sequence[Attribute]
        Every attribute of the class, in order.
    owned : Sequence[str]
        The attributes the class declares itself - a specialization declares none and
        reads the ones of its origin through the descriptors it inherits.
    is_state : Callable[[Any], bool]
        Tells a State class which validates the usual way from any other.
    """
    extension: ModuleType = _extension()
    # the names this class stores - kept for what reads them, the values themselves
    # live inline in the instance, not in slots
    cls.__slots__ = tuple(owned)
    # what each descriptor reads its member from, kept as long as the class is
    definitions: list[object] = []
    for index, field in enumerate(fields):
        if field.name in owned:
            descriptor, definition = extension.member(cls, index, field.name)
            setattr(cls, field.name, descriptor)
            definitions.append(definition)

    cls.__NATIVE_MEMBERS__ = tuple(definitions)
    extension.compile_plan(cls, tuple(_entry(field, is_state) for field in fields))


class ValidationHooks(Immutable):
    """What the extension asks of Python while it validates, by the names it reads them under."""

    missing: Any  # the MISSING marker
    check: Callable[[type[Any], Mapping[str, Any]], None]  # refuses what is not an attribute
    report: Callable[[str, Exception], Any]  # raises a failure with its position
    unexpected: Callable[[type[Any], Any], Any]  # refuses a key of a mapping
    mapping: type[Any]


class ConversionHooks(Immutable):
    """What the extension asks of Python to convert a value it has no spelling for."""

    map: Callable[[dict[Any, Any]], Any]
    map_types: tuple[type[Any], ...]
    basic_value: Callable[[Any, bool, tuple[str, ...]], Any]
    basic_key: Callable[[Any, bool, tuple[str, ...]], Any]
    recursive: Callable[[Any], Any]


class SpellingHooks(Immutable):
    """The types the extension spells itself."""

    uuid_type: type[Any]
    temporal_types: tuple[type[Any], ...]
    path_type: type[Any]


def configure(
    validation: ValidationHooks,
    conversion: ConversionHooks,
    spelling: SpellingHooks,
    /,
) -> None:
    """Hand the extension what it calls back."""
    _extension().configure(validation, conversion, spelling)
