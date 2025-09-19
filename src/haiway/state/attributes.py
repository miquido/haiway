import types
import typing
from collections.abc import Callable, Mapping, MutableMapping, Sequence, Set
from types import GenericAlias, NoneType, UnionType
from typing import (
    Any,
    ClassVar,
    Final,
    ForwardRef,
    Generic,
    Literal,
    Self,
    TypeVar,
    cast,
    final,
    get_args,
    get_origin,
    get_type_hints,
    is_typeddict,
)
from typing import is_typeddict as is_typeddict_ext

import typing_extensions

from haiway.types import Missing

__all__ = (
    "Attribute",
    "resolve_attribute",
    "resolve_class_attributes",
)

_FINAL_WRAPPERS: tuple[Any, ...] = (
    typing.Final,
    typing_extensions.Final,
)

_REQUIRED_WRAPPERS: tuple[Any, ...] = (
    typing.Required,
    typing_extensions.Required,
)

_NOT_REQUIRED_WRAPPERS: tuple[Any, ...] = (
    typing.NotRequired,
    typing_extensions.NotRequired,
)

_OPTIONAL_WRAPPERS: tuple[Any, ...] = (
    typing.Optional,
    typing_extensions.Optional,
)


@final
class Attribute:
    @classmethod
    def resolved(
        cls,
        annotation: Any,
        *,
        attributes: Mapping[str, Self] | None = None,
        arguments: Sequence[Self | str] = (),
        annotations: Sequence[Any] = (),
        alias: str | None = None,
        alias_module: str | None = None,
        module: str | None = None,
        parameters: Mapping[str, Any],
        recursion_guard: MutableMapping[str, Self],
    ) -> Self:
        origin: Any = _resolve_origin(annotation)
        resolved_arguments: Sequence[Self | str]
        if arguments:
            resolved_arguments = arguments

        else:
            resolved_arguments = cast(
                Sequence[Self | str],
                _resolve_arguments(
                    annotation,
                    module=module
                    if module is not None
                    else getattr(annotation, "__module__", origin.__module__),
                    parameters=parameters,
                    recursion_guard=cast(MutableMapping[str, Attribute], recursion_guard),
                ),
            )

        return cls(
            recursion_key=_recursion_key(
                origin=origin,
                arguments=resolved_arguments,
                alias=alias,
                alias_module=alias_module,
            ),
            origin=_resolve_origin(annotation),
            attributes=attributes,
            arguments=resolved_arguments,
            annotations=annotations,
        )

    __slots__ = (
        "annotations",
        "arguments",
        "attributes",
        "origin",
        "recursion_key",
    )

    def __init__(
        self,
        *,
        recursion_key: str,
        origin: type[Any],
        attributes: Mapping[str, Self] | None = None,
        arguments: Sequence[Self | str] = (),
        annotations: Sequence[Any] = (),
    ) -> None:
        self.origin: Any = origin
        self.attributes: Mapping[str, Self] = attributes if attributes is not None else {}
        self.arguments: Sequence[Self | str] = arguments
        self.annotations: Sequence[Any] = tuple(annotations)
        self.recursion_key: str = recursion_key

    def __str__(self) -> str:
        return self.recursion_key

    def updated(
        self,
        *,
        annotations: Sequence[Any],
    ) -> Self:
        if annotations != self.annotations:
            return self.__class__(
                origin=self.origin,
                arguments=self.arguments,
                annotations=annotations,
                recursion_key=self.recursion_key,
            )

        return self


def resolve_class_attributes(
    cls: type[Any],
    /,
    parameters: Mapping[str, Any],
) -> Attribute:
    recursion_guard: MutableMapping[str, Attribute] = {}
    self_attribute: Attribute = Attribute.resolved(
        cls,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )
    recursion_guard: MutableMapping[str, Attribute] = {
        # Use current annotation as reference to Self
        "Self": self_attribute,
        self_attribute.recursion_key: self_attribute,
    }

    attributes: dict[str, Attribute] = {}
    for key, annotation in get_type_hints(
        self_attribute.origin,
        localns={self_attribute.origin.__name__: self_attribute.origin},
        include_extras=True,
    ).items():
        if key.startswith("_"):
            continue  # do not include private or special items

        if get_origin(annotation) is ClassVar:
            continue  # do not include class variables

        attributes[key] = resolve_attribute(
            annotation,
            parameters=parameters,
            module=self_attribute.origin.__module__,
            recursion_guard=recursion_guard,
        )

    self_attribute.attributes = attributes

    return self_attribute


