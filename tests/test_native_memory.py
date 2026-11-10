import copy
import functools
import gc
import json
import math
import os
import random
import sys
import sysconfig
import threading
import types
import weakref
from collections.abc import Callable, Mapping, Sequence, Set
from datetime import date, datetime, time, timedelta
from enum import IntEnum, StrEnum
from functools import Placeholder
from pathlib import Path
from typing import Annotated, Any, Literal, NotRequired, Protocol, TypedDict
from uuid import UUID, SafeUUID

import pytest

from haiway import Alias, Default, Immutable, Map, Meta, State, ValidationError, Validator
from haiway._native import nstruct

# The native storage is checked here for what its tests of behavior would not show: that
# no operation leaks, that the JSON reader stays memory safe on any input, and that the
# native JSON agrees with the standard library both ways. Run under `make sanitize` the
# same tests run against the extension built with AddressSanitizer and UBSan, and with
# HAIWAY_FUZZ_ROUNDS raised they fuzz for longer.

FUZZ_ROUNDS: int = int(os.environ.get("HAIWAY_FUZZ_ROUNDS", "3000"))


class Mode(StrEnum):
    FAST = "fast"
    SLOW = "slow"


class Level(IntEnum):
    LOW = 1
    HIGH = 2


class Settings(TypedDict):
    a: int
    b: NotRequired[str]


def _passing(value: Any) -> Any:
    return value


class Leaf(State):
    number: int
    text: str = "t"


class Checked(State):
    x: Annotated[int, Validator(_passing)]


class Plain(State):
    x: int
    y: int


class Other(State):
    kind: Literal["other"]
    ratio: float


type Tree = int | Sequence[Tree]


class Rich(State):
    identifier: UUID
    created: datetime
    day: date = date(2026, 1, 1)
    hour: time = time(1, 2, 3)
    span: timedelta = timedelta(seconds=5)
    location: Path = Path("a/b")
    mode: Mode = Mode.FAST
    level: Level = Level.LOW
    tags: Sequence[str] = ()
    unique: Set[int] = frozenset()
    scores: Mapping[str, int] = Map()
    pair: tuple[int, str] = (0, "")
    data: bytes = b""
    either: Leaf | Other | None = None
    retry: Checked | Plain | None = None
    settings: Settings | None = None
    meta: Meta = Meta.empty
    tree: Tree = 0
    named: Annotated[str, Alias("display")] = "n"
    made: Sequence[int] = Default(factory=lambda: (1, 2))
    anything: Any = None


class Node(State):
    child: Node | None = None


def _rich(rng: random.Random) -> Rich:
    text: str = rng.choice(["plain", 'quo"te', "back\\slash", "new\nline", "żółw", "😀", "\x01"])
    return Rich(
        identifier=UUID(int=rng.getrandbits(128)),
        created=datetime(2026, rng.randint(1, 12), rng.randint(1, 28), rng.randint(0, 23)),
        tags=tuple(rng.choice([text, "a", "b"]) for _ in range(rng.randint(0, 4))),
        unique=frozenset(rng.randint(0, 9) for _ in range(rng.randint(0, 4))),
        scores=Map(
            {f"k{index}": rng.randint(-(2**70), 2**70) for index in range(rng.randint(0, 3))}
        ),
        pair=(rng.randint(-5, 5), text),
        data=bytes(rng.getrandbits(8) for _ in range(rng.randint(0, 8))),
        either=rng.choice(
            [
                None,
                Leaf(number=rng.randint(0, 9), text=text),
                Other(kind="other", ratio=rng.random()),
            ]
        ),
        retry=rng.choice([None, Checked(x=1), Plain(x=1, y=2)]),
        settings=rng.choice([None, {"a": 1}, {"a": 2, "b": text}]),
        meta=Meta.of(kind=text, count=rng.randint(0, 3)),
        tree=rng.choice([0, (1, (2, (3,))), ()]),
        named=text,
        anything=rng.choice([None, 1, 1.5, text, [1, "x"], {"k": [True, None]}]),
    )


def _blocks() -> int:
    gc.collect()
    return sys.getallocatedblocks()


def _assert_no_leak(operation: Callable[[], None], rounds: int = 1000) -> None:
    # an object kept per round shows as one block or more per round - what an error path
    # keeps in the caches of the interpreter is bounded, and stays well below half of it
    if sys.getallocatedblocks() == 0:
        pytest.skip("the allocator does not count its blocks (PYTHONMALLOC=malloc)")

    for _ in range(rounds):  # warm up every cache the operation fills
        operation()

    before: int = _blocks()
    for _ in range(rounds):
        operation()

    growth: int = _blocks() - before
    assert growth < rounds // 2, f"{growth} blocks kept over {rounds} rounds"


def _swallow(call: Callable[[], Any]) -> None:
    try:
        call()

    except Exception as exc:
        del exc


