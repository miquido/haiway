from collections.abc import Iterable, Mapping, Sequence, Set
from typing import Any, TypeIs

__all__ = (
    "is_bytes_like",
    "is_iterable",
    "is_mapping",
    "is_sequence",
    "is_set",
)

# `isinstance` against a generic abstract class narrows to its unknown parameters - these
# narrow a value of unknown type to the class over `Any`, what it is checked as


def is_mapping(value: object, /) -> TypeIs[Mapping[Any, Any]]:
    return isinstance(value, Mapping)


def is_iterable(value: object, /) -> TypeIs[Iterable[Any]]:
    return isinstance(value, Iterable)


def is_sequence(value: object, /) -> TypeIs[Sequence[Any]]:
    return isinstance(value, Sequence)


def is_set(value: object, /) -> TypeIs[Set[Any]]:
    return isinstance(value, Set)


def is_bytes_like(value: object, /) -> TypeIs[bytes | bytearray | memoryview[Any]]:
    return isinstance(value, bytes | bytearray | memoryview)
