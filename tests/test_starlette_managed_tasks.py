import asyncio
from collections.abc import Iterable, MutableSequence
from typing import Any

import pytest

pytest.importorskip("starlette")

from pytest import MonkeyPatch, mark
from starlette.applications import Starlette
from starlette.requests import Request as HTTPRequest
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Mount, Route

from haiway import ContextPresets, Observability, State, ctx
from haiway.context.managed import (
    ManagedTask,
    ManagedTasks,
    RegisteredTaskCall,
    TaskCompleted,
    TaskOutcome,
    TaskRuntime,
    managed_task,
)
from haiway.starlette import ServerContext, application
from haiway.starlette import context as server_context_module
from tests.asgi import LogCapture, Result, running, send_request
from tests.managed_helpers import Output, Recording, Request, wait_for


class Config(State):
    value: str = "config"


class PoolState(State):
    value: str = "pool"


class PresetState(State):
    value: str = "preset"


class Pool:
    def __init__(self, log: MutableSequence[str]) -> None:
        self.log: MutableSequence[str] = log

    async def __aenter__(self) -> Iterable[State]:
        self.log.append("pool enter")
        return (PoolState(),)

    async def __aexit__(self, *_: Any) -> None:
        self.log.append("pool exit")


class Seen:
    release: asyncio.Event
    started: asyncio.Event
    values: list[str]
    log: MutableSequence[str]

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.release = asyncio.Event()
        self.started = asyncio.Event()
        self.values = []
        self.log = []


seen = Seen()


@managed_task(name="work")
async def work(request: Request) -> Output:
    seen.values.append(f"{ctx.state(Config).value}/{ctx.state(PoolState).value}")
    seen.started.set()
    try:
        await seen.release.wait()

    finally:
        seen.log.append("task ended")

    return Output(n=1)


@managed_task(name="preset")
async def with_preset(request: Request) -> Output:
    seen.values.append(ctx.state(PresetState).value)

    return Output()


@managed_task(name="logs")
async def logs(request: Request) -> Output:
    ctx.log_info("from-the-task")
    return Output()


@pytest.fixture(autouse=True)
def fresh_seen() -> None:
    seen.reset()


handles: list[ManagedTask[Any]] = []


async def outcome_of[Result: State | None](
    context: ServerContext,
    call: RegisteredTaskCall[Result],
) -> TaskOutcome[Result]:
    # as a request of the server does
    with ctx.presets(*context.presets):
        async with ctx.scope("request", *context.request_state):
            return await (await ManagedTasks.spawn(call)).outcome()


async def spawning(request: HTTPRequest) -> Response:
    handles.append(await ManagedTasks.spawn(work(Request())))
    return PlainTextResponse("spawned")


async def availability(request: HTTPRequest) -> Response:
    return PlainTextResponse(str(await ManagedTasks.available()))


@mark.asyncio
async def test_a_request_spawns_a_task_which_runs_in_the_state_of_the_server() -> None:
    # FR-35.1
    handles.clear()
    log: MutableSequence[str] = []
    app: Starlette = application(
        ServerContext(Config(), disposables=(Pool(log),), tasks=TaskRuntime(tasks=(work,))),
        routes=[Route("/example", spawning)],
    )

    async with running(app):
        result: Result = await send_request(app)
        assert result.status == 200 and result.body == b"spawned"
        # the response came back while the task was still held
        await wait_for(seen.started.wait())
        assert not seen.release.is_set()
        seen.release.set()
        assert await wait_for(handles[0].outcome()) == TaskCompleted(Output(n=1))

    assert seen.values == ["config/pool"]


@mark.asyncio
async def test_a_task_still_running_at_shutdown_is_cut_off_before_the_disposables_exit() -> None:
    # FR-35.2
    handles.clear()
    log: MutableSequence[str] = []
    app: Starlette = application(
        ServerContext(Config(), disposables=(Pool(log),), tasks=TaskRuntime(tasks=(work,))),
        routes=[Route("/example", spawning)],
    )

    async with running(app):
        await send_request(app)
        await wait_for(seen.started.wait())
        seen.log = log

    assert log == ["pool enter", "task ended", "pool exit"]


@mark.asyncio
async def test_a_route_of_a_mounted_sub_application_reaches_the_managed_tasks() -> None:
    # FR-35.3
    sub = Starlette(routes=[Route("/available", availability)])
    app: Starlette = application(
        ServerContext(tasks=TaskRuntime(tasks=(work,))),
        routes=[Mount("/sub", app=sub)],
    )

    async with running(app):
        result: Result = await send_request(app, path="/sub/available")

    assert result.body == b"True"


@mark.asyncio
async def test_without_a_runtime_no_managed_tasks_are_available() -> None:
    # FR-35.4
    app: Starlette = application(
        ServerContext(),
        routes=[Route("/available", availability)],
    )

    async with running(app):
        result: Result = await send_request(app, path="/available")

    assert result.body == b"False"


@mark.asyncio
async def test_a_preset_of_the_server_is_entered_by_the_attempt_scope_of_a_task() -> None:
    # FR-35.4
    runtime = TaskRuntime(tasks=(with_preset,))
    context = ServerContext(
        presets=(ContextPresets.of("preset", PresetState(value="from preset")),),
        tasks=runtime,
    )

    async with context.lifespan():
        await outcome_of(context, with_preset(Request()))

    assert seen.values == ["from preset"]


@mark.asyncio
async def test_the_backend_a_preparing_returns_without_trace_headers_records_the_tasks() -> None:
    # FR-35.5
    recordings: list[tuple[Recording, str | None, str | None]] = []

    def preparing(*, traceparent: str | None, tracestate: str | None) -> Observability:
        recording = Recording()
        recordings.append((recording, traceparent, tracestate))
        return recording.observability

    context = ServerContext(observability=preparing, tasks=TaskRuntime(tasks=(logs,)))
    async with context.lifespan():
        await outcome_of(context, logs(Request()))

    assert len(recordings) == 1
    recording, traceparent, tracestate = recordings[0]
    assert (traceparent, tracestate) == (None, None)
    assert recording.messages(containing="from-the-task") == ["from-the-task"]


@mark.asyncio
async def test_the_backend_of_the_runtime_wins_over_the_one_of_the_server() -> None:
    # FR-35.5
    own, declared = Recording(), Recording()
    context = ServerContext(
        observability=declared.observability,
        tasks=TaskRuntime(tasks=(logs,), observability=own.observability),
    )
    async with context.lifespan():
        await outcome_of(context, logs(Request()))

    assert own.messages(containing="from-the-task") == ["from-the-task"]
    assert declared.records == []


@mark.asyncio
async def test_the_default_observability_gives_the_tasks_one_backend_without_a_summary(
    monkeypatch: MonkeyPatch,
) -> None:
    # FR-35.6
    created: list[bool] = []
    original = server_context_module.LoggerObservability

    def spy(*args: Any, debug_context: bool = True) -> Any:
        created.append(debug_context)
        return original(*args, debug_context=debug_context)

    monkeypatch.setattr(server_context_module, "LoggerObservability", spy)
    context = ServerContext(tasks=TaskRuntime(tasks=(logs,)))
    with LogCapture(server_context_module.DEFAULT_LOGGER) as captured:
        async with context.lifespan():
            await outcome_of(context, logs(Request()))
            await outcome_of(context, logs(Request()))

    # one backend for both tasks, the one of the lifespan, and no summary of a tree
    assert created == [False]
    assert len([message for message in captured if "from-the-task" in message]) == 2
    assert [message for message in captured if "Observability summary" in message] == []
