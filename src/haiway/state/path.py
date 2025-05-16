import builtins
import types
import typing
from abc import ABC, abstractmethod
from collections import abc as collections_abc
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from copy import copy
from typing import Any, TypeAliasType, final, get_args, get_origin, overload

from haiway.types import MISSING, Missing, not_missing

__all__ = ("AttributePath",)


class AttributePathComponent(ABC):
    """
    Abstract base class for components in an attribute path.

    This class defines the interface for components that make up an attribute path,
    such as property access, sequence item access, or mapping item access.
    Each component knows how to access and update values at its position in the path.
    """

    @abstractmethod
    def path_str(
        self,
        current: str | None = None,
        /,
    ) -> str:
        """
        Convert this path component to a string representation.

        Parameters
        ----------
        current : str | None
            The current path string to append to

        Returns
        -------
        str
            String representation of the path including this component
        """

    @abstractmethod
    def access(
        self,
        subject: Any,
        /,
    ) -> Any:
        """
        Access the property value from the subject.

        Parameters
        ----------
        subject : Any
            The object to access the property from

        Returns
        -------
        Any
            The value of the property

        Raises
        ------
        AttributeError
            If the property doesn't exist on the subject
        """

    @abstractmethod
    def assigning(
        self,
        subject: Any,
        /,
        value: Any,
    ) -> Any:
        """
        Create a new object with an updated value at this path component.

        Parameters
        ----------
        subject : Any
            The original object to update
        value : Any
            The new value to assign at this path component

        Returns
        -------
        Any
            A new object with the updated value

        Raises
        ------
        TypeError
            If the subject cannot be updated with the given value
        """
        ...


@final
class PropertyAttributePathComponent(AttributePathComponent):
    __slots__ = (
        "_access",
        "_assigning",
        "_name",
    )

    def __init__[Root, Attribute](
        self,
        root: type[Root],
        *,
        attribute: type[Attribute],
        name: str,
    ) -> None:
        root_origin: Any = _unaliased_origin(root)
        attribute_origin: Any = _unaliased_origin(attribute)

        def access(
            subject: Root,
            /,
        ) -> Attribute:
            assert isinstance(subject, root_origin), (  # nosec: B101
                f"AttributePath used on unexpected subject of"
                f" '{type(subject).__name__}' instead of '{root.__name__}' for '{name}'"
            )

            assert hasattr(subject, name), (  # nosec: B101
                f"AttributePath pointing to attribute '{name}'"
                f" which is not available in subject '{type(subject).__name__}'"
            )

            resolved: Any = getattr(subject, name)

            assert isinstance(resolved, attribute_origin), (  # nosec: B101
                f"AttributePath pointing to unexpected value of"
                f" '{type(resolved).__name__}' instead of '{attribute.__name__}' for '{name}'"
            )
            return resolved

        def assigning(
            subject: Root,
            /,
            value: Attribute,
        ) -> Root:
            assert isinstance(subject, root_origin), (  # nosec: B101
                f"AttributePath used on unexpected subject of"
                f" '{type(subject).__name__}' instead of '{root.__name__}' for '{name}'"
            )

            assert hasattr(subject, name), (  # nosec: B101
                f"AttributePath pointing to attribute '{name}'"
                f" which is not available in subject '{type(subject).__name__}'"
            )

            assert isinstance(value, attribute_origin), (  # nosec: B101
                f"AttributePath assigning unexpected value of "
                f"'{type(value).__name__}' instead of '{attribute.__name__}' for '{name}'"
            )

            updated: Root
            # python 3.13 introduces __replace__, we are already implementing it for our types
            if hasattr(subject, "__replace__"):  # can't check full type here
                updated = subject.__replace__(**{name: value})  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType, reportAttributeAccessIssue]

            else:
                updated = copy(subject)
                setattr(updated, name, value)

            return updated  # pyright: ignore[reportUnknownVariableType]

        self._access: Callable[[Any], Any]
        object.__setattr__(
            self,
            "_access",
            access,
        )
        self._assigning: Callable[[Any, Any], Any]
        object.__setattr__(
            self,
            "_assigning",
            assigning,
        )
        self._name: str
        object.__setattr__(
            self,
            "_name",
            name,
        )

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

    def path_str(
        self,
        current: str | None = None,
        /,
    ) -> str:
        """
        Convert this property component to a string representation.

        Parameters
        ----------
        current : str | None
            The current path string to append to

        Returns
        -------
        str
            String representation with the property appended
        """
        if current in (None, ""):
            return self._name

        else:
            return f"{current}.{self._name}"

    def access(
        self,
        subject: Any,
        /,
    ) -> Any:
        """
        Access the property value from the subject.

        Parameters
        ----------
        subject : Any
            The object to access the property from

        Returns
        -------
        Any
            The value of the property
        """
        return self._access(subject)

    def assigning(
        self,
        subject: Any,
        /,
        value: Any,
    ) -> Any:
        """
        Create a new subject with an updated property value.

        Parameters
        ----------
        subject : Any
            The original object
        value : Any
            The new value for the property

        Returns
        -------
        Any
            A new object with the updated property value

        Raises
        ------
        TypeError
            If the subject doesn't support property updates
        """
        return self._assigning(subject, value)


