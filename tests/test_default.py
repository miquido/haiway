from uuid import uuid4

from pytest import MonkeyPatch, raises

from haiway import (
    Default,
    DefaultEnv,
    DefaultFactory,
    DefaultValue,
    Missing,
    State,
    ValidationError,
)


def test_defaultvalue_resolves_a_literal() -> None:
    default_value = Default(123)

    assert default_value.available
    assert default_value() == 123


def test_defaultvalue_resolves_a_factory_on_every_call() -> None:
    counter = 0

    def factory() -> int:
        nonlocal counter
        counter += 1
        return counter

    default_value = Default(factory=factory)

    assert default_value.available
    assert default_value() == 1
    assert default_value() == 2


def test_defaultvalue_without_a_source_is_unavailable() -> None:
    default_value = Default(Missing)

    assert not default_value.available
    assert default_value() is Missing


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
        value: str = DefaultEnv("HAIWAY_TEST_DEFAULT", required=True)

    assert Defaults().value == "from-env"


def test_default_reports_the_missing_environment_variable(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str = DefaultEnv("HAIWAY_TEST_DEFAULT", required=True)

    with raises(ValidationError) as exception:
        Defaults()

    assert "HAIWAY_TEST_DEFAULT" in str(exception.value)


def test_default_allows_missing_environment_variable_when_annotated(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str | Missing = DefaultEnv("HAIWAY_TEST_DEFAULT", fallback=Missing)

    assert Defaults().value is Missing


def test_default_environment_is_not_used_when_value_provided(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str = DefaultEnv("HAIWAY_TEST_DEFAULT", required=True)

    assert Defaults(value="explicit").value == "explicit"


def test_default_falls_back_when_the_environment_is_not_set(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str = DefaultEnv("HAIWAY_TEST_DEFAULT", fallback="fallback")

    assert Defaults().value == "fallback"


def test_default_prefers_the_environment_over_the_fallback(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "from-env")

    class Defaults(State):
        value: str = DefaultEnv("HAIWAY_TEST_DEFAULT", fallback="fallback")

    assert Defaults().value == "from-env"


def test_default_maps_the_environment_value(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "42")

    class Defaults(State):
        value: int = DefaultEnv("HAIWAY_TEST_DEFAULT", mapping=int, fallback=1)

    assert Defaults().value == 42


def test_default_mapping_is_not_applied_to_the_fallback(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: int = DefaultEnv("HAIWAY_TEST_DEFAULT", mapping=int, fallback=1)

    assert Defaults().value == 1


def test_default_reports_an_unmappable_environment_value(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "not-a-number")

    class Defaults(State):
        value: int = DefaultEnv("HAIWAY_TEST_DEFAULT", mapping=int, fallback=1)

    with raises(ValidationError) as exception:
        Defaults()

    assert "HAIWAY_TEST_DEFAULT" in str(exception.value)


def test_default_reads_the_environment_on_each_resolution(monkeypatch: MonkeyPatch) -> None:
    # the class is defined before the variable is exported, which is what an
    # ordinary `load_env()` in `main()` does
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: str = DefaultEnv("HAIWAY_TEST_DEFAULT", fallback="fallback")

    assert Defaults().value == "fallback"

    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "exported-later")

    assert Defaults().value == "exported-later"


def test_default_factory_with_arguments() -> None:
    class Sample(State):
        values: tuple[int, ...] = DefaultFactory(tuple, (1, 2))
        text: str = DefaultFactory(str.join, "-", ["a", "b"])
        number: int = DefaultFactory(int, "ff", base=16)

    sample = Sample()
    assert sample.values == (1, 2)
    assert sample.text == "a-b"
    assert sample.number == 255


def test_default_factory_is_called_for_each_instance() -> None:
    class Sample(State):
        values: list[int] = DefaultFactory(list, [1])

    assert Sample().values is not Sample().values


def test_default_environment_required_reads_and_maps(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_DEFAULT", "7")

    class Defaults(State):
        value: int = DefaultEnv("HAIWAY_TEST_DEFAULT", mapping=int, required=True)

    assert Defaults().value == 7


def test_default_environment_required_with_mapping_reports_unset(
    monkeypatch: MonkeyPatch,
) -> None:
    monkeypatch.delenv("HAIWAY_TEST_DEFAULT", raising=False)

    class Defaults(State):
        value: int = DefaultEnv("HAIWAY_TEST_DEFAULT", mapping=int, required=True)

    with raises(ValidationError) as exception:
        Defaults()

    assert "HAIWAY_TEST_DEFAULT" in str(exception.value)


def test_default_environment_required_rejects_a_fallback() -> None:
    with raises(ValueError):
        DefaultEnv("HAIWAY_TEST_DEFAULT", required=True, fallback="x")  # pyright: ignore[reportCallIssue]


def test_default_value_is_a_plain_store() -> None:
    default_value = DefaultValue(lambda: 1, available=False, constant=True)

    assert default_value() == 1
    assert not default_value.available
    assert default_value.constant
