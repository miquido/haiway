import sys
import types
import typing
from collections.abc import Mapping
from types import NoneType, UnionType
from typing import (
    Any,
    ClassVar,
    ForwardRef,
    Generic,
    Literal,
    TypeAliasType,
    TypeVar,
    get_args,
    get_origin,
    get_type_hints,
)

__all__ = [
    "attribute_annotations",
    "AttributeAnnotation",
]


class AttributeAnnotation:
    def __init__(
        self,
        *,
        origin: Any,
        arguments: list[Any],
    ) -> None:
        self.origin: Any = origin
        self.arguments: list[Any] = arguments

    def __eq__(
        self,
        other: Any,
    ) -> bool:
        return self is other or (
            isinstance(other, self.__class__)
            and self.origin == other.origin
            and self.arguments == other.arguments
        )


def attribute_annotations(
    cls: type[Any],
    /,
    type_parameters: dict[str, Any] | None = None,
) -> dict[str, AttributeAnnotation]:
    type_parameters = type_parameters or {}

    self_annotation = AttributeAnnotation(
        origin=cls,
        arguments=[],  # ignore self arguments here, State will have them resolved at this stage
    )
    localns: dict[str, Any] = {cls.__name__: cls}
    recursion_guard: dict[Any, AttributeAnnotation] = {cls: self_annotation}
    attributes: dict[str, AttributeAnnotation] = {}

    for key, annotation in get_type_hints(cls, localns=localns).items():
        # do not include ClassVars, private or dunder items
        if ((get_origin(annotation) or annotation) is ClassVar) or key.startswith("_"):
            continue

        attributes[key] = _resolve_attribute_annotation(
            annotation,
            self_annotation=self_annotation,
            type_parameters=type_parameters,
            module=cls.__module__,
            localns=localns,
            recursion_guard=recursion_guard,
        )

    return attributes