def test_failed_union_alternative_runs_none_of_its_validators() -> None:
    calls: list[Any] = []

    def recording(value: Any) -> Any:
        calls.append(value)
        return value

    class Recorded(State):
        x: Annotated[int, Validator(recording)]

    class Holder(State):
        value: Recorded | Plain

    # the object fits the first alternative up to its second key - what was read for it
    # is given up with it, and must never be validated into the instance it was read for
    for _ in range(200):
        holder = Holder.from_json('{"value": {"x": 1, "y": 2}}')
        assert holder.value == Plain(x=1, y=2)

    assert calls == []
    read: Any = Holder.from_json('{"value": {"x": 3}}').value
    assert type(read) is Recorded and read.x == 3
    assert calls == [3]


def test_failed_union_alternative_drops_what_its_nested_states_kept_pending() -> None:
    calls: list[Any] = []

    def recording(value: Any) -> Any:
        calls.append(value)
        return value

    class Recorded(State):
        x: Annotated[int, Validator(recording)]

    class Wrapping(State):
        inner: Recorded
        tag: str

    class Loose(State):
        inner: Any
        other: int

    class Holder(State):
        value: Wrapping | Loose

    # the nested instance is read in full before its enclosing one fails at `other`
    for _ in range(200):
        holder = Holder.from_json('{"value": {"inner": {"x": 1}, "other": 2}}')
        assert type(holder.value) is Loose

    assert calls == []


def test_construction_and_update_do_not_leak() -> None:
    rich: Rich = _rich(random.Random(1))
    values: Mapping[str, Any] = {"number": 1, "text": "x"}

    def operation() -> None:
        Leaf(number=1, text="x")
        Leaf(**values)
        rich.updating(tags=("a", "b"), scores={"a": 1})
        rich.updating(display="y")
        _swallow(lambda: Leaf(number="x"))  # pyright: ignore[reportArgumentType]
        _swallow(lambda: Leaf(number=1, unknown=2))  # pyright: ignore[reportCallIssue]
        _swallow(lambda: Leaf())  # noqa: PLW0108  # pyright: ignore[reportCallIssue]
        _swallow(lambda: Rich.validate({"named": "a", "display": "b"}))
        _swallow(lambda: rich.updating(tags=1))  # pyright: ignore[reportArgumentType]

    _assert_no_leak(operation)


def test_validation_does_not_leak() -> None:
    raw: Mapping[str, Any] = json.loads(_rich(random.Random(2)).to_json())

    def operation() -> None:
        Rich.validate(raw)
        _swallow(lambda: Rich.validate({**raw, "tags": [1, 2]}))
        _swallow(lambda: Rich.validate({**raw, "either": {"kind": "nope"}}))

    _assert_no_leak(operation)


def test_json_does_not_leak() -> None:
    rich: Rich = _rich(random.Random(3))
    document: str = rich.to_json()

    def operation() -> None:
        rich.to_json()
        Rich.from_json(document)
        Rich.from_json(document.encode())
        Rich.from_json_array(f"[{document}, {document}]")
        Plain.from_json('{"x": 1, "y": 2}')
        Rich.updating(rich, anything=object()).to_mapping()
        _swallow(lambda: Rich.updating(rich, anything=object()).to_json())
        _swallow(lambda: Rich.from_json(document[:-5]))
        _swallow(lambda: Rich.from_json('{"identifier": 1}'))
        _swallow(lambda: Rich.from_json('{"retry": {"x": 1, "y": 2}}'))

    _assert_no_leak(operation)


def test_conversions_copies_and_comparison_do_not_leak() -> None:
    rich: Rich = _rich(random.Random(4))
    looped: list[Any] = []
    holding: Leaf = Leaf(number=1)
    cyclic: Rich = rich.updating(anything=looped)
    looped.append(cyclic)
    looped.append(holding)

    def operation() -> None:
        rich.to_mapping()
        rich.to_mapping(recursive=False)
        rich.to_basic_object()
        copy.deepcopy(rich)
        copy.deepcopy(cyclic)
        hash(holding)
        _ = rich == copy.deepcopy(rich)

    _assert_no_leak(operation)


def test_immutable_records_do_not_leak() -> None:
    class Record(Immutable):
        a: int
        b: Sequence[int] = ()

    def operation() -> None:
        record = Record(a=1, b=[1])
        _swallow(lambda: object.__setattr__(record, "a", 2))
        _swallow(lambda: object.__delattr__(record, "a"))
        _swallow(lambda: Record())  # noqa: PLW0108  # pyright: ignore[reportCallIssue]

    _assert_no_leak(operation)


class _Slotted:
    __slots__ = ("a", "b", "c")


class _Weakly:
    __slots__ = ("__weakref__",)


class _Plain:
    pass


_ANY: Any = nstruct.compile_spec((nstruct.SPEC_ANY, None, None, None, None, None, ()))


