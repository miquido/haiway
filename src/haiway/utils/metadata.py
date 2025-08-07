import json
from collections.abc import Collection, Iterator, Mapping
from datetime import datetime
from types import EllipsisType
from typing import Any, ClassVar, Final, Self, TypeGuard, cast, final, overload
from uuid import UUID

__all__ = (
    "META_EMPTY",
    "Meta",
    "MetaTags",
    "MetaValue",
    "MetaValues",
)

type MetaValue = Mapping[str, MetaValue] | Collection[MetaValue] | str | float | int | bool | None
type MetaValues = Mapping[str, MetaValue]
type MetaTags = Collection[str]


@final
class Meta(Mapping[str, MetaValue]):
    """
    Immutable metadata container with type-safe access to common fields.

    Meta provides a structured way to store and access metadata as key-value pairs
    with built-in support for common metadata fields like identifiers, names,
    descriptions, tags, and timestamps. All values are validated to ensure they
    conform to the MetaValue type constraints.

    The class implements the Mapping protocol, allowing dict-like access while
    maintaining immutability. It provides builder-style methods (with_*) for
    creating modified copies and convenience properties for accessing standard
    metadata fields.

    Examples
    --------
    >>> meta = Meta.of({"kind": "user", "name": "John"})
    >>> meta = meta.with_tags(["active", "verified"])
    >>> print(meta.kind)  # "user"
    >>> print(meta.tags)  # ("active", "verified")
    """

    __IMMUTABLE__: ClassVar[EllipsisType] = ...

    __slots__ = ("_values",)

    def __init__(
        self,
        values: MetaValues,
        /,
    ):
        assert isinstance(values, Mapping)  # nosec: B101
        self._values: MetaValues
        object.__setattr__(
            self,
            "_values",
            values,
        )

    @overload
    @classmethod
    def of(
        cls,
        meta: Self | MetaValues | None,
        /,
    ) -> Self: ...

    @overload
    @classmethod
    def of(
        cls,
        /,
        **values: MetaValue,
    ) -> Self: ...

    @classmethod
    def of(
        cls,
        meta: Self | MetaValues | None = None,
        /,
        **values: MetaValue,
    ) -> Self:
        """
        Create a Meta instance from various input types.

        This factory method provides a flexible way to create Meta instances,
        handling None values, existing Meta instances, and raw mappings.

        Parameters
        ----------
        meta : Self | MetaValues | None
            The metadata to wrap. Can be None (returns META_EMPTY),
            an existing Meta instance (returns as-is), or a mapping
            of values to validate and wrap.

        **values: MetaValue
            Key-value pairs to be added to the result metadata.

        Returns
        -------
        Self
            A Meta instance containing the provided metadata.
        """
        if meta is None:
            if values:
                return cls({key: _validated_meta_value(value) for key, value in values.items()})

            else:
                return cast(Self, META_EMPTY)

        elif isinstance(meta, Meta):
            assert not values  # nosec: B101
            return cast(Self, meta)

        else:
            assert not values  # nosec: B101
            assert isinstance(meta, Mapping)  # nosec: B101
            return cls({key: _validated_meta_value(value) for key, value in meta.items()})

    @classmethod
    def from_mapping(
        cls,
        mapping: Mapping[str, Any],
        /,
    ) -> Self:
        return cls({key: _validated_meta_value(value) for key, value in mapping.items()})

    @classmethod
    def from_json(
        cls,
        value: str | bytes,
        /,
    ) -> Self:
        match json.loads(value):
            case {**values}:
                return cls({key: _validated_meta_value(val) for key, val in values.items()})

            case other:
                raise ValueError(f"Invalid json: {other}")

    def to_str(self) -> str:
        return self.__str__()

    def to_mapping(
        self,
    ) -> Mapping[str, Any]:
        return self._values

    def to_json(
        self,
    ) -> str:
        return json.dumps(self._values)

    @property
    def kind(self) -> str | None:
        value: MetaValue = self._values.get("kind")
        if value is None:
            return value

        if not isinstance(value, str):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for kind, expected 'str'")

        return value

    def with_kind(
        self,
        kind: str,
        /,
    ) -> Self:
        return self.__class__(
            {
                **self._values,
                "kind": kind,
            }
        )

    @overload
    def get_uuid(
        self,
        key: str,
    ) -> UUID | None: ...

    @overload
    def get_uuid(
        self,
        key: str,
        *,
        default: UUID,
    ) -> UUID: ...

    def get_uuid(
        self,
        key: str,
        *,
        default: UUID | None = None,
    ) -> UUID | None:
        value: MetaValue = self._values.get(key)
        if value is None:
            return default

        if not isinstance(value, str):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'str'")

        return UUID(value)

    def with_uuid(
        self,
        key: str,
        *,
        value: UUID,
    ) -> Self:
        return self.__class__(
            {
                **self._values,
                key: str(value),
            }
        )

    @overload
    def get_datetime(
        self,
        key: str,
    ) -> datetime | None: ...

    @overload
    def get_datetime(
        self,
        key: str,
        *,
        default: datetime,
    ) -> datetime: ...

    def get_datetime(
        self,
        key: str,
        *,
        default: datetime | None = None,
    ) -> datetime | None:
        value: MetaValue = self._values.get(key)
        if value is None:
            return default

        if not isinstance(value, str):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'str'")

        return datetime.fromisoformat(value)

    def with_datetime(
        self,
        key: str,
        *,
        value: datetime,
    ) -> Self:
        return self.__class__(
            {
                **self._values,
                key: value.isoformat(),
            }
        )

    @overload
    def get_str(
        self,
        key: str,
    ) -> str | None: ...

    @overload
    def get_str(
        self,
        key: str,
        *,
        default: str,
    ) -> str: ...

    def get_str(
        self,
        key: str,
        *,
        default: str | None = None,
    ) -> str | None:
        value: MetaValue = self._values.get(key)
        if value is None:
            return default

        if not isinstance(value, str):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'str'")

        return value

    @overload
    def get_int(
        self,
        key: str,
    ) -> int | None: ...

    @overload
    def get_int(
        self,
        key: str,
        *,
        default: int,
    ) -> int: ...

    def get_int(
        self,
        key: str,
        *,
        default: int | None = None,
    ) -> int | None:
        value: MetaValue = self._values.get(key)
        if value is None:
            return default

        if not isinstance(value, int):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'int'")

        return value

    @overload
    def get_float(
        self,
        key: str,
    ) -> float | None: ...

    @overload
    def get_float(
        self,
        key: str,
        *,
        default: float,
    ) -> float: ...

    def get_float(
        self,
        key: str,
        *,
        default: float | None = None,
    ) -> float | None:
        value: MetaValue = self._values.get(key)
        if value is None:
            return default

        if not isinstance(value, float):
            raise TypeError(
                f"Unexpected value '{type(value).__name__}' for {key}, expected 'float'"
            )

        return value

    @overload
    def get_bool(
        self,
        key: str,
    ) -> bool | None: ...

    @overload
    def get_bool(
        self,
        key: str,
        *,
        default: bool,
    ) -> bool: ...

    def get_bool(
        self,
        key: str,
        *,
        default: bool | None = None,
    ) -> bool | None:
        value: MetaValue = self._values.get(key)
        if value is None:
            return default

        if not isinstance(value, bool):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for {key}, expected 'bool'")

        return value

    @property
    def identifier(self) -> UUID | None:
        return self.get_uuid("identifier")

    def with_identifier(
        self,
        identifier: UUID,
        /,
    ) -> Self:
        return self.with_uuid(
            "identifier",
            value=identifier,
        )

    @property
    def name(self) -> str | None:
        value: MetaValue = self._values.get("name")
        if value is None:
            return value

        if not isinstance(value, str):
            raise TypeError(f"Unexpected value '{type(value).__name__}' for name, expected 'str'")

        return value

    def with_name(
        self,
        name: str,
        /,
    ) -> Self:
        return self.__class__(
            {
                **self._values,
                "name": name,
            }
        )

    @property
    def description(self) -> str | None:
        value: MetaValue = self._values.get("description")
        if value is None:
            return value

        if not isinstance(value, str):
            raise TypeError(
                f"Unexpected value '{type(value).__name__}' for description, expected 'str'"
            )

        return value

    def with_description(
        self,
        description: str,
        /,
    ) -> Self:
        return self.__class__(
            {
                **self._values,
                "description": description,
            }
        )

    @property
    def tags(self) -> MetaTags:
        match self._values.get("tags"):
            case [*tags]:
                return tuple(tag for tag in tags if _validate_tag(tag))

            case _:
                return ()

    def with_tags(
        self,
        tags: MetaTags,
        /,
    ) -> Self:
        match self._values.get("tags"):
            case [*current_tags]:
                return self.__class__(
                    {
                        **self._values,
                        "tags": (
                            *current_tags,
                            *(
                                _validated_meta_value(tag)
                                for tag in tags
                                if tag not in current_tags
                            ),
                        ),
                    }
                )

            case _:
                return self.__class__({**self._values, "tags": tags})

    def has_tags(
        self,
        tags: MetaTags,
        /,
    ) -> bool:
        match self._values.get("tags"):
            case [*meta_tags]:
                return all(tag in meta_tags for tag in tags)

            case _:
                return False

    @property
    def created(self) -> datetime | None:
        value: MetaValue = self._values.get("created")
        if value is None:
            return value

        if not isinstance(value, str):
            raise TypeError(
                f"Unexpected value '{type(value).__name__}' for created, expected 'str'"
            )

        return datetime.fromisoformat(value)

    def with_created(
        self,
        created: datetime,
        /,
    ) -> Self:
        return self.__class__(
            {
                **self._values,
                "created": created.isoformat(),
            }
        )

    @property
    def last_updated(self) -> datetime | None:
        value: MetaValue = self._values.get("last_updated")
        if value is None:
            return value

        if not isinstance(value, str):
            raise TypeError(
                f"Unexpected value '{type(value).__name__}' for last_updated, expected 'str'"
            )

        return datetime.fromisoformat(value)

    def with_last_updated(
        self,
        last_updated: datetime,
        /,
    ) -> Self:
        return self.__class__(
            {
                **self._values,
                "last_updated": last_updated.isoformat(),
            }
        )

    def merged_with(
        self,
        values: Self | MetaValues | None,
        /,
    ) -> Self:
        if not values:
            return self  # do not make a copy when nothing will be updated

        return self.__class__(
            {
                **self._values,  # already validated
                **{key: _validated_meta_value(value) for key, value in values.items()},
            }
        )

    def excluding(
        self,
        *excluded: str,
    ) -> Self:
        if not excluded:
            return self

        excluded_set: set[str] = set(excluded)
        return self.__class__(
            {key: value for key, value in self._values.items() if key not in excluded_set}
        )

    def updated(
        self,
        **values: MetaValue,
    ) -> Self:
        return self.__replace__(**values)

    def __replace__(
        self,
        **values: Any,
    ) -> Self:
        return self.merged_with(values)

    def __bool__(self) -> bool:
        return bool(self._values)

    def __contains__(
        self,
        element: Any,
    ) -> bool:
        return element in self._values

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

    def __setitem__(
        self,
        key: str,
        value: Any,
    ) -> MetaValue:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" item - '{key}' cannot be modified"
        )

    def __delitem__(
        self,
        key: str,
    ) -> MetaValue:
        raise AttributeError(
            f"Can't modify immutable {self.__class__.__qualname__},"
            f" item - '{key}' cannot be deleted"
        )

    def __getitem__(
        self,
        key: str,
    ) -> MetaValue:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __copy__(self) -> Self:
        return self  # Metadata is immutable, no need to provide an actual copy

    def __deepcopy__(
        self,
        memo: dict[int, Any] | None,
    ) -> Self:
        return self  # Metadata is immutable, no need to provide an actual copy


def _validated_meta_value(value: Any) -> MetaValue:  # noqa: PLR0911
    match value:
        case None:
            return value

        case str():
            return value

        case int():
            return value

        case float():
            return value

        case bool():
            return value

        case [*values]:
            return tuple(_validated_meta_value(value) for value in values)

        case {**values}:
            return {key: _validated_meta_value(value) for key, value in values.items()}

        case other:
            raise TypeError(f"Invalid Meta value: '{type(other).__name__}'")


def _validate_tag(tag: Any) -> TypeGuard[str]:
    if not isinstance(tag, str):
        raise TypeError(f"Unexpected value '{type(tag).__name__}' for tag, expected 'str'")

    return True


META_EMPTY: Final[Meta] = Meta({})
