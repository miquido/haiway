import sys
from collections.abc import Callable

import pytest

from haiway import State, ctx, statemethod


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
