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

__all__ = [
    "AttributePath",
]


class AttributePathComponent(ABC):
    @abstractmethod
    def path_str(
        self,
        current: str | None = None,
        /,
    ) -> str: ...

    @abstractmethod
    def access(
        self,
        subject: Any,
        /,
    ) -> Any: ...

    @abstractmethod
    def assigning(
        self,
        subject: Any,
        /,
        value: Any,
    ) -> Any: ...


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
        if current:
            return f"{current}.{self._name}"

        else:
            return self._name

    def access(
        self,
        subject: Any,
        /,
    ) -> Any:
        return self._access(subject)

    def assigning(
        self,
        subject: Any,
        /,
        value: Any,
    ) -> Any:
        return self._assigning(subject, value)


@final
class SequenceItemAttributePathComponent(AttributePathComponent):
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
        return f"{current or ''}[{self._index}]"

    def access(
        self,
        subject: Any,
        /,
    ) -> Any:
        return self._access(subject)

    def assigning(
        self,
        subject: Any,
        /,
        value: Any,
    ) -> Any:
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
        return f"{current or ''}[{self._key}]"

    def access(
        self,
        subject: Any,
        /,
    ) -> Any:
        return self._access(subject)

    def assigning(
        self,
        subject: Any,
        /,
        value: Any,
    ) -> Any:
        return self._assigning(subject, value)


@final
class AttributePath[Root, Attribute]:
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
    ) -> None: ...

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
        return tuple(component.path_str() for component in self.__components__)

    def __str__(self) -> str:
        path: str = ""
        for component in self.__components__:
            path = component.path_str(path)

        return path

    def __repr__(self) -> str:
        path: str = self.__root__.__name__
        for component in self.__components__:
            path = component.path_str(path)

        return path

    def __getattr__(
        self,
        name: str,
    ) -> Any:
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
        root: Root,
        /,
    ) -> Attribute: ...

    @overload
    def __call__(
        self,
        root: Root,
        /,
        updated: Attribute,
    ) -> Root: ...

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
