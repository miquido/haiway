# Changelog

Notable changes to Haiway. Versions follow the `version` in `pyproject.toml`.

## 0.66.0

### Changed

- `to_json` writes compact JSON, with no space after `,` and `:` (`{"a":1,"b":[1,2]}`), where it
  wrote `json.dumps`'s default `", "` and `": "` - shorter payloads, written faster. With `indent`
  the output is as before. Code comparing the exact text of the output has to follow.
- An instance which was never initialized - made with `__new__`, or with an `__init__` which did not
  delegate - raises `TypeError: ... is not initialized` for `to_json`, `to_mapping`,
  `to_basic_object`, `hash`, `==` of two instances and `copy.deepcopy`, as `updating` always did.
  They treated it as an instance with no attributes before: it was written as `{}`, hashed and
  compared equal to every other one. This also keeps another thread from reading an instance which
  is being initialized while only some of its attributes are bound: it reads all of them, or raises -
  every value is bound with a release store and read with an acquire load on the free-threaded
  build, wherever an instance is made (the constructor, `updating`, `from_json`, `from_mapping`,
  `validate` and `copy.deepcopy`), and plain accesses with the GIL.
- Several `Validator`s, or several `Verifier`s, on one attribute run one after another in the order
  they are declared, each given what the previous one returned - where only the last of each was
  kept, silently, so `Annotated[int, Validator(a), Validator(b)]` ran `b` alone. Validators run
  before the base validation and verifiers after it, whichever is declared first; declarations
  nested through `Annotated` or a `type` alias run inner first. A `Verifier` has to return the value
  - what it returns is what the attribute holds, a verifier returning `None` stores `None`.
- A State refuses a key which is no attribute with one message, wherever the key is given:
  `Unexpected attribute 'x' for Name` from the constructor and `updating()` as well as from
  `validate` and `from_mapping`, where the first two said `Name has no attribute 'x'`. The
  constructor and `updating()` find all of the keys at once and list them sorted by name,
  `Unexpected attributes 'a', 'b' for Name`; `validate` and `from_mapping` report the first one. The `TypedDict` message,
  `Unexpected key 'x' for Name`, is unchanged.
- `Attribute` and the attribute annotations (`IntegerAttribute`, `ObjectAttribute`, ...) refuse
  assignment after they are made. The annotations hold what an attribute is declared with besides
  its type - alias, description, specification, meta, redaction, whether it is required and its
  verifier - in a single `AttributeMetadata`, which is the only place an `Annotated` is read. The
  `alias`, `description`, `specification`, `meta`, `redaction` and `required` properties are read
  as before, but the constructors of the annotations take `metadata=AttributeMetadata(...)` in
  place of the `alias=`, `description=`, `verifying=`, `required=`, `specification=`, `meta=` and
  `redaction=` keywords, and `verifying` is now `metadata.verifying` - `None` when no verifier is
  declared.
- The `__SPECIFICATION__` of a State which cannot be represented using json schema is no longer
  kept next to a `__SERIALIZABLE__` flag: both are derived from the one specification the class
  holds, if any. Reading them behaves as before - `__SPECIFICATION__` raises a `TypeError` for such
  a class. `__ALIASES__` is gone, `__ALLOWED_FIELDS__` is a mapping of each key an attribute can be
  given under to its name.
- The JSON schema of a State is made to agree with the validation of a JSON document - it no longer
  refuses what the State accepts, and refuses more of what the State refuses. Where it was relied on:
  - `required` lists an attribute only when it can't be left out. One accepting `Missing` - `Any`,
    `object`, `X | Missing`, an unspecialized type parameter - is no longer listed, which is also
    how `inspect.signature` of the class reads it. An annotation holding a `Validator` or a
    `Verifier`, at any level, is never taken as accepting `Missing`, as either may refuse it.
    `Missing` is not an alternative of a union
    anymore (it was `"type": "null"`, which validation refuses): `int | Missing` is `integer`.
  - A fixed tuple states `minItems` and `maxItems`, a tuple with an unbounded part `minItems` for
    the elements around it.
  - A set no longer states `uniqueItems`, which refused a document with a repeated element that is
    read as a set all the same.
  - `datetime`, `time` and `timedelta` no longer state a `format` - they are written without an
    offset, or with a sign when negative, which `date-time`, `time` and `duration` don't allow - and
    `Path` no longer states `format: path`, which is no standard format.
  - A mapping with keys other than `str` states `propertyNames` for `int`, `bool`, `UUID`, enum and
    `Literal` keys.
  - A `Literal` of enum members is written as their values.
- A `Literal` holding a value with no JSON form - a plain `Enum` member or `bytes` - leaves the State
  without a schema, where `json_schema()` failed with a bare `TypeError` on the raw value and
  `serializable=True` accepted the class. With `serializable=True` such a class is refused when it
  is created.
- `NotRequired[T]` on an attribute of a State which has no default is refused with a `TypeError`
  when the class is created. It was listed neither as required in the schema nor accepted as
  omitted by validation, which still required the value.

- **Breaking:** validation of the plain types is stricter, and no string is parsed into a number
  anymore:
  - `bool` accepts `True`/`False`, the ints `0` and `1`, the floats `0.0`, `-0.0` and `1.0` and
    `"true"`/`"false"` in any case. Any other int (`2`, `-1`) was `True` before, and is refused now,
    as is any other string and any other float.
  - `int` accepts an int (not a `bool`) and a float which is a whole number up to 2**53, and refuses
    every string, where `"5"` was read as `5` (and `"1_0"`, `" 1 "` and the digits of any script
    with it). Floats which are not whole numbers, `NaN`, infinities and those beyond 2**53 are
    refused. `float` refuses strings - `"nan"`, `"inf"`, `"1_0.0"` and `"1.5"` were read before.
  - `IntEnum` refuses `bool` (`True` was read as the member of `1`) and numeric strings (`"1"`), and
    reads a float which is a whole number up to 2**53 as the int it equals, as `int` does (`1.0` is
    the member of `1`). A member is read by its value first, by its name second - a value always wins
    over a name. A `StrEnum` reads the same way. An `int` `Literal` reads such a float as the int
    too (`Literal[1]` given `1.0` holds `1`), including the int values of `IntEnum` members within
    it, and a float never matches a `bool` literal.
  - A union reads a whole float as it reads its int: the alternatives which take the float as it is
    win first, then the alternatives which take the int as it is, then the conversions in the order
    they are declared. `bool | int` gives `1` for `1.0` and `IntEnum | int` gives `1`, not a member.
  - `Set[T]` accepts sets and sequences (`list`, `tuple`) only, and refuses mappings, dict views,
    generators, iterators, `str` and `bytes` - and `collections.abc.Set` implementations other than
    the built-in `set` and `frozenset`.
  - `date` refuses a `datetime`, which is an instance of it. `datetime | date` and `date | datetime`
    read an ISO string as the first alternative which reads it - `"2026-01-02"` is a `datetime`
    for the first, a `date` for the second.
  - `bool | int` and `int | bool` keep `1` an `int` and `True` a `bool`: a value which is accepted as
    it is wins over one which would convert, whichever is declared first. Of `bool | IntEnum` a
    `1` and a `1.0` are read as `True`, while an `IntEnum` member is kept.
