from pytest import mark, raises

from haiway import MissingContext, State, ctx
from haiway.context.state import StateContext


class ExampleState(State):
    state: str = "default"


class StateThatFailsInit(State):
    required_param: str


class FakeException(Exception):
    pass


@mark.asyncio
async def test_state_is_available_according_to_context():
    # Outside of context, should instantiate with default values
    assert ctx.state(ExampleState).state == "default"

    async with ctx.scope("default"):
        assert ctx.state(ExampleState).state == "default"

        async with ctx.scope("specified", ExampleState(state="specified")):
            assert ctx.state(ExampleState).state == "specified"

            async with ctx.scope("modified", ExampleState(state="modified")):
                assert ctx.state(ExampleState).state == "modified"

            assert ctx.state(ExampleState).state == "specified"

        assert ctx.state(ExampleState).state == "default"

    # Outside of context again, should instantiate with default values
    assert ctx.state(ExampleState).state == "default"


@mark.asyncio
async def test_state_update_updates_local_context():
    # Outside of context, should instantiate with default values
    assert ctx.state(ExampleState).state == "default"

    async with ctx.scope("default"):
        assert ctx.state(ExampleState).state == "default"

        with ctx.updated(ExampleState(state="updated")):
            assert ctx.state(ExampleState).state == "updated"

            with ctx.updated(ExampleState(state="modified")):
                assert ctx.state(ExampleState).state == "modified"

            assert ctx.state(ExampleState).state == "updated"

        assert ctx.state(ExampleState).state == "default"

    # Outside of context again, should instantiate with default values
    assert ctx.state(ExampleState).state == "default"


@mark.asyncio
async def test_exceptions_are_propagated():
    with raises(FakeException):
        async with ctx.scope("outer"):
            async with ctx.scope("inner"):
                raise FakeException()


def test_state_context_outside_scope_with_default_constructor():
    """Test that StateContext.state() can instantiate states outside of any context."""
    # Outside of any context, should successfully instantiate ExampleState()
    state = StateContext.state(ExampleState)
    assert isinstance(state, ExampleState)
    assert state.state == "default"


def test_state_context_outside_scope_with_default_parameter():
    """Test that StateContext.state() uses default parameter outside of any context."""
    default_state = ExampleState(state="custom_default")

    # Should use the provided default instead of instantiating
    state = StateContext.state(ExampleState, default=default_state)
    assert state is default_state
    assert state.state == "custom_default"


def test_state_context_outside_scope_fails_without_default():
    """Test that StateContext.state() raises MissingContext for non-instantiable states."""
    # StateThatFailsInit requires parameters, so instantiation should fail
    # and MissingContext should be raised
    with raises(MissingContext, match="StateContext requested but not defined"):
        StateContext.state(StateThatFailsInit)


def test_ctx_state_outside_scope_fails_without_default():
    """Test that ctx.state() raises MissingContext for non-instantiable states."""
    # StateThatFailsInit requires parameters, so instantiation should fail
    # and MissingContext should be raised
    with raises(MissingContext, match="StateContext requested but not defined"):
        ctx.state(StateThatFailsInit)


def test_state_context_outside_scope_works_with_explicit_default():
    """Test that StateContext.state() uses provided default for non-instantiable states."""
    default_state = StateThatFailsInit(required_param="test_value")

    # Should use the provided default instead of trying to instantiate
    state = StateContext.state(StateThatFailsInit, default=default_state)
    assert state is default_state
    assert state.required_param == "test_value"


@mark.asyncio
async def test_state_that_fails_init_works_within_context():
    """Test that StateThatFailsInit works when provided explicitly in context scope."""
    test_state = StateThatFailsInit(required_param="context_value")

    async with ctx.scope("test", test_state):
        # Should resolve from context
        state = ctx.state(StateThatFailsInit)
        assert state is test_state
        assert state.required_param == "context_value"


def test_check_state_outside_scope():
    """Test that StateContext.check_state() returns False outside of any context."""
    # Outside of any context, should return False
    assert not StateContext.check_state(ExampleState)
    assert not StateContext.check_state(StateThatFailsInit)


def test_current_state_outside_scope():
    """Test that StateContext.current_state() returns empty tuple outside of any context."""
    # Outside of any context, should return empty tuple
    current = StateContext.current_state()
    assert current == ()
    assert isinstance(current, tuple)
