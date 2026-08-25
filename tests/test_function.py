from typing import Annotated

import pytest

from haiway.attributes.function import Function
from haiway.attributes.validation import ValidationError
from haiway.types import Alias, Default


def test_validate_arguments_skip_consumed_kwargs_in_variadic() -> None:
    def foo(user: int, *, tag: str, **extras: str) -> dict[str, object]:
        return {"user": user, "tag": tag, "extras": extras}

    parametrized = Function(foo)

    result = parametrized(user=1, tag="alpha", note="beta")

    assert result == {"user": 1, "tag": "alpha", "extras": {"note": "beta"}}


def test_validate_arguments_with_positional_only_and_keyword() -> None:
    def foo(user_id: int, /, label: str, *, weight: float = 1.0) -> tuple[int, str, float]:
        return (user_id, label, weight)

    parametrized = Function(foo)

    assert parametrized(4, "item") == (4, "item", 1.0)
    assert parametrized(5, label="text", weight=2.5) == (5, "text", 2.5)

    with pytest.raises(TypeError):
        parametrized(user_id=3, label="x")


def test_validate_arguments_with_variadic_positional() -> None:
    def foo(*values: int) -> tuple[int, ...]:
        return values

    parametrized = Function(foo)

    assert parametrized(1, 2, 3) == (1, 2, 3)


def test_validate_arguments_raises_on_duplicate_positional_keyword() -> None:
    def foo(value: int, extra: int = 0) -> int:
        return value + extra

    parametrized = Function(foo)

    with pytest.raises(TypeError):
        parametrized(1, value=2)


def test_validate_arguments_rejects_unexpected_keyword_without_variadic() -> None:
    def foo(value: int) -> int:
        return value

    parametrized = Function(foo)

    with pytest.raises(TypeError):
        parametrized(value=1, other=2)


def test_validate_arguments_missing_required_positional_raises_type_error() -> None:
    def foo(value: int) -> int:
        return value

    parametrized = Function(foo)

    with pytest.raises(TypeError):
        parametrized()


def test_validate_arguments_missing_required_keyword_only_raises_type_error() -> None:
    def foo(*, flag: bool) -> bool:
        return flag

    parametrized = Function(foo)

    with pytest.raises(TypeError):
        parametrized()


def test_validate_arguments_variadic_positional_type_error_is_wrapped() -> None:
    def foo(*values: int) -> tuple[int, ...]:
        return values

    parametrized = Function(foo)

    with pytest.raises(ValidationError) as excinfo:
        parametrized(1, "oops", 3)

    assert excinfo.value.path[-1] == ".values"


def test_validate_arguments_keyword_form_for_positional_or_keyword() -> None:
    def foo(value: int, other: int = 2) -> tuple[int, int]:
        return (value, other)

    parametrized = Function(foo)

    assert parametrized(value=5) == (5, 2)
    assert parametrized(value=5, other=7) == (5, 7)


def test_validate_arguments_preserves_keyword_only_call() -> None:
    def foo(*, flag: bool) -> bool:
        return flag

    parametrized = Function(foo)

    assert parametrized(flag=True) is True


def test_validate_arguments_extra_positional_without_variadic_raises_type_error() -> None:
    def foo(first: int, second: int) -> int:
        return first + second

    parametrized = Function(foo)

    with pytest.raises(TypeError):
        parametrized(1, 2, 3)


def test_validate_arguments_supports_aliases_for_keywords() -> None:
    def foo(
        user_id: Annotated[int, Alias("user")],
        *,
        tag: Annotated[str, Alias("label")],
    ) -> tuple[int, str]:
        return (user_id, tag)

    parametrized = Function(foo)

    assert parametrized(user=7, label="green") == (7, "green")
    assert parametrized(user_id=8, tag="blue") == (8, "blue")


def test_validate_arguments_accepts_alias_and_canonical_for_different_params() -> None:
    def foo(
        user_id: Annotated[int, Alias("user")],
        *,
        tag: Annotated[str, Alias("label")],
    ) -> tuple[int, str]:
        return (user_id, tag)

    parametrized = Function(foo)

    assert parametrized(user=7, tag="blue") == (7, "blue")


def test_validate_arguments_fails_with_conflicting_alias_and_keyword() -> None:
    def foo(
        user_id: Annotated[int, Alias("user")],
    ) -> int:
        return user_id

    parametrized = Function(foo)

    with pytest.raises(TypeError):
        assert parametrized(user_id=1, user=2) == 2

    with pytest.raises(TypeError):
        assert parametrized(user=2, user_id=1) == 1


def test_validate_arguments_fails_with_alias_and_canonical_for_same_param() -> None:
    def foo(
        user_id: Annotated[int, Alias("user")],
        *,
        tag: Annotated[str, Alias("label")],
    ) -> tuple[int, str]:
        return (user_id, tag)

    parametrized = Function(foo)

    with pytest.raises(TypeError):
        assert parametrized(user=7, user_id=8, tag="blue") == (8, "blue")

    with pytest.raises(TypeError):
        assert parametrized(7, tag="blue", label="green") == (7, "green")


def test_resolves_literal_default_marker() -> None:
    def foo(name: str, greeting: str = Default("hello")) -> str:
        return f"{greeting}, {name}"

    parametrized = Function(foo)

    assert parametrized("bob") == "hello, bob"
    assert parametrized("bob", greeting="hi") == "hi, bob"
    assert parametrized("bob", "hi") == "hi, bob"


def test_resolves_factory_default_marker() -> None:
    produced: list[int] = []

    def next_value() -> int:
        produced.append(len(produced))
        return len(produced)

    def foo(value: int = Default(factory=next_value)) -> int:
        return value

    parametrized = Function(foo)

    # the factory is resolved per call, not once at wrapping
    assert parametrized() == 1
    assert parametrized() == 2
    assert parametrized(10) == 10


def test_resolves_env_default_marker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HAIWAY_TEST_FUNCTION_DEFAULT", "from-env")

    def foo(token: str = Default(env="HAIWAY_TEST_FUNCTION_DEFAULT")) -> str:
        return token

    parametrized = Function(foo)

    assert parametrized() == "from-env"


def test_resolves_keyword_only_default_marker() -> None:
    def foo(name: str, *, suffix: str = Default("!")) -> str:
        return f"{name}{suffix}"

    parametrized = Function(foo)

    assert parametrized("bob") == "bob!"
    assert parametrized("bob", suffix="?") == "bob?"


def test_resolves_positional_only_default_marker() -> None:
    def foo(value: int = Default(7), /) -> int:
        return value

    parametrized = Function(foo)

    assert parametrized() == 7
    assert parametrized(1) == 1


def test_resolves_aliased_default_marker() -> None:
    def foo(value: Annotated[int, Alias("val")] = Default(3)) -> int:
        return value

    parametrized = Function(foo)

    assert parametrized() == 3
    assert parametrized(val=5) == 5


def test_validates_resolved_default_marker() -> None:
    def foo(value: int = Default("not an int")) -> int:  # pyright: ignore[reportArgumentType]
        return value

    parametrized = Function(foo)

    with pytest.raises(ValidationError):
        parametrized()


def test_keeps_plain_defaults_untouched() -> None:
    sentinel: list[int] = []

    def foo(value: list[int] = sentinel) -> list[int]:
        return value

    parametrized = Function(foo)

    # a plain default is bound by python and is not validated or coerced
    assert parametrized() is sentinel
