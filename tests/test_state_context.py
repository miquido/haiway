import asyncio
import threading
from collections.abc import Mapping, Sequence
from typing import Any

import pytest

from haiway import (
    ContextPresets,
    ContextStateMissing,
    Immutable,
    Map,
    State,
    asynchronous,
    ctx,
    statemethod,
)

# How a State behaves once handed to the context - stored as the very instance it is,
# found by its exact type, inherited by the scopes and tasks within, and replaced only
# where it is updated. The context keeps instances, it never copies or validates them
# again, so what holds for an instance outside of it holds within.


class Settings(State):
    name: str
    limit: int = 10


class Behavior(State):
    def label(self) -> str:
        return type(self).__name__


class Implementation(Behavior):
    name: str


class Required(State):
    value: int


class Lazy(State):
    created: int = 0


class Box[Value](State):
    value: Value


class Wired(State):
    value: int

    def __init__(self) -> None:
        super().__init__(value=7)


class Holder(State):
    items: Sequence[int]
    mapping: Mapping[str, int]
    payload: Any = None


class Counter(State):
    count: int = 0

    @statemethod
    def doubled(self) -> int:
        return self.count * 2


class Record(Immutable):
    value: int


@pytest.mark.asyncio
async def test_state_is_the_instance_given_to_the_scope() -> None:
    settings = Settings(name="given")
    async with ctx.scope("test", settings):
        assert ctx.state(Settings) is settings

        async with ctx.scope("inner"):
            assert ctx.state(Settings) is settings


@pytest.mark.asyncio
async def test_state_is_found_by_its_exact_type_only() -> None:
    implementation = Implementation(name="implementation")
    async with ctx.scope("test", implementation):
        assert ctx.state(Implementation) is implementation
        assert not ctx.contains_state(Behavior)
        # the subclass is not what is asked for - the base is made from its defaults
        made: Behavior = ctx.state(Behavior)
        assert type(made) is Behavior
        assert made.label() == "Behavior"


@pytest.mark.asyncio
async def test_a_base_and_its_subclass_are_separate_states() -> None:
    async with ctx.scope("test", Behavior(), Implementation(name="sub")):
        assert ctx.state(Behavior).label() == "Behavior"
        assert ctx.state(Implementation).label() == "Implementation"


@pytest.mark.asyncio
async def test_specializations_of_a_generic_are_separate_states() -> None:
    async with ctx.scope("test", Box[int](value=1), Box[str](value="text")):
        assert ctx.state(Box[int]).value == 1
        assert ctx.state(Box[str]).value == "text"
        # a specialization spelled again is the very same class
        assert ctx.state(Box[int]) is ctx.state(Box[int])
        assert not ctx.contains_state(Box[float])


@pytest.mark.asyncio
async def test_validated_and_decoded_instances_are_stored_as_they_are() -> None:
    decoded = Settings.from_json('{"name": "json", "limit": 3}')
    validated = Box[int].validate({"value": "4"})
    async with ctx.scope("test", decoded, validated):
        assert ctx.state(Settings) is decoded
        assert ctx.state(Box[int]) is validated
        assert ctx.state(Box[int]).value == 4


@pytest.mark.asyncio
async def test_values_of_a_state_in_context_are_the_ones_it_holds() -> None:
    payload: list[int] = [1]
    holder = Holder(items=[1, 2], mapping={"a": 1}, payload=payload)
    async with ctx.scope("test", holder):
        found: Holder = ctx.state(Holder)
        assert found.items == (1, 2)
        assert isinstance(found.mapping, Map)
        assert found.payload is payload

        with pytest.raises(AttributeError):
            found.items = ()  # pyright: ignore[reportAttributeAccessIssue]


@pytest.mark.asyncio
async def test_stored_state_wins_over_an_explicit_default() -> None:
    async with ctx.scope("test", Settings(name="stored")):
        assert ctx.state(Settings, default=Settings(name="default")).name == "stored"

    async with ctx.scope("test"):
        default = Settings(name="default")
        assert ctx.state(Settings, default=default) is default
        assert not ctx.contains_state(Settings)  # a default given is not stored


@pytest.mark.asyncio
async def test_a_missing_state_which_can_not_be_made_is_reported() -> None:
    async with ctx.scope("test"):
        with pytest.raises(ContextStateMissing):
            ctx.state(Required)