@final
class SequenceItemAttributePathComponent[Owner, Value](AttributePathComponent):
    """
    Path component for accessing items in a sequence by index.

    This component represents sequence item access using index notation (seq[index])
    in an attribute path. It provides type-safe access and updates for sequence items.
    """

    __slots__ = (
        "_access",
        "_assigning",
        "_index",
    )

    def __init__[Root: Sequence[Any], Attribute](
        self,
        root: type[Root],
        *,
        attribute: type[Attribute],
        index: int,
    ) -> None:
        root_origin: Any = _unaliased_origin(root)
        attribute_origin: Any = _unaliased_origin(attribute)

        def access(
            subject: Root,
            /,
        ) -> Attribute:
            assert isinstance(subject, root_origin), (  # nosec: B101
                f"AttributePathComponent used on unexpected root of "
                f"'{type(root).__name__}' instead of '{root.__name__}' for '{index}'"
            )

            resolved: Any = subject.__getitem__(index)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

            assert isinstance(resolved, attribute_origin), (  # nosec: B101
                f"AttributePath pointing to unexpected value of "
                f"'{type(resolved).__name__}' instead of '{attribute.__name__}' for '{index}'"
            )
            return resolved

        def assigning(
            subject: Root,
            /,
            value: Attribute,
        ) -> Root:
            assert isinstance(subject, root_origin), (  # nosec: B101
                f"AttributePath used on unexpected root of "
                f"'{type(subject).__name__}' instead of '{root.__name__}' for '{index}'"
            )
            assert isinstance(value, attribute_origin), (  # nosec: B101
                f"AttributePath assigning to unexpected value of "
                f"'{type(value).__name__}' instead of '{attribute.__name__}' for '{index}'"
            )

            temp_list: list[Any] = list(subject)  # pyright: ignore[reportUnknownArgumentType]
            temp_list[index] = value
            return subject.__class__(temp_list)  # pyright: ignore[reportCallIssue, reportUnknownVariableType, reportUnknownMemberType]

        self._access: Callable[[Any], Any]
        object.__setattr__(
            self,
            "_access",
            access,
        )
        self._assigning: Callable[[Any, Any], Any]
        object.__setattr__(
            self,
            "_assigning",
            assigning,
        )
        self._index: Any
        object.__setattr__(
            self,
            "_index",
            index,
        )

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

    def path_str(
        self,
        current: str | None = None,
        /,
    ) -> str:
        """
        Convert this sequence item component to a string representation.

        Parameters
        ----------
        current : str | None
            The current path string to append to

        Returns
        -------
        str
            String representation with the sequence index appended
        """
        return f"{current or ''}[{self._index}]"

    def access(
        self,
        subject: Any,
        /,
    ) -> Any:
        """
        Access the sequence item from the subject.

        Parameters
        ----------
        subject : Any
            The sequence to access the item from

        Returns
        -------
        Any
            The value at the specified index

        Raises
        ------
        IndexError
            If the index is out of bounds
        """
        return self._access(subject)

    def assigning(
        self,
        subject: Any,
        /,
        value: Any,
    ) -> Any:
        """
        Create a new sequence with an updated item value.

        Parameters
        ----------
        subject : Any
            The original sequence
        value : Any
            The new value for the item

        Returns
        -------
        Any
            A new sequence with the updated item

        Raises
        ------
        TypeError
            If the subject doesn't support item updates
        """
        return self._assigning(subject, value)


