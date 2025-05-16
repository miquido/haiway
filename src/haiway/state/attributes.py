import types
import typing
from collections.abc import Callable, Mapping, MutableMapping, Sequence
from types import GenericAlias, NoneType, UnionType
from typing import (
    Any,
    ClassVar,
    ForwardRef,
    Generic,
    Literal,
    ParamSpec,
    Self,
    TypeAliasType,
    TypeVar,
    TypeVarTuple,
    _GenericAlias,  # pyright: ignore
    final,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
    overload,
)

from haiway import types as haiway_types
from haiway.types import MISSING, Missing

__all__ = (
    "AttributeAnnotation",
    "attribute_annotations",
    "resolve_attribute_annotation",
)


@final
class AttributeAnnotation:
    """
    Represents a type annotation for a State attribute with additional metadata.

    This class encapsulates information about a type annotation, including its
    origin type, type arguments, whether it's required, and any extra metadata.
    It's used internally by the State system to track and validate attribute types.
    """

    __slots__ = (
        "arguments",
        "extra",
        "origin",
        "required",
    )

    def __init__(
        self,
        *,
        origin: Any,
        arguments: Sequence[Any] | None = None,
        required: bool = True,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """
        Initialize a new attribute annotation.

        Parameters
        ----------
        origin : Any
            The base type of the annotation (e.g., str, int, List)
        arguments : Sequence[Any] | None
            Type arguments for generic types (e.g., T in List[T])
        required : bool
            Whether this attribute is required (cannot be omitted)
        extra : Mapping[str, Any] | None
            Additional metadata about the annotation
        """
        self.origin: Any = origin
        self.arguments: Sequence[Any]
        if arguments is None:
            self.arguments = ()

        else:
            self.arguments = arguments

        self.required: bool = required

        self.extra: Mapping[str, Any]
        if extra is None:
            self.extra = {}

        else:
            self.extra = extra

    def update_required(
        self,
        required: bool,
        /,
    ) -> Self:
        """
        Update the required flag for this annotation.

        The resulting required flag is the logical AND of the current
        flag and the provided value.

        Parameters
        ----------
        required : bool
            New required flag value to combine with the existing one

        Returns
        -------
        Self
            This annotation with the updated required flag
        """
        object.__setattr__(
            self,
            "required",
            self.required and required,
        )

        return self

    def __str__(self) -> str:
        """
        Convert this annotation to a string representation.

        Returns a readable string representation of the type, including
        its origin type and any type arguments.

        Returns
        -------
        str
            String representation of this annotation
        """
        if alias := self.extra.get("TYPE_ALIAS"):
            return alias

        origin_str: str = getattr(self.origin, "__name__", str(self.origin))
        arguments_str: str
        if self.arguments:
            arguments_str = "[" + ", ".join(str(arg) for arg in self.arguments) + "]"

        else:
            arguments_str = ""

        if module := getattr(self.origin, "__module__", None):
            return f"{module}.{origin_str}{arguments_str}"

        else:
            return f"{origin_str}{arguments_str}"


def attribute_annotations(
    cls: type[Any],
    /,
    type_parameters: Mapping[str, Any],
) -> Mapping[str, AttributeAnnotation]:
    """
    Extract and process type annotations from a class.

    This function analyzes a class's type hints and converts them to AttributeAnnotation
    objects, which provide rich type information used by the State system for validation
    and other type-related operations.

    Parameters
    ----------
    cls : type[Any]
        The class to extract annotations from
    type_parameters : Mapping[str, Any]
        Type parameters to substitute in generic type annotations

    Returns
    -------
    Mapping[str, AttributeAnnotation]
        A mapping of attribute names to their processed type annotations

    Notes
    -----
    Private attributes (prefixed with underscore) and ClassVars are ignored.
    """
    self_annotation = AttributeAnnotation(
        origin=cls,
        # ignore arguments here, State (and draive.DataModel) will have them resolved at this stage
        arguments=[],
    )

    # ignore args_keys here, State (and draive.DataModel) will have them resolved at this stage
    recursion_guard: MutableMapping[str, AttributeAnnotation] = {
        _recursion_key(cls, default=str(self_annotation)): self_annotation
    }

    attributes: dict[str, AttributeAnnotation] = {}
    for key, annotation in get_type_hints(cls, localns={cls.__name__: cls}).items():
        # do not include private or special items
        if key.startswith("_"):
            continue

        # do not include ClassVars
        if (get_origin(annotation) or annotation) is ClassVar:
            continue

        attributes[key] = resolve_attribute_annotation(
            annotation,
            type_parameters=type_parameters,
            module=cls.__module__,
            self_annotation=self_annotation,
            recursion_guard=recursion_guard,
        )

    return attributes


def _resolve_none(
    annotation: Any,
) -> AttributeAnnotation:
    return AttributeAnnotation(origin=NoneType)


def _resolve_missing(
    annotation: Any,
) -> AttributeAnnotation:
    # special case - attributes marked as missing are not required
    # Missing does not work properly within TypedDict though
    return AttributeAnnotation(
        origin=Missing,
        required=False,
    )


def _resolve_literal(
    annotation: Any,
) -> AttributeAnnotation:
    return AttributeAnnotation(
        origin=Literal,
        arguments=get_args(annotation),
    )


def _resolve_forward_ref(
    annotation: ForwardRef | str,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    forward_ref: ForwardRef
    match annotation:
        case str() as string:
            forward_ref = ForwardRef(string, module=module)

        case reference:
            forward_ref = reference

    if evaluated := forward_ref._evaluate(
        globalns=None,
        localns=None,
        recursive_guard=frozenset(),
    ):
        return resolve_attribute_annotation(
            evaluated,
            type_parameters=type_parameters,
            module=module,
            self_annotation=self_annotation,
            recursion_guard=recursion_guard,
        )

    else:
        raise RuntimeError(f"Cannot resolve annotation of {annotation}")


def _resolve_generic_alias(  # noqa: PLR0911, PLR0912
    annotation: GenericAlias,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    match get_origin(annotation):
        case TypeAliasType() as alias:  # pyright: ignore[reportUnnecessaryComparison]
            return _resolve_type_alias(
                alias,
                type_parameters={
                    # verify if we should pass all parameters
                    param.__name__: get_args(annotation)[idx]
                    for idx, param in enumerate(alias.__type_params__)
                },
                module=module,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )

        case origin if issubclass(origin, Generic):
            match origin.__class_getitem__(  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                tuple(
                    type_parameters.get(
                        arg.__name__,
                        arg.__bound__ or Any,
                    )
                    if isinstance(arg, TypeVar)
                    else arg
                    for arg in get_args(annotation)
                )
            ):
                case GenericAlias() as generic_alias:
                    resolved_attribute = AttributeAnnotation(origin=generic_alias.__origin__)
                    if recursion_key := _recursion_key(generic_alias):
                        if recursive := recursion_guard.get(recursion_key):
                            return recursive

                        else:
                            recursion_guard[recursion_key] = resolved_attribute

                    resolved_attribute.arguments = [
                        resolve_attribute_annotation(
                            argument,
                            type_parameters=type_parameters,
                            module=module,
                            self_annotation=self_annotation,
                            recursion_guard=recursion_guard,
                        )
                        for argument in get_args(generic_alias)
                    ]

                    return resolved_attribute

                # use resolved type if it is not an alias again
                case resolved:  # pyright: ignore
                    resolved_attribute = AttributeAnnotation(origin=resolved)

                    if recursion_key := _recursion_key(origin):
                        if recursive := recursion_guard.get(recursion_key):
                            return recursive

                        else:
                            recursion_guard[recursion_key] = resolved_attribute

                    resolved_attribute.arguments = [
                        resolve_attribute_annotation(
                            argument,
                            type_parameters=type_parameters,
                            module=module,
                            self_annotation=self_annotation,
                            recursion_guard=recursion_guard,
                        )
                        for argument in get_args(annotation)
                    ]

                    return resolved_attribute

        case origin:
            resolved_attribute = AttributeAnnotation(origin=origin)

            if recursion_key := _recursion_key(origin):
                if recursive := recursion_guard.get(recursion_key):
                    return recursive

            resolved_attribute.arguments = [
                resolve_attribute_annotation(
                    argument,
                    type_parameters=type_parameters,
                    module=module,
                    self_annotation=self_annotation,
                    recursion_guard=recursion_guard,
                )
                for argument in get_args(annotation)
            ]

            return resolved_attribute


def _resolve_special_generic_alias(
    annotation: Any,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    origin: type[Any] = get_origin(annotation)
    resolved_attribute = AttributeAnnotation(origin=origin)

    if recursion_key := _recursion_key(origin):
        if recursive := recursion_guard.get(recursion_key):
            return recursive

        else:
            recursion_guard[recursion_key] = resolved_attribute

    resolved_attribute.arguments = [
        resolve_attribute_annotation(
            argument,
            type_parameters=type_parameters,
            module=module,
            self_annotation=self_annotation,
            recursion_guard=recursion_guard,
        )
        for argument in get_args(annotation)
    ]

    return resolved_attribute


def _resolve_type_alias(
    annotation: TypeAliasType,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    resolved_attribute = AttributeAnnotation(origin=MISSING)

    if recursion_key := _recursion_key(annotation):
        if recursive := recursion_guard.get(recursion_key):
            return recursive

        else:
            recursion_guard[recursion_key] = resolved_attribute

    resolved: AttributeAnnotation = resolve_attribute_annotation(
        annotation.__value__,
        module=annotation.__module__ or module,
        type_parameters=type_parameters,
        self_annotation=self_annotation,
        recursion_guard=recursion_guard,
    )

    resolved_attribute.origin = resolved.origin
    resolved_attribute.arguments = resolved.arguments
    resolved_attribute.extra = {
        **resolved.extra,
        "TYPE_ALIAS": annotation.__name__,
    }
    resolved_attribute.required = resolved.required

    return resolved_attribute


def _resolve_type_var(
    annotation: TypeVar,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    return resolve_attribute_annotation(
        type_parameters.get(
            annotation.__name__,
            # use bound as default or Any otherwise
            annotation.__bound__ or Any,
        ),
        module=module,
        type_parameters=type_parameters,
        self_annotation=self_annotation,
        recursion_guard=recursion_guard,
    )


def _resolve_type_union(
    annotation: UnionType,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    arguments: Sequence[AttributeAnnotation] = [
        resolve_attribute_annotation(
            argument,
            type_parameters=type_parameters,
            module=module,
            self_annotation=self_annotation,
            recursion_guard=recursion_guard,
        )
        for argument in get_args(annotation)
    ]
    return AttributeAnnotation(
        origin=UnionType,  # pyright: ignore[reportArgumentType]
        arguments=arguments,
        required=all(argument.required for argument in arguments),
    )


def _resolve_callable(
    annotation: Any,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    return AttributeAnnotation(
        origin=Callable,
        arguments=[
            resolve_attribute_annotation(
                argument,
                type_parameters=type_parameters,
                module=module,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )
            for argument in get_args(annotation)
        ],
    )


def _resolve_type_box(
    annotation: Any,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    return resolve_attribute_annotation(
        get_args(annotation)[0],
        type_parameters=type_parameters,
        module=module,
        self_annotation=self_annotation,
        recursion_guard=recursion_guard,
    )


def _resolve_type_not_required(
    annotation: Any,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    return resolve_attribute_annotation(
        get_args(annotation)[0],
        type_parameters=type_parameters,
        module=module,
        self_annotation=self_annotation,
        recursion_guard=recursion_guard,
    ).update_required(False)


def _resolve_type_optional(
    annotation: Any,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    return AttributeAnnotation(
        origin=UnionType,  # pyright: ignore[reportArgumentType]
        arguments=[
            resolve_attribute_annotation(
                get_args(annotation)[0],
                type_parameters=type_parameters,
                module=module,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            ),
            AttributeAnnotation(origin=NoneType),
        ],
    )


def _resolve_type_typeddict(
    annotation: Any,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    resolved_attribute = AttributeAnnotation(origin=annotation)

    if recursion_key := _recursion_key(annotation):
        if recursive := recursion_guard.get(recursion_key):
            return recursive

        else:
            recursion_guard[recursion_key] = resolved_attribute

    resolved_attribute.arguments = [
        resolve_attribute_annotation(
            argument,
            type_parameters=type_parameters,
            module=module,
            self_annotation=self_annotation,
            recursion_guard=recursion_guard,
        )
        for argument in get_args(annotation)
    ]

    attributes: dict[str, AttributeAnnotation] = {}
    for key, element in get_type_hints(
        annotation,
        localns={annotation.__name__: annotation},
    ).items():
        attributes[key] = resolve_attribute_annotation(
            element,
            type_parameters=type_parameters,
            module=getattr(annotation, "__module__", module),
            self_annotation=resolved_attribute,
            recursion_guard=recursion_guard,
        ).update_required(key in annotation.__required_keys__)
    resolved_attribute.extra = {
        "attributes": attributes,
        "required": annotation.__required_keys__,
    }
    return resolved_attribute


def _resolve_type(
    annotation: Any,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    if recursion_key := _recursion_key(annotation):
        if recursive := recursion_guard.get(recursion_key):
            return recursive

        # not updating recursion guard here - it might be a builtin type

    return AttributeAnnotation(
        origin=annotation,
        arguments=[
            resolve_attribute_annotation(
                argument,
                type_parameters=type_parameters,
                module=module,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )
            for argument in get_args(annotation)
        ],
    )


def resolve_attribute_annotation(  # noqa: C901, PLR0911, PLR0912
    annotation: Any,
    /,
    module: str,
    type_parameters: Mapping[str, Any],
    self_annotation: AttributeAnnotation | None,
    recursion_guard: MutableMapping[str, AttributeAnnotation],
) -> AttributeAnnotation:
    """
    Resolve a Python type annotation into an AttributeAnnotation object.

    This function analyzes any Python type annotation and converts it into
    an AttributeAnnotation that captures its structure, including handling
    for special types like unions, optionals, literals, generics, etc.

    Parameters
    ----------
    annotation : Any
        The type annotation to resolve
    module : str
        The module where the annotation is defined (for resolving ForwardRefs)
    type_parameters : Mapping[str, Any]
        Type parameters to substitute in generic type annotations
    self_annotation : AttributeAnnotation | None
        The annotation for Self references, if available
    recursion_guard : MutableMapping[str, AttributeAnnotation]
        Cache to prevent infinite recursion for recursive types

    Returns
    -------
    AttributeAnnotation
        A resolved AttributeAnnotation representing the input annotation

    Raises
    ------
    RuntimeError
        If a Self annotation is used but self_annotation is not provided
    TypeError
        If the annotation is of an unsupported type
    """
    match get_origin(annotation) or annotation:
        case types.NoneType | None:
            return _resolve_none(
                annotation=annotation,
            )

        case haiway_types.Missing:
            return _resolve_missing(
                annotation=annotation,
            )

        case types.UnionType | typing.Union:
            return _resolve_type_union(
                annotation,
                module=module,
                type_parameters=type_parameters,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )

        case typing.Literal:
            return _resolve_literal(annotation)

        case typeddict if is_typeddict(typeddict):
            return _resolve_type_typeddict(
                typeddict,
                module=module,
                type_parameters=type_parameters,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )

        case typing.Callable:  # pyright: ignore
            return _resolve_callable(
                annotation,
                module=module,
                type_parameters=type_parameters,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )

        case typing.Annotated | typing.Final | typing.Required:
            return _resolve_type_box(
                annotation,
                module=module,
                type_parameters=type_parameters,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )

        case typing.NotRequired:
            return _resolve_type_not_required(
                annotation,
                module=module,
                type_parameters=type_parameters,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )

        case typing.Optional:  # optional is a Union[Value, None]
            return _resolve_type_optional(
                annotation,
                module=module,
                type_parameters=type_parameters,
                self_annotation=self_annotation,
                recursion_guard=recursion_guard,
            )

        case typing.Self:  # pyright: ignore
            if self_annotation:
                return self_annotation

            else:
                raise RuntimeError(f"Unresolved Self annotation: {annotation}")

        case _:
            match annotation:
                case str() | ForwardRef():
                    return _resolve_forward_ref(
                        annotation,
                        module=module,
                        type_parameters=type_parameters,
                        self_annotation=self_annotation,
                        recursion_guard=recursion_guard,
                    )

                case GenericAlias():
                    return _resolve_generic_alias(
                        annotation,
                        module=module,
                        type_parameters=type_parameters,
                        self_annotation=self_annotation,
                        recursion_guard=recursion_guard,
                    )

                case _GenericAlias():
                    return _resolve_special_generic_alias(
                        annotation,
                        module=module,
                        type_parameters=type_parameters,
                        self_annotation=self_annotation,
                        recursion_guard=recursion_guard,
                    )

                case TypeAliasType():
                    return _resolve_type_alias(
                        annotation,
                        module=module,
                        type_parameters=type_parameters,
                        self_annotation=self_annotation,
                        recursion_guard=recursion_guard,
                    )

                case TypeVar():
                    return _resolve_type_var(
                        annotation,
                        module=module,
                        type_parameters=type_parameters,
                        self_annotation=self_annotation,
                        recursion_guard=recursion_guard,
                    )

                case ParamSpec():
                    raise NotImplementedError(f"Unresolved ParamSpec annotation: {annotation}")

                case TypeVarTuple():
                    raise NotImplementedError(f"Unresolved TypeVarTuple annotation: {annotation}")

                case _:  # finally use whatever there was
                    return _resolve_type(
                        annotation,
                        module=module,
                        type_parameters=type_parameters,
                        self_annotation=self_annotation,
                        recursion_guard=recursion_guard,
                    )


@overload
def _recursion_key(
    annotation: Any,
    /,
) -> str | None: ...


@overload
def _recursion_key(
    annotation: Any,
    /,
    default: str,
) -> str: ...


def _recursion_key(
    annotation: Any,
    /,
    default: str | None = None,
) -> str | None:
    args_suffix: str
    if arguments := get_args(annotation):
        arguments_string: str = ", ".join(
            _recursion_key(
                argument,
                default="?",
            )
            for argument in arguments
        )
        args_suffix = f"[{arguments_string}]"

    else:
        args_suffix = ""

    if qualname := getattr(annotation, "__qualname__", None):
        return qualname + args_suffix

    module_prefix: str
    if module := getattr(annotation, "__module__", None):
        module_prefix = module + "."

    else:
        module_prefix = ""

    if name := getattr(annotation, "__name__", None):
        return module_prefix + name + args_suffix

    return default
