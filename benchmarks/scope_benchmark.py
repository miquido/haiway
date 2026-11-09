"""Micro benchmarks of the context - scopes, state, updates, tasks and events.

Run with ``make benchmark-scope`` or ``uv run python -O benchmarks/scope_benchmark.py``.

    --json PATH      write the results as json
    --compare PATH   print the ratio against results written earlier
    --filter TEXT    run the cases whose name contains TEXT only
    --budget SECONDS time spent measuring each case (default 0.5)

Every case reports the best of several repeats in microseconds per call. The ``floor``
cases are the primitives a scope is made of - a context variable, an async context
manager, a task group and a task - so the difference between them and a scope is the
cost of the context itself.
"""

import argparse
import asyncio
import contextvars
import gc
import json
import sys
from collections.abc import AsyncGenerator, Awaitable, Callable, Mapping
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter

from haiway import State, ctx


class First(State):
    value: int = 1


class Second(State):
    value: int = 2


class Third(State):
    value: int = 3


class Event(State):
    value: int = 0


_VARIABLE: contextvars.ContextVar[int] = contextvars.ContextVar("variable", default=0)


async def measure(
    call: Callable[[], Awaitable[object]],
    budget: float,
    repeats: int = 5,
) -> float:
    for _ in range(50):  # warm up
        await call()

    started: float = perf_counter()
    for _ in range(50):
        await call()

    single: float = max((perf_counter() - started) / 50, 1e-9)
    count: int = max(1, int(budget / repeats / single))
    best: float = float("inf")
    for _ in range(repeats):
        gc.collect()
        started = perf_counter()
        for _ in range(count):
            await call()

        best = min(best, perf_counter() - started)

    return best / count * 1e6


type Case = Callable[[float], Awaitable[float]]


def within_scope(call: Callable[[], Awaitable[object]], depth: int = 1) -> Case:
    # measured within a root scope holding a state, and as many scopes nested in it
    async def run(budget: float) -> float:
        async def nest(level: int) -> float:
            if level == 0:
                return await measure(call, budget)

            async with ctx.scope(f"level{level}"):
                return await nest(level - 1)

        async with ctx.scope("benchmark", First()):
            return await nest(depth - 1)

    return run


def outside_scope(call: Callable[[], Awaitable[object]]) -> Case:
    async def run(budget: float) -> float:
        return await measure(call, budget)

    return run


def sync(call: Callable[[], object]) -> Callable[[], Awaitable[object]]:
    async def awaitable() -> object:
        return call()

    return awaitable


@asynccontextmanager
async def _empty() -> AsyncGenerator[None]:
    yield


async def _variable() -> None:
    _VARIABLE.reset(_VARIABLE.set(1))


async def _empty_context() -> None:
    async with _empty():
        pass


async def _task_group() -> None:
    async with asyncio.TaskGroup():
        pass


async def _work() -> int:
    return 1


async def _task() -> None:
    await asyncio.ensure_future(_work())


async def _root() -> None:
    async with ctx.scope("root"):
        pass


async def _nested() -> None:
    async with ctx.scope("nested"):
        pass


async def _nested_states() -> None:
    async with ctx.scope("nested", First(), Second(), Third()):
        pass


async def _isolated() -> None:
    async with ctx.scope("isolated", isolated=True):
        pass


async def _updating() -> None:
    with ctx.updating(Second(value=5)):
        pass


async def _spawned() -> None:
    await ctx.spawn(_work)


async def _sent() -> None:
    ctx.send(Event(value=1))


def cases() -> Mapping[str, Case]:
    return {
        "floor/context variable set and reset": outside_scope(_variable),
        "floor/empty async context manager": outside_scope(_empty_context),
        "floor/task group": outside_scope(_task_group),
        "floor/task created and awaited": outside_scope(_task),
        "scope/root": outside_scope(_root),
        "scope/nested": within_scope(_nested),
        "scope/nested, 3 states": within_scope(_nested_states),
        "scope/nested, isolated": within_scope(_isolated),
        "scope/nested, 5 levels deep": within_scope(_nested, depth=5),
        "state/lookup": within_scope(sync(lambda: ctx.state(First))),
        "state/lookup, 5 levels deep": within_scope(sync(lambda: ctx.state(First)), depth=5),
        "state/missing, default": within_scope(sync(lambda: ctx.state(Second, default=None))),
        "state/contains": within_scope(sync(lambda: ctx.contains_state(First))),
        "updating/one state": within_scope(_updating),
        "spawn/task created and awaited": within_scope(_spawned),
        "event/sent, no subscriber": within_scope(_sent),
    }


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--filter", default="")
    parser.add_argument("--budget", type=float, default=0.5)
    arguments = parser.parse_args()

    baseline: Mapping[str, float] = (
        json.loads(arguments.compare.read_text()) if arguments.compare else {}
    )
    results: dict[str, float] = {}
    selected = {name: case for name, case in cases().items() if arguments.filter in name}
    width: int = max((len(name) for name in selected), default=0)

    print(
        f"python {sys.version.split()[0]}{'' if __debug__ else ' -O'},"
        f" budget {arguments.budget}s per case"
    )
    for name, case in selected.items():
        microseconds: float = await case(arguments.budget)
        results[name] = microseconds
        line: str = f"{name:<{width}}  {microseconds:9.3f} us"
        if (previous := baseline.get(name)) is not None:
            line += f"   x{microseconds / previous:5.2f}  (was {previous:.3f} us)"

        print(line)

    if arguments.json:
        arguments.json.write_text(json.dumps(results, indent=2) + "\n")

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
