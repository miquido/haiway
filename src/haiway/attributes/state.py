import json
from collections.abc import Mapping, MutableSequence, Sequence
from types import EllipsisType, GenericAlias
from typing import (
    Any,
    ClassVar,
    Generic,
    Self,
    TypeVar,
    cast,
    dataclass_transform,
)
from weakref import WeakValueDictionary

from haiway.attributes.annotations import (
    AttributeAnnotation,
    ObjectAttribute,
    resolve_self_attribute,
)
from haiway.attributes.coding import StateJSONEncoder
from haiway.attributes.path import AttributePath
from haiway.attributes.validation import ValidationContext, ValidationError
from haiway.types import MISSING, DefaultValue, Missing, not_missing
from haiway.types.immutable import Immutable

__all__ = ("State",)


class StateField[Value](Immutable):
    """
    Represents a field in a State class with its metadata.

    This class holds information about a specific attribute of a State class,
    including its name, typing, default value, and validation rules.
    It is used internally by the State metaclass to manage state attributes
    and ensure their immutability and type safety.
    """

    name: str
    annotation: AttributeAnnotation
    default: DefaultValue[Value]

    def validate(
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
        if value is MISSING:
            return self.annotation.validate(self.default())

        else:
            return self.annotation.validate(value)


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
    - Building ``StateField`` entries from resolved ``AttributeAnnotation`` metadata
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

        self_attribute: ObjectAttribute = resolve_self_attribute(
            cls,
            parameters=type_parameters or {},
        )
        fields: MutableSequence[StateField] = []
        for key, attribute in self_attribute.attributes.items():
            default: Any = getattr(cls, key, MISSING)
            fields.append(
                StateField(
                    name=key,
                    annotation=attribute,
                    default=_resolve_default(default),
                )
            )

        cls.__SELF_ATTRIBUTE__ = self_attribute  # pyright: ignore[reportAttributeAccessIssue]
        cls.__TYPE_PARAMETERS__ = type_parameters  # pyright: ignore[reportAttributeAccessIssue]
        cls.__FIELDS__ = tuple(fields)  # pyright: ignore[reportAttributeAccessIssue]
        cls.__slots__ = tuple(field.name for field in fields)  # pyright: ignore[reportAttributeAccessIssue]
        cls.__match_args__ = cls.__slots__  # pyright: ignore[reportAttributeAccessIssue]
        cls._ = AttributePath(cls, attribute=cls)  # pyright: ignore[reportCallIssue, reportUnknownMemberType, reportAttributeAccessIssue]

        return cls

    def validate(
        cls,
        value: Any,
    ) -> Any: ...

    def __instancecheck__(
        self,
        instance: Any,
    ) -> bool:
        instance_type: type[Any] = type(instance)
        if not self.__subclasscheck__(instance_type):
            return False

        if hasattr(self, "__origin__") or hasattr(instance_type, "__origin__"):
            try:  # TODO: find a better way to validate partially typed instances
                self(**vars(instance))

            except ValidationError:
                return False

        return True

    def __subclasscheck__(
        self,
        subclass: type[Any],
    ) -> bool:
        if self is subclass:
            return True

        self_origin: type[Any] = getattr(self, "__origin__", self)

        # Handle case where we're checking not parameterized type
        if self_origin is self:
            return type.__subclasscheck__(self, subclass)

        subclass_origin: type[Any] = getattr(subclass, "__origin__", subclass)

        # Both must be based on the same generic class
        if self_origin is not subclass_origin:
            return False

        return self._check_type_parameters(subclass)

    def _check_type_parameters(
        self,
        subclass: type[Any],
    ) -> bool:
        self_args: Sequence[Any] | None = getattr(
            self,
            "__args__",
            None,
        )
        subclass_args: Sequence[Any] | None = getattr(
            subclass,
            "__args__",
            None,
        )

        if self_args is None and subclass_args is None:
            return True

        if self_args is None:
            assert subclass_args is not None  # nosec: B101
            self_args = tuple(Any for _ in subclass_args)

        elif subclass_args is None:
            assert self_args is not None  # nosec: B101
            subclass_args = tuple(Any for _ in self_args)

        # Check if the type parameters are compatible (covariant)
        for self_arg, subclass_arg in zip(
            self_args,
            subclass_args,
            strict=True,
        ):
            if self_arg is Any or subclass_arg is Any:
                continue

            # For covariance: GenericState[Child] should be subclass of GenericState[Parent]
            # This means subclass_param should be a subclass of self_param
            if not issubclass(subclass_arg, self_arg):
                return False

        return True


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
        env=MISSING,
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
    - Validated: Custom validation rules can be applied to attributes (sequences and
      sets are coerced to immutable containers; mappings remain regular dicts)

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
    """

    _: ClassVar[Self]
    __IMMUTABLE__: ClassVar[EllipsisType] = ...
    __TYPE_PARAMETERS__: ClassVar[Mapping[str, Any] | None] = None
    __SELF_ATTRIBUTE__: ClassVar[ObjectAttribute]
    __FIELDS__: ClassVar[Sequence[StateField]]

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
        parametrized_type.__args__ = type_arguments  # pyright: ignore[reportAttributeAccessIssue]
        _types_cache[(cls, type_arguments)] = parametrized_type
        return parametrized_type

    @classmethod
    def validate(
        cls,
        value: Any,
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
                raise TypeError(f"'{value}' is not matching expected type of '{cls}'")

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        /,
    ) -> Self:
        return cls(**value)

    @classmethod
    def from_json(
        cls,
        value: str | bytes,
        /,
        decoder: type[json.JSONDecoder] = json.JSONDecoder,
    ) -> Self:
        try:
            return cls(
                **json.loads(
                    value,
                    cls=decoder,
                )
            )

        except Exception as exc:
            raise ValueError(f"Failed to decode {cls.__name__} from json: {exc}") from exc

    @classmethod
    def from_json_array(
        cls,
        value: str | bytes,
        /,
        decoder: type[json.JSONDecoder] = json.JSONDecoder,
    ) -> Sequence[Self]:
        payload: Any
        try:
            payload = json.loads(
                value,
                cls=decoder,
            )

        except Exception as exc:
            raise ValueError(f"Failed to decode {cls.__name__} from json: {exc}") from exc

        match payload:
            case [*elements]:
                try:
                    return tuple(cls(**element) for element in elements)

                except Exception as exc:
                    raise ValueError(
                        f"Failed to decode {cls.__name__} from json array: {exc}"
                    ) from exc

            case _:
                raise ValueError("Provided json is not an array!")

    def to_json(
        self,
        indent: int | None = None,
        encoder_class: type[json.JSONEncoder] = StateJSONEncoder,
    ) -> str:
        mapping: Mapping[str, Any] = self.to_mapping()
        try:
            return json.dumps(
                mapping,
                indent=indent,
                cls=encoder_class,
            )

        except Exception as exc:
            raise ValueError(
                f"Failed to encode {self.__class__.__name__} to json:\n{mapping}"
            ) from exc

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
        for field in self.__FIELDS__:
            with ValidationContext.scope(f".{field.name}"):
                object.__setattr__(
                    self,  # pyright: ignore[reportUnknownArgumentType]
                    field.name,
                    field.validate(
                        kwargs.get(
                            field.name,
                            MISSING,
                        )
                    ),
                )

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
        for field in self.__FIELDS__:
            value: Any | Missing = getattr(self, field.name, MISSING)
            if recursive and isinstance(value, State):
                dict_result[field.name] = value.to_mapping(recursive=recursive)

            elif not_missing(value):
                dict_result[field.name] = value

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
            getattr(self, field.name, MISSING) == getattr(other, field.name, MISSING)
            for field in self.__FIELDS__
        )

    def __hash__(self) -> int:
        hash_values: list[int] = []
        for field in self.__FIELDS__:
            value: Any = getattr(self, field.name, MISSING)

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
        if not kwargs or kwargs.keys().isdisjoint(getattr(self, "__slots__", ())):
            return self  # do not make a copy when nothing will be updated

        updated: Self = object.__new__(self.__class__)
        for field in self.__class__.__FIELDS__:
            update: Any | Missing = kwargs.get(field.name, MISSING)
            if update is MISSING:  # reuse missing elements
                object.__setattr__(
                    updated,
                    field.name,
                    getattr(self, field.name),
                )

            else:  # and validate updates
                with ValidationContext.scope(f".{field.name}"):
                    object.__setattr__(
                        updated,
                        field.name,
                        field.validate(update),
                    )

        return updated
