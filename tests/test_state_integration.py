import asyncio
import gc
import threading
from collections.abc import Generator, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from types import TracebackType
from typing import Any, final

import pytest

from haiway import (
    Configuration,
    ConfigurationRepository,
    ContextPresets,
    Pagination,
    State,
    asynchronous,
    ctx,
)
from haiway.starlette import ServerContext
from haiway.types.frozen import refuse_assignment, refuse_deletion

# A State handed over to the constructs of haiway - scopes, presets, disposables, tasks,
# events, threads, configuration and the server context - is used as the very instance it
# is, and let go of by each of them once it is done with it. Released through reference
# counting alone: the collector is held off while each scenario runs, so a reference kept
# behind - or a cycle only it could break - shows as a finalizer which never ran.


class _Finalized:
    __slots__ = ("_name", "_released")

    def __init__(self, name: str, released: list[str]) -> None:
        self._name: str = name
        self._released: list[str] = released

    def __del__(self) -> None:
        self._released.append(self._name)


class Payload(State):
    name: str
    marker: Any = None


class Other(State):
    value: int = 0


class Snapshot(State):
    items: Sequence[int]
    mapping: Mapping[str, int]


@final
class Record:
    value: int
    __slots__ = ("value",)

    __setattr__ = refuse_assignment
    __delattr__ = refuse_deletion

    def __init__(self, *, value: int) -> None:
        object.__setattr__(self, "value", value)


class _Releasing:
    # a disposable preparing a state, holding nothing of it once it is left
    __slots__ = ("_prepare",)

    def __init__(self, name: str, released: list[str]) -> None:
        self._prepare: tuple[str, list[str]] | None = (name, released)

    async def __aenter__(self) -> State:
        assert self._prepare is not None  # nosec: B101
        name, released = self._prepare
        self._prepare = None
        return _payload(name, released)

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        pass


async def _released(released: list[str], expected: Sequence[str]) -> bool:
    # a finished task, or a call run in a thread, is let go of shortly after it delivered
    # its result - by the callbacks the event loop runs next, and by the thread finishing
    # its own bookkeeping. bounded, so a reference kept for good still fails
    for _ in range(1_000):
        if sorted(released) == sorted(expected):
            return True

        await asyncio.sleep(0.001)

    return False


@contextmanager
def _collector_held_off() -> Generator[None]:
    gc.collect()
    enabled: bool = gc.isenabled()
    gc.disable()
    try:
        yield

    finally:
        if enabled:
            gc.enable()


def _payload(name: str, released: list[str]) -> Payload:
    return Payload(name=name, marker=_Finalized(name, released))


# --- states are released by the constructs holding them --------------------------


@pytest.mark.asyncio
async def test_states_of_a_scope_are_released_when_it_ends() -> None:
    released: list[str] = []
    with _collector_held_off():
        async with ctx.scope("outer", _payload("given", released)):
            async with ctx.scope("nested", _payload("nested", released)):
                assert ctx.state(Payload).name == "nested"

            assert "nested" in released  # replaced by the nested scope, gone with it
            async with ctx.scope("isolated", isolated=True):
                with ctx.updating(_payload("updated", released)):
                    assert ctx.state(Payload).name == "updated"

            assert ctx.state(Payload).name == "given"

        assert sorted(released) == ["given", "nested", "updated"]


@pytest.mark.asyncio
async def test_states_of_disposables_and_presets_are_released_when_the_scope_ends() -> None:
    released: list[str] = []

    async def preset_factory() -> Other:
        return Other(value=7)

    with _collector_held_off():
        preset = ContextPresets.of("preset", _payload("preset", released), preset_factory)
        async with ctx.scope(preset, disposables=(_Releasing("disposable", released),)):
            # an explicit disposable takes precedence over the preset
            assert ctx.state(Payload).name == "disposable"
            assert ctx.state(Other).value == 7

        assert released == ["disposable"]
        del preset  # the preset owns its static state, until it is dropped as well
        assert sorted(released) == ["disposable", "preset"]


@pytest.mark.asyncio
async def test_tasks_and_threads_release_the_states_they_saw() -> None:
    released: list[str] = []

    @asynchronous
    def read_in_thread() -> str:
        return ctx.state(Payload).name

    async def read_in_task() -> str:
        await asyncio.sleep(0)
        return ctx.state(Payload).name

    with _collector_held_off():
        async with ctx.scope("tasks", _payload("shared", released)):
            names: Sequence[str] = await asyncio.gather(
                *(ctx.spawn(read_in_task) for _ in range(20)),
                *(read_in_thread() for _ in range(5)),
            )
            assert set(names) == {"shared"}

        assert await _released(released, ["shared"])


