from copy import deepcopy
from types import EllipsisType, GenericAlias
from typing import (
    Any,
    ClassVar,
    Generic,
    Self,
    TypeVar,
    cast,
    dataclass_transform,
    final,
)
from weakref import WeakValueDictionary

from haiway.state.attributes import AttributeAnnotation, attribute_annotations
from haiway.state.path import AttributePath
from haiway.state.validation import (
    AttributeValidation,
    AttributeValidator,
)
from haiway.types import MISSING, Missing, not_missing

__all__ = [
    "State",
]


@final
class StateAttribute[Value]:
    def __init__(
        self,
        name: str,
        annotation: AttributeAnnotation,
        default: Value | Missing,
        validator: AttributeValidation[Value],
    ) -> None:
        self.name: str = name
        self.annotation: AttributeAnnotation = annotation
        self.default: Value | Missing = default
        self.validator: AttributeValidation[Value] = validator

    def validated(
        self,
        value: Any | Missing,
        /,
    ) -> Value:
        return self.validator(self.default if value is MISSING else value)


@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
    field_specifiers=(),
)
class StateMeta(type):
    def __new__(
        cls,
        /,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        type_parameters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        state_type = type.__new__(
            cls,
            name,
            bases,
            namespace,
            **kwargs,
        )

        attributes: dict[str, StateAttribute[Any]] = {}

        for key, annotation in attribute_annotations(
            state_type,
            type_parameters=type_parameters or {},
        ).items():
            default: Any = getattr(state_type, key, MISSING)
            attributes[key] = StateAttribute(
                name=key,
                annotation=annotation.update_required(default is MISSING),
                default=default,
                validator=AttributeValidator.of(
                    annotation,
                    recursion_guard={
                        str(AttributeAnnotation(origin=state_type)): state_type.validator
                    },
                ),
            )

        state_type.__ATTRIBUTES__ = attributes  # pyright: ignore[reportAttributeAccessIssue]
        state_type.__slots__ = frozenset(attributes.keys())  # pyright: ignore[reportAttributeAccessIssue]
        state_type.__match_args__ = state_type.__slots__  # pyright: ignore[reportAttributeAccessIssue]
        state_type._ = AttributePath(state_type, attribute=state_type)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

        return state_type

    def validator(
        cls,
        value: Any,
        /,
    ) -> Any: ...


_types_cache: WeakValueDictionary[
    tuple[
        Any,
        tuple[Any, ...],
    ],
    Any,
] = WeakValueDictionary()


class State(metaclass=StateMeta):
    """
    Base class for immutable data structures.
    """

    _: ClassVar[Self]
    __IMMUTABLE__: ClassVar[EllipsisType] = ...
    __ATTRIBUTES__: ClassVar[dict[str, StateAttribute[Any]]]

    @classmethod
    def __class_getitem__(
        cls,
        type_argument: tuple[type[Any], ...] | type[Any],
    ) -> type[Self]:
        assert Generic in cls.__bases__, "Can't specialize non generic type!"  # nosec: B101

        type_arguments: tuple[type[Any], ...]
        match type_argument:
            case [*arguments]:
                type_arguments = tuple(arguments)

            case argument:
                type_arguments = (argument,)

        if any(isinstance(argument, TypeVar) for argument in type_arguments):  # pyright: ignore[reportUnnecessaryIsInstance]
            # if we got unfinished type treat it as an alias instead of resolving
            return cast(type[Self], GenericAlias(cls, type_arguments))

        assert len(type_arguments) == len(  # nosec: B101
            cls.__type_params__
        ), "Type arguments count has to match type parameters count"

        if cached := _types_cache.get((cls, type_arguments)):
            return cached

        type_parameters: dict[str, Any] = {
            parameter.__name__: argument
            for (parameter, argument) in zip(
                cls.__type_params__ or (),
                type_arguments or (),
                strict=False,
            )
        }

        parameter_names: str = ",".join(
            getattr(
                argument,
                "__name__",
                str(argument),
            )
            for argument in type_arguments
        )
        name: str = f"{cls.__name__}[{parameter_names}]"
        bases: tuple[type[Self]] = (cls,)

        parametrized_type: type[Self] = StateMeta.__new__(
            cls.__class__,
            name=name,
            bases=bases,
            namespace={"__module__": cls.__module__},
            type_parameters=type_parameters,
        )
        _types_cache[(cls, type_arguments)] = parametrized_type
        return parametrized_type

    @classmethod
    def validator(
        cls,
        value: Any,
        /,
    ) -> Self:
        match value:
            case validated if isinstance(validated, cls):
                return validated

            case {**values}:
                return cls(**values)

            case _:
                raise TypeError(f"Expected '{cls.__name__}', received '{type(value).__name__}'")

    def __init__(
        self,
        **kwargs: Any,
    ) -> None:
        for name, attribute in self.__ATTRIBUTES__.items():
            object.__setattr__(
                self,  # pyright: ignore[reportUnknownArgumentType]
                name,
                attribute.validated(
                    kwargs.get(
                        name,
                        MISSING,
                    ),
                ),
            )

    def updating[Value](
        self,
        path: AttributePath[Self, Value] | Value,
        /,
        value: Value,
    ) -> Self:
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare parameter path by using Self._.path.to.property or explicitly"

        return cast(AttributePath[Self, Value], path)(self, updated=value)

    def updated(
        self,
        **kwargs: Any,
    ) -> Self:
        return self.__replace__(**kwargs)

    def as_dict(self) -> dict[str, Any]:
        dict_result: dict[str, Any] = {}
        for key in self.__ATTRIBUTES__.keys():
            value: Any | Missing = getattr(self, key, MISSING)
            if not_missing(value):
                dict_result[key] = value

        return dict_result

    def __str__(self) -> str:
        attributes: str = ", ".join([f"{key}: {value}" for key, value in vars(self).items()])
        return f"{self.__class__.__name__}({attributes})"

    def __repr__(self) -> str:
        return str(self)

    def __eq__(
        self,
        other: Any,
    ) -> bool:
        if not issubclass(other.__class__, self.__class__):
            return False

        return all(
            getattr(self, key, MISSING) == getattr(other, key, MISSING)
            for key in self.__ATTRIBUTES__.keys()
        )

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable state {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable state {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )

    def __copy__(self) -> Self:
        return self.__class__(**vars(self))

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        copy: Self = self.__class__(
            **{
                key: deepcopy(
                    value,
                    memo,
                )
                for key, value in vars(self).items()
            }
        )
        return copy

    def __replace__(
        self,
        **kwargs: Any,
    ) -> Self:
        return self.__class__(
            **{
                **vars(self),
                **kwargs,
            }
        )
