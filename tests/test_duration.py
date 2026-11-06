from datetime import timedelta

import pytest

from haiway.types.basic import format_duration, parse_duration


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (timedelta(0), "PT0S"),
        (timedelta(seconds=1), "PT1S"),
        (timedelta(minutes=2), "PT2M"),
        (timedelta(hours=3), "PT3H"),
        (timedelta(days=4), "P4D"),
        (timedelta(weeks=1), "P7D"),
        (timedelta(days=1, hours=2, minutes=3, seconds=4), "P1DT2H3M4S"),
        (timedelta(days=1, hours=2, minutes=3, seconds=4, microseconds=500_000), "P1DT2H3M4.5S"),
        (timedelta(microseconds=1), "PT0.000001S"),
        (timedelta(milliseconds=250), "PT0.25S"),
        (timedelta(hours=1, seconds=1), "PT1H1S"),
        (timedelta(days=2, minutes=5), "P2DT5M"),
        (timedelta(hours=25), "P1DT1H"),
        (-timedelta(days=1), "-P1D"),
        (-timedelta(seconds=1), "-PT1S"),
        (-timedelta(days=1, hours=2, microseconds=5), "-P1DT2H0.000005S"),
    ],
)
def test_format_duration(value: timedelta, expected: str) -> None:
    assert format_duration(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("PT0S", timedelta(0)),
        ("P1W", timedelta(weeks=1)),
        ("P2W3D", timedelta(weeks=2, days=3)),
        ("P1D", timedelta(days=1)),
        ("PT1H", timedelta(hours=1)),
        ("PT2M", timedelta(minutes=2)),
        ("PT3S", timedelta(seconds=3)),
        ("P1DT2H3M4S", timedelta(days=1, hours=2, minutes=3, seconds=4)),
        ("P1DT2H3M4.5S", timedelta(days=1, hours=2, minutes=3, seconds=4, milliseconds=500)),
        ("PT1,5S", timedelta(seconds=1, milliseconds=500)),
        ("PT0.05S", timedelta(milliseconds=50)),
        ("PT0.000001S", timedelta(microseconds=1)),
        ("PT90M", timedelta(minutes=90)),
        ("PT36H", timedelta(hours=36)),
        ("-P1D", -timedelta(days=1)),
        ("+PT1H", timedelta(hours=1)),
        ("-PT1.5S", -timedelta(seconds=1, milliseconds=500)),
    ],
)
def test_parse_duration(value: str, expected: timedelta) -> None:
    assert parse_duration(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "P",
        "PT",
        "P1DT",  # time designator without components
        "P1Y",  # calendar units are refused
        "P1M",
        "P1Y2M3D",
        "1D",
        "PT1H ",
        " PT1H",
        "p1d",  # case sensitive
        "P1H",  # hours need the T designator
        "PT1D",  # days belong before T
        "PT1S1M",  # wrong order
        "PT1.S",
        "PT.5S",
        "PT0.1234567S",  # more than microsecond precision
        "PT1.5H",  # fractions are limited to seconds
        "P-1D",
        "garbage",
    ],
)
def test_parse_duration_rejects_invalid_values(value: str) -> None:
    with pytest.raises(ValueError, match="not a valid ISO 8601 duration"):
        parse_duration(value)


@pytest.mark.parametrize(
    "value",
    [
        timedelta(0),
        timedelta(microseconds=1),
        timedelta(days=999, hours=23, minutes=59, seconds=59, microseconds=999_999),
        timedelta(hours=1, microseconds=10),
        -timedelta(days=3, seconds=7, microseconds=123),
        timedelta.max,
        timedelta.min,
    ],
)
def test_duration_round_trip(value: timedelta) -> None:
    assert parse_duration(format_duration(value)) == value
