from collections.abc import Mapping, Sequence
from typing import Any
from uuid import UUID

from pytest import mark, raises

from haiway import ContextMissing, Observability, ObservabilityLevel, ObservabilityLink, ctx
from haiway.attributes import ValidationError
from haiway.context import ContextIdentifier

_TRACEPARENT = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"


def _backend(
    *,
    trace_context: Mapping[str, str],
    logs: list[str] | None = None,
    entered: list[Sequence[ObservabilityLink]] | None = None,
) -> Observability:
    def trace_identifying(scope: ContextIdentifier, /) -> UUID:
        return UUID(int=0xABCDEF)

    def log_recording(
        scope: ContextIdentifier,
        /,
        level: ObservabilityLevel,
        message: str,
        *args: Any,
        exception: BaseException | None,
    ) -> None:
        if logs is not None:
            logs.append(f"{level.name} {message % args if args else message}")

    def noop(scope: ContextIdentifier, /, level: ObservabilityLevel, *_: Any, **__: Any) -> None:
        pass

    def scope_entering(scope: ContextIdentifier, /, *, links: Sequence[ObservabilityLink]) -> str:
        if entered is not None:
            entered.append(links)

        return UUID(int=0xABCDEF).hex

    def scope_exiting(scope: ContextIdentifier, /, *, exception: BaseException | None) -> None:
        pass

    def trace_context_encoding(scope: ContextIdentifier, /) -> Mapping[str, str]:
        return trace_context

    return Observability(
        trace_identifying=trace_identifying,
        log_recording=log_recording,
        event_recording=noop,
        metric_recording=noop,
        attributes_recording=noop,
        scope_entering=scope_entering,
        scope_exiting=scope_exiting,
        trace_context_encoding=trace_context_encoding,
    )


def test_current_outside_scope_raises() -> None:
    with raises(ContextMissing):
        ObservabilityLink.current()


@mark.asyncio
async def test_trace_id_is_the_scope_trace_id() -> None:
    async with ctx.scope("origin", observability=_backend(trace_context={})):
        link = ObservabilityLink.current()
        assert link.trace_id.hex == ctx.trace_id()
        assert link.scope_id == ContextIdentifier.current().scope_id


@mark.asyncio
async def test_scopes_are_entered_without_links() -> None:
    entered: list[Sequence[ObservabilityLink]] = []
    async with ctx.scope("root", observability=_backend(trace_context={}, entered=entered)):
        async with ctx.scope("nested"):
            pass

    assert entered == [(), ()]


@mark.asyncio
async def test_valid_trace_context_is_kept() -> None:
    carrier = {"traceparent": _TRACEPARENT, "tracestate": "a=1,b=2"}
    async with ctx.scope("origin", observability=_backend(trace_context=carrier)):
        assert dict(ObservabilityLink.current().trace_context) == carrier


@mark.asyncio
async def test_other_keys_empty_the_trace_context_and_warn() -> None:
    logs: list[str] = []
    carrier = {"traceparent": _TRACEPARENT, "x": "y"}
    async with ctx.scope("origin", observability=_backend(trace_context=carrier, logs=logs)):
        link = ObservabilityLink.current()

    assert not link.trace_context
    assert link.trace_id == UUID(int=0xABCDEF)
    warnings = [log for log in logs if log.startswith("WARNING")]
    assert len(warnings) == 1
    assert "y" not in warnings[0]


@mark.asyncio
@mark.parametrize(
    "carrier",
    [
        {"traceparent": _TRACEPARENT, "tracestate": ",".join(f"k{i}=v" for i in range(33))},
        {"traceparent": _TRACEPARENT, "tracestate": f"{'k' * 257}=v"},
        {"traceparent": _TRACEPARENT, "tracestate": f"k={'v' * 257}"},
        {"traceparent": _TRACEPARENT, "tracestate": "novalue"},
        {"traceparent": "00-xyz"},
        {"traceparent": "00-00000000000000000000000000000000-00f067aa0ba902b7-01"},
        {"traceparent": _TRACEPARENT.upper()},
        {"traceparent": "01-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
        {"traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-0000000000000000-01"},
        {"traceparent": _TRACEPARENT, "tracestate": "," * 1_000_000},
        {"traceparent": _TRACEPARENT, "tracestate": "a=1" + " \t" * 500_000},
        {"traceparent": _TRACEPARENT, "tracestate": "," * 32},
        {"traceparent": "ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"},
    ],
)
async def test_out_of_bounds_trace_context_is_emptied(carrier: Mapping[str, str]) -> None:
    logs: list[str] = []
    async with ctx.scope("origin", observability=_backend(trace_context=carrier, logs=logs)):
        assert not ObservabilityLink.current().trace_context

    assert len([log for log in logs if log.startswith("WARNING")]) == 1


@mark.asyncio
async def test_tracestate_of_32_members_is_kept() -> None:
    carrier = {
        "traceparent": _TRACEPARENT,
        "tracestate": ",".join(f"k{i}=v" for i in range(32)),
    }
    async with ctx.scope("origin", observability=_backend(trace_context=carrier)):
        assert dict(ObservabilityLink.current().trace_context) == carrier


@mark.parametrize(
    "carrier",
    [
        {"x": "y" * 10**6},
        {"traceparent": "00-xyz"},
        {"tracestate": "," * 1_000_000},
    ],
)
def test_construction_rejects_out_of_bounds_trace_context(carrier: Mapping[str, str]) -> None:
    with raises(ValidationError):
        ObservabilityLink(trace_id=UUID(int=1), scope_id=UUID(int=2), trace_context=carrier)


def test_from_json_rejects_out_of_bounds_trace_context() -> None:
    document = (
        '{"trace_id": "00000000-0000-0000-0000-000000000001",'
        ' "scope_id": "00000000-0000-0000-0000-000000000002",'
        ' "trace_context": {"x": "y"}}'
    )
    with raises(ValueError):
        ObservabilityLink.from_json(document)

    valid = ObservabilityLink.from_json(document.replace('{"x": "y"}', "{}"))
    assert not valid.trace_context
