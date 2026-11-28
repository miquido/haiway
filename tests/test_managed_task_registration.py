import math
from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from pytest import mark, raises

from haiway import State
from haiway.context.managed import (
    RegisteredTask,
    RegisteredTaskCall,
    TaskRetryPolicy,
    managed_task,
)
from haiway.context.managed.encoding import encode_task_result
from haiway.context.managed.registration import registered_tasks_by_name


class A(State):
    n: int


class B(State):
    m: int


class R(State):
    x: float


class Box[Value](State):
    value: Value


class Names(State):
    values: Mapping[str, str]
    items: Sequence[str] = ()


def _register_process() -> RegisteredTask[A, R]:
    @managed_task()
    async def process(argument: A) -> R:
        return R(x=argument.n)

    return process


@managed_task(name="r")
async def named(argument: A) -> R:
    return R(x=argument.n)


@managed_task()
async def process(argument: A) -> R:
    return R(x=argument.n)


@managed_task()
async def nothing(argument: A) -> None:
    return None


@managed_task()
async def boxed(argument: Box[int]) -> Box[str]:
    return Box(value=str(argument.value))


@managed_task()
async def names(argument: Names) -> None:
    return None


def test_name_is_given() -> None:
    assert named.name == "r"


def test_name_defaults_to_function_name() -> None:
    assert process.name == "process"


def test_name_is_the_bare_name_of_a_nested_function() -> None:
    def outer() -> RegisteredTask[A, R]:
        @managed_task()
        async def inner(argument: A) -> R:
            return R(x=1)

        return inner

    assert outer().name == "inner"


def test_given_name_wins_over_function_name() -> None:
    @managed_task(name="r")
    async def other(argument: A) -> R:
        return R(x=1)

    assert other.name == "r"


def test_empty_given_name_raises() -> None:
    with raises(ValueError):
        managed_task(name="")


def test_function_without_name_raises_type_error() -> None:
    class Callable:
        async def __call__(self, argument: A) -> R:
            return R(x=1)

    with raises(TypeError):
        managed_task()(Callable())


def test_function_with_empty_name_raises_value_error() -> None:
    async def function(argument: A) -> R:
        return R(x=1)

    function.__name__ = ""

    with raises(ValueError):
        managed_task()(function)


def test_function_with_non_string_name_raises_type_error() -> None:
    class Callable:
        __name__: Any = 5

        async def __call__(self, argument: A) -> R:
            return R(x=1)

    with raises(TypeError):
        managed_task()(Callable())


def test_given_name_does_not_need_function_name() -> None:
    class Callable:
        async def __call__(self, argument: A) -> R:
            return R(x=1)

    assert managed_task(name="r")(Callable()).name == "r"


def test_applied_directly_raises_type_error() -> None:
    async def function(argument: A) -> R:
        return R(x=1)

    decorating: Any = managed_task

    with raises(TypeError):
        decorating(function)


def test_docstring_explains_the_default_name() -> None:
    assert managed_task.__doc__ is not None
    assert "__name__" in managed_task.__doc__
    assert "rename" in managed_task.__doc__.lower()


_DEFAULT = A(n=1)


async def _int_argument(a: int) -> R: ...
async def _two_arguments(a: A, b: B) -> R: ...
async def _no_argument() -> R: ...
async def _default_argument(a: A = _DEFAULT) -> R: ...
async def _keyword_only(*, a: A) -> R: ...
async def _variadic(*a: A) -> R: ...
async def _optional_argument(a: A | None) -> R: ...
async def _int_result(a: A) -> int: ...
async def _optional_result(a: A) -> R | None: ...


def _with_annotations(annotations: dict[str, Any]) -> Any:
    async def function(a: Any) -> None: ...

    function.__annotations__ = annotations
    return function


_unannotated = _with_annotations({"return": R})
_unannotated_result = _with_annotations({"a": A})
_state_instance_result = _with_annotations({"a": A, "return": "Missing"})
_unknown_argument = _with_annotations({"a": "Missing", "return": R})


@mark.parametrize(
    "function",
    (
        _int_argument,
        _two_arguments,
        _no_argument,
        _default_argument,
        _keyword_only,
        _variadic,
        _unannotated,
        _optional_argument,
        _int_result,
        _unannotated_result,
        _optional_result,
        _state_instance_result,
        _unknown_argument,
    ),
)
def test_unsuitable_function_raises_type_error(function: Any) -> None:
    with raises(TypeError):
        managed_task(name="r")(function)


def test_non_callable_raises_type_error() -> None:
    bad: Any = 5

    with raises(TypeError):
        managed_task(name="r")(bad)


def test_result_class_is_the_annotation() -> None:
    assert process.result is R
    assert nothing.result is type(None)
    assert boxed.result is Box[str]


def test_quoted_annotations_are_resolved() -> None:
    async def function(argument: Any) -> Any:
        return R(x=1)

    function.__annotations__ = {"argument": "A", "return": "R"}
    task = managed_task(name="q")(function)

    assert task.result is R
    assert task(A(n=1)).argument == '{"n":1}'
    assert task(A(n=1)).argument == '{"n":1}'


def test_retry_override_is_kept() -> None:
    policy = TaskRetryPolicy(delays=(0.1,))

    @managed_task(retry=policy)
    async def retried(argument: A) -> None:
        return None

    assert retried.retry == policy
    assert process.retry is None


