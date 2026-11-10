import annotationlib
import inspect
import sys
from collections.abc import Callable, Mapping, Sequence
from enum import IntEnum
from functools import partial
from threading import Lock
from types import (
    BuiltinFunctionType,
    ClassMethodDescriptorType,
    MethodDescriptorType,
    MethodType,
    MethodWrapperType,
    ModuleType,
    WrapperDescriptorType,
)
from typing import TYPE_CHECKING, Any, Final, get_origin
from weakref import WeakKeyDictionary

from haiway._native import nstruct
from haiway.attributes.annotations import (
    AliasAttribute,
    AnyAttribute,
    AttributeAnnotation,
    BoolAttribute,
    BytesAttribute,
    CustomAttribute,
    DateAttribute,
    DatetimeAttribute,
    FloatAttribute,
    FunctionAttribute,
    IntegerAttribute,
    IntEnumAttribute,
    LiteralAttribute,
    MappingAttribute,
    MetaAttribute,
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
    TypedDictAttribute,
    UnionAttribute,
    UUIDAttribute,
    ValidableAttribute,
    _no_verify,  # pyright: ignore[reportPrivateUsage]
    is_callback_protocol,
)
from haiway.attributes.attribute import Attribute
from haiway.types.immutable import Immutable

__all__ = (
    "ConversionHooks",
    "TypeHooks",
    "ValidationHooks",
    "compile",
    "configure",
    "describe",
    "enum_values",
    "install",
    "literal_values",
    "validate",
)

# What the native extension is told about an annotation and about a class, and everything
# Python hands it to call back. This is the one place which knows how the two are spelled
# to each other - the numbers of a kind come from the extension itself, nothing here
# repeats them.


def _constants(prefix: str) -> Mapping[str, int]:
    return {
        name.removeprefix(prefix): getattr(nstruct, name)
        for name in dir(nstruct)
        if name.startswith(prefix)
    }


if TYPE_CHECKING:
    # what a type checker sees - the values at runtime are the ones of the extension

    class Kind(IntEnum):
        ANY = 0
        NONE = 1
        MISSING = 2
        LITERAL = 3
        BOOL = 4
        INT = 5
        FLOAT = 6
        STR = 7
        BYTES = 8
        UUID = 9
        DATETIME = 10
        DATE = 11
        TIME = 12
        TIMEDELTA = 13
        PATH = 14
        STR_ENUM = 15
        INT_ENUM = 16
        TUPLE = 17
        SEQUENCE = 18
        SET = 19
        MAPPING = 20
        META = 21
        TYPED_DICT = 22
        STATE = 23
        CUSTOM_STATE = 24
        CUSTOM = 25
        FUNCTION = 26
        UNION = 27
        VALIDABLE = 28
        ALIAS = 29

else:
    Kind = IntEnum("Kind", dict(_constants("SPEC_")))


# The description of an annotation the extension compiles into its spec:
# (kind, annotation, base, validating, verifying, data, children)
type Description = tuple[int, Any, Any, Any, Any, Any, tuple[Any, ...]]

# annotations which are told by their class alone
_LEAVES: Final[Mapping[type[Any], Kind]] = {
    AnyAttribute: Kind.ANY,
    NoneAttribute: Kind.NONE,
    MissingAttribute: Kind.MISSING,
    BoolAttribute: Kind.BOOL,
    IntegerAttribute: Kind.INT,
    FloatAttribute: Kind.FLOAT,
    StringAttribute: Kind.STR,
    BytesAttribute: Kind.BYTES,
    UUIDAttribute: Kind.UUID,
    DatetimeAttribute: Kind.DATETIME,
    DateAttribute: Kind.DATE,
    TimeAttribute: Kind.TIME,
    TimedeltaAttribute: Kind.TIMEDELTA,
    PathAttribute: Kind.PATH,
    MetaAttribute: Kind.META,
}


def _verifying(annotation: Any) -> Any:
    verifying: Any = getattr(annotation, "verifying", _no_verify)
    return None if verifying is _no_verify else verifying