def _entry(name: str) -> tuple[Any, ...]:
    return (sys.intern(name), None, _ANY, lambda: None, False)


@pytest.mark.parametrize(
    "other",
    [_Slotted, _Weakly, _Plain, int],
    ids=["slots", "weakref", "dict", "builtin"],
)
def test_native_storage_refuses_a_layout_it_does_not_own(other: type) -> None:
    # checked natively, whatever the metaclass in front of it lets through - the values
    # live right after the header, where a base like these keeps its own data
    cls: Any = nstruct.StructMeta("Foreign", (nstruct.Struct, other), {"__slots__": ()})
    with pytest.raises(TypeError, match=r"layout of its own|slots, a __dict__"):
        nstruct.compile_plan(cls, (_entry("value"),), ("value",), False)


def test_native_storage_refuses_a_class_reordering_the_attributes_of_its_base() -> None:
    base: Any = nstruct.StructMeta("Base", (nstruct.Struct,), {"__slots__": ()})
    nstruct.compile_plan(base, (_entry("first"), _entry("second")), ("first", "second"), False)
    derived: Any = nstruct.StructMeta("Derived", (base,), {"__slots__": ()})
    with pytest.raises(TypeError, match="keep the attributes of Base first"):
        nstruct.compile_plan(derived, (_entry("second"), _entry("first")), (), False)

    shorter: Any = nstruct.StructMeta("Shorter", (base,), {"__slots__": ()})
    with pytest.raises(TypeError, match="keep the attributes of Base first"):
        nstruct.compile_plan(shorter, (_entry("first"),), (), False)


class _Inner(Immutable):
    a: Any
    b: Any
    c: Any


class _Outer(Immutable):
    f: Any


class _Holder(State):
    inner: Any


def test_records_are_bound_once_for_good() -> None:
    # every value of a record, as of a State, is bound once - which is what lets the
    # native code read them while code of the user runs on them
    record = _Inner(a=1, b=object(), c=2)
    bound: Any = record.b
    references: int = sys.getrefcount(bound)
    with pytest.raises(AttributeError):
        object.__setattr__(record, "b", None)

    with pytest.raises(AttributeError):
        object.__delattr__(record, "b")

    with pytest.raises(TypeError, match="initialized already"):
        record.__init__(a=2, b=3, c=4)  # pyright: ignore[reportUnknownMemberType]

    assert record.b is bound
    assert sys.getrefcount(bound) == references


def test_conversions_of_records_running_code_of_the_user_do_not_leak() -> None:
    class Spelled(datetime):
        def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
            return "2020-01-01T00:00:00"

    def operation() -> None:
        outer = _Outer(f=_Inner(a=Spelled(2020, 1, 1), b="b", c=[Leaf(number=1)]))
        state = _Holder(inner=outer)
        state.to_json()
        state.to_mapping()
        state.to_basic_object()
        state.to_mapping(recursive=False)
        hash(state)
        _ = state == _Holder(inner=outer)
        copy.deepcopy(state)

    _assert_no_leak(operation)


def test_refused_classes_do_not_leak() -> None:
    def operation() -> None:
        _swallow(lambda: types.new_class("Combined", (State, _Slotted)))
        _swallow(lambda: types.new_class("Combined", (Immutable, _Plain)))
        foreign: Any = nstruct.StructMeta("Foreign", (nstruct.Struct, _Slotted), {"__slots__": ()})
        _swallow(lambda: nstruct.compile_plan(foreign, (_entry("value"),), ("value",), False))

    _assert_no_leak(operation)


class _Frozen(State):
    tags: Sequence[str] = ()
    scores: Mapping[str, int] = {}
    unique: Set[int] = frozenset()
    leaves: Sequence[Leaf] = ()
    nested: Sequence[Sequence[int]] = ()
    leaf: Leaf | None = None


def _full() -> _Frozen:
    return _Frozen(
        tags=["a"],
        scores={"a": 1},
        unique={1},
        leaves=[Leaf(number=1)],
        nested=[[1, 2], [3]],
        leaf=Leaf(number=2),
    )


_FREE_THREADED: bool = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda: _Frozen(), id="defaults"),  # noqa: PLW0108
        pytest.param(_full, id="validated"),
        pytest.param(lambda: _Frozen.from_json(_full().to_json()), id="from_json"),
        pytest.param(lambda: _Frozen.validate(_full().to_mapping()), id="validate"),
        pytest.param(lambda: _full().updating(tags=("b",)), id="updating"),
        pytest.param(
            lambda: copy.deepcopy(_full()),
            id="deepcopy",
            # without the GIL only a container nothing else holds is untracked - and the
            # memo of a deep copy holds the containers it copies
            marks=pytest.mark.skipif(_FREE_THREADED, reason="the memo holds the containers"),
        ),
    ],
)
def test_instances_holding_nothing_cyclic_are_left_to_reference_counting(
    make: Callable[[], _Frozen],
) -> None:
    # neither the instance nor the frozen containers it holds can ever be part of a
    # cycle, so none of them is left for the collector to walk
    instance: _Frozen = make()
    assert not gc.is_tracked(instance)
    for name in instance.__match_args__:
        assert not gc.is_tracked(getattr(instance, name)), name