def test_call_holds_name_argument_result_and_registration() -> None:
    call = process(A(n=3))

    assert call.name == "process"
    assert call.argument == '{"n":3}'
    assert call.result is R
    assert call.registration == process.registration
    assert nothing(A(n=3)).result is type(None)


def test_call_of_a_specialized_generic_argument() -> None:
    assert boxed(Box[int](value=1)).argument == '{"value":1}'
    wrong: Any = Box[str](value="a")

    with raises(TypeError):
        boxed(wrong)


@mark.parametrize("argument", (B(m=1), None, '{"n":1}', 1))
def test_call_with_wrong_argument_raises_type_error(argument: Any) -> None:
    with raises(TypeError):
        process(argument)


def test_call_with_nan_raises_value_error() -> None:
    class Floating(State):
        x: float

    @managed_task()
    async def floating(argument: Floating) -> None:
        return None

    with raises(ValueError):
        floating(Floating(x=math.nan))


@mark.parametrize(
    "argument",
    (
        Names(values={"k": "a\x00b"}),
        Names(values={"a\x00b": "v"}),
        Names(values={}, items=("fine", "a\x00")),
        Names(values={"\x00": ""}),
    ),
)
def test_call_with_nul_raises_value_error(argument: Names) -> None:
    with raises(ValueError):
        names(argument)


def test_call_with_escaped_nul_text_is_accepted() -> None:
    argument = Names(values={"k": "\\u0000"})

    assert names(argument).argument == '{"values":{"k":"\\\\u0000"},"items":[]}'


def test_call_with_nested_non_ascii_text_is_accepted() -> None:
    assert names(Names(values={"ключ": "значение"}, items=("😀",))).name == "names"


@mark.asyncio
async def test_bind_decodes_stored_argument_and_runs() -> None:
    call = process.bind('{"n":4}')

    assert await call() == R(x=4)


@mark.asyncio
async def test_bind_ignores_unknown_keys() -> None:
    call = process.bind('{"n":4,"added_later":true}')

    assert await call() == R(x=4)


@mark.asyncio
async def test_bind_can_be_run_for_each_attempt() -> None:
    call = process.bind('{"n":4}')

    assert await call() == await call()


@mark.parametrize("stored", ("{", "[]", '{"n":"x"}', "{}", '{"n":NaN}', ""))
def test_bind_raises_value_error_when_decoding_fails(stored: str) -> None:
    with raises(ValueError):
        process.bind(stored)


def test_bind_runs_nothing_before_it_is_called() -> None:
    calls: list[int] = []

    @managed_task()
    async def recording(argument: A) -> None:
        calls.append(argument.n)

    recording.bind('{"n":1}')

    assert calls == []


def test_registration_is_drawn_for_each_application() -> None:
    first = _register_process()
    second = _register_process()

    assert isinstance(first.registration, UUID)
    assert first.registration != second.registration
    assert first.name == second.name
    assert first(A(n=1)).registration != second(A(n=1)).registration


def test_applying_one_decorator_twice_draws_two_registrations() -> None:
    decorator = managed_task(name="r")

    async def first(argument: A) -> R:
        return R(x=1)

    async def second(argument: A) -> R:
        return R(x=2)

    assert decorator(first).registration != decorator(second).registration


def test_tasks_of_one_name_are_rejected_together() -> None:
    with raises(ValueError, match="tasks"):
        registered_tasks_by_name((_register_process(), process))


def test_tasks_of_two_equal_given_names_are_rejected() -> None:
    @managed_task(name="r")
    async def other(argument: B) -> None:
        return None

    with raises(ValueError, match="tasks"):
        registered_tasks_by_name((named, other))


def test_tasks_are_listed_by_name() -> None:
    assert registered_tasks_by_name((named, process, nothing)) == {
        "r": named,
        "process": process,
        "nothing": nothing,
    }
    assert registered_tasks_by_name(()) == {}


def test_registered_task_and_call_have_no_public_constructor() -> None:
    call = process(A(n=1))
    constructing_call: Any = RegisteredTaskCall
    constructing_task: Any = RegisteredTask

    with raises(TypeError):
        constructing_call(
            name="process", argument='{"n":1}', result=R, registration=process.registration
        )
    with raises(TypeError):
        constructing_call(
            object(), name="x", argument="{}", result=R, registration=call.registration
        )
    with raises(TypeError):
        constructing_task()


def test_registered_task_and_call_are_immutable() -> None:
    task: Any = process
    call: Any = process(A(n=1))

    with raises(AttributeError):
        task.name = "other"
    with raises(AttributeError):
        del task.name
    with raises(AttributeError):
        call.argument = "{}"
    with raises(AttributeError):
        call.extra = 1


@mark.parametrize(
    ("value", "encoded"),
    (
        (None, "null"),
        (R(x=1.5), '{"x":1.5}'),
        (Names(values={"k": "\\u0000"}), '{"values":{"k":"\\\\u0000"},"items":[]}'),
    ),
)
def test_result_encoding(value: State | None, encoded: str) -> None:
    assert encode_task_result(value) == encoded


@mark.parametrize(
    "value",
    (
        R(x=math.nan),
        R(x=math.inf),
        Names(values={"k": "a\x00"}),
        Names(values={"a\x00": "v"}),
    ),
)
def test_result_encoding_raises_value_error(value: State) -> None:
    with raises(ValueError):
        encode_task_result(value)


def test_result_encoding_round_trips() -> None:
    assert R.from_json(encode_task_result(R(x=2.5))) == R(x=2.5)