@final
class MappingItemAttributePathComponent(AttributePathComponent):
    __slots__ = (
        "_access",
        "_assigning",
        "_key",
    )

    def __init__[Root: Mapping[Any, Any], Attribute](
        self,
        root: type[Root],
        *,
        attribute: type[Attribute],
        key: str | int,
    ) -> None:
        root_origin: Any = _unaliased_origin(root)
        attribute_origin: Any = _unaliased_origin(attribute)

        def access(
            subject: Root,
            /,
        ) -> Attribute:
            assert isinstance(subject, root_origin), (  # nosec: B101
                f"AttributePathComponent used on unexpected root of "
                f"'{type(root).__name__}' instead of '{root.__name__}' for '{key}'"
            )

            resolved: Any = subject.__getitem__(key)  # pyright: ignore[reportUnknownMemberType, reportAttributeAccessIssue]

            assert isinstance(resolved, attribute_origin), (  # nosec: B101
                f"AttributePath pointing to unexpected value of "
                f"'{type(resolved).__name__}' instead of '{attribute.__name__}' for '{key}'"
            )
            return resolved

        def assigning(
            subject: Root,
            /,
            value: Attribute,
        ) -> Root:
            assert isinstance(subject, root_origin), (  # nosec: B101
                f"AttributePath used on unexpected root of "
                f"'{type(subject).__name__}' instead of '{root.__name__}' for '{key}'"
            )
            assert isinstance(value, attribute_origin), (  # nosec: B101
                f"AttributePath assigning to unexpected value of "
                f"'{type(value).__name__}' instead of '{attribute.__name__}' for '{key}'"
            )

            temp_dict: dict[Any, Any] = dict(subject)  # pyright: ignore[reportUnknownArgumentType]
            temp_dict[key] = value
            return subject.__class__(temp_dict)  # pyright: ignore[reportCallIssue, reportUnknownVariableType, reportUnknownMemberType]

        self._access: Callable[[Any], Any]
        object.__setattr__(
            self,
            "_access",
            access,
        )
        self._assigning: Callable[[Any, Any], Any]
        object.__setattr__(
            self,
            "_assigning",
            assigning,
        )
        self._key: Any
        object.__setattr__(
            self,
            "_key",
            key,
        )

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

    def path_str(
        self,
        current: str | None = None,
        /,
    ) -> str:
        """
        Convert this mapping item component to a string representation.

        Parameters
        ----------
        current : str | None
            The current path string to append to

        Returns
        -------
        str
            String representation with the mapping key appended
        """
        return f"{current or ''}[{self._key}]"

    def access(
        self,
        subject: Any,
        /,
    ) -> Any:
        """
        Access the mapping item from the subject.

        Parameters
        ----------
        subject : Any
            The mapping to access the item from

        Returns
        -------
        Any
            The value associated with the key

        Raises
        ------
        KeyError
            If the key doesn't exist in the mapping
        """
        return self._access(subject)

    def assigning(
        self,
        subject: Any,
        /,
        value: Any,
    ) -> Any:
        """
        Create a new mapping with an updated item value.

        Parameters
        ----------
        subject : Any
            The original mapping
        value : Any
            The new value for the item

        Returns
        -------
        Any
            A new mapping with the updated item

        Raises
        ------
        TypeError
            If the subject doesn't support item updates
        """
        return self._assigning(subject, value)