@pytest.mark.asyncio
async def test_a_state_made_from_its_defaults_stays_in_the_scope_it_was_made_in() -> None:
    async with ctx.scope("outer"):
        async with ctx.scope("inner"):
            made: Lazy = ctx.state(Lazy)
            assert ctx.state(Lazy) is made  # made once, then kept

        assert not ctx.contains_state(Lazy)


@pytest.mark.asyncio
async def test_a_state_with_a_constructor_of_its_own_is_made_through_it() -> None:
    async with ctx.scope("test"):
        assert ctx.state(Wired).value == 7


@pytest.mark.asyncio
async def test_updating_replaces_the_state_within_its_block_only() -> None:
    async with ctx.scope("test", Settings(name="outer")):
        updated = ctx.state(Settings).updating(limit=1)
        with ctx.updating(updated):
            assert ctx.state(Settings) is updated

            async with ctx.scope("inner", Settings(name="inner")):
                assert ctx.state(Settings).name == "inner"

            assert ctx.state(Settings) is updated

        assert ctx.state(Settings).name == "outer"
        assert ctx.state(Settings).limit == 10


@pytest.mark.asyncio
async def test_updating_merges_into_the_state_current_when_entered() -> None:
    async with ctx.scope("test", Settings(name="outer")):
        updating = ctx.updating(Counter(count=2))  # made here, entered below
        async with ctx.scope("inner", Settings(name="inner")):
            with updating:
                assert ctx.state(Counter).count == 2
                assert ctx.state(Settings).name == "inner"  # not the state it was made in

            with updating:  # entered again once left
                assert ctx.state(Settings).name == "inner"


@pytest.mark.asyncio
async def test_a_nested_scope_overrides_without_leaking_out() -> None:
    async with ctx.scope("outer", Settings(name="outer"), Counter(count=1)):
        async with ctx.scope("inner", Settings(name="inner")):
            assert ctx.state(Settings).name == "inner"
            assert ctx.state(Counter).count == 1  # inherited

        assert ctx.state(Settings).name == "outer"


@pytest.mark.asyncio
async def test_preset_state_is_overridden_by_the_state_of_the_scope() -> None:
    presets = ContextPresets.of("preset", Settings(name="preset"), Counter(count=5))
    async with ctx.scope(presets, Settings(name="explicit")):
        assert ctx.state(Settings).name == "explicit"
        assert ctx.state(Counter).count == 5


@pytest.mark.asyncio
async def test_spawned_tasks_see_the_state_as_it_was_when_spawned() -> None:
    proceed = asyncio.Event()

    async def read() -> str:
        await proceed.wait()
        return ctx.state(Settings).name

    async with ctx.scope("test", Settings(name="before")):
        task = ctx.spawn(read)
        with ctx.updating(Settings(name="after")):
            proceed.set()
            assert await task == "before"


@pytest.mark.asyncio
async def test_concurrent_tasks_update_their_own_state() -> None:
    async def updating(index: int) -> int:
        with ctx.updating(Counter(count=index)):
            for _ in range(3):
                await asyncio.sleep(0)
                assert ctx.state(Counter).count == index

            return ctx.state(Counter).count

    async with ctx.scope("test", Counter(count=-1)):
        tasks = [ctx.spawn(updating, index) for index in range(20)]
        assert [await task for task in tasks] == list(range(20))
        assert ctx.state(Counter).count == -1


@pytest.mark.asyncio
async def test_state_is_visible_in_threads_running_for_the_scope() -> None:
    @asynchronous
    def read_in_executor() -> tuple[str, int]:
        return ctx.state(Settings).name, threading.get_ident()

    def read_in_thread() -> str:
        return ctx.state(Settings).name

    async with ctx.scope("test", Settings(name="threaded")):
        name, thread = await read_in_executor()
        assert name == "threaded"
        assert thread != threading.get_ident()
        assert await asyncio.to_thread(read_in_thread) == "threaded"


@pytest.mark.asyncio
async def test_statemethod_follows_the_state_in_context() -> None:
    async with ctx.scope("test", Counter(count=2)):
        assert Counter.doubled() == 4
        with ctx.updating(Counter(count=5)):
            assert Counter.doubled() == 10

        assert Counter.doubled() == 4


@pytest.mark.asyncio
async def test_an_immutable_record_is_carried_inside_a_state() -> None:
    record = Record(value=1)
    async with ctx.scope("test", Holder(items=(), mapping={}, payload=record)):
        assert ctx.state(Holder).payload is record
