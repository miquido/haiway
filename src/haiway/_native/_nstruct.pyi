from collections.abc import Callable, Mapping
from typing import Any, Protocol, Self

NOCACHE: object

K_ANY: int
K_INT: int
K_STR: int
K_FLOAT: int
K_BOOL: int
K_CALL: int
K_INSTANCE: int
K_OPTIONAL: int
K_SEQUENCE: int
K_ISINSTANCE: int
K_MAPPING: int
K_TUPLE: int
K_LITERAL: int
K_SET: int
K_UNION: int
K_FLOAT_EXACT: int
S_NONE: int
S_STR: int
S_INT: int

class ValidationHooks(Protocol):
    missing: object
    check: Callable[[type[Any], Mapping[str, Any]], None]
    report: Callable[[str, Exception], Any]
    unexpected: Callable[[type[Any], Any], Any]
    mapping: type[Any]

class ConversionHooks(Protocol):
    map: Callable[[dict[Any, Any]], Any]
    map_types: tuple[type[Any], ...]
    basic_value: Callable[[Any, bool, tuple[str, ...]], Any]
    basic_key: Callable[[Any, bool, tuple[str, ...]], Any]
    recursive: Callable[[Any], Any]

class SpellingHooks(Protocol):
    uuid_type: type[Any]
    temporal_types: tuple[type[Any], ...]
    path_type: type[Any]

class Struct:
    def __new__(cls, *args: Any, **kwargs: Any) -> Self: ...
    def __init__(self, **kwargs: Any) -> None: ...
    def __replace__(self, **kwargs: Any) -> Self: ...
    def __deepcopy__(self, memo: dict[int, Any] | None, /) -> Self: ...
    def updating(self, **kwargs: Any) -> Self: ...
    @classmethod
    def validate(cls, value: Any, /) -> Self: ...

def compile_plan(cls: type[Any], plan: tuple[Any, ...], /) -> None: ...
def member(cls: type[Any], index: int, name: str, /) -> tuple[Any, object]: ...
def to_json(instance: Struct, /) -> str | None: ...
def from_json(cls: type[Struct], text: str | bytes, array: bool, /) -> Any: ...
def basic_object(instance: Struct, strict: bool, path: tuple[str, ...], /) -> Any: ...
def to_mapping(instance: Struct, recursive: bool, /) -> dict[str, Any]: ...
def configure(
    validation: ValidationHooks,
    conversion: ConversionHooks,
    spelling: SpellingHooks,
    /,
) -> None: ...
