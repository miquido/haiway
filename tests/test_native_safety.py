import copy
import importlib
import json
import threading
import time
import types
from collections.abc import Callable, Iterator, Mapping, Sequence, Set
from datetime import date, datetime
from datetime import time as clock
from pathlib import Path
from typing import Annotated, Any

import pytest

from haiway import Default, Map, State, ValidationError, Validator
from haiway._native import nstruct
from haiway.attributes.state import StateMeta

# What the native code does with values whose methods are code of the user, with what it
# is handed by Python hooks, and with instances other threads can reach half made.

# --- bool -------------------------------------------------------------------


class _Lowered(str):
    __slots__ = ()

    def lower(self) -> Any:
        return 12345


class _Flag(State):
    flag: bool


@pytest.mark.parametrize("wrap", (str, _Lowered))
@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("true", True),
        ("TRUE", True),
        ("tRuE", True),
        ("True", True),
        ("false", False),
        ("FALSE", False),
        ("False", False),
        ("fAlSe", False),
    ),
)
def test_bool_reads_a_str_in_any_case(
    wrap: Callable[[str], str],
    text: str,
    expected: bool,
) -> None:
    given: Any = wrap(text)

    assert _Flag(flag=given).flag is expected


@pytest.mark.parametrize("wrap", (str, _Lowered))
@pytest.mark.parametrize("text", ("", "yes", "tru", "truee", "falsee", "trüe", chr(0x17F) + "true"))
def test_bool_refuses_a_str_which_is_not_a_boolean(
    wrap: Callable[[str], str],
    text: str,
) -> None:
    given: Any = wrap(text)

    with pytest.raises(ValidationError, match="expected values"):
        _Flag(flag=given)


# --- spellings of values -----------------------------------------------------


class _SpelledDatetime(datetime):
    def isoformat(self, *args: Any, **kwargs: Any) -> Any:
        return 12345


class _SpelledDate(date):
    def isoformat(self) -> Any:
        return 12345


class _SpelledTime(clock):
    def isoformat(self, *args: Any, **kwargs: Any) -> Any:
        return 12345


class _SpelledPath(type(Path())):
    def as_posix(self) -> Any:
        return 12345


class _Spelled(State):
    value: Any


@pytest.mark.parametrize(
    "value",
    (
        pytest.param(_SpelledDatetime(2020, 1, 1), id="datetime"),
        pytest.param(_SpelledDate(2020, 1, 1), id="date"),
        pytest.param(_SpelledTime(1, 2, 3), id="time"),
        pytest.param(_SpelledPath("/a"), id="path"),
    ),
)
def test_spelling_which_is_no_str_is_refused(value: Any) -> None:
    given = _Spelled(value=value)

    with pytest.raises(ValueError, match="Failed to encode") as encoding:
        given.to_json()

    assert isinstance(encoding.value.__cause__, TypeError)
    assert "non-str" in str(encoding.value.__cause__)

    with pytest.raises(TypeError, match="non-str"):
        given.to_basic_object()


class _GoodDatetime(datetime):
    def isoformat(self, *args: Any, **kwargs: Any) -> str:
        return "custom"


def test_spelling_of_a_subclass_which_is_a_str_is_used() -> None:
    assert json.loads(_Spelled(value=_GoodDatetime(2020, 1, 1)).to_json()) == {"value": "custom"}
    assert _Spelled(value=_GoodDatetime(2020, 1, 1)).to_basic_object() == {"value": "custom"}


# --- hooks -------------------------------------------------------------------


class _Point(State):
    x: int
    y: int


class _Entries(Mapping[str, Any]):
    def __init__(self, entries: Mapping[str, Any]) -> None:
        self._entries: Mapping[str, Any] = entries

    def __getitem__(self, key: str) -> Any:
        return self._entries[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._entries)

    def __len__(self) -> int:
        return len(self._entries)


def _returns(cls: Any, key: Any) -> None:
    return None