def _lookup_recursion_guard(
    name: str,
    *,
    module: str,
    recursion_guard: Mapping[str, Attribute],
) -> Attribute | None:
    if guard := recursion_guard.get(name):
        return guard

    qualified_name: str = f"{module}.{name}" if module else name
    if guard := recursion_guard.get(qualified_name):
        return guard

    return None


def _resolve_origin(
    annotation: Any,
) -> Any:
    origin: Any = get_origin(annotation) or annotation

    if origin is list:
        return Sequence

    if origin is dict:
        return Mapping

    if origin is set:
        return Set

    return origin


def _resolve_arguments(
    annotation: Any,
    *,
    arguments: Sequence[Any] | None = None,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Sequence[Attribute]:
    resolved_arguments: Sequence[Any] | tuple[Any, ...]
    if arguments is not None:
        resolved_arguments = arguments

    else:
        extracted_arguments: tuple[Any, ...] = get_args(annotation)
        if extracted_arguments:
            resolved_arguments = extracted_arguments

        else:
            resolved_arguments = getattr(annotation, "__args__", ())

    if not resolved_arguments:
        return ()

    return tuple(
        resolve_attribute(
            argument,
            parameters=parameters,
            module=module,
            recursion_guard=recursion_guard,
        )
        for argument in resolved_arguments
    )


def _recursion_key(
    origin: Any,
    arguments: Sequence[Any] | None = None,
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
        arguments_str: str
        if arguments:
            arguments_str = "[" + ", ".join(str(arg) for arg in arguments) + "]"

        else:
            arguments_str = ""

        if qualname := getattr(origin, "__qualname__", None):
            recursion_key = f"{qualname}{arguments_str}"

        elif module := getattr(origin, "__module__", None):
            recursion_key = f"{module}.{getattr(origin, "__name__", str(origin))}{arguments_str}"

        else:
            recursion_key = f"{getattr(origin, "__name__", str(origin))}{arguments_str}"

    return recursion_key


def _evaluate_forward_ref(
    annotation: ForwardRef | str,
    /,
    module: str,
) -> Any:
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
        return evaluated

    else:
        raise RuntimeError(f"Cannot resolve annotation of {annotation}")


ATTRIBUTE_MISSING: Final[Attribute] = Attribute(
    recursion_key="Missing",
    origin=Missing,
)

ATTRIBUTE_NONE: Final[Attribute] = Attribute(
    recursion_key="None",
    origin=NoneType,
)


def _resolve_literal(
    annotation: Any,
    /,
) -> Attribute:
    literal_values: Sequence[Any] = get_args(annotation)
    rendered_arguments: Sequence[str] = tuple(
        str(value) if not isinstance(value, str) else value
        for value in literal_values
    )
    return Attribute(
        recursion_key=f"Literal[{', '.join(rendered_arguments)}]",
        origin=Literal,  # pyright: ignore[reportArgumentType]
        arguments=literal_values,
    )


def _resolve_type_alias(
    annotation: typing.TypeAliasType | typing_extensions.TypeAliasType,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    origin: Any = _resolve_origin(annotation.__value__)
    recursion_key: str = _recursion_key(
        origin=origin,
        alias=annotation.__name__,
        alias_module=annotation.__module__,
    )
    if guard := recursion_guard.get(recursion_key):
        return guard

    resolved_attribute: Attribute = Attribute(
        recursion_key=recursion_key,
        origin=origin,
    )
    recursion_guard[recursion_key] = resolved_attribute
    recursion_guard.setdefault(annotation.__name__, resolved_attribute)
    resolved_attribute.arguments = _resolve_arguments(
        annotation.__value__,
        module=annotation.__module__ or module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )

    return resolved_attribute


def _resolve_type_var(
    annotation: typing.TypeVar | typing_extensions.TypeVar,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    return resolve_attribute(
        parameters.get(
            annotation.__name__,
            # use bound as default or Any otherwise
            annotation.__bound__ or Any,
        ),
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )


def _resolve_union(
    annotation: UnionType,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    arguments: Sequence[Attribute] = _resolve_arguments(
        annotation,
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )
    return Attribute(
        recursion_key=" | ".join(argument.recursion_key for argument in arguments),
        origin=UnionType,  # pyright: ignore[reportArgumentType]
        arguments=arguments,
    )


def _resolve_callable(
    annotation: Any,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    arguments: Sequence[Attribute] = _resolve_arguments(
        annotation,
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )
    return Attribute(
        # TODO: Callable should distinguish between return type and arguments
        recursion_key=f"Callable[{','.join(argument.recursion_key for argument in arguments)}]",
        origin=Callable,  # pyright: ignore[reportArgumentType]
        arguments=arguments,
    )


def _resolve_not_required(
    annotation: Any,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    attribute: Attribute = resolve_attribute(
        get_args(annotation)[0],
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )

    return attribute.updated(annotations=(typing.NotRequired, *attribute.annotations))


def _resolve_required(
    annotation: Any,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    attribute: Attribute = resolve_attribute(
        get_args(annotation)[0],
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )

    return attribute.updated(annotations=(typing.Required, *attribute.annotations))


def _resolve_optional(
    annotation: Any,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    arguments: Sequence[Attribute] = [
        resolve_attribute(
            get_args(annotation)[0],
            module=module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        ),
        ATTRIBUTE_NONE,
    ]
    return Attribute(
        recursion_key=" | ".join(argument.recursion_key for argument in arguments),
        origin=UnionType,  # pyright: ignore[reportArgumentType]
        arguments=arguments,
    )


def _resolve_final(
    annotation: Any,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    attribute: Attribute = resolve_attribute(
        get_args(annotation)[0],
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )
    return attribute.updated(annotations=(typing.Final, *attribute.annotations))


def _resolve_annotated(
    annotation: Any,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    annotations: Sequence[Any] = get_args(annotation)
    attribute: Attribute = resolve_attribute(
        annotations[0],
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )

    return attribute.updated(
        annotations=(
            *attribute.annotations,
            *annotations[1:],
        ),
    )


def _resolve_type(
    annotation: Any,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    if get_origin(annotation) is typing.Literal:
        return _resolve_literal(annotation)

    if get_origin(annotation) is typing.Annotated:
        return _resolve_annotated(
            annotation,
            module=module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        )

    annotation_module: str | None = getattr(annotation, "__module__", None)
    if annotation_module is None or annotation_module == "builtins":
        nested_module: str = module
    else:
        nested_module = annotation_module
    recursion_key: str = _recursion_key(
        origin=_resolve_origin(annotation),
        arguments=_resolve_arguments(
            annotation,
            module=nested_module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        ),
    )
    if guard := recursion_guard.get(recursion_key):
        return guard

    attribute: Attribute = Attribute.resolved(
        annotation,
        module=nested_module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )

    recursion_guard[recursion_key] = attribute

    return attribute


def _resolve_generic_alias(
    annotation: GenericAlias,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    origin: Any = annotation.__origin__

    if isinstance(origin, (typing.TypeAliasType, typing_extensions.TypeAliasType)):
        return _resolve_type_alias(
            origin,
            parameters={
                param.__name__: get_args(annotation)[idx]
                for idx, param in enumerate(origin.__type_params__)
            },
            module=module,
            recursion_guard=recursion_guard,
        )

    generic_attribute: Attribute | None = _resolve_generic_type_origin(
        annotation,
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )
    if generic_attribute is not None:
        return generic_attribute

    result: Attribute | None = None

    if origin is typing.Annotated:
        result = _resolve_annotated(
            annotation,
            module=module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        )

    elif origin in _FINAL_WRAPPERS:
        result = _resolve_final(
            annotation,
            module=module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        )

    elif origin in _REQUIRED_WRAPPERS:
        result = _resolve_required(
            annotation,
            module=module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        )

    elif origin in _NOT_REQUIRED_WRAPPERS:
        result = _resolve_not_required(
            annotation,
            module=module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        )

    elif origin in _OPTIONAL_WRAPPERS:
        result = _resolve_optional(
            annotation,
            module=module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        )

    if result is not None:
        return result

    return _resolve_type(
        annotation,
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )


def _resolve_generic_type_origin(
    annotation: GenericAlias,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute | None:
    generic_origin: Any = annotation.__origin__
    if not (isinstance(generic_origin, type) and issubclass(generic_origin, Generic)):
        return None

    resolved_origin = generic_origin.__class_getitem__(  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
        tuple(
            parameters.get(
                arg.__name__,
                arg.__bound__ or Any,
            )
            if isinstance(arg, TypeVar)
            else arg
            for arg in get_args(annotation)
        )
    )

    if isinstance(resolved_origin, GenericAlias):
        origin: Any = _resolve_origin(resolved_origin.__origin__)
        resolved_arguments: Sequence[Any] | None = getattr(resolved_origin, "__args__", None)
        if not resolved_arguments:
            fallback_arguments: tuple[Any, ...] = get_args(resolved_origin)
            resolved_arguments = fallback_arguments or None
        bound_parameters: dict[str, Any]
        generic_parameters: tuple[Any, ...] = getattr(generic_origin, "__parameters__", ())
        if generic_parameters and resolved_arguments:
            bound_parameters = {
                parameter.__name__: argument
                for parameter, argument in zip(
                    generic_parameters,
                    resolved_arguments,
                    strict=False,
                )
                if hasattr(parameter, "__name__")
            }

        else:
            bound_parameters = {}

        merged_parameters: Mapping[str, Any]
        if bound_parameters:
            merged_parameters = {
                **parameters,
                **bound_parameters,
            }

        else:
            merged_parameters = parameters
        arguments: Sequence[Attribute] = _resolve_arguments(
            resolved_origin.__origin__,
            arguments=resolved_arguments,
            module=getattr(resolved_origin, "__module__", module),
            parameters=merged_parameters,
            recursion_guard=recursion_guard,
        )
        recursion_key: str = _recursion_key(
            origin=origin,
            arguments=arguments,
        )
        if guard := recursion_guard.get(recursion_key):
            return guard

        resolved_attribute: Attribute = Attribute(
            recursion_key=recursion_key,
            origin=origin,
            arguments=arguments,
        )
        recursion_guard[recursion_key] = resolved_attribute
        return resolved_attribute

    return _resolve_type(
        resolved_origin,
        module=module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )


def _resolve_typeddict(
    annotation: Any,
    *,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    typeddict_module: str = getattr(annotation, "__module__", module)
    resolved_attribute: Attribute = Attribute.resolved(
        annotation,
        module=typeddict_module,
        parameters=parameters,
        recursion_guard=recursion_guard,
    )
    if guard := recursion_guard.get(resolved_attribute.recursion_key):
        return guard

    recursion_guard[resolved_attribute.recursion_key] = resolved_attribute

    # preserve current Self reference
    self_attribute: Attribute | None = recursion_guard.get("Self", None)
    # temporarily update Self reference to contextual
    recursion_guard["Self"] = resolved_attribute

    attributes: MutableMapping[str, Attribute] = {}
    for key, element in get_type_hints(
        annotation,
        localns={annotation.__name__: annotation},
        include_extras=True,
    ).items():
        # TODO: update required/not requied attributes based on annotation.__required_keys__
        attributes[key] = resolve_attribute(
            element,
            module=typeddict_module,
            parameters=parameters,
            recursion_guard=recursion_guard,
        )

    if self_attribute is not None:  # bring Self back to previous attribute
        recursion_guard["Self"] = self_attribute

    resolved_attribute.attributes = attributes
    return resolved_attribute


def resolve_attribute(  # noqa: C901, PLR0911, PLR0912
    annotation: Any,
    /,
    module: str,
    parameters: Mapping[str, Any],
    recursion_guard: MutableMapping[str, Attribute],
) -> Attribute:
    match annotation:
        case None:
            return ATTRIBUTE_NONE

        case typeddict if is_typeddict(typeddict) or is_typeddict_ext(typeddict):
            return _resolve_typeddict(
                typeddict,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case typing.TypeAliasType() | typing_extensions.TypeAliasType():
            return _resolve_type_alias(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case typing.TypeVar() | typing_extensions.TypeVar():
            return _resolve_type_var(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case types.GenericAlias() | typing._GenericAlias():  # pyright: ignore
            return _resolve_generic_alias(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case typing.ParamSpec() | typing_extensions.ParamSpec():
            raise NotImplementedError(f"Unsupported ParamSpec annotation: {annotation}")

        case typing.Literal:
            return _resolve_literal(annotation)

        case typing.Callable:  # pyright: ignore
            return _resolve_callable(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case typing.Annotated:
            return _resolve_annotated(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case typing.Final:
            return _resolve_final(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case typing.Self | typing_extensions.Self:  # pyright: ignore
            if self_attribute := recursion_guard.get("Self"):
                return self_attribute

            else:
                raise RuntimeError(f"Unresolved Self annotation: {annotation}")

        case origin if origin in _REQUIRED_WRAPPERS:
            return _resolve_required(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case origin if origin in _NOT_REQUIRED_WRAPPERS:
            return _resolve_not_required(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case origin if origin in _OPTIONAL_WRAPPERS:
            return _resolve_optional(
                annotation,
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case typing.TypeVarTuple() | typing_extensions.TypeVarTuple():
            raise NotImplementedError(f"Unsupported TypeVarTuple annotation: {annotation}")

        case typing.ForwardRef() | typing_extensions.ForwardRef():
            if guard := _lookup_recursion_guard(
                annotation.__forward_arg__,
                module=module,
                recursion_guard=recursion_guard,
            ):
                return guard

            return resolve_attribute(
                _evaluate_forward_ref(
                    annotation,
                    module=module,
                ),
                module=module,
                parameters=parameters,
                recursion_guard=recursion_guard,
            )

        case annotation:
            if isinstance(annotation, str):
                if guard := _lookup_recursion_guard(
                    annotation,
                    module=module,
                    recursion_guard=recursion_guard,
                ):
                    return guard

                return resolve_attribute(
                    _evaluate_forward_ref(
                        annotation,
                        module=module,
                    ),
                    module=module,
                    parameters=parameters,
                    recursion_guard=recursion_guard,
                )

            match get_origin(annotation):
                case typing.Literal:
                    return _resolve_literal(annotation)

                case typing.Callable:  # pyright: ignore
                    return _resolve_callable(
                        annotation,
                        module=module,
                        parameters=parameters,
                        recursion_guard=recursion_guard,
                    )

                case typing.Annotated:
                    return _resolve_annotated(
                        annotation,
                        module=module,
                        parameters=parameters,
                        recursion_guard=recursion_guard,
                    )

                case typing.Final:
                    return _resolve_final(
                        annotation,
                        module=module,
                        parameters=parameters,
                        recursion_guard=recursion_guard,
                    )

                case origin if origin in _REQUIRED_WRAPPERS:
                    return _resolve_required(
                        annotation,
                        module=module,
                        parameters=parameters,
                        recursion_guard=recursion_guard,
                    )

                case origin if origin in _NOT_REQUIRED_WRAPPERS:
                    return _resolve_not_required(
                        annotation,
                        module=module,
                        parameters=parameters,
                        recursion_guard=recursion_guard,
                    )

                case origin if origin in _OPTIONAL_WRAPPERS:
                    return _resolve_optional(
                        annotation,
                        module=module,
                        parameters=parameters,
                        recursion_guard=recursion_guard,
                    )

                case typing.Self | typing_extensions.Self:  # pyright: ignore
                    if self_attribute := recursion_guard.get("Self"):
                        return self_attribute

                    else:
                        raise RuntimeError(f"Unresolved Self annotation: {annotation}")

                case _:
                    return _resolve_type(
                        annotation,
                        module=module,
                        parameters=parameters,
                        recursion_guard=recursion_guard,
                    )
