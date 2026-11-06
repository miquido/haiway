from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Annotated, Any, ClassVar, Protocol, runtime_checkable

from pytest import mark, raises

from haiway import Specification, State, ValidationError, ctx


def double(value: int) -> int:
    return value * 2


async def async_double(value: int) -> int:
    return value * 2


class Operation(State):
    apply: Callable[[int], int]


class Defaulted(State):
    apply: Callable[[int], int] = lambda value: value + 1
    label: str = "x"


@runtime_checkable
class Runner(Protocol):
    def run(self) -> int: ...


class UncheckedRunner(Protocol):
    def run(self) -> int: ...


class Impl:
    def run(self) -> int:
        return 1


class Call:
    def __call__(self, value: int) -> int:
        return value


def test_function_is_stored_as_is() -> None:
    assert Operation(apply=double).apply is double
    assert Operation(apply=double).apply(2) == 4


def test_function_is_not_bound_to_the_instance() -> None:
    operation = Operation(apply=double)

    assert not hasattr(operation.apply, "__self__")  # pyright: ignore[reportFunctionMemberAccess]


def test_lambda_default_is_a_plain_function() -> None:
    state = Defaulted()

    assert state.apply(1) == 2
    assert type(state.apply).__name__ == "function"
    assert not hasattr(state.apply, "__self__")
    assert Defaulted().apply is state.apply


def test_default_function_can_be_overridden_and_updated() -> None:
    assert Defaulted(apply=double).apply is double
    assert Defaulted().updating(apply=double).apply is double
    assert Defaulted().updating(label="y").apply is Defaulted().apply


def test_callable_objects_and_builtins_are_accepted() -> None:
    call = Call()

    assert Operation(apply=call).apply is call
    assert Operation(apply=abs).apply is abs
    assert Operation(apply=lambda value: value).apply(3) == 3


@mark.parametrize("value", [3, "text", None, object()])
def test_non_callable_is_rejected(value: Any) -> None:
    with raises(ValidationError, match=r"\.apply"):
        Operation(apply=value)


def test_updating_validates_functions() -> None:
    with raises(ValidationError):
        Operation(apply=double).updating(apply=3)


@mark.asyncio
async def test_async_function_fields() -> None:
    class Async(State):
        apply: Callable[[int], Awaitable[int]]

    assert await Async(apply=async_double).apply(2) == 4


@mark.asyncio
async def test_function_fields_work_within_context() -> None:
    async with ctx.scope("functions", Operation(apply=double)):
        assert ctx.state(Operation).apply(4) == 8

        async with ctx.scope("nested", ctx.state(Operation).updating(apply=abs)):
            assert ctx.state(Operation).apply(-4) == 4

        assert ctx.state(Operation).apply(4) == 8


def test_equality_and_hash_follow_function_identity() -> None:
    assert Operation(apply=double) == Operation(apply=double)
    assert hash(Operation(apply=double)) == hash(Operation(apply=double))
    assert Operation(apply=double) != Operation(apply=abs)
    assert Operation(apply=lambda value: value) != Operation(apply=lambda value: value)


def test_functions_can_be_used_in_collections_of_states() -> None:
    operations = {Operation(apply=double), Operation(apply=double), Operation(apply=abs)}

    assert len(operations) == 2


def test_copying_keeps_function_identity() -> None:
    operation = Operation(apply=double)

    assert deepcopy(operation).apply is double
    assert deepcopy(operation) == operation
    assert deepcopy(operation) is not operation


def test_function_state_is_not_serializable() -> None:
    assert Operation.__SERIALIZABLE__ is False
    assert Operation.json_schema(required=False) is None

    with raises(TypeError, match="cannot be represented using json schema"):
        Operation.json_schema()

    with raises(ValueError, match="Failed to encode Operation to json"):
        Operation(apply=double).to_json()

    with raises(TypeError, match="Can't convert"):
        Operation(apply=double).to_basic_object()


def test_to_mapping_keeps_function_identity() -> None:
    assert Operation(apply=double).to_mapping()["apply"] is double
    assert Operation(apply=double).to_mapping(recursive=False)["apply"] is double


def test_str_shows_the_function() -> None:
    assert "double" in str(Operation(apply=double))


def test_function_and_plain_data_can_be_mixed() -> None:
    class Mixed(State):
        name: str
        apply: Callable[[int], int]
        count: int = 0

    mixed = Mixed(name="n", apply=double)

    assert mixed.updating(count=2).apply is double
    assert Mixed.from_mapping({"name": "n", "apply": double}) == mixed
    assert mixed.to_mapping() == {"name": "n", "apply": double, "count": 0}


def test_optional_function_attribute() -> None:
    class Optional(State):
        apply: Callable[[int], int] | None = None

    assert Optional().apply is None
    assert Optional(apply=double).apply is double

    with raises(ValidationError):
        Optional(apply=3)  # pyright: ignore[reportArgumentType]


def test_function_attribute_may_hold_a_serializable_override_specification() -> None:
    class Named(State):
        apply: Annotated[Callable[[int], int], Specification({"type": "string"})]

    assert Named.__SERIALIZABLE__ is True


def test_class_variables_are_not_attributes() -> None:
    class Registry(State):
        registry: ClassVar[dict[str, int]] = {}
        apply: Callable[[int], int]

    assert [field.name for field in Registry.__FIELDS__] == ["apply"]
    assert Registry.registry == {}
    assert "registry" not in Registry(apply=double).to_mapping()


def test_methods_are_regular_methods() -> None:
    class Calculator(State):
        factor: int

        def multiply(self, value: int) -> int:
            return value * self.factor

    assert Calculator(factor=3).multiply(2) == 6
    assert [field.name for field in Calculator.__FIELDS__] == ["factor"]


# --- protocols ---------------------------------------------------------------


def test_runtime_protocol_attribute_accepts_matching_objects() -> None:
    class Holder(State):
        runner: Runner

    impl = Impl()

    assert Holder(runner=impl).runner is impl

    with raises(ValidationError, match=r"\.runner"):
        Holder(runner=3)  # pyright: ignore[reportArgumentType]

    with raises(ValidationError):
        Holder(runner=object())  # pyright: ignore[reportArgumentType]


def test_runtime_protocol_attribute_is_not_serializable() -> None:
    class Holder(State):
        runner: Runner

    assert Holder.__SERIALIZABLE__ is False


def test_unchecked_protocol_attribute_fails_validation_with_hint() -> None:
    class Holder(State):
        runner: UncheckedRunner

    with raises(ValidationError, match="runtime_checkable"):
        Holder(runner=Impl())


def test_protocol_attribute_is_checked_by_specialized_instance_check() -> None:
    class Box[T](State):
        value: T

    assert isinstance(Box(value=Impl()), Box[Runner])  # pyright: ignore[reportArgumentType]
    assert not isinstance(Box(value=3), Box[Runner])  # pyright: ignore[reportArgumentType]


def test_callable_protocol_attribute_holds_functions() -> None:
    @runtime_checkable
    class Handler(Protocol):
        async def __call__(self, value: int) -> int: ...

    class Holder(State):
        handler: Handler

    holder = Holder(handler=async_double)

    assert holder.handler is async_double
    assert holder.updating(handler=async_double) == holder

    with raises(ValidationError, match=r"\.handler"):
        Holder(handler=3)  # pyright: ignore[reportArgumentType]


@mark.asyncio
async def test_callable_protocol_attribute_can_be_awaited() -> None:
    @runtime_checkable
    class Handler(Protocol):
        async def __call__(self, value: int) -> int: ...

    class Holder(State):
        handler: Handler

    assert await Holder(handler=async_double).handler(3) == 6
