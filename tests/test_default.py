from uuid import uuid4

from pytest import MonkeyPatch, raises

from haiway import MISSING, Default, DefaultValue, Missing, State, ValidationError


def test_defaultvalue_resolves_a_literal() -> None:
    default_value = DefaultValue(default=123)

    assert default_value.available
    assert default_value() == 123


def test_defaultvalue_resolves_a_factory_on_every_call() -> None:
    counter = 0

    def factory() -> int:
        nonlocal counter
        counter += 1
        return counter

    default_value = DefaultValue(default_factory=factory)

    assert default_value.available
    assert default_value() == 1
    assert default_value() == 2


def test_defaultvalue_without_a_source_is_unavailable() -> None:
    default_value = DefaultValue()

    assert not default_value.available
    assert default_value() is MISSING


def test_default_resolves_a_literal() -> None:
    class Defaults(State):
        positional: int = Default(654)
        labelled: int = Default(default=321)

    defaults = Defaults()

    assert defaults.positional == 654
    assert defaults.labelled == 321
    assert Defaults(positional=1, labelled=2) == Defaults(positional=1, labelled=2)


def test_default_resolves_a_factory_per_instance() -> None:
    class Defaults(State):
        generated: str = Default(factory=lambda: uuid4().hex)

    defaults = Defaults()

    assert len(defaults.generated) == 32
    assert defaults.generated != Defaults().generated


def test_default_reads_the_environment(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "from-env")

    class Defaults(State):
        value: str = Default(env="HAIWAY_TEST_DEFAULT")

    assert Defaults().value == "from-env"


def test_default_reports_the_missing_environment_variable(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str = Default(env="HAIWAY_TEST_DEFAULT")

    with raises(ValidationError) as exception:
        Defaults()

    assert "HAIWAY_TEST_DEFAULT" in str(exception.value)


def test_default_allows_missing_environment_variable_when_annotated(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str | Missing = Default(env="HAIWAY_TEST_DEFAULT")

    assert Defaults().value is MISSING


def test_default_environment_is_not_used_when_value_provided(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str = Default(env="HAIWAY_TEST_DEFAULT")

    assert Defaults(value="explicit").value == "explicit"


def test_default_falls_back_when_the_environment_is_not_set(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str = Default(env="HAIWAY_TEST_DEFAULT", default="fallback")

    assert Defaults().value == "fallback"


def test_default_prefers_the_environment_over_the_fallback(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "from-env")

    class Defaults(State):
        value: str = Default(env="HAIWAY_TEST_DEFAULT", default="fallback")

    assert Defaults().value == "from-env"


def test_default_maps_the_environment_value(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "42")

    class Defaults(State):
        value: int = Default(env="HAIWAY_TEST_DEFAULT", mapping=int, default=1)

    assert Defaults().value == 42


def test_default_mapping_is_not_applied_to_the_fallback(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: int = Default(env="HAIWAY_TEST_DEFAULT", mapping=int, default=1)

    assert Defaults().value == 1


def test_default_reports_an_unmappable_environment_value(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "not-a-number")

    class Defaults(State):
        value: int = Default(env="HAIWAY_TEST_DEFAULT", mapping=int, default=1)

    with raises(ValidationError) as exception:
        Defaults()

    assert "HAIWAY_TEST_DEFAULT" in str(exception.value)


def test_default_reads_the_environment_on_each_resolution(monkeypatch: MonkeyPatch) -> None:
    # the class is defined before the variable is exported, which is what an
    # ordinary `load_env()` in `main()` does
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str = Default(env="HAIWAY_TEST_DEFAULT", default="fallback")

    assert Defaults().value == "fallback"

    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "exported-later")

    assert Defaults().value == "exported-later"