- **Breaking:** the keys of a mapping are read from the spelling `to_json` and `to_basic_object`
  write them as, and from no other: `int` keys as canonical decimal integers (no sign `+`, spaces,
  underscores, leading zeros or other scripts), `float` keys as JSON numbers, `bool` keys as
  `"true"`/`"false"` (lowercase), `None` as `"null"`, a `Literal` key as the spelling of its value,
  an enum key as a value, a number or a name, and the infinities and `NaN` of `float` keys as the
  `"inf"`, `"-inf"` and `"nan"` written for them (not the `Infinity`/`NaN` of `json.dumps`). A
  `float` key beyond what a float holds (`"1e400"`) is refused.
  Two keys which convert to one (`Mapping[int, str]` given `{"1": "a", 1: "b"}`) raise a
  `ValidationError` at the second, where it replaced the first silently.
- **Breaking:** the JSON Schema agrees: `bool` is `anyOf` a boolean, `0`/`1` and the words in any
  case; an `IntEnum` is `anyOf` its values and the names of its members, a `StrEnum` takes the names
  next to the values; `propertyNames` of `int`, `float`, `bool`, `None`, `Literal` and enum keys
  state the exact spellings.
- **Breaking:** a mutable plain default - a `list`, `dict`, `set` or `bytearray`, or a subclass of
  them, also as `Default(value)` - raises `TypeError` when the class is created, as every instance
  would share it. Use `DefaultFactory(list)`, `Default(factory=...)` or an immutable value
  (`()`, `frozenset()`, `Map()`).
- **Breaking:** the JSON a State writes and reads is strict. `to_json` - the native writer, with
  `indent` and with a custom encoder class alike - raises `ValueError` for an infinite or `NaN`
  float value wherever it is: an attribute, an `Any`, a nested container, a set or the value of a
  mapping, where `Infinity` and `NaN` were written, which no strict JSON parser reads.
  `from_json` and `from_json_array` refuse the constants `NaN`, `Infinity` and `-Infinity` and a
  number beyond what a float holds (`1e400`) anywhere in the document, a key skipped by
  `ignore_extra` included. A custom `decoder` class is given `parse_float` and `parse_constant`
  hooks which do that, and has to accept and keep them; a decoder overriding `decode` refuses them
  itself or not at all. Float mapping keys keep the `"inf"`, `"-inf"` and `"nan"` spellings both
  ways. `to_mapping`, `to_basic_object` and `from_mapping` work on Python values and keep infinite
  and `NaN` floats.
- **Breaking:** a `bool` variant is no `int` variant, and a `datetime` variant no `date` variant, to
  the variance of a generic State: `Box[bool]` is not a subclass of `Box[int]` - also through union
  arguments, `Box[int | None]` - and `Box(value=True)` is not an instance of `Box[int]`, so a
  `Box[int]` attribute refuses a `Box[bool]` instance. Neither direction relates `bool` and `int`
  for a contravariant parameter. Instances, subclass checks and mappings agree now, and round trip.
- `to_json` and `to_basic_object` raise `ValueError` for a mapping holding two keys which are
  written the same (`{1: "a", "1": "b"}`, `{True: 1, "true": 2}`), where one of them was kept
  silently, or both written for `to_json` natively.
- `==` of a State with something which is no State returns `NotImplemented`, so the other operand
  decides (`instance == mock.ANY` is true). Two States of different classes are still never equal.
- A `Callable[P, R]` attribute of a specialization (`Handler[[int], bool]`) is checked against the
  signature it was given, as `Callable[[int], bool]` is, and a type parameter which is not
  specialized and has constraints takes any one of them (`class Con[T: (int, str)]` refuses
  `1.5`). Such an attribute is no longer left out of `required` and the signature.
- The hash of an instance agrees with `==`: equal instances holding a `Map`, a `UserDict` or a
  mapping proxy and a `dict`, a `frozenset` or a dict view and a `set`, a `UserList` and a `list`,
  `bytes`, a `bytearray` and a `memoryview`, alone or within tuples, lists and mappings, hash the
  same. **Behaviour change:** the hash of an instance holding a `list`, `dict`, `set` or
  `bytearray` (under `Any`, `object` or a custom attribute) now takes what it holds into account,
  so it changes when that is mutated and a `set` or `dict` holding the instance no longer finds it
  - it was left out before. Typed attributes convert those to immutable values; prefer them. A value held anywhere in an instance which raises while it is hashed - an instance
  which is not initialized above all, or an object with a `__hash__` raising `TypeError` - raises;
  only a value with `__hash__ = None` is left out.

### Fixed

- A `bool` attribute given a `str` subclass whose `lower` returns something else than a `str` crashed
  the interpreter, as the validation called the `lower` of the value and read its result as text. The
  text is compared as it is stored now, ignoring the case of ASCII letters, with no code of the value
  running - the spellings accepted are the same. A `datetime`, `date`, `time` or `Path` subclass whose
  `isoformat` or `as_posix` returns no `str` made `to_json` write garbage or crash, and
  `to_basic_object` return what it returned; both raise a `TypeError` now.
- Reading JSON ran code of the user while the document was still being read, and again when the
  document was refused and read once more. A State was taken to run none whatever its fields held,
  so a `Validator`, a `Verifier`, a default factory or an `__init__` of a State nested in a tuple, a
  set, a `TypedDict` or a union ran in the middle of the read - for a document which then failed on
  an attribute after it, twice. Whether reading a class runs code of the user is told in one place
  from its plan - it is constructed natively, and every attribute is validated without code of the
  user and has a default which is always the same - and the attributes which do are kept as the
  document spells them until it has been read through, whatever they nest. A document which is not
  JSON runs no such code now, one which is accepted runs it exactly once, in the order of the
  attributes rather than the order of the keys, and one which is refused runs exactly what
  `cls.validate(json.loads(text))` runs, and never any more. The failure of that code is reported at
  its attribute the way validating the instance reports it - `from_json_array` words it
  `from json array` as before - rather than by reading the document again with the standard library.
- A key which is not an attribute, validated into a State through the general path, left no error set
  when the hook reporting it returned instead of raising, and `signature_fits` dropped the error of a
  flag whose truth value failed. Both are reported.
- Within a free-threaded interpreter a class was reachable, through `__subclasses__` or the garbage
  collector, before its plan was installed, and another thread could read the plan or the size of
  its instances while they were being written. The plan and the call of the class are published once
  everything they need is written, and read with a load which pairs with it. Nothing changes for an
  interpreter with the GIL.
- A recursive `type` alias, `type Json = Mapping[str, Json] | int`, compiled the alias again for each
  level of nesting a validated document reached, so the memory used grew with the deepest one. The
  alias within itself refers to the alias, which has one compiled description whatever the depth.
- Converting an iterable which is not a `Sequence`, a `set` under `Any` say, or a mapping which is not a `dict`, walked the
  object itself while the elements were converted, which failed with a `RuntimeError` for one which
  was changed meanwhile, by the conversion or by another thread. It is walked from a snapshot.
- Reading the first path of an attribute from several threads at once - `Model._.field` or
  `Model._.items[0]` on a free-threaded build - could give each of them a path of its own, so paths
  of the same attribute were not identical. The first one made is kept and given to all.
- The variances of the parameters of a generic State are published as a whole when worked out, where
  they were filled in place, and a thread reading them meanwhile could find a part of them.
- `to_mapping()` turned a `bytearray` or a `memoryview` into a list of integers, where `bytes` stays
  as it is - bytes-like values are all kept as they are, the way `to_basic_object()` encodes all of
  them as base64.
