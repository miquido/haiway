from collections.abc import Mapping, Sequence

__all__ = ("BasicValue", "RawValue")
type RawValue = str | float | int | bool | None
type BasicValue = Mapping[str, BasicValue] | Sequence[BasicValue] | RawValue
