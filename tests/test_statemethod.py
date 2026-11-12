import sys
from collections.abc import Callable
from inspect import iscoroutinefunction

import pytest

from haiway import State, ctx, statemethod
from haiway.context import ContextStateMissing


def _do_stuff(self: Example) -> str:
    """Do the stuff."""
    return self.stuff_doing()


class Example(State):
    stuff_doing: Callable[[], str]

    do_stuff = statemethod(_do_stuff)


@pytest.mark.asyncio
async def test_statemethod_class_call_resolves_from_ctx() -> None:
    async with ctx.scope("ex", Example(stuff_doing=lambda: "from-ctx")):
        assert Example.do_stuff() == "from-ctx"


@pytest.mark.asyncio
async def test_statemethod_instance_call_prefers_instance_over_ctx() -> None:
    # Instance with one behavior
    inst = Example(stuff_doing=lambda: "from-instance")

    # Different behavior present in context
    async with ctx.scope("ex", Example(stuff_doing=lambda: "from-ctx")):
        # Should use provided instance, not context
        assert inst.do_stuff() == "from-instance"


class DecoratedExample(State):
    stuff_doing: Callable[[], str]

    @statemethod
    def do_stuff(self) -> str:
        """Do the stuff."""
        return self.stuff_doing()


def test_statemethod_metadata_and_wrapped() -> None:
    assert DecoratedExample.do_stuff.__name__ == "do_stuff"
    assert DecoratedExample(stuff_doing=lambda: "ok").do_stuff.__name__ == "do_stuff"


@pytest.mark.skipif(sys.flags.optimize > 1, reason="docstrings stripped under -OO")
def test_statemethod_keeps_documentation() -> None:
    assert DecoratedExample.do_stuff.__doc__ == "Do the stuff."
    assert DecoratedExample(stuff_doing=lambda: "ok").do_stuff.__doc__ == "Do the stuff."


def test_statemethod_caches_bound_callables() -> None:
    assert Example.do_stuff is Example.do_stuff
    assert DecoratedExample.do_stuff is DecoratedExample.do_stuff


class Counting(State):
    step: int

    @statemethod
    async def advance(self, value: int) -> int:
        return value + self.step

    @statemethod
    def advance_now(self, value: int) -> int:
        return value + self.step


@pytest.mark.asyncio
async def test_statemethod_async_class_call_awaits_with_ctx_state() -> None:
    async with ctx.scope("count", Counting(step=2)):
        assert await Counting.advance(1) == 3


@pytest.mark.asyncio
async def test_statemethod_async_instance_call_uses_instance() -> None:
    async with ctx.scope("count", Counting(step=2)):
        assert await Counting(step=10).advance(1) == 11


def test_statemethod_keeps_coroutine_function_kind() -> None:
    assert iscoroutinefunction(Counting.advance)
    assert iscoroutinefunction(Counting(step=1).advance)
    assert not iscoroutinefunction(Counting.advance_now)
    assert not iscoroutinefunction(Counting(step=1).advance_now)


@pytest.mark.asyncio
async def test_statemethod_resolves_state_when_the_coroutine_is_created() -> None:
    async with ctx.scope("first", Counting(step=1)):
        pending = Counting.advance(0)

    async with ctx.scope("second", Counting(step=100)):
        assert await pending == 1


@pytest.mark.asyncio
async def test_statemethod_class_call_without_state_in_context_fails() -> None:
    async with ctx.scope("empty"):
        with pytest.raises(ContextStateMissing, match="Counting"):
            Counting.advance_now(1)


def test_statemethod_instance_access_binds_each_time() -> None:
    instance = Counting(step=1)
    assert instance.advance_now is not instance.advance_now
    assert instance.advance_now == instance.advance_now


class Parent(State):
    @statemethod
    def ping(self) -> str:
        return type(self).__name__


class Child(Parent):
    pass


@pytest.mark.asyncio
async def test_statemethod_inherited_class_call_resolves_the_subclass() -> None:
    async with ctx.scope("inherit", Child()):
        assert Child.ping() == "Child"
        assert Child.ping is Child.ping
        assert Child.ping is not Parent.ping