- `typing.ReadOnly` (PEP 705), in a `TypedDict` or on an attribute, failed with
  `Unsupported annotation of 'typing.ReadOnly[int]'`. It is resolved as the type it qualifies and
  changes nothing in the schema.
- A State with an attribute accepting `Missing` listed it in the `required` of its schema, where
  `from_json("{}")` accepted it left out.
- A `Literal` of enum members put the members themselves in the schema, which can't be written as
  JSON.
- `isinstance` against a specialization (`Box[date]`, `Box[Set[int]]`) agrees with validation: a
  `datetime` is no `date`, and only a `set` or `frozenset` is a `Set`.
- A `float` given an int too large for it is a `ValidationError` with a `ValueError` cause, where
  the cause was an `OverflowError`.
- An `IntEnum` key is read as a member, then as the number of a member, then as a name - an enum
  with a member named like the value of another read the wrong member before.
- A `None` key of a mapping is read back (`Mapping[None, int]` round trips), and so is a `Literal`
  key written as a number (`Mapping[Literal["a", 1], int]`).
- A State built from a list whose elements were converted into objects the garbage collector tracks -
  a custom class with a `validate`, through `Sequence[X]`, `tuple[X, ...]` or a fixed tuple, by the
  constructor, `validate`, `updating` or `from_json` - was left untracked itself, so reference
  cycles through it were never collected. It is tracked now - earlier releases leaked the same way.
- `AttributePath` (`State._.x`) no longer asserts the annotation of an attribute with `isinstance`:
  reading and updating a `Literal`, `Any` or `X | Missing` attribute raised `TypeError`, and an
  update refused a value which construction converts (an `int` for a `float`) with an
  `AssertionError`, which `python -O` skipped, so it behaved differently. An update validates as
  construction does, and its `ValidationError` names the whole path from the root (`.inner.x`,
  `.many[0].x`). A value given to an object which is no State along the path, a dataclass, is taken
  as its own `__replace__` or assignment takes it. `AssertionError` remains for a root, or a subject
  along the path, of the wrong type.
- Free-threading: a `set` changed by another thread while it was given for validation raised
  `Set changed size during iteration`. It is copied under its lock before it is iterated.
- JSON Schema: the `$anchor` names are unique in the whole document. Two recursive aliases of one
  shape (`int | Sequence[...]`) and same-named recursive classes of different modules shared one,
  and a `$ref` resolved to the wrong subschema. A recursive State embedded several times is defined
  once and referenced, and `tuple[()]` is `{"type": "array", "maxItems": 0}`, which was invalid
  against the metaschema. `ArraySpecification` gained `maxItems`.
- `x: int = Missing` raises a `TypeError` saying the attribute defaults to `Missing`, which its
  annotation does not accept, and to annotate it with `| Missing`, where it blamed `NotRequired`.

## 0.65.1

### Fixed

- A generic State refers to itself by its own parameters, `children: Sequence[Tree[V]]` within
  `class Tree[V](State)`, where the class failed with a `KeyError` when it was created - and a
  variadic one, `Sequence[Row[*Ts]]`, with a `RecursionError` when it was specialized. It is the
  same reference `Self` makes: `Tree[V]` stands for `Tree` in the class which is not specialized and
  for `Tree[int]` within it, also for `Pair[K, V]`, `Row[*Ts]` and `Handler[P, R]` given their own
  parameters in the order they are declared. Any other specialization of a generic within its own
  declaration - `Pair[B, A]` within `Pair[A, B]`, `Nest[Sequence[V]]`, `C[int]`, or a variadic one
  such as `Row[*Ts, int]` within `Row[*Ts]` - is refused with a `TypeError` when the class is
  declared, also where it is made through a generic alias, where it failed with a `KeyError` or a
  `RecursionError`.
- A quoted annotation of a generic State is evaluated with the type parameters of the class in
  scope, where `children: "Sequence[Tree[V]]"` of `class Tree[V](State)` failed with
  `Cannot resolve annotation of Sequence[Tree[V]]`.
- A State referring to itself through a `type` alias, `type Forest = Sequence[Tree]` of
  `class Tree(State): children: Forest`, resolves the alias to the class being created, where
  evaluating the alias failed with a `NameError` since the class was not defined yet. So does a
  generic alias whose type parameters stand for the own parameters of the class,
  `type Forest[V] = Sequence[Tree[V]]` used as `Forest[V]`. Other parameters of a generic alias are
  not bound to their arguments - one is looked up by name among the parameters of the class - and
  a variadic or `ParamSpec` generic alias is not supported. An undefined name within an alias is
  still reported as a `NameError`, naming the alias.
- A `Mapping` or `TypedDict` entry which failed validation while a garbage collection was due raised
  `SystemError: ... returned NULL without setting an exception` instead of `ValidationError` - in the
  constructor, `validate`, `from_mapping`, `updating` and `from_json` alike. The text of the key was
  made with the failure still pending, which let the collection report and clear it. The failure is
  now taken before the position is built. A key which cannot be printed no longer hides the failure
  either: the position reads `[<unprintable>]` then.

## 0.65.0

### Added

- `State.from_mapping`, `State.from_json` and `State.from_json_array` take a keyword-only
  `ignore_extra` flag. When set, keys which are neither an attribute name nor an alias are skipped
  instead of refused, in every nested plain State, sequence, mapping, tuple, union and TypedDict the
  decode reaches - but not below a class with its own `__init__` or `validate`, which decide for
  themselves and validate what they nest without the flag. JSON values of unknown keys of plain
  States read natively are walked over without being built. The constructor, `updating()`,
  `__replace__` and `validate` keep refusing unknown keys, a class with a `validate` of its own is
  handed the value unchanged, and a validator which decodes on its own does not inherit the flag.
  In a union an alternative which takes the value as it is wins first, flag or not; among the rest
  the first which validates wins, and with the flag that can drop data - list the most specific
  alternative first, or tell them apart with a `Literal` attribute. An object which repeats a key
  after an alternative of a union has failed is left to the standard library with the flag as it is
  without, so the last value of the key decides, and a skipped integer of more than 640 digits is
  left to it too, which applies the limit of the interpreter.

### Changed

- A `TypedDict` attribute refuses a key it does not declare, with `Unexpected key 'x' for Name`,
  where it used to drop it silently - in every way a State is built: the constructor, `validate`,
  `updating()` and the decoding helpers. Only the decoding helpers can opt out with
  `ignore_extra=True`, which gives the previous behaviour; elsewhere the caller has to drop the keys
  itself. `closed` and `extra_items` of PEP 728 are not honoured: undeclared keys are refused, or
  dropped with the flag, never kept or validated against `extra_items`.
- Without the flag, `TypedDict | Mapping[...]` given undeclared keys selects the mapping, which
  keeps every key, where it used to pick the TypedDict and drop them.
- A TypedDict reads a dict subclass from a copy, as a State does, so a `__getitem__` or `__missing__`
  override - a `defaultdict`, say - no longer supplies values: a missing required key is reported.
- The stricter TypedDict applies wherever one is validated, not only when building a State:
  `Attribute.validate`, argument validation of functions and `isinstance` against specialized States.

### Fixed

- `State.from_json` and `State.from_json_array` call the `validate` of a class which declares its
  own, as `from_mapping` and a nested State do, instead of reading the document natively around it.
- A tuple with an unpacked `*tuple[X, ...]` such as `tuple[Leaf, *tuple[int, ...]]` validates its
  elements, where it took the unpacked part for one more element. An unpacked fixed tuple such as
  `*tuple[int, str]` is spliced into the tuple likewise, and a tuple with more than one unbounded
  part is refused when the class is declared.