def _is_plain_state(cls: Any, /) -> bool:
    # a State which validates the usual way - one with a validation of its own is called,
    # not skipped over, so the native code must not decide for it
    if not isinstance(cls, nstruct.StructMeta):
        return False

    validate: Any = inspect.getattr_static(cls, "validate", None)
    return validate is nstruct.Validated.__dict__["validate"]


def describe(
    annotation: AttributeAnnotation,
    /,
) -> Description:
    """
    The description of an annotation the native code compiles its spec from.

    An annotation referring back to itself - a recursive alias, a TypedDict holding
    one of its own - is described as far as the reference, which is left for the
    native code to have described when it is first used.

    Parameters
    ----------
    annotation : AttributeAnnotation
        A resolved annotation of an attribute.

    Returns
    -------
    Description
        ``(kind, annotation, base, validating, verifying, data, children)``.

    Raises
    ------
    TypeError
        For an annotation of a class the native code has no kind of.
    """
    return _describe(annotation, active=set())


def _deferred(annotation: Any) -> Description:
    return (Kind.ALIAS, annotation, None, None, None, None, ())


def _describe(
    annotation: AttributeAnnotation,
    /,
    active: set[int],
) -> Description:
    kind: Kind | None = _LEAVES.get(type(annotation))
    if kind is not None:
        return (kind, annotation, None, None, _verifying(annotation), None, ())

    if id(annotation) in active:
        return _deferred(annotation)  # described already, further out

    active.add(id(annotation))
    try:
        return _describe_compound(annotation, active)

    finally:
        active.discard(id(annotation))


def _describe_compound(  # noqa: C901, PLR0911, PLR0912
    annotation: AttributeAnnotation,
    /,
    active: set[int],
) -> Description:
    def describe(inner: Any) -> Description:
        return _describe(inner, active=active)

    match annotation:
        case FunctionAttribute():
            return (
                Kind.FUNCTION,
                annotation,
                None,
                None,
                _verifying(annotation),
                _signature_description(annotation),
                (),
            )

        case LiteralAttribute():
            return (
                Kind.LITERAL,
                annotation,
                None,
                None,
                _verifying(annotation),
                tuple(annotation.values),  # pyright: ignore[reportUnknownArgumentType, reportUnknownMemberType]
                (),
            )

        case StrEnumAttribute():
            return (
                Kind.STR_ENUM,
                annotation,
                annotation.base,
                None,
                _verifying(annotation),
                None,
                (),
            )

        case IntEnumAttribute():
            return (
                Kind.INT_ENUM,
                annotation,
                annotation.base,
                None,
                _verifying(annotation),
                None,
                (),
            )

        case TupleAttribute():
            return (
                Kind.TUPLE,
                annotation,
                None,
                None,
                _verifying(annotation),
                None,
                tuple(describe(element) for element in annotation.values),  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            )

        case SequenceAttribute():
            return (
                Kind.SEQUENCE,
                annotation,
                None,
                None,
                _verifying(annotation),
                None,
                (describe(annotation.values),),
            )

        case SetAttribute():
            return (
                Kind.SET,
                annotation,
                None,
                None,
                _verifying(annotation),
                None,
                (describe(annotation.values),),
            )

        case MappingAttribute():
            return (
                Kind.MAPPING,
                annotation,
                None,
                None,
                _verifying(annotation),
                None,
                (describe(annotation.keys), describe(annotation.values)),
            )

        case TypedDictAttribute():
            return (
                Kind.TYPED_DICT,
                annotation,
                annotation.base,
                None,
                _verifying(annotation),
                tuple(
                    (key, attribute.required) for key, attribute in annotation.attributes.items()
                ),
                tuple(describe(attribute) for attribute in annotation.attributes.values()),
            )

        case ObjectAttribute():
            base: Any = annotation.base
            return (
                Kind.STATE if _is_plain_state(base) else Kind.CUSTOM_STATE,
                annotation,
                base,
                None,
                _verifying(annotation),
                None,
                (),
            )

        case CustomAttribute():
            return (
                Kind.CUSTOM,
                annotation,
                annotation.base,
                None,
                _verifying(annotation),
                None,
                (),
            )

        case UnionAttribute():
            return (
                Kind.UNION,
                annotation,
                None,
                None,
                _verifying(annotation),
                None,
                tuple(describe(alternative) for alternative in annotation.alternatives),
            )

        case ValidableAttribute():
            return (
                Kind.VALIDABLE,
                annotation,
                None,
                annotation.validating,
                None,
                None,
                (describe(annotation.attribute),),
            )

        case AliasAttribute():
            # what it stands for, when that is known already - left for later otherwise
            if annotation.is_resolved:
                return describe(annotation.resolved)

            return _deferred(annotation)

        case _:
            raise TypeError(f"{type(annotation).__name__} has no native description")


