import annotationlib
import inspect
import sys
from collections.abc import Callable, Mapping, Sequence
from enum import IntEnum
from threading import Lock
from types import (
    BuiltinFunctionType,
    ClassMethodDescriptorType,
    MethodDescriptorType,
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
type SignatureDescription = tuple[tuple[tuple[str | None, int, bool], ...], bool, str]


def _parameters(
    parameters: Sequence[inspect.Parameter],
    /,
) -> tuple[tuple[str | None, int, bool], ...]:
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


def accepts(
    value: Any,
    described: SignatureDescription,
    /,
) -> bool:
    """
    Tell whether a callable which is not a plain function can be called the way described.

    Parameters
    ----------
    value : Any
        A callable - a class, a builtin, a descriptor or an object called through a ``__call__``
        which is not a function.
    described : SignatureDescription
        The parameters the callable is called with, whether others may follow, and how the
        errors word it.

    Returns
    -------
    bool
        ``False`` only when its signature proves it can't be called that way - a callable
        without a signature to read is accepted.
    """
    found: tuple[_Answers, Any] | None = _answers(value)
    if found is None:
        return _accepts(value, described)

    answers, name = found
    with _ANSWERS_LOCK:
        accepted: bool | None = answers.get((name, described))

    if accepted is None:
        accepted = _accepts(value, described)
        with _ANSWERS_LOCK:
            answers[(name, described)] = accepted

    return accepted


# what callables were told to take, by what owns their signature - a class, the type of
# what a builtin is bound to, the type a descriptor is of, or the class of an object - as
# reading a signature takes microseconds. Held weakly by that owner, so nothing is kept alive
# by having been asked about
type _Answers = dict[tuple[Any, SignatureDescription], bool]
_ANSWERS: Final[WeakKeyDictionary[Any, _Answers]] = WeakKeyDictionary()
_ANSWERS_LOCK: Final[Lock] = Lock()
_DESCRIPTORS: Final[tuple[type[Any], ...]] = (
    MethodDescriptorType,
    WrapperDescriptorType,
    ClassMethodDescriptorType,
)


def _owner(value: Any, /) -> tuple[Any, Any] | None:
    # what owns the signature of a callable, and its name within that owner
    kind: Any = value.__class__
    if isinstance(value, type):
        return (value, None)

    if isinstance(value, BuiltinFunctionType | MethodWrapperType):
        bound: Any = value.__self__
        if bound is None:
            return None  # nothing to hold it by

        owner: Any = bound if isinstance(bound, ModuleType) else bound.__class__
        return (owner, ("bound", value.__name__))

    if isinstance(value, _DESCRIPTORS):
        objclass: Any = value.__objclass__
        return (objclass, ("descriptor", value.__name__))

    if isinstance(kind.__call__, _DESCRIPTORS):
        return (kind, ("called", None))  # called through a __call__ of C

    return None  # its own signature, which may differ for every one of its kind


def _answers(value: Any, /) -> tuple[_Answers, Any] | None:
    owned: tuple[Any, Any] | None = _owner(value)
    if owned is None:
        return None

    owner, name = owned
    try:
        with _ANSWERS_LOCK:
            return (_ANSWERS.setdefault(owner, {}), name)

    except TypeError:  # an owner which can't be referred to weakly
        return None


def _accepts(value: Any, described: SignatureDescription, /) -> bool:
    signature: inspect.Signature
    try:
        signature = inspect.signature(
            value,
            annotation_format=annotationlib.Format.FORWARDREF,
        )

    except TypeError, ValueError:
        return True  # what can't be told is not refused

    # compared the way the parameters of a function are - one rule for both
    return nstruct.signature_fits(
        _parameters(tuple(signature.parameters.values())),
        described[0],
        described[1],
    )


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
    accepts: Callable[[Any, Any], bool]  # whether a callable can be called the way described
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


def configure(
    validation: ValidationHooks,
    conversion: ConversionHooks,
    types: TypeHooks,
    /,
) -> None:
    """Hand the extension what it calls back."""
    nstruct.configure(validation, conversion, types)
