---
name: haiway
description: Write, review and refactor Python code that uses the Haiway framework (`from haiway import State, ctx, ...`) - immutable State types, Protocol-based services with @statemethod, contextual dependencies through ctx.scope, structured concurrency, observability, and the httpx/postgres/rabbitmq/starlette/fastapi/opentelemetry integrations. Use whenever a project imports haiway, when asked to add a service, state, scope, background work or integration in such a project, or when asked to design an application "the Haiway way".
---

# Writing code with Haiway

Haiway (Python 3.15+) is a strictly typed, functional, async framework. Everything is built from
three primitives: immutable `State`, the scoped context `ctx`, and structured concurrency. There is
no DI container and no mutable service object: behavior is `Protocol`-typed functions held inside a
`State` and resolved from the current scope.

Follow the existing code of the project first; use this skill for what it does not already settle.
When unsure about an API, read the installed package (`haiway.__all__`, docstrings, `help(...)`) or
the docs at https://miquido.github.io/haiway/ rather than guessing - names change between versions.

## Principles to design by

1. **Make illegal states unrepresentable.** Prefer unions, closed `Literal`s and small value types
   over optional fields valid only together, or flags meaningful in some states only.
2. **One source of truth.** Never store what can be computed; never repeat a name, value or list
   that already has a home.
3. **Small, composable interfaces.** One narrow responsibility per `Protocol`, type or function;
   combine by composition, not by growing one interface.

## Rules that are easy to get wrong

**Types and data**

- Subclass `State` for every domain, config and service object. It is immutable: change through
  `.updating(...)`, never by assignment.
- Annotate every attribute with abstract collections - `Sequence`/`Mapping`/`Set` - never
  `list`/`dict`/`set`. Whatever is written, values are stored as `tuple`/`Map`/`frozenset`.
- A `State` declaring attributes is final and has a single base: it cannot be subclassed, mixed in
  or combined. Reuse by holding an instance as an attribute, by parametrizing a generic
  (`class Box[T](State)`), or by inheriting from an attribute-less `State` that only adds behavior.
- Newest typing syntax: `type` aliases, PEP 695 generics, `X | None`. No loose `Any` except at
  third-party boundaries; no `cast` unless provably safe and local. Narrow unions with
  `match`/`isinstance`.
- `Missing` is the "not provided" sentinel, distinct from `None`: test with `is Missing`, never by
  truthiness (it is truthy); `case Missing:` captures instead of comparing.
- Mark secrets `Annotated[str, Sensitive()]` (redacted in `repr`/`str`, still present in
  `to_json`/`to_mapping`); read them from the environment with `DefaultEnv("NAME", required=True)`
  or `getenv_str`, never `os.getenv`.

**Services and dependencies**

- A contract is a `Protocol` named as a gerund (`UserFetching`), fully typed, `async` for I/O.
- An implementation is a plain function; a service is a `State` holding those functions and
  exposing `@statemethod` entry points. Dependencies are resolved with `ctx.state(Type)` inside the
  implementation - not passed through constructors, not stored in globals.
- `ctx.state(Type)` looks up the exact type. When it is absent, a `State` constructible without
  arguments is built from its defaults; otherwise `ContextStateMissing` is raised.
- Install state with `ctx.scope(...)`; external resources (clients, pools, files) are
  **disposables** so cleanup always runs. Tests install fakes the same way.
- Layout per functionality: `types.py` (Protocols + payload State), `state.py` (service State +
  statemethods), `implementation.py` (functions + factory). See `references/patterns.md`.

**Concurrency**

- All I/O is async. Never block the event loop; bridge unavoidable sync calls with
  `@asynchronous`, never with custom threads.
- Start tasks with `ctx.spawn(...)` (scoped, joined by the scope on exit).
  `ctx.spawn(...)` outside of any scope raises `ContextMissing`. Work which must outlive the scope
  goes to managed tasks (`ManagedTasks`; shape and guide in `references/recipes.md`).
- Use `gather(...)`, `concurrently(...)`, `execute_concurrently(...)` instead of raw
  `asyncio.gather`/`create_task`. Wrap async generators left early in `ctx.closing(...)`.

