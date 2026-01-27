import json
import typing
from collections.abc import (
    Iterable,
    Mapping,
    MutableMapping,
    MutableSequence,
    MutableSet,
    Sequence,
    Set,
)
from copy import deepcopy
from dataclasses import fields, is_dataclass
from types import GenericAlias
from typing import (
    Any,
    ClassVar,
    Generic,
    Literal,
    NoReturn,
    Self,
    TypeVar,
    cast,
    dataclass_transform,
    final,
    overload,
)

from haiway.attributes.annotations import ObjectAttribute, resolve_self_attribute
from haiway.attributes.attribute import Attribute
from haiway.attributes.coding import AttributesJSONEncoder
from haiway.attributes.path import AttributePath
from haiway.attributes.validation import ValidationContext
from haiway.types import (
    MISSING,
    Default,
    DefaultValue,
    Missing,
    TypeSpecification,
    not_missing,
)

__all__ = ("State",)


@dataclass_transform(
    kw_only_default=True,
    frozen_default=True,
    field_specifiers=(Default,),
)
class StateMeta(type):
    """
    Metaclass for State classes that manages attribute definitions and validation.

    This metaclass is responsible for:
    - Processing attribute annotations and defaults
    - Building ``Attribute`` entries from resolved ``AttributeAnnotation`` metadata
    - Setting up validation for attributes
    - Managing generic type parameters and specialization
    - Creating immutable class instances

    The dataclass_transform decorator allows State classes to be treated
    like dataclasses by static type checkers while using custom initialization
    and validation logic.
    """

    __SELF_ATTRIBUTE__: ObjectAttribute
    __TYPE_PARAMETERS__: Mapping[str, Any] | None
    __SPECIFICATION__: TypeSpecification
    __FIELDS__: Sequence[Attribute]
    __ALLOWED_FIELDS__: Set[str]
    __SERIALIZABLE__: bool
    __slots__: tuple[str, ...]

    def __new__(  # noqa: C901, PLR0912
        mcs,
        /,
        name: str,
        bases: tuple[type, ...],
        namespace: dict[str, Any],
        type_parameters: dict[str, Any] | None = None,
        serializable: bool = False,
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

        cls.__SELF_ATTRIBUTE__ = self_attribute  # pyright: ignore[reportConstantRedefinition]
        cls.__TYPE_PARAMETERS__ = type_parameters  # pyright: ignore[reportConstantRedefinition]
        cls._ = AttributePath(cls, attribute=cls)  # pyright: ignore[reportCallIssue, reportUnknownMemberType, reportAttributeAccessIssue]

        if not bases:  # handle base class - no fields specified
            assert not type_parameters  # nosec: B101
            cls.__SPECIFICATION__ = {  # pyright: ignore[reportConstantRedefinition]
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            }
            cls.__FIELDS__ = ()  # pyright: ignore[reportAttributeAccessIssue, reportConstantRedefinition]
            cls.__ALLOWED_FIELDS__ = frozenset()  # pyright: ignore[reportConstantRedefinition]
            cls.__SERIALIZABLE__ = True  # pyright: ignore[reportConstantRedefinition]

            return cls  # early exit - base class

        specification_fields: MutableMapping[str, TypeSpecification] | None = {}
        required_fields: MutableSequence[str] = []
        allowed_fields: MutableSet[str] = set()
        fields: MutableSequence[Attribute] = []
        for key, attribute in self_attribute.attributes.items():
            field: Attribute = Attribute(
                name=key,
                annotation=attribute,
                default=_resolve_default(getattr(cls, key, MISSING)),
            )
            assert key not in allowed_fields  # nosec: B101
            allowed_fields.add(key)
            if attribute.alias:
                assert attribute.alias not in allowed_fields  # nosec: B101
                allowed_fields.add(attribute.alias)

            fields.append(field)

            if specification_fields is None:
                continue  # skip specification if it already failed

            if field.specification is None:
                # there will be no specification at all
                if field.required:
                    specification_fields = None

                # else continue skipping this field

            elif field.alias is not None:
                specification_fields[field.alias] = field.specification
                if field.required:
                    required_fields.append(field.alias)

            else:
                specification_fields[field.name] = field.specification
                if field.required:
                    required_fields.append(field.name)

        if specification_fields is not None:  # it is technically not serializable otherwise
            cls.__SPECIFICATION__ = {  # pyright: ignore[reportAttributeAccessIssue, reportConstantRedefinition]
                "type": "object",
                "properties": specification_fields,
                "required": required_fields,
                "additionalProperties": False,
            }
            cls.__SERIALIZABLE__ = True  # pyright: ignore[reportConstantRedefinition]

        elif serializable:
            raise TypeError(f"{cls.__name__} requires serialization but cannot produce json schema")

        else:  # no specification
            cls.__SERIALIZABLE__ = False  # pyright: ignore[reportConstantRedefinition]
            cls.__SPECIFICATION__ = _no_specification  # pyright: ignore[reportAttributeAccessIssue, reportConstantRedefinition]

        cls.__FIELDS__ = tuple(fields)  # pyright: ignore[reportConstantRedefinition]
        cls.__ALLOWED_FIELDS__ = frozenset(allowed_fields)  # pyright: ignore[reportConstantRedefinition]
        cls.__slots__ = tuple(  # pyright: ignore[reportAttributeAccessIssue]
            field.name for field in fields
        )
        cls.__match_args__ = cls.__slots__  # pyright: ignore[reportAttributeAccessIssue]

        return cls

    def validate(
        cls,
        value: Any,
    ) -> Any: ...

    def __instancecheck__(
        self,
        instance: Any,
    ) -> bool:
        instance_type: type[Any] = type(instance)  # pyright: ignore[reportUnknownVariableType]
        if not self.__subclasscheck__(instance_type):
            return False

        if hasattr(self, "__origin__") or hasattr(instance_type, "__origin__"):
            return all(
                field.annotation.check(getattr(instance, field.name)) for field in self.__FIELDS__
            )

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


def _resolve_default(
    value: DefaultValue | Any | Missing,
) -> DefaultValue:
    if isinstance(value, DefaultValue):
        return value

    return DefaultValue(
        default=value,
        default_factory=MISSING,
        env=MISSING,
    )


@final
class _NoSpecification:
    __slots__ = ()

    def __get__(self, instance: object, owner: type[object]) -> NoReturn:
        raise TypeError(f"{owner.__name__} cannot be represented using json schema")


_no_specification: _NoSpecification = _NoSpecification()

_types_cache: MutableMapping[
    tuple[
        Any,
        tuple[Any, ...],
    ],
    Any,
] = {}


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
    updated_user = user.updating(age=31)
    ```
    """

    _: ClassVar[Self]

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
        if isinstance(value, cls):
            return value

        elif isinstance(value, Mapping | typing.Mapping):
            for key in cast(Mapping[Any, Any], value.keys()):
                if key not in cls.__ALLOWED_FIELDS__:
                    raise TypeError(f"Unexpected attribute '{key}' for {cls.__name__}")

            for field in cls.__FIELDS__:
                if field.alias is None:
                    continue

                if field.alias in value and field.name in value:
                    raise TypeError(
                        f"Duplicate attribute '{field.name}'"
                        f" with alias '{field.alias}' for {cls.__name__}"
                    )

            return cls(**value)

        else:
            raise TypeError(f"'{value}' is not matching expected type of '{cls}'")

    @classmethod
    def from_mapping(
        cls,
        value: Mapping[str, Any],
        /,
    ) -> Self:
        """
        Build an instance from a mapping of attribute values.

        Parameters
        ----------
        value : Mapping[str, Any]
            Mapping containing attribute names or aliases and their values.

        Returns
        -------
        Self
            New instance constructed from the provided mapping.
        """
        return cls.validate(value)

    @overload
    @classmethod
    def json_schema(
        cls,
        *,
        indent: int | None = None,
        required: Literal[True] = True,
    ) -> str: ...

    @overload
    @classmethod
    def json_schema(
        cls,
        *,
        indent: int | None = None,
        required: Literal[False],
    ) -> str | None: ...

    @classmethod
    def json_schema(
        cls,
        *,
        indent: int | None = None,
        required: bool = True,
    ) -> str | None:
        """
        Render this State's JSON Schema definition.

        Parameters
        ----------
        indent : int | None, optional
            Indentation passed to ``json.dumps`` for pretty-printing.
        required : bool, default=False
            When ``True``, raises if the class has no specification.

        Returns
        -------
        str | None
            JSON Schema string when available; ``None`` if no schema is defined
            and ``required`` is ``False``.

        Raises
        ------
        TypeError
            If ``required`` is ``True`` but the class does not declare a schema.
        """
        if cls.__SERIALIZABLE__:
            return json.dumps(
                cls.__SPECIFICATION__,
                indent=indent,
            )

        elif required:
            raise TypeError(f"{cls.__name__} cannot be represented using json schema")

        return None

    @classmethod
    def from_json(
        cls,
        value: str | bytes,
        /,
        decoder: type[json.JSONDecoder] = json.JSONDecoder,
    ) -> Self:
        """
        Deserialize an instance from a JSON object payload.

        Parameters
        ----------
        value : str | bytes
            JSON payload representing a single instance.
        decoder : type[json.JSONDecoder], default=json.JSONDecoder
            Decoder class used by ``json.loads``.

        Returns
        -------
        Self
            Instance built from the decoded payload.

        Raises
        ------
        ValueError
            If the payload cannot be decoded or does not match the schema.
        """
        try:
            return cls.validate(
                json.loads(
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
        """
        Deserialize a sequence of instances from a JSON array payload.

        Parameters
        ----------
        value : str | bytes
            JSON payload representing an array of objects.
        decoder : type[json.JSONDecoder], default=json.JSONDecoder
            Decoder class used by ``json.loads``.

        Returns
        -------
        Sequence[Self]
            Tuple of instances decoded from the array payload.

        Raises
        ------
        ValueError
            If decoding fails or the payload is not an array of valid objects.
        """
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
                    return tuple(cls.validate(element) for element in elements)

                except Exception as exc:
                    raise ValueError(
                        f"Failed to decode {cls.__name__} from json array: {exc}"
                    ) from exc

            case _:
                raise ValueError("Provided json is not an array!")

    def to_json(
        self,
        indent: int | None = None,
        encoder_class: type[json.JSONEncoder] = AttributesJSONEncoder,
    ) -> str:
        """
        Serialize this instance to a JSON string.

        Parameters
        ----------
        indent : int | None, optional
            Indentation passed to ``json.dumps`` for pretty-printing.
        encoder_class : type[json.JSONEncoder], default=StateJSONEncoder
            Encoder class responsible for encoding custom types.

        Returns
        -------
        str
            JSON representation of this instance.

        Raises
        ------
        ValueError
            If encoding fails.
        """
        mapping: Mapping[str, Any] = self.to_mapping(recursive=True)
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
                    field.validate_from(kwargs),
                )

    def updating(
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
        recursive: bool = True,
    ) -> Mapping[str, Any]:
        """
        Convert this instance to a mapping of attribute names to values.

        Parameters
        ----------
        recursive : bool, default=True
            If True, nested instances are also converted to mappings

        Returns
        -------
        Mapping[str, Any]
            A mapping of attribute names to values
        """
        dict_result: dict[str, Any] = {}
        if recursive:
            for field in self.__FIELDS__:
                key: str = field.alias if field.alias is not None else field.name
                value: Any | Missing = getattr(self, field.name, MISSING)

                if not_missing(value):
                    dict_result[key] = _recursive_mapping(value)

        else:
            for field in self.__FIELDS__:
                key: str = field.alias if field.alias is not None else field.name
                value: Any | Missing = getattr(self, field.name, MISSING)
                if not_missing(value):
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
        attributes: str = ", ".join(
            [
                f"{field.alias or field.name}: {getattr(self, field.name)}"
                for field in self.__class__.__FIELDS__
            ]
        )
        return f"{self.__class__.__name__}({attributes})"

    def __repr__(self) -> str:
        """
        Return the canonical representation of this instance.

        Returns
        -------
        str
            ``repr`` string mirroring ``__str__`` for readability.
        """
        attributes: str = ", ".join(f"{name}: {getattr(self, name)}" for name in self.__slots__)
        return f"{self.__class__.__name__}({attributes})"

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
        """
        Compute a hash value for this immutable instance.

        Returns
        -------
        int
            Hash derived from non-missing attribute values.
        """
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
    ) -> NoReturn:
        """
        Disallow attribute assignment to preserve immutability.

        Parameters
        ----------
        name : str
            Attribute name being set.
        value : Any
            Incoming value (unused).

        Raises
        ------
        AttributeError
            Always raised to signal immutability.
        """
        raise AttributeError(
            f"Can't modify immutable state {self.__class__.__qualname__},"
            f" attribute - '{name}' cannot be modified"
        )

    def __delattr__(
        self,
        name: str,
    ) -> NoReturn:
        """
        Disallow attribute deletion to preserve immutability.

        Parameters
        ----------
        name : str
            Attribute name being deleted.

        Raises
        ------
        AttributeError
            Always raised to signal immutability.
        """
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

        This internal method is used by updating() to create a new instance
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
        if not kwargs:
            return self  # do not make a copy when nothing will be updated

        fields: Sequence[Attribute] = self.__class__.__FIELDS__
        alias_to_name: dict[str, str] = {
            field.alias if field.alias is not None else field.name: field.name for field in fields
        }
        valid_keys: set[str] = set(alias_to_name.keys()) | set(alias_to_name.values())

        if kwargs.keys().isdisjoint(valid_keys):
            return self  # do not make a copy when nothing will be updated

        canonical_updates: dict[str, Any] = {}
        for key, value in kwargs.items():
            if key in valid_keys:
                canonical_updates[alias_to_name.get(key, key)] = value

        if not canonical_updates:
            return self

        updated: Self = object.__new__(self.__class__)
        for field in fields:
            update: Any | Missing = canonical_updates.get(field.name, MISSING)
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


def _recursive_mapping(  # noqa: PLR0911
    value: Any,
) -> Any:
    if isinstance(value, str | bytes | float | int | bool | None):
        return value

    elif isinstance(value, State):
        return value.to_mapping(recursive=True)

    elif is_dataclass(value):
        return {
            field.name: _recursive_mapping(getattr(value, field.name)) for field in fields(value)
        }

    elif isinstance(value, Mapping | typing.Mapping):
        return {key: _recursive_mapping(element) for key, element in value.items()}  # pyright: ignore[reportUnknownVariableType]

    elif isinstance(value, Iterable | typing.Iterable):
        return [_recursive_mapping(element) for element in value]  # pyright: ignore[reportUnknownVariableType]

    elif hasattr(value, "to_mapping") and callable(value.to_mapping):
        return value.to_mapping()

    else:
        return deepcopy(value)