class _Loose(State):
    value: Any


@pytest.mark.parametrize(
    "make",
    [
        pytest.param(lambda record: [1], id="list"),  # pyright: ignore[reportUnknownLambdaType]
        pytest.param(lambda record: ([1],), id="tuple of list"),  # pyright: ignore[reportUnknownLambdaType]
        pytest.param(lambda record: (record,), id="tuple of record"),  # pyright: ignore[reportUnknownLambdaType]
        pytest.param(lambda record: {"a": [1]}, id="dict"),  # pyright: ignore[reportUnknownLambdaType]
        pytest.param(lambda record: Map({"a": (record,)}), id="map of record"),  # pyright: ignore[reportUnknownLambdaType]
    ],
)
def test_instances_which_can_be_part_of_a_cycle_stay_tracked(
    make: Callable[[_Outer], Any],
) -> None:
    record = _Outer(f=[1])  # a record holding a list can be part of a cycle through it
    assert gc.is_tracked(record)
    assert gc.is_tracked(_Loose(value=make(record)))


def test_updates_look_at_what_replaced_the_values() -> None:
    record = _Outer(f=[1])
    untracked = _Loose(value=(1,))
    assert not gc.is_tracked(untracked)
    assert gc.is_tracked(untracked.updating(value=[1]))
    assert gc.is_tracked(untracked.updating(value=(record,)))
    assert not gc.is_tracked(gc_free := untracked.updating(value=(2,)))
    assert gc.is_tracked(gc_free.updating(value=([1],)))


def test_containers_deeper_than_looked_into_stay_tracked() -> None:
    deep: Any = ()
    for _ in range(1_000):  # deeper than is looked into, without exhausting the stack
        deep = (deep,)

    assert gc.is_tracked(_Loose(value=deep))


class _Identified(State):
    identifier: UUID
    scores: Mapping[str, int]
    leaves: Sequence[Leaf]
    aliases: Sequence[UUID] = ()


def test_instances_holding_a_uuid_are_left_to_reference_counting() -> None:
    # a UUID is tracked by the collector, while what it holds - its number and a member of
    # SafeUUID - can never lead back to the instance holding it
    instance = _Identified(
        identifier=UUID(int=7),
        scores={"a": 1},
        leaves=[Leaf(number=1)],
        aliases=[UUID(int=8)],
    )
    assert not gc.is_tracked(instance)
    for name in instance.__match_args__:
        assert not gc.is_tracked(getattr(instance, name)) or name == "identifier", name


class _UUIDSubclass(UUID):
    __slots__ = ("extra",)


def test_instances_holding_a_uuid_which_may_lead_back_stay_tracked() -> None:
    rebound = UUID(int=9)
    object.__setattr__(rebound, "is_safe", [])  # a slot can be rebound to anything
    assert gc.is_tracked(_Loose(value=rebound))
    assert gc.is_tracked(_Loose(value=_UUIDSubclass(int=10)))


def test_cycle_through_a_rebound_uuid_is_collected() -> None:
    finalized: list[bool] = []

    class Finalized:
        def __del__(self) -> None:
            finalized.append(True)

    loop: list[Any] = [Finalized()]
    identifier = UUID(int=11)
    object.__setattr__(identifier, "is_safe", loop)
    state = _Loose(value=(identifier,))
    loop.append(state)  # the cycle: state -> tuple -> uuid -> list -> state
    del state, identifier, loop
    gc.collect()
    assert finalized == [True]


def test_cycle_through_a_record_is_collected() -> None:
    finalized: list[bool] = []

    class Finalized:
        def __del__(self) -> None:
            finalized.append(True)

    loop: list[Any] = []
    record = _Outer(f=loop)
    state = _Loose(value=(record, Finalized()))
    loop.append(state)  # the cycle: state -> tuple -> record -> list -> state
    del state, record, loop
    gc.collect()
    assert finalized == [True]


class _Checked(State):
    value: Annotated[int, Validator(_passing)]


class _Within(State):
    checked: _Checked
    items: Sequence[_Checked] = ()


def test_instances_read_from_json_holding_checked_ones_are_left_to_reference_counting() -> None:
    # what holds an instance bound late is let go of once that one is - from the innermost
    within = _Within.from_json('{"checked": {"value": 1}, "items": [{"value": 2}]}')
    assert not gc.is_tracked(within)
    assert not gc.is_tracked(within.checked)
    assert not gc.is_tracked(within.items)
    assert not gc.is_tracked(within.items[0])


_reentering: list[bool] = []