@pytest.mark.asyncio
async def test_executor_threads_do_not_keep_the_states_of_their_first_caller() -> None:
    # an executor starts its threads when it is first asked to run something - without
    # the GIL a thread inherits the context it is started from by default, which would
    # keep the states of that scope for as long as the thread lives
    released: list[str] = []

    @asynchronous(executor=ThreadPoolExecutor(max_workers=1))
    def read_in_thread() -> str:
        return ctx.state(Payload).name

    with _collector_held_off():
        async with ctx.scope("first", _payload("first", released)):
            assert await read_in_thread() == "first"

        assert await _released(released, ["first"])
        async with ctx.scope("second", _payload("second", released)):
            assert await read_in_thread() == "second"  # the very same thread

        assert await _released(released, ["first", "second"])


@pytest.mark.asyncio
async def test_events_carry_the_instance_and_release_it() -> None:
    released: list[str] = []
    with _collector_held_off():
        async with ctx.scope("events"):
            subscription = ctx.subscribe(Payload)
            sent: Payload = _payload("event", released)
            ctx.send(sent)
            received: Payload = await anext(subscription)
            assert received is sent  # neither copied nor validated again
            del sent, received, subscription

        assert released == ["event"]


@pytest.mark.asyncio
async def test_server_context_releases_the_states_it_prepared() -> None:
    released: list[str] = []
    with _collector_held_off():
        server = ServerContext(Other(value=1), disposables=(_Releasing("prepared", released),))
        async with server.lifespan():
            states: Sequence[State] = server.request_state()
            assert [type(state) for state in states] == [Payload, Other]
            del states

        del server
        assert released == ["prepared"]


# --- the context keeps instances as they are --------------------------------------


@pytest.mark.asyncio
async def test_isolated_scope_inherits_states_and_keeps_its_updates() -> None:
    async with ctx.scope("root", Other(value=1)):
        async with ctx.scope("isolated", isolated=True):
            assert ctx.state(Other).value == 1
            with ctx.updating(Other(value=2)):
                assert ctx.state(Other).value == 2

            assert ctx.state(Other).value == 1

        assert ctx.state(Other).value == 1


@pytest.mark.asyncio
async def test_states_held_by_the_context_add_nothing_for_the_collector() -> None:
    snapshot = Snapshot(items=[1, 2], mapping={"a": 1})
    async with ctx.scope("untracked", snapshot, Other(value=1)):
        for state in (ctx.state(Snapshot), ctx.state(Other)):
            assert not gc.is_tracked(state)

        assert ctx.state(Snapshot) is snapshot


@pytest.mark.asyncio
async def test_states_and_records_are_shared_by_threads_and_tasks_as_they_are() -> None:
    snapshot = Snapshot(items=list(range(50)), mapping={str(i): i for i in range(50)})
    record = Record(value=3)
    document: str = snapshot.to_json()

    @asynchronous
    def convert_in_thread() -> tuple[str, int, int]:
        state: Snapshot = ctx.state(Snapshot)
        return state.to_json(), hash(state), threading.get_ident()

    async def update_in_task(index: int) -> Snapshot:
        await asyncio.sleep(0)
        return ctx.state(Snapshot).updating(items=(index,))

    async with ctx.scope("shared", snapshot):
        converted = await asyncio.gather(*(convert_in_thread() for _ in range(8)))
        updated = await asyncio.gather(*(ctx.spawn(update_in_task, index) for index in range(20)))

    assert {json for json, _, _ in converted} == {document}
    assert {value for _, value, _ in converted} == {hash(snapshot)}
    assert [state.items for state in updated] == [(index,) for index in range(20)]
    assert ctx.state(Snapshot, default=snapshot) is snapshot  # nothing left behind
    assert record.value == 3
    with pytest.raises(AttributeError):
        record.value = 4  # pyright: ignore[reportAttributeAccessIssue]


# --- states made by the helpers ---------------------------------------------------


def test_pagination_survives_json() -> None:
    pagination = Pagination.of(token="next", limit=10, region="eu", page=2)
    restored = Pagination.from_json(pagination.to_json())

    assert restored == pagination
    assert restored.arguments == {"region": "eu", "page": 2}
    assert Pagination.validate(pagination.to_mapping()) == pagination
    assert not gc.is_tracked(restored)


class ServiceConfiguration(Configuration):
    host: str = "localhost"
    ports: Sequence[int] = (80,)


@pytest.mark.asyncio
async def test_configuration_repository_keeps_the_instances_it_is_given() -> None:
    defined = ServiceConfiguration(host="defined", ports=[1, 2])
    async with ctx.scope("configuration", ConfigurationRepository.volatile()):
        await ConfigurationRepository.define(defined)
        loaded = await ServiceConfiguration.load()

    assert loaded is defined
    assert ServiceConfiguration.from_json(defined.to_json()) == defined
    assert not gc.is_tracked(defined)
