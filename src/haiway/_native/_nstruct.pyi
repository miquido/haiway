from typing import Any, Self

SPEC_ANY: int
SPEC_NONE: int
SPEC_MISSING: int
SPEC_LITERAL: int
SPEC_BOOL: int
SPEC_INT: int
SPEC_FLOAT: int
SPEC_STR: int
SPEC_BYTES: int
SPEC_UUID: int
SPEC_DATETIME: int
SPEC_DATE: int
SPEC_TIME: int
SPEC_TIMEDELTA: int
SPEC_PATH: int
SPEC_STR_ENUM: int
SPEC_INT_ENUM: int
SPEC_TUPLE: int
SPEC_SEQUENCE: int
SPEC_SET: int
SPEC_MAPPING: int
SPEC_META: int
SPEC_TYPED_DICT: int
SPEC_STATE: int
SPEC_CUSTOM_STATE: int
SPEC_CUSTOM: int
SPEC_FUNCTION: int
SPEC_UNION: int
SPEC_VALIDABLE: int
SPEC_ALIAS: int

class StructMeta(type): ...

class Struct(metaclass=StructMeta):
    def __new__(cls, *args: Any, **kwargs: Any) -> Self: ...
    def __init__(self, **kwargs: Any) -> None: ...
    def __replace__(self, **kwargs: Any) -> Self: ...
    def __deepcopy__(self, memo: dict[int, Any] | None, /) -> Self: ...
    def updating(self, **kwargs: Any) -> Self: ...

class Validated(Struct):
    @classmethod
    def validate(cls, value: Any, /) -> Self: ...

class Spec: ...

def compile_spec(description: tuple[Any, ...], /) -> Spec: ...
def signature_fits(
    parameters: tuple[tuple[str | None, int, bool], ...],
    expected: tuple[tuple[str | None, int, bool], ...],
    extensible: bool,
    /,
) -> bool: ...
def validate(spec: Spec, value: Any, /) -> Any: ...
def check(spec: Spec, value: Any, /) -> bool: ...
def compile_plan(
    cls: type[Any],
    plan: tuple[Any, ...],
    owned: tuple[str, ...],
    reporting: bool,
    /,
) -> None: ...
def to_json(instance: Struct, /) -> str | None: ...
def from_json(cls: type[Struct], text: str | bytes, array: bool, /) -> Any: ...
def basic_object(instance: Struct, strict: bool, path: tuple[str, ...], /) -> Any: ...
def to_mapping(instance: Struct, recursive: bool, /) -> dict[str, Any]: ...

# the hooks are read by name - `haiway.attributes.native` declares what each holds
def configure(validation: object, conversion: object, types: object, /) -> None: ...
