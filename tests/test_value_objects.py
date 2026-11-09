from typing import Any

import pytest

from haiway import Alias, Default, Description, Missing, Sensitive, Specification
from haiway.types.default import DefaultValue


def _objects() -> list[Any]:
    return [
        Alias("name"),
        Description("text"),
        Sensitive(),
        DefaultValue(default=1),
        Specification({"type": "string"}),
        Missing,
    ]


@pytest.mark.parametrize("instance", _objects(), ids=lambda value: type(value).__name__)
def test_value_objects_reject_modification(instance: Any) -> None:
    with pytest.raises(AttributeError):
        instance.attribute = 1

    with pytest.raises(AttributeError):
        del instance.attribute


def test_existing_attributes_are_protected_too() -> None:
    alias = Alias("name")
    description = Description("text")
    sensitive = Sensitive(redaction="***")

    with pytest.raises(AttributeError):
        alias.name = "other"  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError):
        description.description = "other"  # pyright: ignore[reportAttributeAccessIssue]

    with pytest.raises(AttributeError):
        sensitive.redaction = "other"  # pyright: ignore[reportAttributeAccessIssue]

    assert alias.name == "name"
    assert description.description == "text"
    assert sensitive.redaction == "***"


def test_sensitive_rejects_empty_redaction() -> None:
    with pytest.raises(ValueError):
        Sensitive(redaction="")


def test_missing_renders_by_name() -> None:
    assert str(Missing) == "Missing"
    assert repr(Missing) == "Missing"


def test_default_value_resolves_lazily_and_reports_availability() -> None:
    constant = DefaultValue(default=5)
    factory = DefaultValue(default_factory=list)
    unset = DefaultValue()

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
