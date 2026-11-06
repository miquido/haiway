from asyncio import CancelledError
from types import TracebackType

import pytest

from haiway.utils.exceptions import raise_collected, thrown_exception


def _traceback() -> TracebackType:
    try:
        raise RuntimeError("source")

    except RuntimeError as exc:
        assert exc.__traceback__ is not None
        return exc.__traceback__


def test_thrown_exception_from_instance_returns_it() -> None:
    exception = ValueError("boom")

    assert thrown_exception(exception) is exception


def test_thrown_exception_instance_with_value_is_rejected() -> None:
    with pytest.raises(TypeError, match="separate value"):
        thrown_exception(ValueError("boom"), "extra")


def test_thrown_exception_instantiates_type_without_value() -> None:
    result = thrown_exception(KeyError)

    assert type(result) is KeyError
    assert result.args == ()


def test_thrown_exception_instantiates_type_with_value() -> None:
    result = thrown_exception(ValueError, "message")

    assert type(result) is ValueError
    assert result.args == ("message",)


def test_thrown_exception_reuses_value_of_matching_type() -> None:
    value = ValueError("original")

    assert thrown_exception(ValueError, value) is value


def test_thrown_exception_reuses_value_of_subtype() -> None:
    value = KeyError("original")

    assert thrown_exception(LookupError, value) is value


def test_thrown_exception_wraps_value_of_unrelated_type() -> None:
    value = KeyError("original")

    result = thrown_exception(ValueError, value)

    assert type(result) is ValueError
    assert result.args == (value,)


def test_thrown_exception_attaches_traceback() -> None:
    traceback = _traceback()

    from_type = thrown_exception(ValueError, None, traceback)
    from_instance = thrown_exception(ValueError("x"), None, traceback)

    assert from_type.__traceback__ is traceback
    assert from_instance.__traceback__ is traceback


def test_thrown_exception_without_traceback_leaves_it_empty() -> None:
    assert thrown_exception(ValueError("x")).__traceback__ is None


def test_raise_collected_does_nothing_for_no_exceptions() -> None:
    assert raise_collected((), message="unused") is None


def test_raise_collected_raises_single_exception_itself() -> None:
    exception = ValueError("single")

    with pytest.raises(ValueError, match="single") as info:
        raise_collected([exception], message="ignored")

    assert info.value is exception


def test_raise_collected_single_keeps_prepared_context() -> None:
    cause = KeyError("prepared")
    exception = ValueError("single")
    exception.__context__ = cause

    with pytest.raises(ValueError) as info:
        raise_collected([exception], message="ignored")

    assert info.value.__context__ is cause


def test_raise_collected_single_does_not_pick_up_active_exception_as_context() -> None:
    exception = ValueError("single")

    try:
        try:
            raise RuntimeError("currently handled")

        except RuntimeError:
            raise_collected([exception], message="ignored")

    except ValueError as caught:
        assert caught is exception
        assert caught.__context__ is None


def test_raise_collected_single_cancellation_is_raised_as_is() -> None:
    cancelled = CancelledError()

    with pytest.raises(CancelledError) as info:
        raise_collected([cancelled], message="ignored")

    assert info.value is cancelled


def test_raise_collected_groups_regular_exceptions() -> None:
    first = ValueError("first")
    second = KeyError("second")

    with pytest.raises(ExceptionGroup) as info:
        raise_collected([first, second], message="collected")

    assert info.value.message == "collected"
    assert info.value.exceptions == (first, second)


def test_raise_collected_group_is_catchable_by_member_type() -> None:
    with pytest.raises(ExceptionGroup) as info:
        raise_collected([ValueError("a"), ValueError("b"), KeyError("c")], message="collected")

    matched, rest = info.value.split(ValueError)

    assert matched is not None
    assert len(matched.exceptions) == 2
    assert rest is not None
    assert len(rest.exceptions) == 1


def test_raise_collected_collapses_only_cancellations() -> None:
    with pytest.raises(CancelledError):
        raise_collected([CancelledError(), CancelledError()], message="ignored")


def test_raise_collected_mixed_cancellation_and_error_keeps_both() -> None:
    cancelled = CancelledError()
    failure = ValueError("failure")

    with pytest.raises(BaseExceptionGroup) as info:
        raise_collected([cancelled, failure], message="collected")

    assert not isinstance(info.value, ExceptionGroup)
    assert info.value.message == "collected"
    assert info.value.exceptions == (cancelled, failure)


def test_raise_collected_groups_base_exceptions() -> None:
    interrupt = KeyboardInterrupt()
    failure = ValueError("failure")

    with pytest.raises(BaseExceptionGroup) as info:
        raise_collected([failure, interrupt], message="collected")

    assert not isinstance(info.value, ExceptionGroup)
    assert info.value.exceptions == (failure, interrupt)
