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
from typing import TYPE_CHECKING, Any, Final, Literal
from weakref import WeakKeyDictionary

from haiway._native import nstruct
from haiway.attributes.annotations import (
    AliasAttribute,
    AnyAttribute,
    AttributeAnnotation,
    BoolAttribute,
    BytesAttribute,
    CallableSignature,
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
    ProtocolSignature,
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
                tuple(annotation.values),
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
                tuple(describe(element) for element in annotation.values),
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


# the data of a function spec: the parameters to check, whether more arguments may follow,
# and the wording of the error
type SignatureDescription = tuple[SignatureParameters, bool, str]
# parameters as (name, kind, defaulted) - a positional only parameter has no name, as it
# is never passed by name
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
    match annotation.signature:
        case None:
            return None  # accepts any call - nothing to check

        case CallableSignature(arguments=arguments, extensible=extensible):
            return (
                ((None, int(inspect.Parameter.POSITIONAL_ONLY), False),) * arguments,
                extensible,
                f"with {arguments} positional argument{'' if arguments == 1 else 's'}",
            )

        case ProtocolSignature(protocol=protocol, parameters=parameters, extensible=extensible):
            return (
                _parameters(parameters),
                extensible,
                f"as {protocol.__name__}{inspect.Signature(parameters)}",
            )


def parameters(
    value: Any,
    /,
) -> SignatureParameters | None:
    # the extension hook reading callables which are not Python functions - classes,
    # builtins, descriptors, objects with a __call__ of C. Partials are already unwrapped
    # by the extension, so value is what they call in the end
    found: tuple[_Read, Any] | None = _read_of(value)
    if found is None:
        return _read(value)

    read, key = found
    with _READ_LOCK:
        if key in read:
            return read[key]

    known: SignatureParameters | None = _read(value)
    with _READ_LOCK:
        read[key] = known

    return known


# parameters read by inspect, cached per owner of the signature as reading one takes
# microseconds. An owner is the class, the module, or the class of the bound object which
# gives the callable, the class of a descriptor, what a wrapper wraps, or the class of an
# object with a __call__ of C. Keyed weakly, so caching keeps nothing alive. A callable is
# cached only when getting it from its owner gives it back - otherwise it is one of a kind
type _Read = dict[Any, SignatureParameters | None]
_READ: Final[WeakKeyDictionary[Any, _Read]] = WeakKeyDictionary()
_READ_LOCK: Final[Lock] = Lock()
_DESCRIPTORS: Final[tuple[type[Any], ...]] = (
    MethodDescriptorType,
    WrapperDescriptorType,
    ClassMethodDescriptorType,
)
_ABSENT: Final[object] = object()

# where a cached signature is found within its owner
type _ReadKey = (
    tuple[Literal["class", "called"], None]
    | tuple[Literal["bound", "descriptor"], str]
    | tuple[Literal["wrapped"], type[Any]]
)


def _owner(value: Any, /) -> tuple[Any, _ReadKey] | None:
    if isinstance(value, type):
        return (value, ("class", None))

    if isinstance(value, BuiltinFunctionType | MethodWrapperType):
        return _bound_owner(value)

    if isinstance(value, _DESCRIPTORS):
        return _descriptor_owner(value)

    return _object_owner(value)


def _bound_owner(value: BuiltinFunctionType | MethodWrapperType, /) -> tuple[Any, _ReadKey] | None:
    # a builtin function of a module, or a builtin method of a class or of an object
    bound: Any = value.__self__
    name: str = value.__name__
    owner: Any
    found: Any
    try:
        match bound:
            case None:
                return None  # nothing to cache it by

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
            return None  # not the one its owner gives

    except Exception:
        return None  # the attribute can't be got

    return (owner, ("bound", name))


def _held(cls: type[Any], name: str, /) -> Any:
    # the attribute along the MRO, without running a descriptor
    for base in cls.__mro__:
        attribute: Any = base.__dict__.get(name, _ABSENT)
        if attribute is not _ABSENT:
            return attribute

    return None


def _got(attribute: Any, instance: Any, owner: Any, /) -> Any:
    # the attribute as attribute access on the instance, or on the owner, returns it
    kind: Any = attribute.__class__
    getter: Any = getattr(kind, "__get__", None)
    return attribute if getter is None else getter(attribute, instance, owner)


def _descriptor_owner(
    value: MethodDescriptorType | WrapperDescriptorType | ClassMethodDescriptorType,
    /,
) -> tuple[Any, _ReadKey] | None:
    objclass: Any = value.__objclass__
    if _held(objclass, value.__name__) is not value:
        return None  # not the one its class holds

    return (objclass, ("descriptor", value.__name__))


def _object_owner(value: Any, /) -> tuple[Any, _ReadKey] | None:
    if isinstance(value, partial | MethodType) or getattr(value, "__signature__", None) is not None:
        return None  # a signature of its own, which may differ between objects of its class

    kind: type[Any] = value.__class__
    if getattr(value, "__wrapped__", None) is not None:
        try:  # a wrapper, like a cached function, is read as what it wraps
            wrapped: Any = inspect.unwrap(value, stop=_unwrapped)

        except ValueError:
            return None  # a cycle of wrappers

        if wrapped is value:
            return None  # inspect reads the wrapper itself

        return (wrapped, ("wrapped", kind))

    if (
        isinstance(kind.__call__, _DESCRIPTORS)
        and getattr(value, "__code__", None) is None
        and getattr(value, "__text_signature__", None) is None
    ):
        return (kind, ("called", None))  # a __call__ of C is the same for its whole class

    # a compiled function with its own code, or a __call__ which may differ between objects
    return None


def _unwrapped(value: Any, /) -> bool:
    # where inspect stops unwrapping, reading the signature from there
    return hasattr(value, "__signature__") or isinstance(value, MethodType)


def _read_of(value: Any, /) -> tuple[_Read, _ReadKey] | None:
    owned: tuple[Any, _ReadKey] | None = _owner(value)
    if owned is None:
        return None

    owner, key = owned
    try:
        with _READ_LOCK:
            return (_READ.setdefault(owner, {}), key)

    except TypeError:  # an owner which can't be weakly referenced
        return None


def _read(value: Any, /) -> SignatureParameters | None:
    signature: inspect.Signature
    try:
        signature = inspect.signature(
            value,
            annotation_format=annotationlib.Format.FORWARDREF,
        )

    except TypeError, ValueError:
        return None  # no signature to read - accepted without a check

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
    parameters: Callable[[Any], SignatureParameters | None]  # of what is not a Python function
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
    partial: type[Any]  # functools.partial - unwrapped to the function it calls
    placeholder: Any  # functools.Placeholder - a position a partial leaves to the call


def configure(
    validation: ValidationHooks,
    conversion: ConversionHooks,
    types: TypeHooks,
    /,
) -> None:
    """Hand the extension what it calls back."""
    nstruct.configure(validation, conversion, types)
