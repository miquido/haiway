"""Micro benchmarks of URITemplate - creation, expansion and matching.

Run with ``make benchmark-uri`` or ``uv run python -O benchmarks/uri_template_benchmark.py``.

    --json PATH      write the results as json
    --compare PATH   print the ratio against results written earlier
    --filter TEXT    run the cases whose name contains TEXT only
    --budget SECONDS time spent measuring each case (default 0.2)

Every case reports the best of five repeats per call. The ``reference/`` cases do comparable
work with the standard library alone - the floor a template can be measured against.
"""

import argparse
import json
import sys
from collections.abc import Callable, Mapping
from itertools import count
from pathlib import Path
from timeit import Timer
from urllib.parse import parse_qs, quote, urlencode, urlparse

from haiway import URITemplate

SIMPLE: str = "https://api.example.com/users{/user}"
FULL: str = "https://{tenant}.example.com/files{/name}{?page,tag}{#section}"
SIMPLE_VALUES: Mapping[str, object] = {"user": "alice"}
FULL_VALUES: Mapping[str, object] = {
    "tenant": "acme",
    "name": "a b.txt",
    "page": 2,
    "tag": ["x", "y"],
    "section": "top",
}
SIMPLE_URI: str = "https://api.example.com/users/alice"
FULL_URI: str = "https://acme.example.com/files/a%20b.txt?page=2&tag=x&tag=y#top"
FOREIGN_URI: str = "https://acme.example.org/files/a%20b.txt"

SIMPLE_TEMPLATE: URITemplate = URITemplate.of(SIMPLE)
FULL_TEMPLATE: URITemplate = URITemplate.of(FULL)
UNIQUE: count[int] = count()


def cases() -> Mapping[str, Callable[[], object]]:
    return {
        "create/cached": lambda: URITemplate.of(FULL),
        "create/parse": lambda: URITemplate.of(f"https://x.test/{next(UNIQUE)}{{/name}}{{?page}}"),
        "variables": lambda: FULL_TEMPLATE.variables,
        "expand/simple": lambda: SIMPLE_TEMPLATE.expand(SIMPLE_VALUES),
        "expand/full": lambda: FULL_TEMPLATE.expand(FULL_VALUES),
        "match/simple": lambda: SIMPLE_TEMPLATE.match(SIMPLE_URI),
        "match/full": lambda: FULL_TEMPLATE.match(FULL_URI),
        "match/miss": lambda: FULL_TEMPLATE.match(FOREIGN_URI),
        "matches/full": lambda: FULL_TEMPLATE.matches(FULL_URI),
        "reference/expand-simple": _reference_expand_simple,
        "reference/expand-full": _reference_expand,
        "reference/match-full": _reference_match,
    }


def _reference_expand_simple() -> str:
    return f"https://api.example.com/users/{quote('alice', safe='')}"


def _reference_expand() -> str:
    return (
        f"https://acme.example.com/files/{quote('a b.txt', safe='')}"
        f"?{urlencode([('page', '2'), ('tag', 'x'), ('tag', 'y')])}#top"
    )


def _reference_match() -> object:
    parsed = urlparse(FULL_URI)
    return parse_qs(parsed.query), parsed.path.split("/"), parsed.fragment


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
    return min(timer.repeat(repeat=repeats, number=loops)) / loops * 1e9


def format_time(nanoseconds: float) -> str:
    if nanoseconds >= 1e6:
        return f"{nanoseconds / 1e6:9.2f} ms"

    if nanoseconds >= 1e3:
        return f"{nanoseconds / 1e3:9.2f} us"

    return f"{nanoseconds:9.1f} ns"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0] if __doc__ else None)
    parser.add_argument("--json", type=Path)
    parser.add_argument("--compare", type=Path)
    parser.add_argument("--filter", default="")
    parser.add_argument("--budget", type=float, default=0.2)
    arguments = parser.parse_args()

    baseline: Mapping[str, float] = (
        json.loads(arguments.compare.read_text()) if arguments.compare else {}
    )
    selected = {name: case for name, case in cases().items() if arguments.filter in name}
    width: int = max((len(name) for name in selected), default=0)
    results: dict[str, float] = {}

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