@final
class AttributePath[Root, Attribute]:
    """
    Represents a path to an attribute within a nested structure.

    AttributePath enables type-safe attribute access and updates for complex
    nested structures, particularly State objects. It provides a fluent interface
    for building paths using attribute access (obj.attr) and item access (obj[key])
    syntax.

    The class is generic over two type parameters:
    - Root: The type of the root object the path starts from
    - Attribute: The type of the attribute the path points to

    AttributePaths are immutable and can be reused. When applied to different
    root objects, they will access the same nested path in each object.

    Examples
    --------
    Creating paths:
    ```python
    # Access user.name
    User._.name

    # Access users[0].address.city
    User._.users[0].address.city

    # Access data["key"]
    Data._["key"]
    ```

    Using paths:
    ```python
    # Get value
    name = User._.name(user)

    # Update value
    updated_user = user.updating(User._.name, "New Name")
    ```
    """

    __slots__ = (
        "__attribute__",
        "__components__",
        "__root__",
    )

    @overload
    def __init__(
        self,
        root: type[Root],
        /,
        *,
        attribute: type[Root],
    ) -> None: ...

    @overload
    def __init__(
        self,
        root: type[Root],
        /,
        *components: AttributePathComponent,
        attribute: type[Attribute],
    ) -> None:
        """
        Initialize a new attribute path.

        Parameters
        ----------
        root : type[Root]
            The root type this path starts from
        *components : AttributePathComponent
            Path components defining the traversal from root to attribute
        attribute : type[Attribute]
            The type of the attribute at the end of this path

        Raises
        ------
        AssertionError
            If no components are provided and root != attribute
        """

    def __init__(
        self,
        root: type[Root],
        /,
        *components: AttributePathComponent,
        attribute: type[Attribute],
    ) -> None:
        assert components or root == attribute  # nosec: B101
        self.__root__: type[Root]
        object.__setattr__(
            self,
            "__root__",
            root,
        )
        self.__attribute__: type[Attribute]
        object.__setattr__(
            self,
            "__attribute__",
            attribute,
        )
        self.__components__: tuple[AttributePathComponent, ...]
        object.__setattr__(
            self,
            "__components__",
            components,
        )

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

    @property
    def components(self) -> Sequence[str]:
        """
        Get the components of this path as strings.

        Returns
        -------
        Sequence[str]
            String representations of each path component
        """
        return tuple(component.path_str() for component in self.__components__)

    def __str__(self) -> str:
        """
        Get a string representation of this path.

        The string starts empty and builds up by appending each component.

        Returns
        -------
        str
            A string representation of the path (e.g., ".attr1.attr2[0]")
        """
        path: str = ""
        for component in self.__components__:
            path = component.path_str(path)

        return path

    def __repr__(self) -> str:
        """
        Get a detailed string representation of this path.

        Unlike __str__, this includes the root type name at the beginning.

        Returns
        -------
        str
            A detailed string representation of the path (e.g., "User.name[0]")
        """
        path: str = self.__root__.__name__
        for component in self.__components__:
            path = component.path_str(path)

        return path

    def __getattr__(
        self,
        name: str,
    ) -> Any:
        """
        Extend the path with property access to the specified attribute.

        This method is called when using dot notation (path.attribute) on an
        AttributePath instance. It creates a new AttributePath that includes
        the additional property access.

        Parameters
        ----------
        name : str
            The attribute name to access

        Returns
        -------
        AttributePath
            A new AttributePath extended with the attribute access

        Raises
        ------
        AttributeError
            If the attribute is not found or cannot be accessed
        """
        try:
            return object.__getattribute__(self, name)

        except (AttributeError, KeyError):
            pass  # continue

        assert not name.startswith(  # nosec: B101
            "_"
        ), f"Accessing private/special attribute paths ({name}) is forbidden"

        try:
            annotation: Any = self.__attribute__.__annotations__[name]

        except Exception as exc:
            raise AttributeError(
                f"Failed to prepare AttributePath caused by inaccessible"
                f" type annotation for '{name}' within '{self.__attribute__.__name__}'"
            ) from exc

        return AttributePath[Root, Any](
            self.__root__,
            *(
                *self.__components__,
                PropertyAttributePathComponent(
                    root=self.__attribute__,
                    attribute=annotation,
                    name=name,
                ),
            ),
            attribute=annotation,
        )

    def __getitem__(
        self,
        key: str | int,
    ) -> Any:
        """
        Extend the path with item access using the specified key.

        This method is called when using item access notation (path[key]) on an
        AttributePath instance. It creates a new AttributePath that includes the
        additional item access component.

        Parameters
        ----------
        key : str | int
            The key or index to access. String keys are used for mapping access
            and integer keys for sequence/tuple access.

        Returns
        -------
        AttributePath
            A new AttributePath extended with the item access component

        Raises
        ------
        TypeError
            If the key type is incompatible with the attribute type or if the
            attribute type does not support item access
        """
        """
        Extend the path with item access using the specified key.

        This method is called when using item access notation (path[key]) on an
        AttributePath instance. It creates a new AttributePath that includes the
        additional item access component.

        Parameters
        ----------
        key : str | int
            The key or index to access. String keys are used for mapping access
            and integer keys for sequence/tuple access.

        Returns
        -------
        AttributePath
            A new AttributePath extended with the item access component

        Raises
        ------
        TypeError
            If the key type is incompatible with the attribute type or if the
            attribute type does not support item access
        """
        match _unaliased_origin(self.__attribute__):
            case collections_abc.Mapping | typing.Mapping | builtins.dict:
                match get_args(_unaliased(self.__attribute__)):
                    case (builtins.str | builtins.int, element_annotation):
                        return AttributePath[Root, Any](
                            self.__root__,
                            *(
                                *self.__components__,
                                MappingItemAttributePathComponent(
                                    root=self.__attribute__,  # pyright: ignore[reportArgumentType]
                                    attribute=element_annotation,
                                    key=key,
                                ),
                            ),
                            attribute=element_annotation,
                        )

                    case other:
                        raise TypeError(
                            "Unsupported Mapping type annotation",
                            self.__attribute__.__name__,
                        )

            case builtins.tuple:
                if not isinstance(key, int):
                    raise TypeError(
                        "Unsupported tuple type annotation",
                        self.__attribute__.__name__,
                    )

                match get_args(_unaliased(self.__attribute__)):
                    case (element_annotation, builtins.Ellipsis | types.EllipsisType):
                        return AttributePath[Root, Any](
                            self.__root__,
                            *(
                                *self.__components__,
                                SequenceItemAttributePathComponent(
                                    root=self.__attribute__,  # pyright: ignore[reportArgumentType]
                                    attribute=element_annotation,
                                    index=key,
                                ),
                            ),
                            attribute=element_annotation,
                        )

                    case other:
                        return AttributePath[Root, Any](
                            self.__root__,
                            *(
                                *self.__components__,
                                SequenceItemAttributePathComponent(
                                    root=self.__attribute__,  # pyright: ignore[reportArgumentType]
                                    attribute=other[key],
                                    index=key,
                                ),
                            ),
                            attribute=other[key],
                        )

            case collections_abc.Sequence | typing.Sequence | builtins.list:
                if not isinstance(key, int):
                    raise TypeError(
                        "Unsupported Sequence type annotation",
                        self.__attribute__.__name__,
                    )

                match get_args(_unaliased(self.__attribute__)):
                    case (element_annotation,):
                        return AttributePath[Root, Any](
                            self.__root__,
                            *(
                                *self.__components__,
                                SequenceItemAttributePathComponent(
                                    root=self.__attribute__,  # pyright: ignore[reportArgumentType]
                                    attribute=element_annotation,
                                    index=key,
                                ),
                            ),
                            attribute=element_annotation,
                        )

                    case other:
                        raise TypeError(
                            "Unsupported Seqence type annotation",
                            self.__attribute__.__name__,
                        )

            case other:
                raise TypeError("Unsupported type annotation", other)

    @overload
    def __call__(
        self,
        source: Root,
        /,
    ) -> Attribute:
        """
        Access the attribute value at this path in the source object.

        This overload is used when retrieving a value without updating it.

        Parameters
        ----------
        source : Root
            The source object to access the attribute in

        Returns
        -------
        Attribute
            The attribute value at this path

        Raises
        ------
        AttributeError
            If any component in the path doesn't exist
        TypeError
            If any component in the path is of the wrong type
        """

    @overload
    def __call__(
        self,
        source: Root,
        /,
        updated: Attribute,
    ) -> Root:
        """
        Create a new root object with an updated attribute value at this path.

        This overload is used when updating a value.

        Parameters
        ----------
        source : Root
            The source object to update
        updated : Attribute
            The new value to set at this path

        Returns
        -------
        Root
            A new root object with the updated attribute value

        Raises
        ------
        AttributeError
            If any component in the path doesn't exist
        TypeError
            If any component in the path is of the wrong type
        """

    def __call__(
        self,
        root: Root,
        /,
        updated: Attribute | Missing = MISSING,
    ) -> Root | Attribute:
        assert isinstance(root, _unaliased_origin(self.__root__)), (  # nosec: B101
            f"AttributePath '{self.__repr__()}' used on unexpected root of "
            f"'{type(root).__name__}' instead of '{self.__root__.__name__}'"
        )

        if not_missing(updated):
            assert isinstance(updated, _unaliased_origin(self.__attribute__)), (  # nosec: B101
                f"AttributePath '{self.__repr__()}' assigning to unexpected value of "
                f"'{type(updated).__name__}' instead of '{self.__attribute__.__name__}'"
            )

            resolved: Any = root
            updates_stack: deque[tuple[Any, AttributePathComponent]] = deque()
            for component in self.__components__:
                updates_stack.append((resolved, component))
                resolved = component.access(resolved)

            updated_value: Any = updated
            while updates_stack:
                subject, component = updates_stack.pop()
                updated_value = component.assigning(
                    subject,
                    value=updated_value,
                )

            return updated_value

        else:
            resolved: Any = root
            for component in self.__components__:
                resolved = component.access(resolved)

            assert isinstance(resolved, _unaliased_origin(self.__attribute__)), (  # nosec: B101
                f"AttributePath '{self.__repr__()}' pointing to unexpected value of "
                f"'{type(resolved).__name__}' instead of '{self.__attribute__.__name__}'"
            )

            return resolved


def _unaliased_origin(base: type[Any]) -> type[Any]:
    match base:
        case TypeAliasType() as aliased:
            return get_origin(aliased.__value__) or aliased.__value__

        case concrete:
            return get_origin(concrete) or concrete


def _unaliased(base: type[Any]) -> type[Any]:
    match base:
        case TypeAliasType() as aliased:
            return aliased.__value__

        case concrete:
            return concrete
