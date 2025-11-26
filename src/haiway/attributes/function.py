from collections.abc import Callable, Mapping, Sequence
from functools import update_wrapper
from inspect import Parameter as InspectParameter
from inspect import _empty as INSPECT_EMPTY  # pyright: ignore[reportPrivateUsage]
from inspect import signature
from typing import Any, get_type_hints

from haiway.attributes.annotations import AttributeAnnotation, resolve_attribute
from haiway.attributes.attribute import Attribute
from haiway.attributes.validation import ValidationContext
from haiway.types import MISSING, DefaultValue

__all__ = ("Function",)


class Function[**Args, Result]:
    def __init__(
        self,
        function: Callable[Args, Result],
        /,
    ) -> None:
        assert not isinstance(function, Function)  # nosec: B101

        self._call: Callable[Args, Result] = function
        self._positional_arguments: Sequence[Attribute] = []
        self._variadic_positional_arguments: Attribute | None = None
        self._keyword_arguments: Mapping[str, Attribute] = {}
        self._aliased_keyword_arguments: Mapping[str, Attribute] = {}
        self._variadic_keyword_arguments: Attribute | None = None
        type_hints: Mapping[str, Any] = get_type_hints(
            function,
            include_extras=True,
        )
        for parameter in signature(function).parameters.values():
            match parameter.kind:
                case InspectParameter.POSITIONAL_ONLY:
                    self._positional_arguments.append(
                        _resolve_parameter(
                            parameter,
                            module=function.__module__,
                            type_hint=type_hints.get(parameter.name),
                        )
                    )

                case InspectParameter.POSITIONAL_OR_KEYWORD:
                    resolved: Attribute = _resolve_parameter(
                        parameter,
                        module=function.__module__,
                        type_hint=type_hints.get(parameter.name),
                    )
                    self._positional_arguments.append(resolved)
                    self._keyword_arguments[parameter.name] = resolved
                    if resolved.alias:
                        self._aliased_keyword_arguments[resolved.alias] = resolved

                case InspectParameter.KEYWORD_ONLY:
                    resolved: Attribute = _resolve_parameter(
                        parameter,
                        module=function.__module__,
                        type_hint=type_hints.get(parameter.name),
                    )
                    self._keyword_arguments[parameter.name] = resolved
                    if resolved.alias:
                        self._aliased_keyword_arguments[resolved.alias] = resolved

                case InspectParameter.VAR_POSITIONAL:
                    assert self._variadic_positional_arguments is None  # nosec: B101
                    self._variadic_positional_arguments = _resolve_parameter(
                        parameter,
                        module=function.__module__,
                        type_hint=type_hints.get(parameter.name),
                    )

                case InspectParameter.VAR_KEYWORD:
                    assert self._variadic_keyword_arguments is None  # nosec: B101
                    self._variadic_keyword_arguments = _resolve_parameter(
                        parameter,
                        module=function.__module__,
                        type_hint=type_hints.get(parameter.name),
                    )

        update_wrapper(self, function)

    def validate_arguments(  # noqa: C901
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> tuple[list[Any], dict[str, Any]]:
        validated_args: list[Any] = []
        validated_kwargs: dict[str, Any] = {}
        consumed_args: set[str] = set()

        for idx, value in enumerate(args):
            attribute: Attribute
            if idx < len(self._positional_arguments):
                attribute = self._positional_arguments[idx]
                with ValidationContext.scope(f".{attribute.name}"):
                    validated_args.append(attribute.validate(value))

                consumed_args.add(attribute.name)
                if attribute.alias is not None:
                    consumed_args.add(attribute.alias)

            elif self._variadic_positional_arguments is not None:
                attribute = self._variadic_positional_arguments
                with ValidationContext.scope(f".{attribute.name}"):
                    validated_args.append(attribute.validate(value))

            else:
                raise TypeError(f"Unexpected positional argument at index {idx}") from None

        for key, value in kwargs.items():
            if key in consumed_args:
                raise TypeError(f"Duplicate argument '{key}' for {self.__class__.__name__}")

            if key in self._keyword_arguments:
                attribute: Attribute = self._keyword_arguments[key]
                with ValidationContext.scope(f".{key}"):
                    validated_kwargs[attribute.name] = attribute.validate(value)

                consumed_args.add(attribute.name)
                if attribute.alias is not None:
                    consumed_args.add(attribute.alias)

            elif key in self._aliased_keyword_arguments:
                attribute: Attribute = self._aliased_keyword_arguments[key]
                assert attribute.alias is not None  # nosec: B101
                with ValidationContext.scope(f".{attribute.name}"):
                    validated_kwargs[attribute.name] = attribute.validate(value)

                consumed_args.add(attribute.name)
                consumed_args.add(attribute.alias)

            elif self._variadic_keyword_arguments is not None:
                attribute = self._variadic_keyword_arguments
                with ValidationContext.scope(f".{key}"):
                    validated_kwargs[key] = attribute.validate(value)

            else:
                raise TypeError(f"Unexpected keyword argument '{key}'") from None

        return validated_args, validated_kwargs

    def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        validated_args, validated_kwargs = self.validate_arguments(*args, **kwargs)
        return self._call(*validated_args, **validated_kwargs)  # pyright: ignore[reportCallIssue]


def _resolve_parameter(
    parameter: InspectParameter,
    /,
    *,
    module: str,
    type_hint: Any,
) -> Attribute:
    if parameter.annotation is INSPECT_EMPTY or type_hint is None:
        raise TypeError(f"Untyped argument {parameter.name}")

    attribute: AttributeAnnotation = resolve_attribute(
        type_hint,
        module=module,
        resolved_parameters={},
        recursion_guard={},
    )

    if isinstance(parameter.default, DefaultValue):
        return Attribute(
            name=parameter.name,
            annotation=attribute,
            default=parameter.default,
        )

    else:
        return Attribute(
            name=parameter.name,
            annotation=attribute,
            default=DefaultValue(
                default=MISSING if parameter.default is INSPECT_EMPTY else parameter.default,
            ),
        )
