import base64
from collections.abc import Generator, Iterable, Mapping, Sequence
from datetime import date, datetime, time
from pathlib import Path
from typing import Any
from uuid import UUID

from haiway.types.map import Map

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


# Conversion of a value to its basic - json compatible - representation. Each
# leaf is encoded to the spelling the matching attribute validation reads back,
# so the result of encoding can be validated into the type it came from:
#
#   str/int/float/bool/None     unchanged
#   StrEnum/IntEnum             unchanged - they ride the str/int guards
#   UUID                        str(value)
#   datetime/date/time          value.isoformat()
#   Path                        value.as_posix()
#   bytes-like                  base64 encoded str
#
# Anything else - a plain `Enum`, a `Decimal`, `MISSING` - has no spelling the
# validation would read back. Encoding it would trade a loud failure here for a
# silent one on the way in, so `strict` rejects it instead.
#
# `State` is not resolved here - `types` can't import `attributes`. Its own
# conversion covers the structured values, this one covers the leaves, the keys
# and the plain collections of `Map` and `Meta`.
def basic_value(  # noqa: PLR0911
    value: Any,
    /,
    *,
    strict: bool = True,
    path: tuple[str, ...] = (),
) -> BasicValue:
    # `str` first - `StrEnum` rides this guard, and neither may reach the
    # iterable branch below
    if value is None or isinstance(value, str):
        return value

    # `bool` rides the `int` guard, `IntEnum` rides it as well
    if isinstance(value, bool | int | float):
        return value

    # before the iterable branch - `bytes` is a sequence of ints
    if isinstance(value, bytes | bytearray | memoryview):
        # base64, the spelling `BytesAttribute` validation reads back
        return base64.b64encode(
            value  # pyright: ignore[reportUnknownArgumentType]
        ).decode("utf-8")

    if isinstance(value, UUID):
        return str(value)

    # `datetime` before `date` - it is a subclass of it
    if isinstance(value, datetime | date | time):
        return value.isoformat()

    if isinstance(value, Path):
        return value.as_posix()

    if isinstance(value, Mapping):
        return basic_object(
            value,  # pyright: ignore[reportUnknownArgumentType]
            strict=strict,
            path=path,
        )

    if isinstance(value, Iterable):
        return tuple(
            basic_value(
                element,  # pyright: ignore[reportUnknownArgumentType]
                strict=strict,
                path=(*path, f"[{index}]"),
            )
            for index, element in enumerate(value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
        )

    if strict:
        raise TypeError(
            f"Can't convert '{type(value).__name__}' to a basic value{_reported_path(path)}"
        )

    # left to the caller - i.e. a custom json encoder handling the remainder
    unsupported: Any = value
    return unsupported


def basic_object(
    mapping: Mapping[Any, Any],
    /,
    *,
    strict: bool = True,
    path: tuple[str, ...] = (),
) -> BasicObject:
    return Map(_basic_items(mapping, strict=strict, path=path))


# Keys are converted through the same table and then spelled the way json spells
# them - which is also what `json.dumps` does to them on its own, so an already
# encodable mapping keeps the output it had.
def basic_key(
    key: Any,
    /,
    *,
    strict: bool = True,
    path: tuple[str, ...] = (),
) -> Any:
    # never strict - an unsupported key is reported as a key below instead of as
    # a value from within the conversion
    match basic_value(key, strict=False, path=path):
        case str() as basic:
            # a `datetime` key keeps the ISO 'T' this way, where `str()` of it
            # would spell a space
            return basic

        case None:
            return "null"

        # `bool` before `int` - it is a subclass of it, and `str()` would spell
        # it "True"/"False" instead of the "true"/"false" of json
        case bool() as basic:
            return "true" if basic else "false"

        case int() | float() as basic:
            return str(basic)

        case other:
            if strict:
                raise TypeError(
                    f"Can't convert '{type(key).__name__}' to a basic object"
                    f" key{_reported_path(path)}"
                )

            return other


def _basic_items(
    mapping: Mapping[Any, Any],
    /,
    strict: bool,
    path: tuple[str, ...],
) -> Generator[Any]:
    for key, value in mapping.items():
        key_basic: Any = basic_key(
            key,
            strict=strict,
            path=path,
        )

        yield (
            key_basic,
            basic_value(
                value,
                strict=strict,
                # the converted key - in strict mode always a `str`, so the path
                # can't render a key through its `repr`
                path=(*path, f'["{key_basic}"]'),
            ),
        )


def _reported_path(
    path: tuple[str, ...],
    /,
) -> str:
    if not path:
        return ""

    return f" at '{''.join(path)}'"
