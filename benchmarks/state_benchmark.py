"""Micro benchmarks of State operations.

Run with ``make benchmark`` or ``uv run python benchmarks/state_benchmark.py``.

    --json PATH      write the results as json
    --compare PATH   print the ratio against results written earlier
    --filter TEXT    run the cases whose name contains TEXT only
    --budget SECONDS time spent measuring each case (default 0.2)

Every case reports the best of several repeats in nanoseconds per call - the
least disturbed run, which is the one worth comparing between revisions. Absolute
numbers depend on the machine, compare runs made on the same one.
"""

import argparse
import asyncio
import copy
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from timeit import Timer
from typing import Annotated, Any
from uuid import UUID

from haiway import Alias, State, ctx, statemethod


class Mode(StrEnum):
    FAST = "fast"
    SLOW = "slow"


class Simple(State):
    number: int
    text: str
    ratio: float
    flag: bool


class Defaulted(State):
    number: int
    text: str = "text"
    ratio: float = 1.0
    flag: bool = False


class Aliased(State):
    identifier: Annotated[int, Alias("id")]
    display: Annotated[str, Alias("name")]


class Rich(State):
    identifier: UUID
    created: datetime
    location: Path
    mode: Mode
    tags: Sequence[str]
    scores: Mapping[str, int]
    values: Sequence[int]


class Inner(State):
    number: int
    text: str


class Middle(State):
    inner: Inner
    label: str


class Outer(State):
    middle: Middle
    items: Sequence[Inner]


class Box[T](State):
    value: T


class Operation(State):
    apply: Callable[[int], int]
    label: str = "operation"


class Large(State):
    values: Sequence[int]
    items: Sequence[Inner]
    mapping: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class PlainSimple:
    number: int
    text: str
    ratio: float
    flag: bool


SIMPLE_JSON: str = '{"number": 1, "text": "t", "ratio": 1.5, "flag": true}'


def _double(value: int) -> int:
    return value * 2


class Stated(State):
    number: int

    @statemethod
    def add(self, value: int) -> int:
        return self.number + value


SIMPLE_KWARGS: Mapping[str, Any] = {"number": 1, "text": "text", "ratio": 1.5, "flag": True}
ALIASED_KWARGS: Mapping[str, Any] = {"id": 1, "name": "name"}
RICH_KWARGS: Mapping[str, Any] = {
    "identifier": UUID(int=1),
    "created": datetime(2026, 1, 2, 3, 4, 5),
    "location": Path("/tmp/file"),
    "mode": Mode.FAST,
    "tags": ("a", "b", "c"),
    "scores": {f"key{index}": index for index in range(10)},
    "values": tuple(range(10)),
}
RICH_RAW: Mapping[str, Any] = {
    "identifier": str(UUID(int=1)),
    "created": "2026-01-02T03:04:05",
    "location": "/tmp/file",
    "mode": "fast",
    "tags": ["a", "b", "c"],
    "scores": {f"key{index}": index for index in range(10)},
    "values": list(range(10)),
}
OUTER_RAW: Mapping[str, Any] = {
    "middle": {"inner": {"number": 1, "text": "a"}, "label": "l"},
    "items": [{"number": index, "text": "t"} for index in range(10)],
}
LARGE_KWARGS: Mapping[str, Any] = {
    "values": tuple(range(1000)),
    "items": tuple(Inner(number=index, text="t") for index in range(100)),
    "mapping": {f"key{index}": index for index in range(100)},
}
LARGE_RAW: Mapping[str, Any] = {
    "values": list(range(1000)),
    "items": [{"number": index, "text": "t"} for index in range(100)],
    "mapping": {f"key{index}": index for index in range(100)},
}


