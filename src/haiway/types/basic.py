from collections.abc import Mapping, Sequence

__all__ = (
    "BasicObject",
    "BasicValue",
    "FlatObject",
    "RawValue",
)

type RawValue = str | float | int | bool | None
type BasicValue = Mapping[str, BasicValue] | Sequence[BasicValue] | RawValue
type BasicObject = Mapping[str, BasicValue]
type FlatObject = Mapping[str, RawValue]
