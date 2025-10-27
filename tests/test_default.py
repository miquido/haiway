from uuid import uuid4

from haiway import Default, DefaultValue, State


def test_defaultvalue_consumes_default_alias() -> None:
    default_value = DefaultValue(default=123)

    assert default_value() == 123


def test_defaultvalue_consumes_default_factory_alias() -> None:
    counter = 0

    def factory() -> int:
        nonlocal counter
        counter += 1
        return counter

    default_value = DefaultValue(default_factory=factory)

    assert default_value() == 1
    assert default_value() == 2


def test_default_function_forwards_dataclass_aliases() -> None:
    class Defaults(State):
        value: int = Default(default=321)
        generated: str = Default(default_factory=lambda: uuid4().hex)

    defaults = Defaults()

    assert defaults.value == 321
    assert len(defaults.generated) == 32
    assert defaults.generated != Defaults().generated


def test_default_function_supports_legacy_aliases() -> None:
    class Defaults(State):
        value: int = Default(654)

    defaults = Defaults()

    assert defaults.value == 654
