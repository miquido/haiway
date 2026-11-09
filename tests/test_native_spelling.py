import json
import random
from datetime import UTC, date, datetime, time, timedelta, timezone
from typing import Any
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from haiway import State

# The values met most are spelled natively rather than by the methods which spell them -
# what is written has to be exactly what `str` and `isoformat` write, for every value of
# the exact types, while anything else is still left to those methods.


class _Holder(State):
    value: Any


def _spelled(value: Any) -> Any:
    basic: Any = _Holder(value=value).to_basic_object()["value"]
    assert json.loads(_Holder(value=value).to_json())["value"] == basic
    return basic


class _SpelledDatetime(datetime):
    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        return "custom"


class _SpelledUUID(UUID):
    __slots__ = ()

    def __str__(self) -> str:
        return "custom"


@pytest.mark.parametrize(
    "value",
    [
        datetime(2026, 11, 9, 12, 30, 15),
        datetime(2026, 11, 9, 12, 30, 15, 1),
        datetime(1, 1, 1),
        datetime(9999, 12, 31, 23, 59, 59, 999999),
        datetime(2026, 11, 9, 12, tzinfo=UTC),
        datetime(2026, 11, 9, 12, 0, 0, 500, tzinfo=UTC),
        datetime(2026, 11, 9, 12, tzinfo=timezone(timedelta(hours=-3, minutes=-30))),
        datetime(2026, 11, 9, 12, tzinfo=timezone(timedelta(seconds=1))),
        datetime(2026, 7, 1, 12, tzinfo=ZoneInfo("Europe/Warsaw")),
        datetime(2026, 11, 9, 12, fold=1),
        date(1, 1, 1),
        date(2026, 11, 9),
        date(9999, 12, 31),
        time(0, 0),
        time(23, 59, 59, 999999),
        time(12, 30, tzinfo=UTC),
    ],
    ids=repr,
)
def test_dates_and_times_are_spelled_as_isoformat_does(value: datetime | date | time) -> None:
    assert _spelled(value) == value.isoformat()


@pytest.mark.parametrize(
    "value",
    [UUID(int=0), UUID(int=(1 << 128) - 1), UUID("12345678-1234-5678-1234-567812345678"), uuid4()],
    ids=str,
)
def test_uuids_are_spelled_as_str_does(value: UUID) -> None:
    assert _spelled(value) == str(value)


def test_subclasses_keep_their_own_spelling() -> None:
    assert _spelled(_SpelledDatetime(2026, 11, 9)) == "custom"
    assert _spelled(_SpelledUUID(int=1)) == "custom"


def test_uuid_with_a_rebound_number_is_left_to_str() -> None:
    # a slot can be rebound to anything - what is not a plain number of 128 bits at most is
    # spelled by `str`, whatever it makes of it
    rebound = UUID(int=1)
    object.__setattr__(rebound, "int", True)
    assert _spelled(rebound) == str(rebound)

    oversized = UUID(int=1)
    object.__setattr__(oversized, "int", 1 << 130)
    with pytest.raises(OverflowError):
        str(oversized)

    with pytest.raises(OverflowError):
        _Holder(value=oversized).to_basic_object()


def test_random_datetimes_are_spelled_as_isoformat_does() -> None:
    generator = random.Random(15)
    for _ in range(2000):
        value = datetime(2000, 1, 1) + timedelta(
            days=generator.randrange(0, 30000),
            microseconds=generator.randrange(0, 86_400_000_000),
        )
        if generator.random() < 0.5:
            value = value.replace(tzinfo=UTC)

        assert _spelled(value) == value.isoformat()
