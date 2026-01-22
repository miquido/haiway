import pytest

from haiway.postgres.state import _validate_migration_names


def test_validate_migration_names_detects_gaps_and_invalid():
    with pytest.raises(ValueError):
        list(_validate_migration_names(["migration_0", "migration_2"]))

    with pytest.raises(ValueError):
        list(_validate_migration_names(["migration_a"]))

    with pytest.raises(ValueError):
        list(_validate_migration_names(["other"]))


def test_validate_migration_names_sorts_unordered_inputs():
    assert list(_validate_migration_names(["migration_2", "migration_0", "migration_1"])) == [
        "migration_0",
        "migration_1",
        "migration_2",
    ]


def test_validate_migration_names_accepts_contiguous_sequence():
    # Should not raise
    assert list(_validate_migration_names(["migration_0", "migration_1", "migration_2"])) == [
        "migration_0",
        "migration_1",
        "migration_2",
    ]


def test_validate_migration_names_detects_duplicates():
    with pytest.raises(ValueError):
        list(_validate_migration_names(["migration_0", "migration_0"]))