**Errors**

- Translate provider/SDK exceptions into typed ones at the boundary; keep context (provider,
  operation, identifiers), redact sensitive payloads. Never raise bare `Exception`.
- Misuse of an API is a programming error and is checked by assertions; expected runtime failures
  are typed exceptions.

**Observability**

- Never `print` or use `logging` directly. Use `ctx.log_debug/info/warning/error` and
  `ctx.record_debug/info/warning/error`. A record is an `event=`, or a metric
  (`metric=, value=, kind=`, optional `unit=`), each optionally with `attributes=`, or
  `attributes=` alone - never an event and a metric in one call. There is no `ctx.record(...)`.
- Never log secrets or full request bodies.

## Don't reinvent - check before writing

Haiway already ships helpers agents tend to skip: `@retry`, `@timeout`, `@throttle`, `@cache`,
`@asynchronous`, `gather`/`concurrently`/`stream_concurrently`, `AsyncQueue`, `getenv_*`/`DefaultEnv`,
`Validator`/`Verifier`, `AttributePath`, `Files`, `Configuration`, `URITemplate`, `Paginated`, the
event bus, and the extras (`httpx`, `postgres`, `rabbitmq`, `starlette`, `fastapi`, `opentelemetry`).
Scan `references/overlooked.md` before writing retry loops, env parsing, caching, queues, URL
building, file access or a client wrapper, and before adding a third-party dependency.

## Workflow

1. Look at how the project already splits functionalities and names things; match it. Put new code
   in the right layer: entrypoints -> features -> solutions -> integrations -> commons, where a
   layer depends only on those after it (features never on integrations directly).
2. Model the data as `State` first, then the `Protocol`s, then implementations, then wiring.
3. Wire dependencies at the edges (application entry, request handler, test) with `ctx.scope`.
4. Write tests with `pytest.mark.asyncio`; isolate state with `ctx.scope(...)`, install fakes as
   state, no network.
5. Run the project's formatter, linter, type checker and tests (ruff, bandit, pyright strict,
   pytest - see `references/project.md`) and fix findings instead of silencing them; do not loosen
   types or add ignores.
6. Public symbols get NumPy-style docstrings (Parameters/Returns/Raises); private helpers none.

## Reviewing Haiway code - checklist

- Any `list`/`dict`/`set` annotation, mutable shared container, `print`, `logging`, `os.getenv`,
  `asyncio.gather` or `create_task`, blocking call in `async def`, global singleton or hand-passed
  dependency?
- Service logic reaching for a client directly instead of a `Protocol` held in `State`?
- Invalid combinations of fields that a union would forbid? Values stored that could be derived?
- Resources opened without a disposable? Background task relying on `ctx.state(...)`?
- An event subscription created after the events it should see may already be sent?
- Secrets without `Sensitive()`, or logged?
- Exceptions from a provider leaking through untranslated?
- A hand-written helper that `references/overlooked.md` already lists?

## References

Read the one that matches the task; each is self-contained.

- `references/patterns.md` - the building blocks with code: modelling `State`, configuration,
  services, disposable resources, features, wiring with scopes and presets, tasks, errors, tests.
  Read it when adding or changing a state, service, resource or feature.
- `references/recipes.md` - preferred solutions for recurring problems: entrypoint, scoped data,
  optional dependencies, concurrency choices, resilient adapters, parsing input, streaming, events,
  sync bridging, testing situations, migrating existing code.
- `references/integrations.md` - HTTP, Postgres, RabbitMQ, Starlette/FastAPI, OpenTelemetry,
  configuration and files: wiring, usage and the gotchas of each. Read it before touching one.
- `references/overlooked.md` - "instead of X use Y" table of helpers that are easy to skip. Read it
  before writing any utility.
- `references/style.md` - immutability, typing, functional style, errors, logging and documentation
  conventions in full. Read it before a larger piece of work or a review.
- `references/project.md` - package layout and dependency direction, pyproject, ruff/pyright/pytest
  setup, make targets. Read it when creating a project or a new package.
