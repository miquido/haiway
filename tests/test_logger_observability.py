import asyncio
from collections.abc import Generator
from logging import DEBUG, Handler, Logger, LogRecord, getLogger
from uuid import UUID, uuid4

from pytest import fixture, mark, raises

from haiway import LoggerObservability, ctx
from haiway.context.identifier import ContextIdentifier


class _CollectingHandler(Handler):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[LogRecord] = []

    def emit(
        self,
        record: LogRecord,
    ) -> None:
        self.records.append(record)


@fixture
def logger() -> Generator[tuple[Logger, list[LogRecord]]]:
    handler = _CollectingHandler()
    instance: Logger = getLogger(f"observability-{uuid4().hex}")
    instance.addHandler(handler)
    instance.setLevel(DEBUG)
    yield instance, handler.records
    instance.removeHandler(handler)


def _messages(records: list[LogRecord]) -> list[str]:
    return [record.getMessage() for record in records]


@mark.asyncio
async def test_concurrent_root_scopes_are_summarized_separately(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    # one instance backing two independent trees at once - neither may be
    # mistaken for a scope nested within the other
    instance, records = logger
    observability = LoggerObservability(instance)

    async def worker(index: int) -> None:
        async with ctx.scope(f"root-{index}", observability=observability):
            await asyncio.sleep(0.01)

    await asyncio.gather(worker(1), worker(2))

    messages = _messages(records)
    for index in (1, 2):
        assert any(f"Exiting scope: root-{index}" in message for message in messages)

    assert sum("Observability summary" in message for message in messages) == 2
    # a tree mistaken for a nested one would never complete, leaving the failure
    # to surface only as the framework reporting a broken scope exit
    assert not any("Failed to properly exit observability scope" in m for m in messages)


@mark.asyncio
async def test_deferred_completion_logs_every_outliving_scope(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    # the scope owning the spawned one joins it before leaving, so it completes
    # after that one did - and it still has to report its own exit and duration
    instance, records = logger
    observability = LoggerObservability(instance)

    async def leaf() -> None:
        async with ctx.scope("leaf"):
            await asyncio.sleep(0.01)

    async with ctx.scope("root", observability=observability):
        async with ctx.scope("mid"):
            ctx.spawn(leaf)
            await asyncio.sleep(0)

    messages = _messages(records)
    assert any("Exiting scope: leaf" in message for message in messages)
    assert any("Exiting scope: mid" in message for message in messages)
    # the parent completes only after the scope spawned within it
    leaf_exit = next(i for i, m in enumerate(messages) if "Exiting scope: leaf" in m)
    mid_exit = next(i for i, m in enumerate(messages) if "Exiting scope: mid" in m)
    assert leaf_exit < mid_exit
    assert (
        sum(
            "scope_time" in message and "Observability summary" not in message
            for message in messages
        )
        == 3
    )


@mark.asyncio
async def test_trace_identifier_matches_the_one_resolved_within_the_scope(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    instance, _ = logger

    async with ctx.scope("root", observability=LoggerObservability(instance)) as trace_id:
        # entering a scope and resolving the identity within it must agree
        assert trace_id == ctx.trace_id()


@mark.asyncio
async def test_trace_identifier_is_rendered_as_hex(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    instance, _ = logger

    async with ctx.scope("root", observability=LoggerObservability(instance)) as trace_id:
        # unpadded lowercase hex, so the value pastes straight into a trace backend
        assert len(trace_id) == 32
        assert trace_id == trace_id.lower()
        int(trace_id, 16)


@mark.asyncio
async def test_nested_scopes_inherit_the_trace_identifier_of_their_root(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    instance, _ = logger

    async with ctx.scope("root", observability=LoggerObservability(instance)) as root_trace:
        async with ctx.scope("nested") as nested_trace:
            # one trace per tree - everything below the root correlates with it
            assert nested_trace == root_trace
            assert ctx.trace_id() == root_trace


@mark.asyncio
async def test_concurrent_root_scopes_get_separate_trace_identifiers(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    instance, _ = logger
    observability = LoggerObservability(instance)
    traces: list[str] = []

    async def tree(name: str) -> None:
        async with ctx.scope(name, observability=observability) as trace_id:
            await asyncio.sleep(0)  # interleave the trees
            traces.append(trace_id)

    await asyncio.gather(tree("first"), tree("second"))

    # a single instance backing several trees has to tell them apart, the same
    # way a tracing backend does - the identifier belongs to the tree, not to it
    assert len(set(traces)) == 2


def test_trace_identifier_of_an_untracked_scope_is_zero() -> None:
    # resolving one has no tree to answer for - it reports "no trace" rather
    # than failing, matching how recording within such a scope is skipped
    observability = LoggerObservability(getLogger("untracked"))
    scope_id = uuid4()
    untracked = ContextIdentifier(
        parent_id=scope_id,
        scope_id=scope_id,
        name="untracked",
        path=(scope_id,),
    )

    assert observability.trace_identifying(untracked) == UUID(int=0)


def test_recording_within_an_untracked_scope_is_skipped() -> None:
    # a scope this instance never saw has no store - recording for it must be
    # skipped rather than failing, the same way the OpenTelemetry adapter does
    observability = LoggerObservability(getLogger("untracked"))
    scope_id = uuid4()
    untracked = ContextIdentifier(
        parent_id=scope_id,
        scope_id=scope_id,
        name="untracked",
        path=(scope_id,),
    )

    observability.log_recording(untracked, DEBUG, "message", exception=None)  # pyright: ignore[reportArgumentType]
    observability.event_recording(untracked, DEBUG, event="event", attributes={})  # pyright: ignore[reportArgumentType]
    observability.metric_recording(  # pyright: ignore[reportArgumentType]
        untracked,
        DEBUG,  # pyright: ignore[reportArgumentType]
        metric="metric",
        value=1,
        unit=None,
        kind="counter",
        attributes={},
    )
    observability.attributes_recording(untracked, DEBUG, {"key": "value"})  # pyright: ignore[reportArgumentType]
    observability.scope_exiting(untracked, exception=None)


@mark.asyncio
async def test_summary_is_skipped_without_debug_context(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    instance, records = logger

    async with ctx.scope(
        "root",
        observability=LoggerObservability(instance, debug_context=False),
    ):
        ctx.record_info(attributes={"key": "value"})

    messages = _messages(records)
    assert any("Exiting scope: root" in message for message in messages)
    assert not any("Observability summary" in message for message in messages)


@mark.asyncio
async def test_default_observability_reports_a_failing_scope() -> None:
    # a scope without observability falls back to this implementation, which has
    # to keep reporting failures the way the previous default did
    handler = _CollectingHandler()
    instance: Logger = getLogger("default-failing")
    instance.addHandler(handler)
    instance.setLevel(DEBUG)
    try:
        with raises(ValueError):
            async with ctx.scope("default-failing"):
                raise ValueError("boom")

    finally:
        instance.removeHandler(handler)

    messages = _messages(handler.records)
    assert any("Scope error: boom" in message for message in messages)
    # scope lifecycle describes execution shape, not what the application did
    lifecycle = [
        record
        for record in handler.records
        if "Entering scope" in record.getMessage() or "Exiting scope" in record.getMessage()
    ]
    assert lifecycle
    assert all(record.levelno == DEBUG for record in lifecycle)
    # the default stays lean - no tree retained and nothing summarized
    assert not any("Observability summary" in message for message in messages)


@mark.asyncio
async def test_summary_is_rendered_with_debug_context(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    instance, records = logger

    async with ctx.scope(
        "root",
        observability=LoggerObservability(instance, debug_context=True),
    ):
        async with ctx.scope("nested"):
            ctx.record_info(attributes={"key": "value"})

    summaries = [m for m in _messages(records) if "Observability summary" in m]
    assert len(summaries) == 1
    # the nested scope and what it recorded both belong to the tree
    assert "nested" in summaries[0]
    assert "key" in summaries[0]


@mark.asyncio
async def test_trace_context_is_empty_without_a_backend_encoder(
    logger: tuple[Logger, list[LogRecord]],
) -> None:
    instance, _ = logger
    async with ctx.scope("root", observability=LoggerObservability(instance)):
        # a logger has no distributed trace position to hand out, so there is
        # nothing to propagate - callers of it have to keep working regardless
        assert ctx.trace_context() == {}