def compile(  # noqa: A001
    annotation: AttributeAnnotation,
    /,
) -> Any:
    """
    The compiled spec of an annotation.

    Parameters
    ----------
    annotation : AttributeAnnotation
        A resolved annotation of an attribute.

    Returns
    -------
    Any
        What ``validate`` of the extension reads.
    """
    return nstruct.compile_spec(describe(annotation))


def validate(
    spec: Any,
    value: Any,
    /,
) -> Any:
    """
    The value as the compiled spec accepts it.

    Parameters
    ----------
    spec : Any
        A spec made by ``compile``.
    value : Any
        What to validate.

    Returns
    -------
    Any
        The validated value, converted where the annotation converts.
    """
    return nstruct.validate(spec, value)


# how a function is called - the parameters it is told, whether others may follow, and how
# the errors word it - the data of the spec of a function
type SignatureDescription = tuple[SignatureParameters, bool, str]
# the parameters of a callable, each (name, kind, defaulted) - the name of a positional only
# one left out, as it is never given by name
type SignatureParameters = tuple[tuple[str | None, int, bool], ...]


def _parameters(
    parameters: Sequence[inspect.Parameter],
    /,
) -> SignatureParameters:
    return tuple(
        (
            None if parameter.kind is parameter.POSITIONAL_ONLY else parameter.name,
            int(parameter.kind),
            parameter.default is not parameter.empty,
        )
        for parameter in parameters
    )


def _signature_description(annotation: FunctionAttribute, /) -> SignatureDescription | None:
    parameters: Sequence[inspect.Parameter] = annotation.parameters
    extensible: bool = annotation.extensible
    if extensible and not parameters:
        return None  # called any way at all - nothing to check

    origin: Any = get_origin(annotation.base) or annotation.base
    description: str
    if is_callback_protocol(origin):
        description = f"as {origin.__name__}{inspect.Signature(parameters)}"

    else:
        description = (
            f"with {len(parameters)} positional argument{'' if len(parameters) == 1 else 's'}"
        )

    return (_parameters(parameters), extensible, description)


def parameters(
    value: Any,
    /,
) -> SignatureParameters | None:
    """
    Read the parameters of a callable which is not a plain function.

    The extension reads functions from their code, and leaves the rest - classes, builtins,
    descriptors, objects called through a ``__call__`` which is not a function - to the
    signature Python reads. What a partial gives is left by the extension in either case,
    so ``value`` is what such a partial calls in the end.

    Parameters
    ----------
    value : Any
        A callable which is not a function written in Python.

    Returns
    -------
    SignatureParameters | None
        Its parameters as ``(name, kind, defaulted)``, the name of a positional only one
        left out - or ``None`` when it has no signature to read, which is never refused.
    """
    found: tuple[_Read, Any] | None = _read_of(value)
    if found is None:
        return _read(value)

    read, name = found
    with _READ_LOCK:
        if name in read:
            return read[name]

    known: SignatureParameters | None = _read(value)
    with _READ_LOCK:
        read[name] = known

    return known


# what was read of callables, by what owns their signature - a class, a module, a class or an
# object a builtin is bound to, the class a descriptor is of, what a wrapper wraps, the class of
# an object called through a __call__ of C - as reading a signature takes microseconds. Held
# weakly by that owner, so nothing is kept alive by having been read. A callable is held by an
# owner only when the owner tells its signature - when it gives that very callable
type _Read = dict[Any, SignatureParameters | None]
_READ: Final[WeakKeyDictionary[Any, _Read]] = WeakKeyDictionary()
_READ_LOCK: Final[Lock] = Lock()
_DESCRIPTORS: Final[tuple[type[Any], ...]] = (
    MethodDescriptorType,
    WrapperDescriptorType,
    ClassMethodDescriptorType,
)


