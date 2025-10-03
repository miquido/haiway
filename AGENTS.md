# AGENTS.md

Rules for coding agents to contribute correctly and safely.

## Development Toolchain

- Python: 3.12+
- Virtualenv: managed by uv, available at `./.venv`, assume already set up and working within venv
- Formatting: Ruff formatter (`make format`), no other formatter
- Linters/Type-checkers: Ruff, Bandit, Pyright (strict). Run via `make lint`
- Tests: `make test` or targeted `pytest tests/test_...::test_...`

## Project Layout

- `src/haiway/`: Core framework package.
  - `attributes/`: Attribute annotations, state objects, and validation helpers.
  - `context/`: Structured-concurrency context management, scopes, and lifecycle utilities.
  - `helpers/`: Cross-cutting helpers for configuration, async orchestration, retries, throttling, and HTTP adapters.
  - `httpx/`: Thin wrappers around `httpx.AsyncClient` aligned with haiway abstractions.
  - `opentelemetry/`: Observability utilities and exporters for OpenTelemetry integration.
  - `postgres/`: Async Postgres client, configuration, and typed row/state helpers built on top of `asyncpg` patterns.
  - `types/`: Fundamental typed primitives (e.g., `Missing`, immutable containers) shared across modules.
  - `utils/`: Generic async utilities (queues, streams, env helpers, logging bootstrap, metadata helpers).
- `tests/`: Pytest suite mirroring package structure; keep new tests alongside the code they cover.
- `docs/`: MkDocs content, author guides, and API references; update navigation in `mkdocs.yml` when adding pages.
- `config/pre-push`: Git hook script that mirrors CI checks; run manually if the hook is not installed.
- `Makefile`: Entry point for common dev tasks (`format`, `lint`, `test`, `docs`).
- `pyproject.toml` & `uv.lock`: Project metadata, dependencies, and pinned lockfile for `uv` environments.

Public exports are centralized in `src/haiway/__init__.py`.

## Core Framework Concepts

- `State` objects are immutable data carriers that surface through contexts; use keyword-only constructors and NumPy-style docstrings when they are public.
- `Immutable` (see `src/haiway/utils/queue.py`) disallows attribute mutation—set internal state with `object.__setattr__` and avoid mutating collections in place.
- `ctx` provides structured dependency injection; use `ctx.scope("name", state_instance)` to bind dependencies and `ctx.spawn(coro)` for detached async work.
- Observability flows through `Observability*` types; emit attributes and metrics inside scopes instead of calling logging APIs directly.
- Configuration/state lifecycles rely on `Disposable`/`Disposables`; register clean-up via context managers rather than relying on `__del__`.
- Async adapters (HTTP, Postgres, streaming) wrap external SDKs—prefer extending the existing helper modules instead of introducing new third-party usage sites.

## Style & Patterns

### Typing & Immutability

- Ensure latest, most strict typing syntax available from python 3.12+
- Strict typing only: no untyped public APIs, no loose `Any` unless required by third-party boundaries
- Prefer explicit attribute access with static types. Avoid dynamic `getattr` except at narrow boundaries.
- Prefer abstract immutable protocols: `Mapping`, `Sequence`, `Iterable` over `dict`/`list`/`set` in public types
- Use `final` where applicable; avoid inheritance, prefer type composition
- Use precise unions (`|`) and narrow with `match`/`isinstance`, avoid `cast` unless provably safe and localized
- Favor structural typing (Protocols) for async clients and adapters; runtime-checkable protocols like `HTTPRequesting` keep boundaries explicit.
- Guard immutability with assertions when crossing context boundaries; failure messages should aid debugging but never leak secrets.

### Concurrency & Async

- All I/O is async, keep boundaries async and use `ctx.spawn` for detached tasks
- Ensure structured concurrency concepts and valid coroutine usage
- Rely on haiway and asyncio packages with coroutines, avoid custom threading
- Await long-running operations directly; never block the event loop with sync calls.
- Prefer async iterators/streams (`AsyncStream`, `AsyncQueue`) and context managers to coordinate backpressure.

