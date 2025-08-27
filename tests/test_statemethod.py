from __future__ import annotations

from collections.abc import Callable

import pytest

from haiway import State, ctx, statemethod


class Example(State):
    stuff_doing: Callable[[], str]

    @statemethod
    def do_stuff(self) -> str:
        return self.stuff_doing()


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