def _resolve_attribute_annotation(  # noqa: C901, PLR0911, PLR0912, PLR0913
    annotation: Any,
    /,
    self_annotation: AttributeAnnotation | None,
    type_parameters: dict[str, Any],
    module: str,
    localns: dict[str, Any],
    recursion_guard: Mapping[Any, AttributeAnnotation],  # TODO: verify recursion!
) -> AttributeAnnotation:
    # resolve annotation directly if able
    match annotation:
        # None
        case types.NoneType | types.NoneType():
            return AttributeAnnotation(
                origin=NoneType,
                arguments=[],
            )

        # forward reference through string
        case str() as forward_ref:
            return _resolve_attribute_annotation(
                ForwardRef(forward_ref, module=module)._evaluate(
                    globalns=None,
                    localns=localns,
                    recursive_guard=frozenset(),
                ),
                self_annotation=self_annotation,
                type_parameters=type_parameters,
                module=module,
                localns=localns,
                recursion_guard=recursion_guard,  # we might need to update it somehow?
            )

        # forward reference directly
        case typing.ForwardRef() as reference:
            return _resolve_attribute_annotation(
                reference._evaluate(
                    globalns=None,
                    localns=localns,
                    recursive_guard=frozenset(),
                ),
                self_annotation=self_annotation,
                type_parameters=type_parameters,
                module=module,
                localns=localns,
                recursion_guard=recursion_guard,  # we might need to update it somehow?
            )

        # generic alias aka parametrized type
        case types.GenericAlias() as generic_alias:
            match get_origin(generic_alias):
                # check for an alias with parameters
                case typing.TypeAliasType() as alias:  # pyright: ignore[reportUnnecessaryComparison]
                    type_alias: AttributeAnnotation = AttributeAnnotation(
                        origin=TypeAliasType,
                        arguments=[],
                    )
                    resolved: AttributeAnnotation = _resolve_attribute_annotation(
                        alias.__value__,
                        self_annotation=None,
                        type_parameters=type_parameters,
                        module=module,
                        localns=localns,
                        recursion_guard=recursion_guard,
                    )
                    type_alias.origin = resolved.origin
                    type_alias.arguments = resolved.arguments
                    return type_alias

                # check if we can resolve it as generic
                case parametrized if issubclass(parametrized, Generic):
                    parametrized_type: Any = parametrized.__class_getitem__(  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
                        *(
                            type_parameters.get(
                                arg.__name__,
                                arg.__bound__ or Any,
                            )
                            if isinstance(arg, TypeVar)
                            else arg
                            for arg in get_args(generic_alias)
                        )
                    )

                    match parametrized_type:
                        # verify if we got any specific type or generic alias again
                        case types.GenericAlias():
                            return AttributeAnnotation(
                                origin=parametrized,
                                arguments=[
                                    _resolve_attribute_annotation(
                                        argument,
                                        self_annotation=self_annotation,
                                        type_parameters=type_parameters,
                                        module=module,
                                        localns=localns,
                                        recursion_guard=recursion_guard,
                                    )
                                    for argument in get_args(generic_alias)
                                ],
                            )

                        # use resolved type if it is not an alias again
                        case _:
                            return AttributeAnnotation(
                                origin=parametrized_type,
                                arguments=[],
                            )

                # anything else - try to resolve a concrete type or use as is
                case origin:
                    return AttributeAnnotation(
                        origin=origin,
                        arguments=[
                            _resolve_attribute_annotation(
                                argument,
                                self_annotation=self_annotation,
                                type_parameters=type_parameters,
                                module=module,
                                localns=localns,
                                recursion_guard=recursion_guard,
                            )
                            for argument in get_args(generic_alias)
                        ],
                    )

        # type alias
        case typing.TypeAliasType() as alias:
            type_alias: AttributeAnnotation = AttributeAnnotation(
                origin=TypeAliasType,
                arguments=[],
            )
            resolved: AttributeAnnotation = _resolve_attribute_annotation(
                alias.__value__,
                self_annotation=None,
                type_parameters=type_parameters,
                module=module,
                localns=localns,
                recursion_guard=recursion_guard,
            )
            type_alias.origin = resolved.origin
            type_alias.arguments = resolved.arguments
            return type_alias

        # type parameter
        case typing.TypeVar():
            return _resolve_attribute_annotation(
                # try to resolve it from current parameters if able
                type_parameters.get(
                    annotation.__name__,
                    # use bound as default or Any otherwise
                    annotation.__bound__ or Any,
                ),
                self_annotation=None,
                type_parameters=type_parameters,
                module=module,
                localns=localns,
                recursion_guard=recursion_guard,
            )

        case typing.ParamSpec():
            sys.stderr.write(
                "ParamSpec is not supported for attribute annotations,"
                " ignoring with Any type - it might incorrectly validate types\n"
            )
            return AttributeAnnotation(
                origin=Any,
                arguments=[],
            )

        case typing.TypeVarTuple():
            sys.stderr.write(
                "TypeVarTuple is not supported for attribute annotations,"
                " ignoring with Any type - it might incorrectly validate types\n"
            )
            return AttributeAnnotation(
                origin=Any,
                arguments=[],
            )

        case _:
            pass  # proceed to resolving based on origin

    # resolve based on origin if any
    match get_origin(annotation) or annotation:
        case types.UnionType | typing.Union:
            return AttributeAnnotation(
                origin=UnionType,  # pyright: ignore[reportArgumentType]
                arguments=[
                    recursion_guard.get(
                        argument,
                        _resolve_attribute_annotation(
                            argument,
                            self_annotation=self_annotation,
                            type_parameters=type_parameters,
                            module=module,
                            localns=localns,
                            recursion_guard=recursion_guard,
                        ),
                    )
                    for argument in get_args(annotation)
                ],
            )

        case typing.Callable:  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]
            return AttributeAnnotation(
                origin=typing.Callable,
                arguments=[
                    _resolve_attribute_annotation(
                        argument,
                        self_annotation=self_annotation,
                        type_parameters=type_parameters,
                        module=module,
                        localns=localns,
                        recursion_guard=recursion_guard,
                    )
                    for argument in get_args(annotation)
                ],
            )

        case typing.Self:  # pyright: ignore[reportUnknownMemberType]
            if not self_annotation:
                sys.stderr.write(
                    "Unresolved Self attribute annotation,"
                    " ignoring with Any type - it might incorrectly validate types\n"
                )
                return AttributeAnnotation(
                    origin=Any,
                    arguments=[],
                )

            return self_annotation

        # unwrap from irrelevant type wrappers
        case typing.Annotated | typing.Final | typing.Required | typing.NotRequired:
            return _resolve_attribute_annotation(
                get_args(annotation)[0],
                self_annotation=self_annotation,
                type_parameters=type_parameters,
                module=module,
                localns=localns,
                recursion_guard=recursion_guard,
            )

        case typing.Optional:  # optional is a Union[Value, None]
            return AttributeAnnotation(
                origin=UnionType,  # pyright: ignore[reportArgumentType]
                arguments=[
                    _resolve_attribute_annotation(
                        get_args(annotation)[0],
                        self_annotation=self_annotation,
                        type_parameters=type_parameters,
                        module=module,
                        localns=localns,
                        recursion_guard=recursion_guard,
                    ),
                    AttributeAnnotation(
                        origin=NoneType,
                        arguments=[],
                    ),
                ],
            )

        case typing.Literal:
            return AttributeAnnotation(
                origin=Literal,
                arguments=list(get_args(annotation)),
            )

        case other:  # finally use whatever there was
            return AttributeAnnotation(
                origin=other,
                arguments=[
                    _resolve_attribute_annotation(
                        argument,
                        self_annotation=self_annotation,
                        type_parameters=type_parameters,
                        module=module,
                        localns=localns,
                        recursion_guard=recursion_guard,
                    )
                    for argument in get_args(other)
                ],
            )