- A tuple with an unbounded part, such as `tuple[Leaf, *Ts]` of a generic State which is not
  specialized or one specialized with `*tuple[int, ...]`, is validated natively: its elements are
  validated like those of any other tuple, and its JSON schema lists the elements which lead it.
- The name of a specialized generic State spells its arguments in full, as they are written:
  `Box[list[int]]`, `Box[int | None]`, `Box[Literal['a']]` and `Box[*tuple[int, ...]]` where it read
  `Box[list]`, `Box[Union]`, `Box[Literal]` and `Box[tuple]`, and the signature of a `ParamSpec` as
  `Handler[[int, str]]`.
- A `TypeVarTuple` specialized with `...`, as in `Row[int, ...]`, is refused with a `TypeError`
  instead of being accepted and validating anything - an unbounded tuple is `*tuple[int, ...]`.
- A `TypeVarTuple` with an unbounded default, `class Box[*Ts = *tuple[int, ...]]`, binds it as it
  does when it is given, where it stood for anything.
- `Box[Unpack[tuple[int, ...]]]` and `Box[*tuple[int, ...]]` are one and the same specialization.
- `tuple[*tuple[int, ...]]` and `tuple[*Ts]` are read as `tuple[int, ...]` and `tuple[Any, ...]`
  are, and a bare `tuple` or `typing.Tuple` takes any elements where it took none - only
  `tuple[()]` is the empty tuple.
- A bare `Unpack[tuple]` or `Unpack[typing.Tuple]` within a tuple, as in
  `tuple[int, Unpack[tuple], str]`, is an unbounded part of anything, where it was refused or taken
  for an empty one - `Unpack[tuple[()]]` stays the empty one.
- `tuple[*tuple[Tree, ...]]` of a recursive `type Tree = int | tuple[*tuple[Tree, ...]]` resolves,
  where it failed with an `AssertionError`.
- An unpacked type alias, `*IntTuple` of `type IntTuple = tuple[int, ...]`, is accepted within a
  tuple, fixed or unbounded.
- A forward reference to a generic specialized with a class of the same name as another one,
  `"Box[Item]"` next to `"Box[Item]"` of a different `Item`, resolves to its own class instead of the
  first one resolved.

## 0.64.0

### Added

- `StateResponse` of `haiway.starlette` and `haiway.fastapi` answers with a `State`, or a sequence
  of them, written as the JSON body by `State.to_json`, with `application/json` as the media type
  unless given. The State is encoded where the response is built, so a value which fails to encode
  raises `ValueError` from the handler. The body carries the actual values, `Sensitive` attributes
  included. In FastAPI annotate the endpoint with `StateResponse`, as the return of a `Response` skips
  its serialization.

## 0.63.0

### Added

- `State` types take a `description` class keyword, as in
  `class AssistantConfig(Configuration, description="Settings of the assistant")`. It is kept as
  `__DESCRIPTION__` and written into the type's JSON schema, so it survives `python -OO` and a State
  nested in another one describes itself there. A `Description` on the attribute holding the State
  takes precedence, and a specialization of a generic keeps the description of its origin.

## 0.62.1

### Added

- `gather()` runs the given coroutines concurrently, without a limit of running tasks, returns their
  results in order and cancels the rest when one fails. See the concurrency guide.
- `skills/haiway` is an agent skill for users of Haiway - principles, rules and review checklist,
  style and immutability notes, a guide to easily overlooked helpers and extras, recipes for common
  problems, building blocks from state to features, integration usage and gotchas, project structure
  and tooling. Copy it into `~/.claude/skills/` or the `.claude/skills/` of a project.
- `ObservabilityLevelChecking` is exported from `haiway.context.observability`.
- `ContextPresets.preparing` tells whether entering a scope with the preset has anything to prepare.
- `haiway.postgres.observability` exports `IDLE_CONNECTIONS_ATTRIBUTES` and
  `USED_CONNECTIONS_ATTRIBUTES`, the attributes of the connection count gauge.

### Changed

- The Starlette and FastAPI `application(...)` factories take `exception_handler` - the handler of
  the `500` of an unhandled failure, nested below `ContextMiddleware` so its response carries the
  trace headers - in place of `exception_handlers` and `lifespan`. Handlers of other exceptions and
  status codes go in `exception_handlers` among the extra arguments, as in the framework itself, and
  already answer within the request scope; `Exception` and `500` do not belong there. The factories
  install the lifespan of the context alone - startup work is a disposable or belongs in an
  application built by hand, entering `context.lifespan(app)` within a lifespan of its own.
- `debug` of those factories defaults to `__debug__` instead of `False`.
- `ServerContext.request_state` is a property, not a method.
- `ContextMiddleware` writes the trace headers as the backend provides them, asserting they are valid
  header values, instead of leaving out an entry a header can not hold.
- `DefaultEnv(..., required=True)` with a fallback, and `Default` combining a factory with a value,
  are programming errors checked by assertions instead of `ValueError`.
- `Alias`, `Description`, `Default`, `Sensitive` and `Specification` are plain annotation objects
  again - copying one creates a new instance, and `Sensitive` can be assigned to.
- A scope entered with presets which carry static state only takes the synchronous path, like a
  scope without presets.
- `Map`, `Meta` mutators which always raise are typed `NoReturn`.
- `Paginated.__getitem__` with a slice is typed as `Sequence[Element]`.

### Removed

- `ExceptionHandling` of `haiway.fastapi` and `ServerContext.composed_lifespan()`.

### Performance

- The `operation_attributes` of Postgres operations without an error are shared instead of built for
  each call.

## 0.62.0

### Added

- `gather()` runs the given coroutines concurrently, without a limit of running tasks, returns their
  results in order and cancels the rest when one fails. See the concurrency guide.
