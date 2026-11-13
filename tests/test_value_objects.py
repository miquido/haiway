import copy
from collections.abc import Callable
from typing import Any

import pytest

from haiway import Alias, Default, DefaultEnv, Description, Missing, Sensitive, Specification
from haiway.types.default import DefaultValue


def _objects() -> list[Any]:
    return [
        Alias("name"),
        Description("text"),
        Sensitive(),
        Default(1),
        Specification({"type": "string"}),
        Missing,
    ]


@pytest.mark.parametrize("instance", _objects(), ids=lambda value: type(value).__name__)
def test_value_objects_reject_modification(instance: Any) -> None:
    with pytest.raises(AttributeError):
        instance.attribute = 1

    with pytest.raises(AttributeError):
        del instance.attribute


def test_sensitive_rejects_empty_redaction() -> None:
    with pytest.raises(ValueError):
        Sensitive(redaction="")


def test_missing_renders_by_name() -> None:
    assert str(Missing) == "Missing"
    assert repr(Missing) == "Missing"


def test_default_value_resolves_lazily_and_reports_availability() -> None:
    constant: Any = Default(5)
    factory: Any = Default(factory=list)
    unset: Any = Default(Missing)

    assert constant() == 5
    assert constant.available is True
    assert factory() == []
    assert factory() is not factory()
    assert unset.available is False
    assert unset() is Missing


def test_default_helper_with_factory_builds_default_value() -> None:
    marker: Any = Default(factory=lambda: 3)

    assert isinstance(marker, DefaultValue)
    assert marker() == 3


@pytest.mark.parametrize("instance", _objects(), ids=lambda value: type(value).__name__)
def test_value_objects_are_their_own_copies(instance: Any) -> None:
    assert copy.copy(instance) is instance
    assert copy.deepcopy(instance) is instance


@pytest.mark.parametrize(
    "making",
    [
        lambda: Alias(""),
        lambda: Specification({}),  # pyright: ignore[reportArgumentType]
        lambda: Default(1, factory=list),
        lambda: DefaultEnv("VARIABLE", required=True, fallback=1),  # pyright: ignore[reportCallIssue]
    ],
    ids=[
        "empty alias",
        "empty specification",
        "default and factory",
        "required env with fallback",
    ],
)
def test_value_objects_reject_invalid_arguments(making: Callable[[], Any]) -> None:
    # raised rather than asserted - an optimized build would strip an assertion
    with pytest.raises(ValueError):
        making()
