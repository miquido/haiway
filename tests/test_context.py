from haiway import MissingContext, ScopeMetrics, State, ctx
from pytest import mark, raises


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
    executions: int = 0

    async def completion(metrics: ScopeMetrics):
        nonlocal executions
        executions += 1

    async with ctx.scope("outer", completion=completion):
        assert executions == 0

        async with ctx.scope("inner", completion=completion):
            assert executions == 0

        assert executions == 1

    assert executions == 2


@mark.asyncio
async def test_metrics_are_recorded_within_context():
    def verify_example_metrics(state: str):
        async def completion(metrics: ScopeMetrics):
            assert metrics.read(ExampleState, default=ExampleState()).state == state

        return completion

    async with ctx.scope("outer", completion=verify_example_metrics("outer-in-out")):
        ctx.record(ExampleState(state="outer-in"))

        async with ctx.scope("inner", completion=verify_example_metrics("inner")):
            ctx.record(ExampleState(state="inner"))

        ctx.record(
            ExampleState(state="-out"),
            merge=lambda lhs, rhs: ExampleState(state=lhs.state + rhs.state),
        )
