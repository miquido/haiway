from collections.abc import Callable, Mapping, MutableSet, Sequence
from functools import update_wrapper
from inspect import Parameter as InspectParameter
from inspect import _empty as INSPECT_EMPTY  # pyright: ignore[reportPrivateUsage]
from inspect import signature
from typing import Any, get_type_hints

from haiway.attributes.annotations import AttributeAnnotation, resolve_attribute
from haiway.attributes.attribute import Attribute
from haiway.attributes.validation import ValidationError
from haiway.types import MISSING, DefaultValue

__all__ = ("Function",)


class Function[**Args, Result]:
    """
    Wrap a callable with runtime argument validation.

    Parameters
    ----------
    function : Callable[Args, Result]
        Callable to wrap. Its signature and resolved type hints define the
        accepted positional, keyword, variadic, and aliased arguments.

    Returns
    -------
    Result
        Result returned by the wrapped callable after validated arguments are
        passed through.

    Raises
    ------
    TypeError
        Raised when the wrapped callable has untyped parameters, when
        unexpected or duplicate arguments are provided, or when invocation
        cannot be matched to the inspected signature.
    Exception
        Propagates validation errors raised while resolving attribute
        definitions or validating argument values before invocation.

    Notes
    -----
    ``Function`` inspects the wrapped callable's signature, resolves type hints
    through Haiway's attribute system, and validates every argument before
    calling the underlying callable. Keyword aliases declared through
    ``typing.Annotated[..., Alias(...)]`` are supported in the same way as for
    ``State`` fields.
    """

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
        # names of the parameters declaring `Default(...)` instead of a plain
        # default - python binds the marker itself for an omitted one, so their
        # value has to be resolved here before the call. a plain default is left
        # to python, which already binds the intended value and never validates
        # it, so resolving one here would coerce a value the caller never passed
        self._deferred_defaults: MutableSet[str] = set()
        type_hints: Mapping[str, Any] = get_type_hints(
            function,
            include_extras=True,
        )
        for parameter in signature(function).parameters.values():
            if isinstance(parameter.default, DefaultValue):
                self._deferred_defaults.add(parameter.name)

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
                validated_args.append(_validated(attribute, attribute.name, value))
                consumed_args.add(attribute.name)
                if attribute.alias is not None:
                    consumed_args.add(attribute.alias)

            elif self._variadic_positional_arguments is not None:
                attribute = self._variadic_positional_arguments
                validated_args.append(_validated(attribute, attribute.name, value))

            else:
                raise TypeError(f"Unexpected positional argument at index {idx}") from None

        for key, value in kwargs.items():
            if key in consumed_args:
                raise TypeError(f"Duplicate argument '{key}' for {self.__class__.__name__}")

            if key in self._keyword_arguments:
                attribute: Attribute = self._keyword_arguments[key]
                validated_kwargs[attribute.name] = _validated(attribute, key, value)
                consumed_args.add(attribute.name)
                if attribute.alias is not None:
                    consumed_args.add(attribute.alias)

            elif key in self._aliased_keyword_arguments:
                attribute: Attribute = self._aliased_keyword_arguments[key]
                assert attribute.alias is not None  # nosec: B101
                validated_kwargs[attribute.name] = _validated(
                    attribute,
                    attribute.name,
                    value,
                )

                consumed_args.add(attribute.name)
                consumed_args.add(attribute.alias)

            elif self._variadic_keyword_arguments is not None:
                attribute = self._variadic_keyword_arguments
                validated_kwargs[key] = _validated(attribute, key, value)

            else:
                raise TypeError(f"Unexpected keyword argument '{key}'") from None

        self._fill_deferred_defaults(
            validated_args,
            validated_kwargs,
            provided_positional=len(args),
            consumed_args=consumed_args,
        )

        return validated_args, validated_kwargs

    def _fill_deferred_defaults(
        self,
        validated_args: list[Any],
        validated_kwargs: dict[str, Any],
        /,
        *,
        provided_positional: int,
        consumed_args: set[str],
    ) -> None:
        if not self._deferred_defaults:
            return  # nothing declares a marker, python resolves every default

        # a positional only parameter can be filled by position alone, so the
        # fill stops at the first slot with nothing to provide - appending past
        # it would bind the value to the wrong parameter. the slots left behind
        # are the ones python resolves itself, by its default or by raising
        for index in range(provided_positional, len(self._positional_arguments)):
            attribute: Attribute = self._positional_arguments[index]
            if attribute.name in self._keyword_arguments:
                break  # reached the keyword fillable ones, filled below

            if attribute.name not in self._deferred_defaults:
                break  # nothing to resolve here, later slots would shift

            validated_args.append(_validated(attribute, attribute.name, MISSING))
            consumed_args.add(attribute.name)

        for name, attribute in self._keyword_arguments.items():
            if name in consumed_args or name not in self._deferred_defaults:
                continue  # already provided, or resolved by python itself

            validated_kwargs[name] = _validated(attribute, name, MISSING)

    def __call__(
        self,
        *args: Args.args,
        **kwargs: Args.kwargs,
    ) -> Result:
        validated_args, validated_kwargs = self.validate_arguments(*args, **kwargs)
        return self._call(*validated_args, **validated_kwargs)  # pyright: ignore[reportCallIssue]


def _validated(
    attribute: Attribute,
    name: str,
    value: Any,
    /,
) -> Any:
    try:
        return attribute.validate(value)

    # the position of the failure is spelled out here rather than established
    # before validating - an argument which validates pays nothing for it
    except Exception as exc:
        ValidationError.report(f".{name}", exc)


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