def _owner(value: Any, /) -> tuple[Any, Any] | None:
    # what owns the signature of a callable, and its name within that owner
    if isinstance(value, type):
        return (value, None)

    if isinstance(value, BuiltinFunctionType | MethodWrapperType):
        return _bound_owner(value)

    if isinstance(value, _DESCRIPTORS):
        return _descriptor_owner(value)

    return _object_owner(value)


def _bound_owner(value: BuiltinFunctionType | MethodWrapperType, /) -> tuple[Any, Any] | None:
    # a builtin is told by the attribute it was got of - a function of a module, a method
    # of a class, or of the class of an object - only when that attribute gives it again
    bound: Any = value.__self__
    name: str = value.__name__
    owner: Any
    found: Any
    try:
        match bound:
            case None:
                return None  # nothing to hold it by

            case ModuleType():
                owner = bound
                found = bound.__dict__.get(name)

            case type():
                owner = bound
                found = _got(_held(bound, name), None, bound)

            case _:
                owner = bound.__class__
                found = _got(_held(owner, name), bound, owner)

        if type(found) is not type(value) or found != value:
            return None  # not what the owner gives - one of its own

    except Exception:
        return None  # an attribute which can't be got - nothing to tell it by

    return (owner, ("bound", name))


def _held(cls: type[Any], name: str, /) -> Any:
    # what a class holds by the name, along the classes it is made of - without running what
    # getting it would run
    for base in cls.__mro__:
        attribute: Any = base.__dict__.get(name, _ABSENT)
        if attribute is not _ABSENT:
            return attribute

    return None


_ABSENT: Final[object] = object()


def _got(attribute: Any, instance: Any, owner: Any, /) -> Any:
    # an attribute as getting it of an instance, or of the owner itself, gives it
    kind: Any = attribute.__class__
    getter: Any = getattr(kind, "__get__", None)
    return attribute if getter is None else getter(attribute, instance, owner)


def _descriptor_owner(
    value: MethodDescriptorType | WrapperDescriptorType | ClassMethodDescriptorType,
    /,
) -> tuple[Any, Any] | None:
    objclass: Any = value.__objclass__
    if _held(objclass, value.__name__) is not value:
        return None  # not what its class holds - one of its own

    return (objclass, ("descriptor", value.__name__))


def _object_owner(value: Any, /) -> tuple[Any, Any] | None:
    # read of the object itself rather than of its kind - told apart by what it is
    if isinstance(value, partial) or getattr(value, "__signature__", None) is not None:
        return None  # a signature of its own, which may differ for every one of its kind

    kind: Any = value.__class__
    if getattr(value, "__wrapped__", None) is not None:
        try:  # a wrapper - a cached function, a static method - is read as what it wraps
            wrapped: Any = inspect.unwrap(value, stop=_unwrapped)

        except ValueError:
            return None  # wrapping itself, without an end

        if wrapped is value:
            return None  # read of itself after all

        return (wrapped, ("wrapped", kind))

    if (
        isinstance(kind.__call__, _DESCRIPTORS)
        and getattr(value, "__code__", None) is None
        and getattr(value, "__text_signature__", None) is None
    ):
        return (kind, ("called", None))  # called through a __call__ of C, the same for its kind

    # its own signature - a compiled function telling its own code, or a __call__ which may
    # differ for every one of its kind
    return None


def _unwrapped(value: Any, /) -> bool:
    # where inspect stops unwrapping - its signature is read from there
    return hasattr(value, "__signature__") or isinstance(value, MethodType)


def _read_of(value: Any, /) -> tuple[_Read, Any] | None:
    owned: tuple[Any, Any] | None = _owner(value)
    if owned is None:
        return None

    owner, name = owned
    try:
        with _READ_LOCK:
            return (_READ.setdefault(owner, {}), name)

    except TypeError:  # an owner which can't be referred to weakly
        return None


