from asyncio import get_running_loop

from pytest import mark, raises

from haiway import MissingContext, ScopeMetrics, State, ctx


class ExampleState(State):
    state: str = "default"


class FakeException(Exception):
    pass


@mark.asyncio
async def test_state_is_available_according_to_context():
    with raises(MissingContext):
        assert ctx.state(ExampleState).state == "default"

    async with ctx.scope("default"):
        assert ctx.state(ExampleState).state == "default"

        async with ctx.scope("specified", ExampleState(state="specified")):
            assert ctx.state(ExampleState).state == "specified"

            async with ctx.scope("modified", ExampleState(state="modified")):
                assert ctx.state(ExampleState).state == "modified"

            assert ctx.state(ExampleState).state == "specified"

        assert ctx.state(ExampleState).state == "default"

    with raises(MissingContext):
        assert ctx.state(ExampleState).state == "default"


@mark.asyncio
async def test_state_update_updates_local_context():
    with raises(MissingContext):
        assert ctx.state(ExampleState).state == "default"

    async with ctx.scope("default"):
        assert ctx.state(ExampleState).state == "default"

        with ctx.updated(ExampleState(state="updated")):
            assert ctx.state(ExampleState).state == "updated"

            with ctx.updated(ExampleState(state="modified")):
                assert ctx.state(ExampleState).state == "modified"

            assert ctx.state(ExampleState).state == "updated"

        assert ctx.state(ExampleState).state == "default"

    with raises(MissingContext):
        assert ctx.state(ExampleState).state == "default"


@mark.asyncio
async def test_exceptions_are_propagated():
    with raises(FakeException):
        async with ctx.scope("outer"):
            async with ctx.scope("inner"):
                raise FakeException()


@mark.asyncio
async def test_completions_are_called_according_to_context_exits():
    completion_future = get_running_loop().create_future()
    nested_completion_future = get_running_loop().create_future()
    executions: int = 0

    def completion(metrics: ScopeMetrics):
        nonlocal executions
        executions += 1
        completion_future.set_result(())

    def nested_completion(metrics: ScopeMetrics):
        nonlocal executions
        executions += 1
        nested_completion_future.set_result(())

    async with ctx.scope("outer", completion=completion):
        assert executions == 0

        async with ctx.scope("inner", completion=nested_completion):
            assert executions == 0

        await nested_completion_future
        assert executions == 1

    await completion_future
    assert executions == 2


@mark.asyncio
async def test_async_completions_are_called_according_to_context_exits():
    completion_future = get_running_loop().create_future()
    nested_completion_future = get_running_loop().create_future()
    executions: int = 0

    async def completion(metrics: ScopeMetrics):
        nonlocal executions
        executions += 1
        completion_future.set_result(())

    async def nested_completion(metrics: ScopeMetrics):
        nonlocal executions
        executions += 1
        nested_completion_future.set_result(())

    async with ctx.scope("outer", completion=completion):
        assert executions == 0

        async with ctx.scope("inner", completion=nested_completion):
            assert executions == 0

        await nested_completion_future
        assert executions == 1

    await completion_future
    assert executions == 2


@mark.asyncio
async def test_metrics_are_recorded_within_context():
    completion_future = get_running_loop().create_future()
    nested_completion_future = get_running_loop().create_future()
    metric: ExampleState = ExampleState()
    nested_metric: ExampleState = ExampleState()

    async def completion(metrics: ScopeMetrics):
        nonlocal metric
        metric = metrics.read(ExampleState, default=ExampleState())
        completion_future.set_result(())

    async def nested_completion(metrics: ScopeMetrics):
        nonlocal nested_metric
        nested_metric = metrics.read(ExampleState, default=ExampleState())
        nested_completion_future.set_result(())

    async with ctx.scope("outer", completion=completion):
        ctx.record(ExampleState(state="outer-in"))

        async with ctx.scope("inner", completion=nested_completion):
            ctx.record(ExampleState(state="inner"))

        await nested_completion_future
        assert nested_metric.state == "inner"

        ctx.record(
            ExampleState(state="-out"),
            merge=lambda lhs, rhs: ExampleState(state=lhs.state + rhs.state),
        )

    await completion_future
    assert metric.state == "outer-in-out"