def _reentrant(value: int) -> int:
    # the class being validated, created and converted while one of its values validates
    if not _reentering:
        _reentering.append(True)
        try:
            _Reentrant(value=value).to_json()
            _Reentrant.from_json(f'{{"value": {value}}}')

        finally:
            _reentering.clear()

    return value


class _Reentrant(State):
    value: Annotated[int, Validator(_reentrant)]


def test_validation_entering_the_class_it_validates_does_not_leak() -> None:
    reentrant = _Reentrant(value=1)

    def operation() -> None:
        _Reentrant(value=2)
        reentrant.updating(value=3)
        _Reentrant.validate({"value": 4})
        _Reentrant.from_json('{"value": 5}')

    _assert_no_leak(operation)


def _interrupting(value: int) -> int:
    if value:  # the default is let through, what is given interrupts
        raise KeyboardInterrupt

    return value


class _Interrupted(State):
    first: Leaf
    value: Annotated[int, Validator(_interrupting)] = 0


def _interrupted(call: Callable[[], Any]) -> None:
    # an exception which is not an error is passed on as it is - never wrapped, or swallowed
    try:
        call()

    except KeyboardInterrupt:
        return

    raise AssertionError("KeyboardInterrupt was not passed on")


def test_interruptions_pass_through_without_leaking() -> None:
    instance = _Interrupted(first=Leaf(number=1))

    def operation() -> None:
        _interrupted(lambda: _Interrupted(first=Leaf(number=1), value=1))
        _interrupted(lambda: instance.updating(value=1))
        _interrupted(lambda: _Interrupted.validate({"first": {"number": 1}, "value": 1}))
        _interrupted(lambda: _Interrupted.from_json('{"first": {"number": 1}, "value": 1}'))

    _assert_no_leak(operation)


def _interrupting_default() -> Any:
    raise KeyboardInterrupt


def test_a_plan_failing_while_it_is_compiled_does_not_leak() -> None:
    # a constant default is validated while the plan is compiled, and an interruption there
    # gives the plan up with what it allocated already
    def operation() -> None:
        cls: Any = nstruct.StructMeta("Failing", (nstruct.Struct,), {"__slots__": ()})
        entries = (
            _entry("first"),
            (sys.intern("second"), None, _ANY, _interrupting_default, True),
        )
        _interrupted(lambda: nstruct.compile_plan(cls, entries, ("first", "second"), False))
        with pytest.raises(TypeError, match="never given a plan"):
            cls()

    _assert_no_leak(operation, rounds=300)


def test_specializations_are_collected_with_their_origin() -> None:
    # an origin and its variants refer to each other - a cycle the collector breaks
    def define() -> tuple[weakref.ref[type[Any]], weakref.ref[type[Any]]]:
        class Box[Value](State):
            value: Value

        variant: Any = Box[int]
        variant(value=1).to_json()
        return weakref.ref(Box), weakref.ref(variant)

    references = [define() for _ in range(10)]
    gc.collect()
    assert all(origin() is None and variant() is None for origin, variant in references)


def _random_items(generator: random.Random, depth: int) -> list[Any]:
    return [_random_value(generator, depth + 1) for _ in range(generator.randrange(3))]


_LEAVES: Mapping[str, Callable[[random.Random, int], Any]] = {
    "int": lambda generator, depth: generator.randrange(1000),
    "str": lambda generator, depth: "x" * generator.randrange(3),
    "uuid": lambda generator, depth: UUID(int=generator.randrange(1 << 64)),
    "none": lambda generator, depth: None,
}

_CONTAINERS: Mapping[str, Callable[[random.Random, int], Any]] = {
    "tuple": lambda generator, depth: tuple(_random_items(generator, depth)),
    "list": _random_items,
    "dict": lambda generator, depth: dict(enumerate(_random_items(generator, depth))),
    "frozenset": lambda generator, depth: frozenset(range(generator.randrange(3))),
    "map": lambda generator, depth: Map(dict(enumerate(_random_items(generator, depth)))),
    "leaf": lambda generator, depth: Leaf(number=generator.randrange(10)),
    "record": lambda generator, depth: _Outer(f=_random_value(generator, depth + 1)),
}


def _random_value(generator: random.Random, depth: int) -> Any:
    kinds: Mapping[str, Callable[[random.Random, int], Any]] = (
        {**_LEAVES, **_CONTAINERS} if depth < 3 else _LEAVES
    )
    return kinds[generator.choice(tuple(kinds))](generator, depth)


def _can_lead_back(value: Any) -> bool:
    # whether anything reachable from a value could ever refer back to what holds it
    pending: list[Any] = [value]
    seen: set[int] = set()
    while pending:
        current: Any = pending.pop()
        if id(current) in seen or (not gc.is_tracked(current) and type(current) in (int, str)):
            continue

        seen.add(id(current))
        if isinstance(current, (list, dict, set)):
            return True

        if isinstance(current, type):
            continue  # a class is not followed - every instance refers to its own

        if isinstance(current, SafeUUID):
            continue

        pending.extend(
            referent for referent in gc.get_referents(current) if not isinstance(referent, type)
        )

    return False


