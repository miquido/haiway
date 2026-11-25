from typing import Any, Final, Self

SPEC_ANY: Final[int]
SPEC_NONE: Final[int]
SPEC_MISSING: Final[int]
SPEC_LITERAL: Final[int]
SPEC_BOOL: Final[int]
SPEC_INT: Final[int]
SPEC_FLOAT: Final[int]
SPEC_STR: Final[int]
SPEC_BYTES: Final[int]
SPEC_UUID: Final[int]
SPEC_DATETIME: Final[int]
SPEC_DATE: Final[int]
SPEC_TIME: Final[int]
SPEC_TIMEDELTA: Final[int]
SPEC_PATH: Final[int]
SPEC_STR_ENUM: Final[int]
SPEC_INT_ENUM: Final[int]
SPEC_TUPLE: Final[int]
SPEC_OPEN_TUPLE: Final[int]
SPEC_SEQUENCE: Final[int]
SPEC_SET: Final[int]
SPEC_MAPPING: Final[int]
SPEC_META: Final[int]
SPEC_TYPED_DICT: Final[int]
SPEC_STATE: Final[int]
SPEC_CUSTOM_STATE: Final[int]
SPEC_CUSTOM: Final[int]
SPEC_FUNCTION: Final[int]
SPEC_UNION: Final[int]
SPEC_VALIDABLE: Final[int]
SPEC_ALIAS: Final[int]

class StructMeta(type): ...

class Struct(metaclass=StructMeta):
    def __new__(cls, *args: Any, **kwargs: Any) -> Self: ...
    def __init__(self, **kwargs: Any) -> None: ...
    def __replace__(self, **kwargs: Any) -> Self: ...
    def __deepcopy__(self, memo: dict[int, Any] | None, /) -> Self: ...
    def updating(self, **kwargs: Any) -> Self: ...
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
def accepts_missing(spec: Spec, /) -> bool: ...
def key_spelling(key: Any, /) -> str | None: ...
def spelled_once(spelled: set[Any], key: Any, /) -> None: ...
def validate_instance[Instance: Struct](
    cls: type[Instance],
    value: Any,
    ignore_extra: bool,
    /,
) -> Instance: ...
def compile_plan(
    cls: type[Any],
    plan: tuple[Any, ...],
    owned: tuple[str, ...],
    /,
) -> None: ...
def to_json(instance: Struct, /) -> str | None: ...
def from_json(
    cls: type[Struct],
    text: str | bytes,
    array: bool,
    ignore_extra: bool,
    /,
) -> Any: ...
def validates_itself(cls: type[Any], /) -> bool: ...
def basic_object(instance: Struct, strict: bool, path: tuple[str, ...], /) -> Any: ...
def to_mapping(instance: Struct, recursive: bool, /) -> dict[str, Any]: ...

# the hooks are read by name - `haiway.attributes.native` declares what each holds
def configure(validation: object, conversion: object, types: object, /) -> None: ...