### Exceptions & Error Translation

- Translate provider/SDK errors into appropriate typed exceptions
- Don’t raise bare `Exception`, preserve contextual information in exception construction
- Wrap third-party exceptions at the boundary and include actionable context (`provider`, `operation`, identifiers) while redacting sensitive payloads.

### Logging & Observability

- Use observability hooks (`ObservabilityLogRecording`, metrics, traces) instead of `print`/`logging`—tests assert on emitted events.
- Always include scope identifiers when emitting events so records thread through async boundaries.
- Surface user-facing errors via structured events before raising typed exceptions.

## Testing & CI

- No network in unit tests, mock providers/HTTP
- Keep tests fast and specific to the code you change, start with unit tests around new types/functions and adapters
- Use fixtures from `tests/` or add focused ones; avoid heavy integration scaffolding
- Linting/type gates must be clean: `make format` then `make lint`
- Mirror package layout in `tests/`; colocate new tests alongside features and prefer `pytest` parametrization over loops.
- Test async flows with `pytest.mark.asyncio`; use `ctx.scope` in tests to isolate state and avoid leaking globals.
- Use `mypy`/`pyright`-style type assertions (e.g., `reveal_type`) only locally and delete them before committing.
- Run `uv sync` if dependencies change; never edit `.venv` manually.

### Async tests

- Use `pytest-asyncio` for coroutine tests (`@pytest.mark.asyncio`).
- Avoid real I/O and network; stub provider calls and HTTP.
- Reuse existing pytest fixtures when available or add focused ones next to the tests that use them.

### Self verification

- Ensure type checking soundness as a part of the workflow
- Do not mute or ignore errors, double check correctness and seek for solutions
- Verify code correctness with unit tests or by running ad-hoc scripts
- Ask for additional guidance and confirmation when uncertain or about to modify additional elements
- Capture tricky edge cases in regression tests before fixing them to prevent silent behaviour changes.

## Documentation

- Public symbols: add NumPy-style docstrings. Include Parameters/Returns/Raises sections and rationale when not obvious
- Internal helpers: avoid docstrings, keep names self-explanatory
- If behavior/API changes, update relevant docs under `docs/` and examples if applicable
- Skip module docstrings
- Add usage snippets that exercise async scopes; readers should see how to wire states through `ctx`.

### Docs (MkDocs)

- Site is built with MkDocs + Material and `mkdocstrings` for API docs.
- Author pages under `docs/` and register navigation in `mkdocs.yml` (`nav:` section).
- Preview locally: `make docs-server` (serves at http://127.0.0.1:8000).
- Lint `make docs-lint` and format `make docs-format` after editing.
- Build static site: `make docs` (outputs to `site/`).
- Keep docstrings high‑quality; `mkdocstrings` pulls them into reference pages.
- When adding public APIs, update examples/guides as needed and ensure cross-links render.

## Preferred Workflow

1. Sync dependencies with `uv sync` before running tools; never modify `.venv` directly.
2. Implement changes in small commits; use feature branches when possible.
3. Format with `make format` and run `make lint`; fix lint/type issues before running tests.
4. Execute targeted `pytest` first, then finish with `make test` before requesting code review.
5. Update `src/haiway/__init__.py` exports and docs when altering public APIs so mkdocstrings stays accurate.
6. Capture behavioural changes in tests and observability expectations so regressions are obvious.

## Security & Secrets

- Never log secrets or full request bodies containing keys/tokens
- Use environment variables for credentials, resolve via helpers like `getenv_str`

## Contribution Checklist

- Build: `make format` succeeds
- Quality: `make lint` is clean (Ruff, Bandit, Pyright strict)
- Tests: `make test` passes, add/update tests if behavior changes
- Types: strict, no ignores, no loosening of typing
- API surface: update `__init__.py` exports and docs if needed
