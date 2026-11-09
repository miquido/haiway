import copy
import pickle  # nosec: B403
import typing

import pytest

from haiway import Missing, State


class _Optional(State):
    value: int | Missing = Missing


def test_missing_stays_the_one_instance_when_copied_or_pickled() -> None:
    # everything compares with Missing by identity - a copy of it would be a value
    assert copy.copy(Missing) is Missing
    assert copy.deepcopy(Missing) is Missing
    assert pickle.loads(pickle.dumps(Missing)) is Missing  # nosec: B301

    duplicate = copy.deepcopy(_Optional())
    assert duplicate.value is Missing
    assert duplicate.to_mapping() == {}
    assert duplicate.to_json() == "{}"


def test_missing_is_a_sentinel_and_its_own_type() -> None:
    # a PEP 661 sentinel - the value and the type at once, as None is
    assert type(Missing) is sentinel
    assert repr(Missing) == "Missing"
    assert str(Missing) == "Missing"
    assert bool(Missing) is True  # unlike None - compared by identity, never by truth
    assert Missing != None  # noqa: E711
    assert set(typing.get_args(int | Missing)) == {int, Missing}

    with pytest.raises(AttributeError):
        Missing.attribute = 1  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError):
        Missing.attribute  # pyright: ignore[reportAttributeAccessIssue]  # noqa: B018
