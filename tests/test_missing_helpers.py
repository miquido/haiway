import pytest

from haiway import MISSING, Missing, is_missing, not_missing, unwrap_missing


def test_is_missing_and_not_missing() -> None:
    assert is_missing(MISSING) is True
    assert not_missing(MISSING) is False

    values: tuple[object, ...] = (None, 0, "", False, [], 1, "x")
    for value in values:
        assert is_missing(value) is False
        assert not_missing(value) is True


def test_unwrap_missing_default() -> None:
    assert unwrap_missing(MISSING, default=5) == 5
    assert unwrap_missing(3, default=5) == 3
    assert unwrap_missing(0, default=5) == 0
    assert unwrap_missing(None, default=5) is None


def test_unwrap_missing_mapping() -> None:
    assert unwrap_missing(2, default="none", mapping=str) == "2"
    assert unwrap_missing(MISSING, default="none", mapping=str) == "none"
    assert unwrap_missing(0, default="none", mapping=str) == "0"


def test_unwrap_missing_mapping_not_called_for_missing() -> None:
    def fail(_: int) -> int:
        raise AssertionError("mapping must not be called")

    assert unwrap_missing(MISSING, default=1, mapping=fail) == 1


def test_missing_is_singleton_falsy_and_immutable() -> None:
    assert Missing() is MISSING
    assert not MISSING
    assert MISSING == Missing()
    assert hash(MISSING) == hash(Missing())
    assert str(MISSING) == "MISSING"

    with pytest.raises(AttributeError):
        MISSING.attribute = 1  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError):
        MISSING.attribute  # pyright: ignore[reportAttributeAccessIssue]  # noqa: B018
