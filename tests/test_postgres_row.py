from collections.abc import Iterable, Iterator, Sequence
from decimal import Decimal
from typing import Any

from pytest import fail, fixture

from haiway.postgres import types as postgres_types
from haiway.postgres.types import PostgresRow, PostgresValue


class FakeAsyncpgRecord:
    """Minimal asyncpg.Record stand-in mirroring positional iteration."""

    def __init__(self, items: Sequence[tuple[str, PostgresValue]]) -> None:
        self._items: list[tuple[str, PostgresValue]] = list(items)
        self._mapping: dict[str, PostgresValue] = dict(self._items)

    def __contains__(self, key: object) -> bool:
        if isinstance(key, int):
            return 0 <= key < len(self._items)
        return key in self._mapping

    def __getitem__(self, key: int | str) -> PostgresValue:
        if isinstance(key, int):
            return self._items[key][1]
        return self._mapping[key]

    def __iter__(self) -> Iterator[PostgresValue]:
        for _, value in self._items:
            yield value

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str, default: Any = None) -> PostgresValue:
        return self._mapping.get(key, default)

    def items(self) -> Iterable[tuple[str, PostgresValue]]:
        return tuple(self._items)

    def keys(self) -> Iterable[str]:
        return tuple(key for key, _ in self._items)

    def values(self) -> Iterable[PostgresValue]:
        return tuple(value for _, value in self._items)


@fixture(autouse=True)
def patch_record(monkeypatch) -> None:
    monkeypatch.setattr(postgres_types, "Record", FakeAsyncpgRecord)


def test_postgres_row_iterates_over_column_names() -> None:
    record: FakeAsyncpgRecord = FakeAsyncpgRecord(
        (
            ("id", 1),
            ("email", "test@example.com"),
        )
    )
    row: PostgresRow = PostgresRow(record)

    assert list(row) == ["id", "email"]
    assert list(row.keys()) == ["id", "email"]
    assert list(row.values()) == [1, "test@example.com"]


def test_postgres_row_items_preserve_mapping_pairs() -> None:
    record: FakeAsyncpgRecord = FakeAsyncpgRecord(
        (
            ("id", 1),
            ("name", "Anna"),
            ("active", True),
        )
    )
    row: PostgresRow = PostgresRow(record)

    assert list(row.items()) == [
        ("id", 1),
        ("name", "Anna"),
        ("active", True),
    ]
    assert dict(row) == {
        "id": 1,
        "name": "Anna",
        "active": True,
    }


def test_postgres_row_dict_matches_original_mapping() -> None:
    items: tuple[tuple[str, PostgresValue], ...] = (
        ("first", 10),
        ("second", "value"),
        ("third", False),
    )
    record: FakeAsyncpgRecord = FakeAsyncpgRecord(items)
    row: PostgresRow = PostgresRow(record)

    assert dict(row) == dict(record.items())
    for key in record.keys():
        assert row[key] == record[key]


def test_postgres_row_supports_mapping_pattern_matching() -> None:
    record: FakeAsyncpgRecord = FakeAsyncpgRecord(
        (
            ("id", 42),
            ("email", "user@example.com"),
            ("active", True),
        )
    )
    row: PostgresRow = PostgresRow(record)

    match row:
        case {"id": 42, "email": email, "active": True} if isinstance(email, str):
            assert email == "user@example.com"
        case _:
            fail("PostgresRow should match mapping patterns using column names")


def test_postgres_row_get_float_accepts_decimal() -> None:
    record: FakeAsyncpgRecord = FakeAsyncpgRecord((("amount", Decimal("12.5")),))
    row: PostgresRow = PostgresRow(record)

    assert row.get_float("amount") == 12.5


def test_postgres_row_get_float_accepts_int_as_numeric() -> None:
    record: FakeAsyncpgRecord = FakeAsyncpgRecord((("amount", 7),))
    row: PostgresRow = PostgresRow(record)

    assert row.get_float("amount") == 7.0