def test_untracked_instances_never_reach_what_could_lead_back_to_them() -> None:
    generator = random.Random(23)
    checked: int = 0
    for _ in range(3000):
        value: Any = _random_value(generator, 0)
        instance = _Loose(value=value)
        if gc.is_tracked(instance):
            continue

        checked += 1
        assert not _can_lead_back(instance), value

    assert checked > 0


def test_classes_are_collected() -> None:
    def define() -> weakref.ref[type[Any]]:
        class Temporary(State):
            value: int
            items: Sequence[Leaf] = ()

        Temporary(value=1, items=[Leaf(number=1)]).to_json()
        return weakref.ref(Temporary)

    references = [define() for _ in range(20)]
    gc.collect()
    assert all(reference() is None for reference in references)


def _depth(node: Node | None) -> int:
    count: int = 0
    while node is not None:
        node = node.child
        count += 1

    return count


@pytest.mark.parametrize("depth", [50, sys.getrecursionlimit() * 2])
def test_nested_documents_are_read_to_any_depth_the_interpreter_allows(depth: int) -> None:
    document: str = '{"child": ' * depth + "null" + "}" * depth
    assert _depth(Node.from_json(document)) == depth


def test_documents_nested_beyond_the_stack_are_refused_without_crashing() -> None:
    depth: int = 1_000_000
    with pytest.raises(ValueError):
        Node.from_json('{"child": ' * depth + "null" + "}" * depth)

    with pytest.raises(ValueError):
        Node.from_json('{"child": ' * depth)


def _same(left: State | None, right: State | None) -> bool:
    if left is None or right is None:
        return left is right

    # NaN is not equal to itself - the spelling of both is what has to match
    return type(left) is type(right) and left.to_json() == right.to_json()


def _oracle(cls: type[State], document: str | bytes) -> State | None:
    try:
        return cls.validate(json.loads(document))

    except Exception:
        return None


