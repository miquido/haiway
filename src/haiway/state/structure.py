from collections.abc import Mapping
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
from haiway.state.validation import AttributeValidation, AttributeValidator
from haiway.types import MISSING, DefaultValue, Missing, not_missing

__all__ = ("State",)


@final
class StateAttribute[Value]:
    """
    Represents an attribute in a State class with its metadata.

    This class holds information about a specific attribute in a State class,
    including its name, type annotation, default value, and validation rules.
    It is used internally by the State metaclass to manage state attributes
    and ensure their immutability and type safety.
    """

    __slots__ = (
        "annotation",
        "default",
        "name",
        "validator",
    )

    def __init__(
        self,
        name: str,
        annotation: AttributeAnnotation,
        default: DefaultValue[Value],
        validator: AttributeValidation[Value],
    ) -> None:
        """
        Initialize a new StateAttribute.

        Parameters
        ----------
        name : str
            The name of the attribute
        annotation : AttributeAnnotation
            The type annotation of the attribute
        default : DefaultValue[Value]
            The default value provider for the attribute
        validator : AttributeValidation[Value]
            The validation function for the attribute values
        """
        self.name: str
        object.__setattr__(
            self,
            "name",
            name,
        )
        self.annotation: AttributeAnnotation
        object.__setattr__(
            self,
            "annotation",
            annotation,
        )
        self.default: DefaultValue[Value]
        object.__setattr__(
            self,
            "default",
            default,
        )
        self.validator: AttributeValidation[Value]
        object.__setattr__(
            self,
            "validator",
            validator,
        )

    def validated(
        self,
        value: Any | Missing,
        /,
    ) -> Value:
        """
        Validate and potentially transform the provided value.

        If the value is MISSING, the default value is used instead.
        The value (or default) is then passed through the validator.

        Parameters
        ----------
        value : Any | Missing
            The value to validate, or MISSING to use the default

        Returns
        -------
        Value
            The validated and potentially transformed value
        """
        return self.validator(self.default() if value is MISSING else value)

    def __setattr__(
        self,
        name: str,
        value: Any,
    ) -> Any:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> None:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be deleted"
        )


