import base64
import re
from collections.abc import Generator, Iterable, Mapping, Sequence
from datetime import date, datetime, time, timedelta
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
#   timedelta                   ISO 8601 duration, i.e. P1DT2H3M4.5S
#   Path                        value.as_posix()
#   bytes-like                  base64 encoded str
#
# Anything else - a plain `Enum`, a `Decimal`, `Missing` - has no spelling the
# validation would read back. Encoding it would trade a loud failure here for a
# silent one on the way in, so `strict` rejects it instead.
#
# `State` is not resolved here - `types` can't import `attributes`. Its own
# conversion covers the structured values, this one covers the leaves, the keys
# and the plain collections of `Map` and `Meta`.
_DURATION: re.Pattern[str] = re.compile(
    r"(?P<sign>[+-])?P"
    r"(?:(?P<weeks>\d+)W)?"
    r"(?:(?P<days>\d+)D)?"
    r"(?:T(?=\d)"
    r"(?:(?P<hours>\d+)H)?"
    r"(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)(?:[.,](?P<fraction>\d{1,6}))?S)?"
    r")?"
)


def format_duration(
    value: timedelta,
    /,
) -> str:
    """Spell a ``timedelta`` as an ISO 8601 duration, i.e. ``P1DT2H3M4.5S``."""
    sign: str = "-" if value < timedelta(0) else ""
    magnitude: timedelta = abs(value)
    hours, remainder = divmod(magnitude.seconds, 3600)
    minutes, seconds = divmod(remainder, 60)

    date_part: str = f"{magnitude.days}D" if magnitude.days else ""
    time_part: str = f"{hours}H" if hours else ""
    time_part += f"{minutes}M" if minutes else ""
    if seconds or magnitude.microseconds:
        fraction: str = f".{magnitude.microseconds:06d}".rstrip("0")
        time_part += f"{seconds}{'' if fraction == '.' else fraction}S"

    if not date_part and not time_part:
        return "PT0S"  # a zero duration still needs a component

    return f"{sign}P{date_part}{'T' + time_part if time_part else ''}"


def parse_duration(
    value: str,
    /,
) -> timedelta:
    """Read an ISO 8601 duration - weeks, days, hours, minutes and seconds.

    Years and months are refused, their length depends on the calendar date
    they are counted from, which a ``timedelta`` does not have.

    Raises
    ------
    ValueError
        When the value is not a duration made of the supported components.
    """
    matched: re.Match[str] | None = _DURATION.fullmatch(value)
    if matched is None or all(
        matched[name] is None for name in ("weeks", "days", "hours", "minutes", "seconds")
    ):
        raise ValueError(f"'{value}' is not a valid ISO 8601 duration of weeks, days and time")

    duration: timedelta = timedelta(
        weeks=int(matched["weeks"] or 0),
        days=int(matched["days"] or 0),
        hours=int(matched["hours"] or 0),
        minutes=int(matched["minutes"] or 0),
        seconds=int(matched["seconds"] or 0),
        microseconds=int((matched["fraction"] or "").ljust(6, "0") or 0),
    )

    return -duration if matched["sign"] == "-" else duration


def basic_value(  # noqa: C901, PLR0911
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
        return base64.b64encode(value).decode("utf-8")  # pyright: ignore[reportUnknownArgumentType]

    if isinstance(value, UUID):
        return str(value)

    # `datetime` before `date` - it is a subclass of it
    if isinstance(value, datetime | date | time):
        return value.isoformat()

    if isinstance(value, timedelta):
        return format_duration(value)

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
            f"Can't convert '{_reported_type(value)}' to a basic value{_reported_path(path)}"
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
                    f"Can't convert '{_reported_type(key)}' to a basic object"
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


def _reported_type(value: Any) -> str:
    # a sentinel is its own type - `Missing` above all - while every sentinel's type is the same
    if isinstance(value, sentinel):
        return repr(value)

    return type(value).__name__


def _reported_path(
    path: tuple[str, ...],
    /,
) -> str:
    if not path:
        return ""

    return f" at '{''.join(path)}'"