def cases() -> Mapping[str, Callable[[], object]]:
    """The calls to measure by name - the inputs are prepared once, ahead of them."""
    simple: Simple = Simple(**SIMPLE_KWARGS)
    simple_equal: Simple = Simple(**SIMPLE_KWARGS)
    rich: Rich = Rich(**RICH_KWARGS)
    rich_json: str = rich.to_json()
    outer: Outer = Outer.validate(OUTER_RAW)
    outer_json: str = outer.to_json()
    large: Large = Large(**LARGE_KWARGS)
    large_json: str = large.to_json()
    box_type: type[Box[int]] = Box[int]
    box: Box[int] = box_type(value=1)
    rich_equal: Rich = Rich(**RICH_KWARGS)
    operation: Operation = Operation(apply=_double)
    plain: PlainSimple = PlainSimple(**SIMPLE_KWARGS)
    aliased: Aliased = Aliased(**ALIASED_KWARGS)
    stated: Stated = Stated(number=1)

    return {
        # creation
        "create/simple": lambda: Simple(**SIMPLE_KWARGS),
        "create/defaulted (defaults only)": lambda: Defaulted(number=1),
        "create/aliased (by alias)": lambda: Aliased(**ALIASED_KWARGS),
        "create/aliased (by name)": lambda: Aliased(identifier=1, display="n"),
        "create/rich (live values)": lambda: Rich(**RICH_KWARGS),
        "create/function attribute": lambda: Operation(apply=_double),
        "create/generic variant": lambda: Box[int](value=1),
        "create/large (1000 + 100 states + 100 map)": lambda: Large(**LARGE_KWARGS),
        "reference/frozen dataclass create": lambda: PlainSimple(**SIMPLE_KWARGS),
        # validation of input
        "validate/instance (returned as is)": lambda: Simple.validate(simple),
        "validate/mapping simple": lambda: Simple.validate(SIMPLE_KWARGS),
        "validate/mapping rich (raw spellings)": lambda: Rich.validate(RICH_RAW),
        "validate/mapping nested (3 levels + 10 items)": lambda: Outer.validate(OUTER_RAW),
        "validate/mapping large (raw)": lambda: Large.validate(LARGE_RAW),
        "validate/nested instance attribute": lambda: Middle(inner=outer.middle.inner, label="l"),
        # updates
        "update/one attribute": lambda: simple.updating(number=2),
        "update/all attributes": lambda: simple.updating(
            number=2, text="other", ratio=2.5, flag=False
        ),
        "update/by alias": lambda: aliased.updating(id=2),
        "update/nothing (same instance)": simple.updating,
        "update/nested attribute": lambda: outer.updating(middle=outer.middle.updating(label="x")),
        "update/large, one attribute": lambda: large.updating(mapping={"a": 1}),
        "reference/dataclass replace": lambda: replace(plain, number=2),
        # reading
        "read/attribute": lambda: simple.number,
        "read/nested attribute": lambda: outer.middle.inner.number,
        "read/path (State._.attribute)": lambda: Simple._.number(simple),  # pyright: ignore[reportAttributeAccessIssue, reportCallIssue]
        # comparison and identity
        "equal/simple": lambda: simple == simple_equal,
        "equal/rich": lambda: rich == rich_equal,
        "hash/simple": lambda: hash(simple),
        "hash/rich": lambda: hash(rich),
        "hash/function attribute": lambda: hash(operation),
        # type checks
        "isinstance/plain class": lambda: isinstance(simple, Simple),
        "isinstance/specialized variant": lambda: isinstance(box, box_type),
        "specialize/cached lookup": lambda: Box[int],
        # conversion
        "to_mapping/simple": simple.to_mapping,
        "to_mapping/rich": rich.to_mapping,
        "to_mapping/nested": outer.to_mapping,
        "to_basic_object/simple": simple.to_basic_object,
        "to_basic_object/rich": rich.to_basic_object,
        "to_basic_object/nested": outer.to_basic_object,
        "to_basic_object/large": large.to_basic_object,
        "to_json/simple": simple.to_json,
        "to_json/rich": rich.to_json,
        "to_json/nested": outer.to_json,
        "to_json/large": large.to_json,
        "from_json/simple": lambda: Simple.from_json(
            '{"number":1,"text":"t","ratio":1.5,"flag":true}'
        ),
        "from_json/rich": lambda: Rich.from_json(rich_json),
        "from_json/nested": lambda: Outer.from_json(outer_json),
        "from_json/large": lambda: Large.from_json(large_json),
        "json_schema/rich": Rich.json_schema,
        # representation and copies
        "str/simple": lambda: str(simple),
        "repr/rich": lambda: repr(rich),
        "copy/shallow": lambda: copy.copy(simple),
        "copy/deep simple": lambda: copy.deepcopy(simple),
        "copy/deep nested": lambda: copy.deepcopy(outer),
        # statemethod - the class call resolves the state from the scope main enters
        "statemethod/class call (state from ctx)": lambda: Stated.add(1),
        "statemethod/instance call": lambda: stated.add(1),
        "statemethod/instance access": lambda: stated.add,
        "statemethod/class access": lambda: Stated.add,
        # definition
        "define/class with four attributes": _define_simple,
        "define/generic class and a variant": _define_generic,
    }


def _define_simple() -> object:
    class Defined(State):
        number: int
        text: str
        ratio: float
        flag: bool

    return Defined


def _define_generic() -> object:
    class Defined[T](State):
        value: T

    return Defined[int]


def measure(
    call: Callable[[], object],
    /,
    budget: float,
    repeats: int = 5,
) -> float:
    timer: Timer = Timer(call)
    loops, elapsed = timer.autorange()
    # sized so that every repeat takes its share of the budget
    loops = max(1, int(budget / repeats / (elapsed / loops)))
    best: float = min(timer.repeat(repeat=repeats, number=loops))
    return best / loops * 1e9


def format_time(nanoseconds: float) -> str:
    if nanoseconds >= 1e6:
        return f"{nanoseconds / 1e6:9.2f} ms"

    if nanoseconds >= 1e3:
        return f"{nanoseconds / 1e3:9.2f} us"

    return f"{nanoseconds:9.1f} ns"


async def run(arguments: argparse.Namespace) -> int:
    async with ctx.scope("benchmark", Stated(number=1)):
        return measure_all(arguments)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--filter", default="")
    parser.add_argument("--budget", type=float, default=0.2)
    return asyncio.run(run(parser.parse_args()))


def measure_all(arguments: argparse.Namespace) -> int:
    baseline: Mapping[str, float] = (
        json.loads(arguments.compare.read_text()) if arguments.compare else {}
    )
    results: dict[str, float] = {}
    selected = {name: case for name, case in cases().items() if arguments.filter in name}
    width: int = max((len(name) for name in selected), default=0)

    print(f"python {sys.version.split()[0]}, budget {arguments.budget}s per case")
    for name, call in selected.items():
        nanoseconds: float = measure(call, budget=arguments.budget)
        results[name] = nanoseconds
        line: str = f"{name:<{width}}  {format_time(nanoseconds)}"
        if (previous := baseline.get(name)) is not None:
            line += f"   x{nanoseconds / previous:5.2f}  (was {format_time(previous).strip()})"

        print(line)

    if arguments.json:
        arguments.json.write_text(json.dumps(results, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