@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
    field_specifiers=(),
)
class StateMeta(type):
    """
    Metaclass for State classes that manages attribute definitions and validation.

    This metaclass is responsible for:
    - Processing attribute annotations and defaults
    - Creating StateAttribute instances for each attribute
    - Setting up validation for attributes
    - Managing generic type parameters and specialization
    - Creating immutable class instances

    The dataclass_transform decorator allows State classes to be treated
    like dataclasses by static type checkers while using custom initialization
    and validation logic.
    """

    def __new__(
        mcs,
        /,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        type_parameters: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> Any:
        cls = type.__new__(
            mcs,
            name,
            bases,
            namespace,
            **kwargs,
        )

        attributes: dict[str, StateAttribute[Any]] = {}

        for key, annotation in attribute_annotations(
            cls,
            type_parameters=type_parameters or {},
        ).items():
            default: Any = getattr(cls, key, MISSING)
            attributes[key] = StateAttribute(
                name=key,
                annotation=annotation.update_required(default is MISSING),
                default=_resolve_default(default),
                validator=AttributeValidator.of(
                    annotation,
                    recursion_guard={str(AttributeAnnotation(origin=cls)): cls.validator},
                ),
            )

        cls.__TYPE_PARAMETERS__ = type_parameters  # pyright: ignore[reportAttributeAccessIssue]
        cls.__ATTRIBUTES__ = attributes  # pyright: ignore[reportAttributeAccessIssue]
        cls.__slots__ = frozenset(attributes.keys())  # pyright: ignore[reportAttributeAccessIssue]
        cls.__match_args__ = cls.__slots__  # pyright: ignore[reportAttributeAccessIssue]
        cls._ = AttributePath(cls, attribute=cls)  # pyright: ignore[reportCallIssue, reportUnknownMemberType, reportAttributeAccessIssue]

        return cls

    def validator(
        cls,
        value: Any,
        /,
    ) -> Any:
        """
        Placeholder for the validator method that will be implemented in each State class.

        This method validates and potentially transforms a value to ensure it
        conforms to the class's requirements.

        Parameters
        ----------
        value : Any
            The value to validate

        Returns
        -------
        Any
            The validated value
        """
        ...

    def __instancecheck__(
        self,
        instance: Any,
    ) -> bool:
        # check for type match
        if self.__subclasscheck__(type(instance)):
            return True

        # otherwise check if we are dealing with unparametrized base
        # against the parametrized one, our generic subtypes have base of unparametrized type
        if type(instance) not in self.__bases__:
            return False

        try:
            # validate instance to check unparametrized fields
            _ = self(**vars(instance))

        except Exception:
            return False

        else:
            return True

    def __subclasscheck__(
        self,
        subclass: type[Any],
    ) -> bool:
        if self is subclass:
            return True

        self_origin: type[Any] = getattr(self, "__origin__", self)
        subclass_origin: type[Any] = getattr(subclass, "__origin__", subclass)

        # Handle case where we're checking a parameterized type against unparameterized
        if self_origin is self:
            return type.__subclasscheck__(self, subclass)

        # Both must be based on the same generic class
        if not issubclass(subclass_origin, self_origin):
            return False

        return self._check_type_parameters(subclass)

    def _check_type_parameters(
        self,
        subclass: type[Any],
    ) -> bool:
        self_params: Mapping[str, Any] | None = getattr(self, "__TYPE_PARAMETERS__", None)
        subclass_params: Mapping[str, Any] | None = getattr(subclass, "__TYPE_PARAMETERS__", None)

        if self_params is None:
            return True

        # If subclass doesn't have type parameters, look in the MRO for a parametrized base
        if subclass_params is None:
            subclass_params = self._find_parametrized_base(subclass)
            if subclass_params is None:
                return False

        # Check if the type parameters are compatible (covariant)
        for key, self_param in self_params.items():
            subclass_param: type[Any] = subclass_params.get(key, Any)
            if self_param is Any:
                continue

            # For covariance: GenericState[Child] should be subclass of GenericState[Parent]
            # This means subclass_param should be a subclass of self_param
            if not issubclass(subclass_param, self_param):
                return False

        return True

    def _find_parametrized_base(
        self,
        subclass: type[Any],
    ) -> Mapping[str, Any] | None:
        self_origin: type[Any] = getattr(self, "__origin__", self)
        for base in getattr(subclass, "__mro__", ()):
            if getattr(base, "__origin__", None) is not self_origin:
                continue

            subclass_params: Mapping[str, Any] | None = getattr(base, "__TYPE_PARAMETERS__", None)
            if subclass_params is not None:
                return subclass_params

        return None


def _resolve_default[Value](
    value: DefaultValue[Value] | Value | Missing,
) -> DefaultValue[Value]:
    """
    Ensure a value is wrapped in a DefaultValue container.

    Parameters
    ----------
    value : DefaultValue[Value] | Value | Missing
        The value or default value container to resolve

    Returns
    -------
    DefaultValue[Value]
        The value wrapped in a DefaultValue container
    """
    if isinstance(value, DefaultValue):
        return cast(DefaultValue[Value], value)

    return DefaultValue[Value](
        value,
        factory=MISSING,
    )


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

    State provides a framework for creating immutable, type-safe data classes
    with validation. It's designed to represent application state that can be
    safely shared and updated in a predictable manner.

    Key features:
    - Immutable: Instances cannot be modified after creation
    - Type-safe: Attributes are validated based on type annotations
    - Generic: Can be parameterized with type variables
    - Declarative: Uses a class-based declaration syntax similar to dataclasses
    - Validated: Custom validation rules can be applied to attributes

    State classes can be created by subclassing State and declaring attributes:

    ```python
    class User(State):
        name: str
        age: int
        email: str | None = None
    ```

    Instances are created using standard constructor syntax:

    ```python
    user = User(name="Alice", age=30)
    ```

    New instances with updated values can be created from existing ones:

    ```python
    updated_user = user.updated(age=31)
    ```

    Path-based updates are also supported:

    ```python
    updated_user = user.updating(User._.age, 31)
    ```
    """

    _: ClassVar[Self]
    __IMMUTABLE__: ClassVar[EllipsisType] = ...
    __TYPE_PARAMETERS__: ClassVar[Mapping[str, Any] | None] = None
    __ATTRIBUTES__: ClassVar[dict[str, StateAttribute[Any]]]

    @classmethod
    def __class_getitem__(
        cls,
        type_argument: tuple[type[Any], ...] | type[Any],
    ) -> type[Self]:
        """
        Create a specialized version of a generic State class.

        This method enables the generic type syntax Class[TypeArg] for State classes.

        Parameters
        ----------
        type_argument : tuple[type[Any], ...] | type[Any]
            The type arguments to specialize the class with

        Returns
        -------
        type[Self]
            A specialized version of the class

        Raises
        ------
        AssertionError
            If the class is not generic or is already specialized,
            or if the number of type arguments doesn't match the parameters
        """
        assert Generic in cls.__bases__, "Can't specialize non generic type!"  # nosec: B101
        assert cls.__TYPE_PARAMETERS__ is None, "Can't specialize already specialized type!"  # nosec: B101

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
        # Set origin for subclass checks
        parametrized_type.__origin__ = cls  # pyright: ignore[reportAttributeAccessIssue]
        _types_cache[(cls, type_arguments)] = parametrized_type
        return parametrized_type

    @classmethod
    def validator(
        cls,
        value: Any,
        /,
    ) -> Self:
        """
        Validate and convert a value to an instance of this class.

        Parameters
        ----------
        value : Any
            The value to validate and convert

        Returns
        -------
        Self
            An instance of this class

        Raises
        ------
        TypeError
            If the value cannot be converted to an instance of this class
        """
        match value:
            case validated if isinstance(validated, cls):
                return validated

            case {**values}:
                return cls(**values)

            case _:
                raise TypeError(f"Expected '{cls.__name__}', received '{type(value).__name__}'")

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        /,
    ) -> Self:
        return cls(**value)

    def __init__(
        self,
        **kwargs: Any,
    ) -> None:
        """
        Initialize a new State instance.

        Creates a new instance with the provided attribute values.
        Attributes not specified will use their default values.
        All attributes are validated according to their type annotations.

        Parameters
        ----------
        **kwargs : Any
            Attribute values for the new instance

        Raises
        ------
        Exception
            If validation fails for any attribute
        """
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
        """
        Create a new instance with an updated value at the specified path.

        Parameters
        ----------
        path : AttributePath[Self, Value] | Value
            An attribute path created with Class._.attribute syntax
        value : Value
            The new value for the specified attribute

        Returns
        -------
        Self
            A new instance with the updated value

        Raises
        ------
        AssertionError
            If path is not an AttributePath
        """
        assert isinstance(  # nosec: B101
            path, AttributePath
        ), "Prepare parameter path by using Self._.path.to.property or explicitly"

        return cast(AttributePath[Self, Value], path)(self, updated=value)

    def updated(
        self,
        **kwargs: Any,
    ) -> Self:
        """
        Create a new instance with updated attribute values.

        This method creates a new instance with the same attribute values as this
        instance, but with any provided values updated.

        Parameters
        ----------
        **kwargs : Any
            New values for attributes to update

        Returns
        -------
        Self
            A new instance with updated values
        """
        return self.__replace__(**kwargs)

    def to_str(self) -> str:
        """
        Convert this instance to a string representation.

        Returns
        -------
        str
            A string representation of this instance
        """
        return self.__str__()

    def to_mapping(
        self,
        recursive: bool = False,
    ) -> Mapping[str, Any]:
        """
        Convert this instance to a mapping of attribute names to values.

        Parameters
        ----------
        recursive : bool, default=False
            If True, nested State instances are also converted to mappings

        Returns
        -------
        Mapping[str, Any]
            A mapping of attribute names to values
        """
        dict_result: dict[str, Any] = {}
        for key in self.__ATTRIBUTES__.keys():
            value: Any | Missing = getattr(self, key, MISSING)
            if recursive and isinstance(value, State):
                dict_result[key] = value.to_mapping(recursive=recursive)

            elif not_missing(value):
                dict_result[key] = value

        return dict_result

    def __str__(self) -> str:
        """
        Get a string representation of this instance.

        Returns
        -------
        str
            A string representation in the format "ClassName(attr1: value1, attr2: value2)"
        """
        attributes: str = ", ".join([f"{key}: {value}" for key, value in vars(self).items()])
        return f"{self.__class__.__name__}({attributes})"

    def __repr__(self) -> str:
        return str(self)

    def __eq__(
        self,
        other: Any,
    ) -> bool:
        """
        Check if this instance is equal to another object.

        Two State instances are considered equal if they are instances of the
        same class or subclass and have equal values for all attributes.

        Parameters
        ----------
        other : Any
            The object to compare with

        Returns
        -------
        bool
            True if the objects are equal, False otherwise
        """
        if not issubclass(other.__class__, self.__class__):
            return False

        return all(
            getattr(self, key, MISSING) == getattr(other, key, MISSING)
            for key in self.__ATTRIBUTES__.keys()
        )

    def __hash__(self) -> int:
        hash_values: list[int] = []
        for key in self.__ATTRIBUTES__.keys():
            value: Any = getattr(self, key, MISSING)

            # Skip MISSING values to ensure consistent hashing
            if value is MISSING:
                continue

            # Convert to hashable representation
            try:
                hash_values.append(hash(value))

            except TypeError:
                continue  # skip unhashable

        return hash((self.__class__, tuple(hash_values)))

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
        """
        Create a shallow copy of this instance.

        Since State is immutable, this returns the instance itself.

        Returns
        -------
        Self
            This instance
        """
        return self  # State is immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        """
        Create a deep copy of this instance.

        Since State is immutable, this returns the instance itself.

        Parameters
        ----------
        memo : dict[int, Any] | None
            Memoization dictionary for already copied objects

        Returns
        -------
        Self
            This instance
        """
        return self  # State is immutable, no need to provide an actual copy

    def __replace__(
        self,
        **kwargs: Any,
    ) -> Self:
        """
        Create a new instance with replaced attribute values.

        This internal method is used by updated() to create a new instance
        with updated values.

        Parameters
        ----------
        **kwargs : Any
            New values for attributes to replace

        Returns
        -------
        Self
            A new instance with replaced values
        """
        return self.__class__(
            **{
                **vars(self),
                **kwargs,
            }
        )
