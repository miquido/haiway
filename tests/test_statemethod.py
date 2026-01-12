from __future__ import annotations

from collections.abc import Callable

import pytest

from haiway import State, ctx, statemethod


def _do_stuff(self: Example) -> str:
    """Do the stuff."""
    return self.stuff_doing()


class Example(State):
    stuff_doing: Callable[[], str]

    do_stuff = statemethod(_do_stuff)


class ChildExample(Example):
    pass


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


def test_statemethod_metadata_and_wrapped() -> None:
    class DecoratedExample(State):
        stuff_doing: Callable[[], str]

        @statemethod
        def do_stuff(self) -> str:
            """Do the stuff."""
            return self.stuff_doing()

    bound = DecoratedExample.do_stuff
    assert bound.__name__ == "do_stuff"
    assert bound.__doc__ == "Do the stuff."

    instance = DecoratedExample(stuff_doing=lambda: "ok")
    instance_bound = instance.do_stuff
    assert instance_bound.__name__ == "do_stuff"
    assert instance_bound.__doc__ == "Do the stuff."


def test_statemethod_caches_bound_callables() -> None:
    assert Example.do_stuff is Example.do_stuff
    assert Example.do_stuff is not ChildExample.do_stuff


@pytest.mark.asyncio
async def test_statemethod_class_access_uses_owner_type() -> None:
    async with ctx.scope("ex", ChildExample(stuff_doing=lambda: "child")):
        assert ChildExample.do_stuff() == "child"