@pytest.mark.parametrize("seed", range(4))
def test_native_json_writes_what_the_standard_library_writes(seed: int) -> None:
    rng = random.Random(seed)
    for _ in range(max(FUZZ_ROUNDS // 20, 50)):
        rich: Rich = _rich(rng)
        written: str = rich.to_json()
        assert written == json.dumps(rich.to_basic_object())
        assert Rich.from_json(written) == rich


_ALPHABET: str = '{}[]",:0123456789-+.eEtrufalsnNIy\\u \n' + "abcxyzé😀"


def _mutated(rng: random.Random, document: str) -> str:
    characters: list[str] = list(document)
    for _ in range(rng.randint(1, 4)):
        at: int = rng.randrange(len(characters) + 1)
        operation: float = rng.random()
        if operation < 0.4 and characters:
            del characters[min(at, len(characters) - 1)]

        elif operation < 0.8:
            characters.insert(at, rng.choice(_ALPHABET))

        elif characters:
            characters[min(at, len(characters) - 1)] = rng.choice(_ALPHABET)

    return "".join(characters)


_SPECIAL: Sequence[str | bytes] = (
    "",
    "[]",
    "null",
    '{"identifier": "' + "0" * 32 + '", "created": "2026-01-01"}',
    '{"identifier": 1e400}',
    '{"scores": {"a": ' + "9" * 400 + "}}",
    '{"named": "\\ud800"}',
    '{"named": "\\ud83d\\ude00", "display": "x"}',
    '{"tags": ["a", "a"], "tags": ["b"]}',
    b'{"named": "\xff"}',
    b"\xef\xbb\xbf{}",
    '{"retry": {"x": 1, "y": 2}, "either": {"kind": "other", "ratio": NaN}}',
    '{"tree": ' + "[" * 300 + "]" * 300 + "}",
)


@pytest.mark.parametrize("seed", range(4))
def test_native_json_reads_what_the_standard_library_reads(seed: int) -> None:
    rng = random.Random(seed)
    documents: list[str | bytes] = [*_SPECIAL]
    for _ in range(max(FUZZ_ROUNDS // 4, 100)):
        mutated: str = _mutated(rng, _rich(rng).to_json())
        encoded: bool = rng.random() < 0.2
        documents.append(mutated.encode("utf-8", "surrogatepass") if encoded else mutated)

    for document in documents:
        native: Any = nstruct.from_json(Rich, document, False)
        expected: State | None = _oracle(Rich, document)
        if native is not None:  # read natively - exactly what the standard library reads
            assert _same(native, expected), document

        try:
            read: State | None = Rich.from_json(document)

        except ValueError:
            read = None

        assert _same(read, expected), document


def test_values_read_natively_are_not_nan_sensitive() -> None:
    # a float read natively compares the way one read by the standard library does
    other: Any = nstruct.from_json(Other, '{"kind": "other", "ratio": NaN}', False)
    assert other is not None
    assert math.isnan(other.ratio)


def test_validation_errors_keep_their_position_natively() -> None:
    with pytest.raises(ValueError) as exc:
        Rich.from_json('{"identifier": "' + "0" * 32 + '", "created": "x"}')

    assert isinstance(exc.value.__cause__, ValidationError)
    assert ".created" in str(exc.value.__cause__)


class Shared[Value](State):
    value: Value
    items: Sequence[int] = ()


def test_states_are_used_from_many_threads_at_once() -> None:
    # passes trivially with the GIL - run with PYTHON_GIL=0 on a free-threaded build it is
    # what tells the native code is ready to run without it
    shared: Rich = _rich(random.Random(5))
    document: str = shared.to_json()
    variants: list[type[Any]] = []
    failures: list[BaseException] = []
    barrier = threading.Barrier(8)

    def work(index: int) -> None:
        try:
            barrier.wait()
            variants.append(Shared[int])  # specialized by every thread at the same time
            for round_index in range(300):
                updated: Rich = shared.updating(tags=(str(index), str(round_index)))
                assert updated.tags == (str(index), str(round_index))
                assert Rich.from_json(document) == shared
                assert updated.to_json() != document
                Shared[int](value=round_index, items=[index])
                hash(shared)
                copy.deepcopy(updated)

        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=work, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert failures == []
    assert all(variant is variants[0] for variant in variants)


def test_checking_function_arguments_does_not_leak() -> None:
    class Holding(State):
        function: Callable[[int, str], None]

    def taking(first: int, second: str) -> None: ...
    def defaulted(first: int, second: str, third: bytes = b"", *, fourth: int = 0) -> None: ...
    def refused(first: int) -> None: ...

    class Callee:
        def taking(self, first: int, second: str) -> None: ...

    class Called:
        def __call__(self, first: int, second: str) -> None: ...

    class Constructed:
        def __init__(self, first: int, second: str) -> None: ...

    many: Any = {}
    exec(  # nosec: B102
        f"def many({', '.join(f'a{index}' for index in range(70))}, *, last): pass", many
    )

    def operation() -> None:
        Holding(function=taking)
        Holding(function=defaulted)
        Holding(function=Callee().taking)
        Holding(function=Called())
        Holding(function=Constructed)
        Holding(function=functools.partial(defaulted, 1))
        Holding(function=functools.partial(defaulted, fourth=1))
        Holding(function=functools.partial(many["many"], *range(68), last=1))
        _swallow(lambda: Holding(function=refused))
        _swallow(lambda: Holding(function=functools.partial(refused, 1)))
        _swallow(lambda: Holding(function=functools.partial(taking, second="")))
        _swallow(lambda: Holding(function=functools.partial(many["many"], a69=1)))
        _swallow(lambda: Holding(function=1))  # pyright: ignore[reportArgumentType]

    _assert_no_leak(operation)


def test_owners_of_builtins_held_as_functions_are_collected() -> None:
    class Holding(State):
        function: Callable[[str], object]

    def define() -> weakref.ref[type[Any]]:
        class Temporary(dict[str, Any]):
            pass

        Holding(function=Temporary().get)
        return weakref.ref(Temporary)

    references = [define() for _ in range(20)]
    gc.collect()
    assert all(reference() is None for reference in references)


def test_classes_held_as_functions_are_collected() -> None:
    class Holding(State):
        factory: Callable[[], object]

    def define() -> weakref.ref[type[Any]]:
        class Temporary:
            def method(self) -> None: ...

        Holding(factory=Temporary)
        Holding(factory=Temporary().method)
        return weakref.ref(Temporary)

    references = [define() for _ in range(20)]
    gc.collect()
    assert all(reference() is None for reference in references)


class _CalledBack(Protocol):
    def __call__(self, request: str, /, retries: int, *, timeout: float = 1.0) -> str: ...


def test_checking_protocol_functions_does_not_leak() -> None:
    class Holding(State):
        handler: _CalledBack

    def exact(request: str, /, retries: int, *, timeout: float = 1.0) -> str:
        return request

    def renamed(request: str, /, attempts: int, *, timeout: float = 1.0) -> str:
        return request

    def based(client: object, request: str, /, retries: int, *, timeout: float = 1.0) -> str:
        return request

    def named(request: str, /, retries: int, *, timeout: float = 1.0, client: object) -> str:
        return request

    class Service:
        def __call__(self, request: str, /, retries: int, *, timeout: float = 1.0) -> str:
            return request

    class Constructed:
        def __init__(self, request: str, /, retries: int, *, timeout: float = 1.0) -> None:
            pass

    told = (("a", 1, False), (None, 0, True), ("b", 3, False), ("keywords", 4, False))

    def operation() -> None:
        Holding(handler=exact)
        Holding(handler=Service())
        Holding(handler=functools.partial(based, object()))
        Holding(handler=functools.partial(named, client=object()))
        Holding(handler=Constructed)  # pyright: ignore[reportArgumentType]
        _swallow(lambda: Holding(handler=renamed))  # pyright: ignore[reportArgumentType]
        _swallow(lambda: Holding(handler=functools.partial(exact, "request")))  # pyright: ignore[reportArgumentType]
        _swallow(lambda: Holding(handler=functools.partial(exact, unknown=1)))  # pyright: ignore[reportArgumentType]
        _swallow(lambda: Holding(handler=1))  # pyright: ignore[reportArgumentType]
        nstruct.signature_fits(told, told, False)
        _swallow(lambda: nstruct.signature_fits(((1, 1, False),), told, False))
        _swallow(lambda: nstruct.signature_fits(told, "not a tuple", False))  # pyright: ignore[reportArgumentType]

    _assert_no_leak(operation)


def test_checking_partials_with_placeholders_does_not_leak() -> None:
    class Holding(State):
        function: Callable[[int, str], None]

    def three(first: int, second: str, third: bytes, /) -> None: ...
    def variadic(first: int, *rest: Any) -> None: ...
    def named(first: int, second: str, *, third: bytes) -> None: ...

    class Constructed:
        def __init__(self, first: int, second: str, third: bytes, /) -> None: ...

    class Unreadable:
        @property
        def __signature__(self) -> Any:
            raise RuntimeError("unreadable")

        def __call__(self, *arguments: Any) -> None: ...

    many: Any = {}
    exec(f"def many({', '.join(f'a{index}' for index in range(40))}, /): pass", many)  # nosec: B102
    two_holes: list[Any] = [Placeholder] * 2 + [0] * 38
    many_holes: list[Any] = [Placeholder] * 20 + [0] * 20
    # kept apart from the partials around them by attributes of their own
    inner_named = functools.partial(named, third=b"")
    inner_named.kept = True
    inner_holed = functools.partial(three, Placeholder, Placeholder, b"")
    inner_holed.kept = True

    def operation() -> None:
        Holding(function=functools.partial(three, Placeholder, Placeholder, b""))
        Holding(function=functools.partial(variadic, Placeholder, Placeholder, 0))
        Holding(function=functools.partial(Constructed, Placeholder, Placeholder, b""))
        Holding(function=functools.partial(many["many"], *two_holes))
        _swallow(lambda: Holding(function=functools.partial(many["many"], *many_holes)))
        Holding(function=functools.partial(inner_named, third=b"joined"))
        Holding(function=functools.partial(inner_holed))
        _swallow(lambda: Holding(function=functools.partial(inner_holed, Placeholder, "")))
        _swallow(lambda: Holding(function=functools.partial(three, Placeholder, "", b"")))
        _swallow(lambda: Holding(function=functools.partial(named, Placeholder, "", first=1)))
        _swallow(lambda: Holding(function=functools.partial(Constructed, 1, Placeholder, b"")))
        _swallow(lambda: Holding(function=functools.partial(Unreadable(), Placeholder, 0)))  # pyright: ignore[reportArgumentType]

    _assert_no_leak(operation)


def test_functions_are_checked_from_many_threads_at_once() -> None:
    # every kind of callable at once - the answers Python caches are shared by the threads
    class Holding(State):
        handler: _CalledBack

    class Factory(State):
        factory: Callable[[], object]

    def exact(request: str, /, retries: int, *, timeout: float = 1.0) -> str:
        return request

    def renamed(request: str, /, attempts: int, *, timeout: float = 1.0) -> str:
        return request

    def based(request: str, client: object, /, retries: int, *, timeout: float = 1.0) -> str:
        return request

    class Constructed:
        def __init__(self, request: str, /, retries: int, *, timeout: float = 1.0) -> None:
            pass

    failures: list[BaseException] = []
    barrier = threading.Barrier(8)

    def work(index: int) -> None:
        try:
            barrier.wait()
            for _ in range(200):
                assert Holding(handler=exact).handler is exact
                assert Holding(handler=functools.partial(exact, timeout=index)) is not None
                Holding(handler=Constructed)  # pyright: ignore[reportArgumentType]
                Holding(handler=functools.partial(Constructed, timeout=index))  # pyright: ignore[reportArgumentType]
                Holding(handler=functools.partial(based, Placeholder, index))
                Factory(factory=dict)
                Factory(factory=datetime.now)
                Factory(factory=date.today)
                Factory(factory=object)
                with pytest.raises(ValidationError):
                    Holding(handler=renamed)  # pyright: ignore[reportArgumentType]

        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=work, args=(index,)) for index in range(8)]
    for thread in threads:
        thread.start()

    for thread in threads:
        thread.join()

    assert failures == []