def _read(value: Any, /) -> SignatureParameters | None:
    signature: inspect.Signature
    try:
        signature = inspect.signature(
            value,
            annotation_format=annotationlib.Format.FORWARDREF,
        )

    except TypeError, ValueError:
        return None  # what can't be told is not refused

    return _parameters(tuple(signature.parameters.values()))


def enum_values(
    base: Any,
    names: bool,
    /,
) -> str:
    """The members of an enum spelled for an error - their values, or their names."""
    return ", ".join(member.name if names else str(member.value) for member in base)


def literal_values(
    values: Sequence[Any],
    /,
) -> str:
    """The values of a literal spelled for an error."""
    return ", ".join(repr(literal) for literal in values)


def _entry(field: Attribute) -> tuple[Any, ...]:
    # the entry is what `compile_plan` of the extension reads, field by field. the names
    # are interned, as the names of the keyword arguments of a call are - which is what
    # lets the native code tell one from another by identity
    return (
        sys.intern(field.name),
        sys.intern(field.alias) if field.alias is not None else None,
        field.spec,
        field._validated_default,  # pyright: ignore[reportPrivateUsage]
        field.default.constant,
    )


def install(
    cls: Any,
    /,
    fields: Sequence[Attribute],
    owned: Sequence[str],
) -> None:
    """
    Give a State class its native storage and its plan.

    Parameters
    ----------
    cls : Any
        Class which was just created.
    fields : Sequence[Attribute]
        Every attribute of the class, in order, each with its compiled spec.
    owned : Sequence[str]
        The attributes the class declares itself - a specialization declares none and
        reads the ones of its origin through the descriptors it inherits.
    """
    # the names this class stores - kept for what reads them, the values themselves
    # live inline in the instance, not in slots
    cls.__slots__ = tuple(owned)
    # the plan is kept on the class, with a descriptor for each attribute it declares
    nstruct.compile_plan(
        cls,
        tuple(_entry(field) for field in fields),
        tuple(sys.intern(name) for name in owned),
        True,  # a State validates - failures are reported at their position
    )


class ValidationHooks(Immutable):
    """What the extension asks of Python while it validates, by the names it reads them under."""

    missing: Any  # the Missing marker
    check: Callable[[type[Any], Mapping[str, Any]], None]  # refuses what is not an attribute
    report: Callable[[str, Exception], Any]  # raises a failure with its position
    unexpected: Callable[[type[Any], Any], Any]  # refuses a key of a mapping
    describe: Callable[[Any], Description]  # the description of an annotation resolved late
    parameters: Callable[[Any], SignatureParameters | None]  # read of what is not a function
    enum_values: Callable[[Any, bool], str]
    literal_values: Callable[[Sequence[Any]], str]
    b64decode: Callable[[str], bytes]
    parse_duration: Callable[[str], Any]
    exception_group: type[Any]


class ConversionHooks(Immutable):
    """What the extension asks of Python to convert a value it has no spelling for."""

    map: type[Any]
    basic_value: Callable[[Any, bool, tuple[str, ...]], Any]
    basic_key: Callable[[Any, bool, tuple[str, ...]], Any]
    recursive: Callable[[Any], Any]


class TypeHooks(Immutable):
    """The types the extension tells a value by, and converts a spelling into."""

    mapping: type[Any]
    sequence: type[Any]
    set: type[Any]
    iterable: type[Any]
    uuid: type[Any]
    safe_uuid: type[Any]  # what a UUID holds besides its number
    datetime: type[Any]
    date: type[Any]
    time: type[Any]
    timedelta: type[Any]
    path: type[Any]
    pathlike: type[Any]
    meta: type[Any]
    partial: type[Any]  # functools.partial - what is unwrapped to the function it calls
    placeholder: Any  # functools.Placeholder - a position of a partial given by the call


def configure(
    validation: ValidationHooks,
    conversion: ConversionHooks,
    types: TypeHooks,
    /,
) -> None:
    """Hand the extension what it calls back."""
    nstruct.configure(validation, conversion, types)