- `URITemplate` is an immutable URI template following [RFC 6570](https://www.rfc-editor.org/rfc/rfc6570)
  which expands values into a URI (`expand()`) and matches a URI back into its parameters
  (`match()`, `matches()`). Matching holds every decoded value to the segment it was found in, so
  the template can guard lookups. Only a subset of the RFC is supported - `{name}`, `{/name}`,
  `{?a,b*}` and `{#name}` - anything else is refused when the template is created. See the
  helpers guide.

### Changed

- A `Literal` attribute accepts a value of the type of one of its values, not one which is merely
  equal to it: `Literal[1]` no longer takes `True` or `1.0`, and `Literal[True]` no longer takes
  `1`. A plain `str` or `int` given for a literal `StrEnum` or `IntEnum` member - what JSON carries
  of one - is accepted as that member instead of being kept as the plain value, and such a member
  given for the plain literal it is equal to (`Literal["a"]`, `Literal[1]`) is accepted as that
  plain literal - the attribute holds one of its literals either way.
- `AsyncQueue.enqueue()` may be called from another thread, like `finish()`, `cancel()` and
  `clear()` - it is handed over to the loop of the queue and takes effect there.
- A call handed over to the loop of an `AsyncQueue`, an `AsyncStream` or the events of a scope is
  handed over whenever it is made outside of that running loop, also when the loop is not running:
  it takes effect once the loop runs, and raises `RuntimeError` when the loop is closed. It used to
  run in place for a loop which was not running.
- Misuse of `AsyncQueue` and `AsyncStream` is a programming error checked by assertions instead of
  raised exceptions: a non-positive `limit` of a queue (was `ValueError`), `AsyncStream.send()`
  outside of the loop of the stream (was `RuntimeError`), and reading either of them
  (`is_finished`/`finished`, `pending_next()`, iteration) outside of its loop.
- `Paginated.items` is typed as `Sequence[Element]` - still a plain tuple - and indexing takes an
  `int` or a slice of them.

### Removed

- `StreamResponse` no longer warns about a server advertising an ASGI spec version below 2.4.
  The requirement stays, it is just not checked for each application.

### Fixed

- `from_json` resolves a union the way `from_mapping` does. An object given to a union of a
  `State` and an alternative accepting mappings (`Mapping[...]`, `Any`, a `TypedDict`, `Set[...]`)
  was read as the `State` by `from_json` alone, and an object repeating a key could be read as a
  later alternative than the one `json.loads` would have led to.
- `from_json` no longer writes into a freed instance when an object read as a `Mapping` of `State`
  values repeats a key - such a document is read the way `json.loads` leaves it, the last value
  of the key staying.
- A `State` holding a `Meta` is left to reference counting like any other acyclic instance - it
  used to stay tracked by the garbage collector.
- A malformed spec description given to the extension is refused with a `TypeError` instead of
  crashing the interpreter.
- A `Literal` mixing an enum member with the plain value it carries (`Literal[Code.ONE, 1]`)
  keeps a value of the very type whatever the order of the literals.
- `from_json` reads the document when an object of a union repeats a key after an alternative
  has already failed - which alternative fits is what `json.loads` leaves of it - also when the
  key is repeated within a value read as `Any`.
- A response stream recorded a consumer which went away mid-stream as a failure again - it is
  recorded as a disconnect, while a body failing with an `OSError` of its own stays a failure.
- `AsyncQueue.finish(clear=True)` and `aclose()` release a consumer which was handed an element
  also when the queue was finished already, so the element is not put back after the buffer was
  dropped.
- Reading the entries of a dictionary into an instance no longer looks attributes up while
  holding the lock of the dictionary, which a free-threaded build could let go of.

### Performance

- `validate` and `from_mapping` read a dictionary in one walk, without copying it first: up to
  30% faster for instances built from mappings, nested ones included, and close to 20% for large
  ones.

## 0.61.1

### Added

- `DefaultFactory(fn, *args, **kwargs)` creates a default produced by calling `fn` with the given
  arguments, replacing `Default(factory=lambda: fn(...))`.
- `DefaultEnv(name, ...)` reads a default from an environment variable and replaces
  `Default(env=..., mapping=..., default=...)`, which is removed. What happens when the variable is
  not set has to be stated: `fallback=` gives a value (`Missing` makes the value optional) and
  `required=True` raises, naming the variable. `DefaultValue` no longer stores the variable name.
- `AttributePath.serialized_components` returns the components of a path as found in serialized
  data (the output of `to_mapping`): a field is named by its `Alias` when it has one, mapping keys
  are strings and sequence or tuple indices are integers. `components` and `str(path)` keep
  returning Python attribute names. Queries and filters against stored data no longer have to
  resolve aliases on their own.
- `AttributePathComponent` requires a new `serialized()` method - custom implementations of the
  protocol have to add it.

## 0.61.0

### Native State

The C extension introduced as opt-in in 0.60.0 becomes the only implementation: it is the storage
of `State`, and validates every built-in annotation, updates, compares, hashes and
converts to mappings and JSON. There is no Python implementation of those any more. The extension
is built for Linux, macOS and Windows on CPython 3.15, and the test suite runs on each.

The extension is written to run without the GIL - its state is kept per module, it never holds a
borrowed reference into a container another thread could change, and it validates snapshots of
the inputs it is given - but it still requires the GIL: on a free-threaded interpreter, importing
it enables the GIL. Supporting the free-threaded build is left for a later release.

#### Breaking changes

- **Python 3.15 or newer is required.** 3.14 is no longer supported.
- **The compiled extension is required.** Wheels carry it (`cp315`, Linux, macOS and Windows);
  installing from the source distribution needs a C compiler. `HAIWAY_NATIVE` has no meaning any
  more.
- `Map` and `Meta` subclass the builtin `frozendict`, so they can't be mutated through the `dict`
  methods either.

  Migration: `isinstance(map, dict)` is `False` - check against `Mapping`. `{} | map` and
  `{} | meta` are plain `dict`s, as `dict.__or__` handles a `frozendict` itself.
- `State.__init__` refuses to initialize an instance a second time, and a `State` instance can't be
  rebound through `object.__setattr__`.
- **A `State` class can't be changed once it is created** - assigning or deleting an attribute of
  the class raises `TypeError`, also through `type.__setattr__`. Replacing the descriptor of an
  attribute hid the value of every instance, and an `__init__` assigned later was passed over.

  Migration: declare methods and class variables in the class body, or set them from
  `__init_subclass__` of an attribute-less base, which runs before the class is complete. A class
  decorator can no longer add to a `State`, and `mock.patch.object(SomeState, "method")` is
  refused - hold what a test replaces as an attribute and construct the state with it.
- **`Immutable` is removed** - a value which has to be validated, compared and serialized is a
  `State`, and a record holding what it is given as it is, a final, slotted class refusing
  `__setattr__` and `__delattr__` (equality and hashing by identity, and `copy`/`deepcopy` returning the
  instance itself, as `Immutable` had). Unlike `Immutable`, such a class can be rebound through `object.__setattr__`.
  `HTTPResponse`, `HTTPXClient` and `PostgresConnectionPool`, which change after they are made, are
  plain classes with `__slots__`, with the same constructors and methods.

  Migration: replace `class Record(Immutable)` with such a class, or with a `State` when the values should be
  validated. `Default(...)` markers become defaults of the constructor.
- **A `State` has a single base** - the `State` it refines, which declares no attributes (or the
  origin of a generic specialization), and `Generic` when it declares type parameters. Mixins and
  multiple inheritance are refused.

  Migration: share behavior through an attribute-less `State` base, and compose by holding instances
  as attributes.
- **`Missing` is a builtin PEP 661 sentinel, and `MISSING` is gone.** `Missing` is both the value
  and the type, the way `None` is: `value: int | Missing = Missing`. It stays the one instance when
  copied or pickled - the old `MISSING` did not, which lost it in a deep copy of a `State`.

  Migration: replace `MISSING` with `Missing` and `Missing()` with `Missing`; compare with
  `value is Missing`. The helpers `is_missing`, `not_missing` and `unwrap_missing` are gone -
  `value is Missing` and `value is not Missing` narrow the type on their own, and
  `default if value is Missing else value` takes the place of `unwrap_missing`. `Missing` is truthy, so `if value:` no longer tells it apart - nothing in
  Haiway relied on that. Within `match`, use a dotted name (`case haiway.Missing:`), as a bare
  `case Missing:` captures the value.
- The `validate` and `check` methods of the attribute annotations are gone - the compiled spec of
  an `Attribute` validates (`haiway.attributes.native.compile` and `validate`).
- **Function fields check that a value can be called the way the annotation says.** A
  `Callable[[int, str], None]` refuses a function of one argument, or one requiring a third, when
  the state is created instead of failing once it is called. A protocol whose only member is
  `__call__` is checked by that signature - every call it allows has to be one the value takes,
  so a parameter which may be given by name has to have the same name, and one which may be left
  out a default - where it accepted anything callable before, and a protocol of `__call__` alone
  no longer needs `@runtime_checkable`. Arguments left open - `...`, a `ParamSpec`, what follows
  `Concatenate`, `*Ts` - and the types of arguments are not checked. A partial is read as what it
  calls, given its arguments - `functools.Placeholder` ones are given by the call first, by
  position only - and its keywords, joined with those of partials around it. A bound method is
  read the same way, as what it calls given the object first - a partial held by a class
  included. A parameter given by position already, by a partial or as the object a method is
  bound to, refuses its name given again, even to `**kwargs`. `FunctionAttribute.arguments`, which was always empty, is replaced by
  `signature` - a `CallableSignature` or a `ProtocolSignature` read from the annotation, `None`
  when it accepts any call.
  Migration: fix the functions the check refuses - they could not have been called the way the
  annotation says.
- **`ContextObservability.record_log` takes the format arguments as one tuple** -
  `record_log(level, message, (first, second), exception=None)` - handing them on without
  unpacking and packing them again. Migration: wrap the arguments in a tuple; `ctx.log_*` are
  unchanged.
- **`Paginated.of` is removed** - the constructor takes the items by position, the same way
  `of` did: `Paginated(items, pagination=pagination)`. Passing `items=` by name keeps working.
  Migration: replace `Paginated.of(` with `Paginated(`.
- **An opened `File` belongs to the event loop which entered its context.** Using it, or exiting
  its context, from another loop raises `FileException`; once the context is exited it can be
  entered again on any loop. Operations of an open file run one at a time in the order they were
  started, ordered on the event loop instead of a lock, and one abandoned by cancellation keeps
  running until done - the next operation and closing the context wait for it. Migration: open a
  file within the loop which uses it.
- **`http.route` of a route served from a `Mount` carries the templates of the mounts leading to
  it** - `/api/users/{id}` rather than `/users/{id}`, so the routes of different mounts are no
  longer reported as one in traces and in the request duration metric. A route reached more than
  one way through the route table records no `http.route`. Dashboards and alerts keyed by the
  previous values need updating.
- **Invalid arguments raise instead of failing an assertion**, so an optimized build (`python -O`)
  no longer lets them through:
  - `Alias("")` and `Specification({})` raise `ValueError`.
  - A `Default` combining a factory with a value or a variable raises `ValueError`, and so does
    a mapping without a variable.
  - `Meta.of` raises `ValueError` for values given together with metadata (an optimized build
    dropped them), and `TypeError` for something which is not a mapping.
  - `AsyncQueue(limit=...)` raises `ValueError` for a limit which is not positive (an optimized
    build made a queue dropping everything).
  - A second consumer of an `AsyncQueue` or an `AsyncStream`, and sending to an `AsyncStream`
    from another event loop, raise `RuntimeError`.
  - `ConfigurationRepository.volatile` and `define` raise `TypeError` for arguments which are
    not configurations, or a value missing or given where it does not belong.

  Migration: catch the new exceptions instead of `AssertionError`.
- **`Meta` refuses keys which are not strings** in `validate`, `of`, `from_mapping` and
  `merged_with`, as `|` always did - a `TypeError`.
- `ConfigurationRepository.volatile` lists the configurations which loading accepts as the given
  class, compared by `isinstance` - it compared the names of the classes.

#### Added

- **Native `State`.** Instances hold their attributes inline, with no `__dict__` or `__weakref__`,
  and take the memory a `__slots__` object would. Creating, updating, validating a mapping and
  comparing an instance is 4 to 10 times faster than the Python implementation was, converting to a
  mapping, a basic object or JSON up to 100 times for large states, reading JSON 5 to 14 times.
  An instance proven unable to be part of a reference cycle - one holding scalars, tuples,
  frozensets, `Map`s, UUIDs and other such instances only - is left out of the collections of the
  garbage collector together with those containers, and is reclaimed by reference counting alone,
  however many are kept alive. Instances holding mutable objects, through `Any` for example, or
  containers nested too deep to check cheaply stay tracked.
- **Native validation of every built-in annotation**, with the messages the annotations have always
  raised with. The code of the user - a `Validator`, a `Verifier`, a class validating itself, a
  default factory - is what still runs in Python.
- **JSON in both directions.** `to_json`, `from_json` and `from_json_array` write and read the JSON
  without the intermediate Python objects, for the default `JSONEncoder` and `JSONDecoder` and no
  `indent`. A union of `State` classes is read without a second pass, and reading validates as it
  goes, running the code of the user only once the document is known to be right.
- `make native` rebuilds the extension.
- `make benchmark-http` measures the HTTP client - responses, the facade and the HTTPX backend -
  against `httpx2` alone, through its `MockTransport` (`benchmarks/http_benchmark.py`).

#### Changed

- **The build backend is `setuptools`**, as no backend of `uv` compiles C. `uv` stays the tool for
  everything else.
- **UUIDs, dates, times and datetimes convert faster.** `to_json` and `to_basic_object` spell them
  natively - exactly as `str` and `isoformat` do - for the exact types, naive or, for datetimes,
  in UTC, so a state holding them converts about a third faster. Times with a timezone, datetimes
  in other timezones and subclasses keep their own spelling.
- **The HTTP client costs less per request.** The facade reads the URL of a request once for what
  it records, and a streamed body hands its chunks over with fewer frames.
- **Scopes cost less to enter and exit.** A nested scope is about 28% cheaper and a root scope
  about 23%, reading the context no longer raises on a miss, and `ctx.spawn` skips the type checks
  of a plain coroutine function, so a request handled through Starlette or FastAPI costs about 10%
  less. `make benchmark-scope` measures the context (`benchmarks/scope_benchmark.py`).
- **A log nobody keeps costs half as much.** `Observability` takes an optional `level_checking`,
  asked before a log, event, metric or attributes are passed on; the logger and OpenTelemetry
  backends provide it, so a `ctx.log_debug` with `DEBUG` turned off returns in about 210 ns instead
  of 460 ns, without formatting its arguments - also when logging out of any scope.
- **Validating a mapping costs less.** A dictionary given for a `State` attribute skips the Python
  `isinstance` hook of the class, a str or int of an enum is looked up by value instead of calling
  the enum, and an int key is spelled natively by `to_basic_object` and `to_json` - validating a
  mapping is 12 to 42% faster, and converting a `dict[int, ...]` about 4 times (`to_basic_object`)
  and 12 times (`to_json`) faster.
- **`ctx.updating` merges into the state current when it is entered**, as `ctx.scope` does, instead
  of the state current when it was called. Entering it where it was made behaves the same.
- `ctx.log_warning` and `ctx.log_debug` accept any `BaseException`, as `ctx.log_error` does.
- **Tasks spawned within `ctx.disposables` are joined before the disposables are released**, and
  cancelled together with a failing body - they belonged to the enclosing scope and could outlive
  the resources they used. Leaving `ctx.disposables` now waits for them.
- **A failed task is logged when it fails**, not only once its scope ends, and a scope keeps at most
  32 errors of its tasks - the rest are counted in the message of the raised `ExceptionGroup`. A
  scope living long, a server for instance, held every failure until it ended.
- `make coverage-native` measures the line coverage of the native extension by the tests. CI runs
  Bandit, and the whole suite on the free-threaded build with the GIL off.

#### Fixed

- **A generic `State` keeps the metadata of an annotated type parameter** - in
  `value: Annotated[T, ...]` a `Sensitive` marker, an `Alias`, a `Description` and the validators
  were ignored, so a `Sensitive` value was printed in `repr` and in logs. Present since generic
  states were introduced.
- **A type parameter of a generic `TypedDict` or class is validated** - bound to its argument
  without resolving it, it was taken as is, and `Required[T]`, `NotRequired[T]` and `Final[T]`
  around a parameter validated nothing.
- **A collection of a type parameter validates its elements** - in a generic `State` or `TypedDict`,
  `Sequence[T]`, `Mapping[K, T]`, `Set[T]`, `list[T]` and the other collections of a parameter
  accepted elements of any type once specialized, so `Holder[int]` took `["one"]`.
- `ctx.disposables` releases its disposables with the failure of a task spawned within it, or with
  the cancellation of joining them - they were released as if the body succeeded, so a disposable
  committing on success committed.
- `cache` and `cache_externally` return a coroutine function - `retry` over a cached function
  never retried, and `throttle` over one refused it.
- `asynchronous` wraps a function annotated with names declared after it - the annotations were
  read eagerly, raising `NameError`. The wrapper carries the attributes of the function through
  `functools.update_wrapper`, so its `__annotations__` are those of the function - the return
  annotation is no longer rewritten to a `Coroutine`.
- A finished `AsyncQueue` or `AsyncStream` raises its finish reason with the same traceback on
  every call - raising the same instance again extended its traceback each time.
- `escape_controls`, and the formatting of logs with it, escapes the Unicode line and paragraph
  separators (U+2028, U+2029), which split a record into lines as a line feed does.
- `Alias`, `Description`, `Default`, `Sensitive` and `Specification` can be copied, returning
  themselves as other immutable values do - `copy` and `deepcopy` raised `AttributeError`.
- No private API of the standard library or CPython is used any more: the default cache key, the
  resolution of generic aliases, callback protocols and forward references, and the lazy
  compilation of aliases in the extension (`PyMutex` instead of CPython's internal atomics) use
  public interfaces. A test guards against reintroducing them.
- `State.updating`, `validate`, `__replace__` and `__deepcopy__` carry docstrings, and the
  protocols used in public signatures of the helpers (`CacheClear`, `FileReading`, `FileWriting`,
  `PathTraversing`, `MQMessageAcknowledging`, `MQMessageRejecting` and the `Configuration*ing`
  protocols) are exported from `haiway.helpers`.
- An event subscription made after its scope began closing - from a task spawned within it -
  completes at once instead of waiting forever.
- The observability backend learns a scope ended when resetting its context fails, as for an async
  generator holding a scope closed from another task.
- The error of a task which the body awaited and reraised is no longer chained as its own cause.
- A scope cancelled again while it disposes what it prepared before failing or being cancelled
  waits for that disposal to complete - it went on while the disposal ran detached.
- `process_concurrently` keeps only the tasks still running - it held every task it spawned until
  the source was exhausted.
- A cached function called while disposables are prepared or disposed writes its result to the
  cache in place - spawning the write failed, and the result was lost to the error.
- A synchronous `@retry` logs the failure it retries with its traceback, as the asynchronous one
  does.
- Validating a `State` from a dictionary reads a snapshot of it, so a validator changing the
  dictionary does not change what the later attributes are read from.

- A validator or verifier of a union alternative runs once for a value - it ran a second time when
  the value matched the alternative but failed its validation, and its error was reported twice.
- A mapping whose `items()` does not yield pairs of a key and a value is read through its keys, as
  it always was outside of a union, rather than crashing the validation of a union holding a
  `Mapping`.
- `@asynchronous` submits its calls from an empty context, so a thread the executor starts for one
  never keeps the context of the caller - on the free-threaded build threads inherit it, which kept
  the state of the first scope using it alive for as long as the thread lived.
- A class variable of a `State` declared under `from __future__ import annotations` is told from
  an attribute - `ClassVar[...]` as text was read as an annotation which is not supported. A bare
  `ClassVar` is one as well.
- `updating` an instance which was never initialized (`State.__new__`) is refused when it would
  carry a value over - it returned an instance missing the attributes which were not given.
- The Starlette `ContextMiddleware` no longer records a `WebSocketDisconnect` as a failure of the
  connection - it is withheld while the scope is left and reraised, as `ClientDisconnect` is.
- The Starlette `ContextMiddleware` leaves out a trace header it can not send - a name which is not
  an HTTP token, or a value which is not printable ASCII - instead of failing the response.
- The Starlette `ContextMiddleware` escapes control characters of the requested path in the name of
  the scope and in `url.path`, so a request can not forge lines of the records.

## 0.60.0

### OpenTelemetry integration

The OpenTelemetry bridge was reviewed against the OpenTelemetry specification and semantic
conventions. Telemetry now lands where backends expect it - at the cost of a few changes in what is
exported. No public signature was changed incompatibly.

#### Breaking changes

- **Events and exceptions are exported as log records, not span events.** OpenTelemetry deprecated
  the span event API (`Span.add_event`, `Span.record_exception`) in favor of log based events.
  `ctx.record_*(event=...)` now emits a log record with `event_name`, the severity of its level and
  the scope context. A failed scope emits one `exception` log record. Logging with `exception=...`
  and then raising the same exception reports it once. Nothing is added to `span.events` any more.

  Migration: make sure the logs signal is exported and ingested by your backend (the `configure()`
  path does this, `autoconfigure()` uses whatever logger provider you installed). Dashboards, alerts
  or queries reading span events named after your events or `exception` must read log records
  instead - filter on `event_name`, and correlate with the span through `trace_id` and `span_id`.

- **Failed span status carries the exception type only.** The status description was
  `TypeName: message` and is now `TypeName`; the new `error.type` span attribute holds the same
  value. The message may carry sensitive data - it is available on the `exception` log record.

  Migration: alerts matching the status description text should match `error.type` or the exception
  log record instead.

- **Instrumentation scope is `haiway`, not the service.** The tracer, meter and logger are named
  `haiway` and carry the Haiway version. The service stays identified by the resource
  (`service.name`, `service.version`, ...).

  Migration: queries or views filtering on the instrumentation scope name (`otel.scope.name`,
  `scope_name`) must use `haiway`, or better the resource attributes.

- **`service.instance.id` defaults to a random UUID** instead of the process id, which collided
  across containers.

  Migration: none needed. Pass `instance=` to `configure()` for a stable identifier.

- **Log attribute `scope.name` is now `haiway.scope.name`**, so it does not read as the
  instrumentation scope name in backends.

  Migration: update log queries filtering on `scope.name`.

#### Added

- **Scope kind.** `ctx.scope(..., kind=...)` takes a `ScopeKind` - `"internal"` (default),
  `"server"`, `"client"`, `"producer"` or `"consumer"` - exported from `haiway` and `haiway.context`
  and available as `ContextIdentifier.kind`. The OpenTelemetry bridge maps it to the span kind.
  Starlette and FastAPI request scopes are `"server"`.
- **Route based server span names.** A `server` span is renamed `{method} {route}` once
  `http.request.method` and `http.route` are recorded, and `{method}` for an unrouted request. The
  raw path no longer ends up in span names (it stays in `url.path`); the scope name itself, and so
  logger output, is unchanged. Migration: span name based filters and dashboards for HTTP requests
  now see `GET /users/{id}` instead of `GET /users/12345`.
- **`make benchmark-otel`** and `benchmarks/opentelemetry_benchmark.py` measure the bridge against
  the logger backend.

#### Messaging

- **RabbitMQ publishing is traced and measured.** Each publish runs in a `producer` scope named
  `publish {destination}` with `messaging.*` attributes, injects the trace context into the message
  headers (explicit `attributes` win over a header of the same name), and records
  `messaging.client.operation.duration` and `messaging.client.sent.messages` - a publish which did
  not complete, cancelled ones included, is reported with `error.type`. Migration: none required;
  consumers on other stacks read `traceparent` from the headers.
- **Settling a message records an event.** `acknowledge()`, `reject()` and `async with message`
  record a `messaging.settle` event within the current scope - `ack` at debug level, `reject` at
  info level - with the system, queue, routing key, delivery tag, body size and the plain settle
  options. No scope is opened for the processing. `MQMessage` accepts an optional `source`
  (`MQMessageSource`) for custom queue adapters to describe their messages the same way. Migration:
  none required.

#### Semantic conventions

- **`error.type` is the exception class `__qualname__` everywhere.** The HTTP client used the class
  `__name__`, which differs for nested classes.
- **HTTP client events use `url.full`** instead of `url`. Migration: update queries on the
  `http.request`, `http.response` and `http.request.error` events.
- **Postgres no longer records the adapter method as `db.operation.name`**, which the database
  conventions reserve for what the database was asked to do (`SELECT`, ...). The adapter method
  (`fetch`, `fetch_one`, `execute`, `acquire_connection`) moved to `haiway.postgres.operation`.
  Migration: queries filtering `db.operation.name` on adapter method names must use
  `haiway.postgres.operation`.
- **`db.client.connection.count` is split by `db.client.connection.state`** instead of `state`.

#### Security

- **Unknown HTTP methods are bounded.** The request method is chosen by the caller and is a
  dimension of the request metrics and part of the server span name. A method outside the standard
  set is now recorded as `_OTHER` (span name `HTTP`), with the received value in
  `http.request.method_original` on the span, so a caller can not create unbounded metric series or
  span names. Migration: queries for custom methods must use `http.request.method_original`.
- **Scheme relative URLs are redacted.** The HTTP client recorded `//user:password@host/...` with
  its userinfo, as it recognised only `scheme://` URLs - and one quoting `https://` in its path
  slipped past the check as well. Userinfo is now replaced with `REDACTED` for those too.
- **`server.address` is read only from the authority of the URL itself.** It was taken from any
  `://` in the URL, a relative URL quoting another one in its path or query included
  (`/login?next=https://other.example`), which recorded a host the request was never addressed to
  and let the content of a path pick the values of a metric dimension. Migration: none required;
  relative URLs record no `server.address`, as documented.
- **Websocket spans are named after the route** - `WS /rooms/{room}` - instead of the requested
  path, which holds identifiers and tokens more often than not.
- **Exceptions are reported once across nested scopes**, not once per scope they travel through:
  each scope hands what it reported on to its parent.
- **Logged values from untrusted headers are bounded** - a malformed `traceparent` is truncated
  before it is warned about, in addition to being escaped.
- **Messages of exception causes are redacted in exported stack traces.** The chain of causes of an
  exception - what the integrations wrap with `raise ... from error`, a database driver message
  quoting a value of a statement among them - is exported by type and frames, with the messages
  replaced by `<redacted>`. The message of the exception itself is exported as it is. Migration:
  diagnose from the types and frames of a cause, or log its message yourself where it is safe to.

#### Changed

- Attribute and event sanitizing is skipped for spans which are not recording (sampled out).

#### Known limitations

- Exporters are gRPC only - `OTEL_EXPORTER_OTLP_PROTOCOL` is not honored.
- Some integration attributes still deviate from the semantic conventions: HTTP client events carry
  an unprefixed `duration`, and connection metrics have no pool name.
- RabbitMQ consuming records settle events only - the message carries the trace context of the
  publisher in its headers, but a scope nested within the one of the consumer loop stays in the
  trace of that loop.

### Native State (opt-in)

A C extension takes over the storage, validation, updating, equality, hashing, conversion to
mappings and JSON of `State`. The pure Python implementation is the default and stays complete -
nothing changes unless `HAIWAY_NATIVE=1` is set, which uses the extension and fails, rather than
falling back, when it is not built. Both implementations run the same test suite on Linux, macOS and
Windows with Python 3.14 and 3.15.

#### Added

- **Native `State`** (`HAIWAY_NATIVE=1`). Instances hold their attributes inline, with no `__dict__`
  or `__weakref__`, so an attribute can't be rebound through `object.__setattr__` either. Creating,
  updating, validating a mapping and comparing an instance is 4 to 10 times faster, converting to a
  mapping, a basic object or JSON up to 100 times for large states. Attribute reads and request
  handling are unchanged, and an instance is slightly larger than one with slots.
- **JSON in both directions.** `to_json`, `from_json` and `from_json_array` write and read the JSON
  without the intermediate Python objects, for the default `JSONEncoder` and `JSONDecoder` and no
  `indent`. Anything else - another encoder or decoder, a value with no native spelling, a malformed
  or unusual document - is handled by the Python implementation, which also reports every error.
- `make native` rebuilds the extension, `make test-native` runs the tests against it.
- Wheels carry the extension (`cp314` and `cp315`, Linux, macOS and Windows), the source
  distribution compiles it when there is a compiler and goes without it otherwise.

#### Changed

- **The build backend is `setuptools`**, as no backend of `uv` compiles C. `uv` stays the tool for
  everything else.
- `State.__init__` of the native implementation refuses to initialize an instance a second time,
  and reports a key which is not an attribute after the values before it were validated, not ahead
  of them. The Python implementation is unchanged.
- `Map` and `Meta` subclass the builtin `frozendict` where Python has one (3.15), so they can't be
  mutated through the `dict` methods either. They stay `dict` based on 3.14.

  Migration: `isinstance(map, dict)` is `False` on 3.15 - check against `Mapping`. `{} | map` and
  `{} | meta` are plain `dict`s, as `dict.__or__` handles a `frozendict` itself.

### Generics

- **Type parameter defaults (PEP 696).** A defaulted parameter may be left out of a specialization,
  and an unspecialized generic stands for its defaults, so `Box(value="x")` fails validation when
  `Box` is `class Box[T = int](State)`. A parameter without a default stands for its bound, or for
  anything, as before.
- **Variadic parameters (PEP 646) and parameter specifications (PEP 612)** are specialized and
  validated position by position, and given to a generic attribute of another generic class.
- **Variance** is honored when declared, and inferred for parameters of the `class Box[T]` syntax
  from where the attributes use them - read from only is covariant, used as an argument of a
  callable is contravariant, both is invariant. It is followed through nested generic States, type
  aliases and `Concatenate`. A parameter declared without a variance keeps being covariant.

  Migration: a generic `State` using its parameter as an argument of a callable attribute now
  accepts and rejects other variants than before, in `isinstance`, `issubclass` and validation.
- `class Box(State, Generic[T])` can be specialized. It was refused with "takes 0 type arguments".
- Specializing a generic looks up an existing variant first - `Box[int]` is about twice as fast.

### Other changes

- `AttributePath` keeps the path reached by an attribute or an item, up to 256 item paths for each
  path - `State._.attribute` is about four times faster.
- Identifiers of scopes and traces come from a generator of their own - a random version 4 UUID
  without a system call - which also reseeds in a forked process. They are not secrets, and are no
  longer drawn from the operating system. Request handling is about 8% cheaper.
- The license metadata is the SPDX expression `MIT`.
