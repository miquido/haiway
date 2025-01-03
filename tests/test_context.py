from pytest import mark, raises

from haiway import MissingContext, State, ctx


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