@pytest.mark.parametrize(
    "given",
    (
        pytest.param({"x": 1, "y": 2, "z": 3}, id="dict"),
        pytest.param(_Entries({"x": 1, "y": 2, "z": 3}), id="mapping"),
    ),
)
def test_hook_of_unexpected_key_which_returns_still_refuses(
    given: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hook: Any = vars(importlib.import_module("haiway.attributes.state"))["_unexpected_key"]
    monkeypatch.setattr(hook, "__code__", _returns.__code__)

    with pytest.raises(TypeError, match="unexpected attributes"):
        nstruct.validate_instance(_Point, given, False)


class _Raising:
    def __bool__(self) -> bool:
        raise RuntimeError("no truth")


def test_signature_fits_reports_a_failing_flag() -> None:
    parameters = ((None, 0, False),)
    extensible: Any = _Raising()

    with pytest.raises(RuntimeError, match="no truth"):
        nstruct.signature_fits(parameters, parameters, extensible)

    assert nstruct.signature_fits(parameters, parameters, True) is True


# --- instances which are not initialized -------------------------------------


class _Pair(State):
    first: int
    second: int = 0


def _root(error: BaseException) -> BaseException:
    while error.__cause__ is not None:
        error = error.__cause__

    return error


def _equal_to_blank(blank: Any) -> Any:
    return blank == _Pair.__new__(_Pair)


def _equal_to_made(blank: Any) -> Any:
    return blank == _Pair(first=1)


def _made_equal_to(blank: Any) -> Any:
    return _Pair(first=1) == blank


def _shallow_mapping(blank: Any) -> Any:
    return blank.to_mapping(recursive=False)


def _updated(blank: Any) -> Any:
    return blank.updating(first=1)


def _native_basic(blank: Any) -> Any:
    return nstruct.basic_object(blank, True, ())


def _native_mapping(blank: Any) -> Any:
    return nstruct.to_mapping(blank, False)


@pytest.mark.parametrize(
    "operation",
    (
        pytest.param(_Pair.to_json, id="to_json"),
        pytest.param(hash, id="hash"),
        pytest.param(_equal_to_blank, id="equal blank"),
        pytest.param(_equal_to_made, id="equal"),
        pytest.param(_made_equal_to, id="equal reversed"),
        pytest.param(_Pair.to_mapping, id="to_mapping"),
        pytest.param(_shallow_mapping, id="to_mapping shallow"),
        pytest.param(_Pair.to_basic_object, id="to_basic_object"),
        pytest.param(copy.deepcopy, id="deepcopy"),
        pytest.param(_updated, id="updating"),
        pytest.param(nstruct.to_json, id="native to_json"),
        pytest.param(_native_basic, id="native basic"),
        pytest.param(_native_mapping, id="native mapping"),
    ),
)
def test_instance_which_is_not_initialized_is_refused(operation: Callable[[Any], Any]) -> None:
    blank: Any = _Pair.__new__(_Pair)

    with pytest.raises((TypeError, ValueError)) as refused:
        operation(blank)

    assert isinstance(_root(refused.value), TypeError)
    assert "_Pair is not initialized" in str(_root(refused.value))


def test_instance_which_is_not_initialized_is_refused_within_another() -> None:
    class Holder(State):
        inner: Any

    blank: Any = _Pair.__new__(_Pair)
    holder = Holder(inner=blank)

    with pytest.raises(TypeError, match="not initialized"):
        hash(holder)

    with pytest.raises(TypeError, match="not initialized"):
        holder.to_mapping()

    with pytest.raises(ValueError, match="Failed to encode"):
        holder.to_json()


def test_instance_which_is_not_initialized_is_equal_to_itself_only() -> None:
    blank: Any = _Pair.__new__(_Pair)

    assert blank == blank  # noqa: PLR0124
    assert not blank != blank  # noqa: PLR0124

    _Pair.__init__(blank, first=1)

    assert blank == _Pair(first=1)
    assert hash(blank) == hash(_Pair(first=1))
    assert json.loads(blank.to_json()) == {"first": 1, "second": 0}


def test_instance_being_initialized_is_never_read_half_bound() -> None:
    values = {f"f{i}": i for i in range(40)}
    namespace: dict[str, Any] = {"State": State}
    exec(
        "class Wide(State):\n" + "\n".join(f"    f{i}: int" for i in range(40)),
        namespace,
    )
    wide: Any = namespace["Wide"]
    torn: list[Any] = []

    for _ in range(200):
        shared: Any = wide.__new__(wide)
        start = threading.Barrier(3)

        def initialize(shared: Any = shared, start: threading.Barrier = start) -> None:
            start.wait()
            wide.__init__(shared, **values)

        def read(shared: Any = shared, start: threading.Barrier = start) -> None:
            start.wait()
            for _ in range(20):
                try:
                    read_values: Any = json.loads(shared.to_json())

                except ValueError:
                    continue  # not initialized yet - refused whole

                if read_values != values:
                    torn.append(read_values)

        threads = [
            threading.Thread(target=initialize),
            threading.Thread(target=read),
            threading.Thread(target=read),
        ]
        for thread in threads:
            thread.start()

        for thread in threads:
            thread.join()

        assert json.loads(shared.to_json()) == values

    assert torn == []


# --- classes reached before they are given a plan ----------------------------


class _Base(State):
    pass


def _constructed_whole(subclass: type[_Base], fields: int) -> bool | None:
    created: Any = subclass
    try:
        instance: Any = created(f0=5)

    except TypeError, AttributeError:
        return None

    return instance.f0 == 5 and instance.f29 == 29 and len(instance.to_mapping()) == fields


def test_class_reached_while_it_is_being_created_is_usable_or_refused() -> None:
    fields = 30
    source = "class Created(Base):\n" + "\n".join(f"    f{i}: int = {i}" for i in range(fields))
    done = threading.Event()
    enough = threading.Event()
    wrong: list[bool] = []
    used: list[bool] = []
    keep: list[Any] = []

    def create() -> None:
        while not done.is_set():
            namespace: dict[str, Any] = {"Base": _Base}
            exec(source, namespace)
            keep.append(namespace["Created"])
            del keep[:-20]

    def use() -> None:
        while not done.is_set():
            # a class not given its plan yet is refused, which counts for neither
            outcomes: list[bool | None] = [
                _constructed_whole(subclass, fields) for subclass in _Base.__subclasses__()
            ]
            used.extend(True for outcome in outcomes if outcome is True)
            wrong.extend(False for outcome in outcomes if outcome is False)
            if len(used) >= 100:
                enough.set()

    threads = [threading.Thread(target=create) for _ in range(2)] + [
        threading.Thread(target=use) for _ in range(3)
    ]
    for thread in threads:
        thread.start()

    enough.wait(timeout=10)  # bounded - the assertions below tell what was reached
    done.set()
    for thread in threads:
        thread.join()

    assert wrong == []
    assert used  # some of the classes were constructed, not only refused


# --- a recursive alias -------------------------------------------------------


def _descriptions_for(
    aliases: str,
    nest: Callable[[Any], Any],
    depth: int,
    monkeypatch: pytest.MonkeyPatch,
) -> int:
    native = importlib.import_module("haiway.attributes.native")
    scope: dict[str, Any] = vars(native)
    original: Any = scope["_describe"]
    described: list[Any] = []

    def counting(annotation: Any, /, active: Any) -> Any:
        described.append(annotation)
        return original(annotation, active=active)

    monkeypatch.setattr("haiway.attributes.native._describe", counting)

    # `type` statements are made within a namespace of their own, so each class has its aliases
    namespace: dict[str, Any] = {"Mapping": Mapping, "Sequence": Sequence}
    exec(aliases, namespace)
    document_class: Any = types.new_class(
        "Document",
        (State,),
        exec_body=lambda body: body.update(__annotations__={"value": namespace["Root"]}),
    )

    document: Any = 1
    for _ in range(depth):
        document = nest(document)

    assert document_class.validate({"value": document}) == document_class(value=document)
    assert document_class.from_json(json.dumps({"value": document})) == document_class(
        value=document
    )
    return len(described)


def _in_mapping(value: Any) -> Any:
    return {"k": value}


def _in_mapping_of_sequence(value: Any) -> Any:
    return {"k": [value]}


@pytest.mark.parametrize(
    ("aliases", "nest"),
    [
        pytest.param("type Root = Mapping[str, Root] | int", _in_mapping, id="recursive"),
        pytest.param(
            "type Root = Mapping[str, Other] | int\ntype Other = Sequence[Root] | str",
            _in_mapping_of_sequence,
            id="mutually recursive",
        ),
    ],
)
def test_recursive_alias_is_described_once_whatever_the_depth(
    aliases: str,
    nest: Callable[[Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {
        depth: _descriptions_for(aliases, nest, depth, monkeypatch) for depth in (1, 10, 50, 100)
    }

    # what the aliases and what they stand for need - and nothing more for each level of nesting
    assert len(set(counts.values())) == 1, counts


@pytest.mark.parametrize(
    ("aliases", "nest"),
    [
        pytest.param("type Root[T] = Mapping[str, Root[T]] | T", _in_mapping, id="generic"),
        pytest.param(
            "type Root[T] = Mapping[str, Other[T]] | T\ntype Other[T] = Sequence[Root[T]] | str",
            _in_mapping_of_sequence,
            id="mutually recursive generic",
        ),
    ],
)
def test_recursive_generic_alias_is_described_once_whatever_the_depth(
    aliases: str,
    nest: Callable[[Any], Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `Root[T]` within the body is not the `Root` itself, so the body is unfolded once more
    # when a document first reaches it (depth 2) - from there on it is the one linked to
    counts = {
        depth: _descriptions_for(aliases, nest, depth, monkeypatch) for depth in (10, 50, 100)
    }

    assert len(set(counts.values())) == 1, counts


# --- a set which changes while it is converted -------------------------------


class _Spelling(datetime):
    owner: Any

    def isoformat(self, *args: Any, **kwargs: Any) -> str:
        self.owner.add(object())  # what converting an element does to the set it is in
        return "spelled"


def test_elements_of_an_iterable_are_converted_from_a_snapshot() -> None:
    elements: set[Any] = {1, 2, 3}
    element = _Spelling(2020, 1, 1)
    element.owner = elements
    elements.add(element)

    converted: Any = _Spelled(value=elements).to_basic_object()["value"]

    assert sorted(map(str, converted)) == sorted(["1", "2", "3", "spelled"])
    assert len(elements) == 5


# --- what reading a document runs code for, told through many classes --------

_runs: list[Any] = []


def _counted(value: Any) -> Any:
    _runs.append(value)
    return value


class _Counted(State):
    number: Annotated[int, Validator(_counted)]


def _nested(inner: type[Any], levels: int) -> type[Any]:
    for _ in range(levels):
        inner = types.new_class(
            "Wrapped",
            (State,),
            exec_body=lambda namespace, inner=inner: namespace.update(
                __annotations__={"value": inner, "tag": int}
            ),
        )

    return inner


@pytest.mark.parametrize("levels", (3, 70))
def test_document_of_classes_nested_deeper_than_purity_is_told_for_is_read_once(
    levels: int,
) -> None:
    wrapped = _nested(_Counted, levels)
    document: Any = {"number": 7}
    for _ in range(levels):
        document = {"value": document, "tag": 1}

    _runs.clear()
    read = wrapped.from_json(json.dumps(document))

    assert read == wrapped.validate(document)
    assert _runs == [7, 7]  # once for the read, once for validating the same document

    _runs.clear()
    refused = json.dumps({**document, "tag": "x"})
    with pytest.raises(ValueError, match="Failed to decode"):
        wrapped.from_json(refused)

    reference = len(_runs)
    _runs.clear()
    with pytest.raises(ValidationError):
        wrapped.validate(json.loads(refused))

    assert reference == len(_runs)


class _Plain(State):
    number: int


@pytest.mark.parametrize("levels", (3, 70, 150))
def test_document_of_plain_classes_nested_deeper_than_purity_is_told_for_is_read(
    levels: int,
) -> None:
    wrapped = _nested(_Plain, levels)
    document: Any = {"number": 7}
    for _ in range(levels):
        document = {"value": document, "tag": 1}

    assert wrapped.from_json(json.dumps(document)) == wrapped.validate(document)

    with pytest.raises(ValueError, match="Failed to decode"):
        wrapped.from_json(json.dumps({**document, "tag": "x"}))


def test_default_which_was_not_told_pure_when_the_class_was_made_is_read() -> None:
    chain: list[type[Any]] = [_Plain]
    for _ in range(70):
        chain.append(
            types.new_class(
                "Defaulted",
                (State,),
                exec_body=lambda namespace, inner=chain[-1]: namespace.update(
                    __annotations__={"value": inner | None},
                    value=None,
                ),
            )
        )

    # too deep to be told when the last of them was made - what is asked of the first ones
    # first is remembered, and the last is told from it
    for built in chain[1:60]:
        assert built.from_json('{"value": null}') == built()

    assert chain[-1].from_json("{}") == chain[-1]()
    assert chain[-1].from_json('{"value": {"value": null}}') == chain[-1].validate(
        {"value": {"value": None}}
    )


class _Tree(State):
    number: Annotated[int, Validator(_counted)]
    children: tuple[_Tree, ...] = ()


def test_class_referring_to_itself_is_read_once_per_instance() -> None:
    document = (
        '{"number": 1, "children": [{"number": 2, "children": [{"number": 3}]}, {"number": 4}]}'
    )
    _runs.clear()

    assert _Tree.from_json(document).children[0].children[0].number == 3
    assert sorted(_runs) == [1, 2, 3, 4]


# --- defaults of attributes whose spec holds code of the user -----------------


class _Checked(State):
    x: Annotated[int, Validator(_counted)]


def _declared(value: Any) -> Any:
    return Default(value)


class _Optional(State):
    a: int = 0
    child: _Checked | None = None


class _Given(State):
    child: _Checked | None = _Checked(x=1)


class _Converted(State):
    # the range is converted by the pure alternative, so what is kept is what it converts to
    child: _Checked | Sequence[int] | None = _declared(range(1, 3))


class _Mapped(State):
    # a mapping is built into `_Checked` by code of the user, so it is built every time
    child: _Checked | None = _declared(Map({"x": 1}))


class _Validated(State):
    value: Annotated[int, Validator(_counted)] = 0


def test_default_accepted_as_it_is_is_kept_whatever_code_the_spec_holds() -> None:
    _runs.clear()

    assert _Optional().child is None
    assert _Given().child is _Given().child
    assert _Given.from_json("{}").child is _Given().child
    assert _Optional.from_json('{"a": 1}').child is None
    assert _runs == []


def test_default_converted_by_a_pure_alternative_is_kept() -> None:
    _runs.clear()

    assert _Converted().child == (1, 2)
    assert _Converted().child is _Converted().child
    assert _Converted.from_json("{}").child is _Converted().child
    assert _runs == []


def test_default_which_code_of_the_user_builds_is_built_for_each_instance() -> None:
    _runs.clear()

    first, second = _Mapped(), _Mapped()

    assert first.child == second.child == _Checked(x=1)
    assert first.child is not second.child
    assert _runs == [1, 1, 1]  # one for each, and the one compared with
    _runs.clear()

    _Mapped.from_json("{}")

    assert _runs == [1]


class _Broken(Sequence[int]):
    def __init__(self, error: type[BaseException]) -> None:
        self._error: type[BaseException] = error

    def __len__(self) -> int:
        return 1

    def __getitem__(self, index: Any) -> Any:
        raise self._error("not a sequence of numbers")


def test_default_which_fails_to_be_checked_is_left_to_fail_where_it_is_used() -> None:
    class Broken(State):
        child: _Checked | Sequence[int] | None = _declared(_Broken(ValueError))

    with pytest.raises(ValidationError):
        Broken()

    with pytest.raises(ValueError, match="Failed to decode"):
        Broken.from_json("{}")


class _CheckError(Exception):
    pass


class _Refusing(StateMeta):
    def __instancecheck__(cls, instance: Any) -> bool:
        raise _CheckError("the check of the class failed")


class _Interrupting(StateMeta):
    def __instancecheck__(cls, instance: Any) -> bool:
        raise KeyboardInterrupt


def _kept(value: Any) -> Any:
    return value


class _RefusedByCheck(State, metaclass=_Refusing):
    q: Annotated[int, Validator(_kept)]


class _InterruptedByCheck(State, metaclass=_Interrupting):
    q: Annotated[int, Validator(_kept)]


def test_default_of_state_whose_check_fails_is_left_to_fail_where_it_is_used() -> None:
    class Holder(State):
        child: _RefusedByCheck = _declared(5)

    with pytest.raises(ValidationError):
        Holder()


def test_default_of_state_whose_check_is_interrupted_interrupts_the_class() -> None:
    with pytest.raises(KeyboardInterrupt):
        types.new_class(
            "InterruptedHolder",
            (State,),
            exec_body=lambda body: body.update(
                __annotations__={"child": _InterruptedByCheck}, child=Default(5)
            ),
        )


def test_default_which_is_interrupted_while_it_is_checked_interrupts_the_class() -> None:
    declared = _declared(_Broken(KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        types.new_class(
            "Interrupted",
            (State,),
            exec_body=lambda body: body.update(
                __annotations__={"child": _Checked | Sequence[int] | None}, child=declared
            ),
        )


def test_default_with_validator_on_its_value_is_validated_for_each_instance() -> None:
    _runs.clear()

    _Validated()
    _Validated.from_json("{}")

    assert _runs == [0, 0]


class _Leaf(State):
    a: int


class _Listed(State):
    items: Sequence[_Leaf] = Default(factory=tuple)


class _FactoryValidated(State):
    value: Annotated[int, Validator(_counted)] = Default(factory=int)


def test_value_given_for_attribute_with_computed_default_is_read_as_any_other() -> None:
    assert _Listed.from_json('{"items": [{"a": 1}, {"a": 2}]}') == _Listed(
        items=(_Leaf(a=1), _Leaf(a=2))
    )
    assert _Listed.from_json("{}") == _Listed(items=())

    with pytest.raises(ValueError, match=r"\.items\[1\]\.a"):
        _Listed.from_json('{"items": [{"a": 1}, {"a": "x"}]}')


def test_computed_default_with_code_of_the_user_is_validated_once_the_document_is_right() -> None:
    _runs.clear()

    assert _FactoryValidated.from_json('{"value": 3}') == _FactoryValidated(value=3)
    assert _runs == [3, 3]  # one for each, and the one compared with
    _runs.clear()

    assert _FactoryValidated.from_json("{}").value == 0
    assert _runs == [0]


def _chain(length: int, last: type[State]) -> type[State]:
    cls: type[State] = last
    for index in range(length):
        cls = types.new_class(
            f"Chained{index}",
            (State,),
            exec_body=lambda body, cls=cls: body.update(
                __annotations__={"next": cls, "number": int}
            ),
        )
    return cls


@pytest.mark.parametrize("leaf", (_Leaf, _Counted))
def test_graph_reaching_more_classes_than_are_remembered_is_read_correctly_each_time(
    leaf: type[State],
) -> None:
    root: Any = _chain(100, leaf)
    document: Any = {"number": 0}
    inner: Any = document
    for index in range(1, 100):
        inner["next"] = {"number": index}
        inner = inner["next"]
    inner["next"] = {"a": 1} if leaf is _Leaf else {"number": 1}

    _runs.clear()
    for _ in range(3):
        instance: Any = root.from_json(json.dumps(document))
        assert instance.number == 0
        assert instance.next.number == 1
        assert instance == root.from_mapping(document)

    assert len(_runs) == (0 if leaf is _Leaf else 6)  # for each of the readings, and the compared


# --- a mapping which changes while it is converted ----------------------------


class _Adding(dict[Any, Any]):
    def add(self, item: Any) -> None:
        self[object()] = item


def test_items_of_a_mapping_are_converted_from_a_snapshot() -> None:
    entries = _Adding()
    element = _Spelling(2020, 1, 1)
    element.owner = entries
    entries["a"] = 1
    entries["b"] = element

    converted: Any = _Spelled(value=_Entries(entries)).to_basic_object()["value"]

    assert dict(converted) == {"a": 1, "b": "spelled"}
    assert len(entries) == 3


class _Numbers(State):
    values: Set[int]


class _Carrying[T](State):
    value: T


def _written_by_the_changing_thread(values: Set[int], /) -> bool:
    # the thread adds 0..99 in order, then discards 0..99 in order, starting from 0..9
    ordered: list[int] = sorted(values)
    if not ordered:
        return True

    growing: bool = ordered == list(range(ordered[-1] + 1))
    shrinking: bool = ordered == list(range(ordered[0], 100))
    return growing or shrinking


def test_a_set_changed_by_another_thread_is_validated_as_one_snapshot() -> None:
    # without the GIL a set iterated while another thread changes it fails - a set given
    # is copied as it is at one moment instead, so whatever it holds then is accepted
    shared: set[int] = set(range(10))
    carrying = _Carrying(value=shared)
    stop = threading.Event()

    def change() -> None:
        while not stop.is_set():
            for number in range(100):
                shared.add(number)

            for number in range(100):
                shared.discard(number)

    changing = threading.Thread(target=change)
    changing.start()
    try:
        deadline: float = time.monotonic() + 0.5
        while time.monotonic() < deadline:
            values: Set[int] = _Numbers(values=shared).values
            assert _written_by_the_changing_thread(values), sorted(values)
            assert isinstance(carrying, _Carrying[Set[int]])

    finally:
        stop.set()
        changing.join()
