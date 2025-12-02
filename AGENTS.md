Haiway is a python framework helping to build high-quality codebases. Focuses on strict typing and functional programming principles extended with structured concurrency concepts. Delivers opinionated, strict rules and patterns resulting in modular, safe and highly maintainable applications.

## Development Toolchain

- Python: 3.13+
- Virtualenv: managed by uv, available at `./.venv`, assume already set up and working within venv
- Formatting: Ruff formatter (`make format`), no other formatter
- Linters/Type-checkers: Ruff, Bandit, Pyright (strict). Run via `make lint`
- Tests: using pytest, run using `make test` or targeted `pytest tests/test_...::test_...`

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
- `Makefile`: Entry point for common dev tasks (`format`, `lint`, `test`, `docs`).
- `pyproject.toml`: Project metadata, dependencies, and configuration.

Public exports are centralized in `src/haiway/__init__.py`.

## Style & Patterns

### Typing & Immutability

- Ensure latest, most strict typing syntax available from python 3.13+
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

### Exceptions & Error Translation

- Translate provider/SDK errors into appropriate typed exceptions
- Don’t raise bare `Exception`, preserve contextual information in exception construction
- Wrap third-party exceptions at the boundary and include actionable context (`provider`, `operation`, identifiers) while redacting sensitive payloads.

### Logging & Observability

- Use observability hooks (logs, metrics, traces) from `ctx` helper (`ctx.log_*`, `ctx.record`) instead of `print`/`logging`—tests assert on emitted events.
- Surface user-facing errors via structured events before raising typed exceptions.

## Testing & CI

- No network in unit tests, mock providers/HTTP
- Keep tests fast and specific to the code you change, start with unit tests around new types/functions and adapters
- Use fixtures from `tests/` or add focused ones; avoid heavy integration scaffolding
- Linting/type gates must be clean: `make format` then `make lint`
- Mirror package layout in `tests/`; colocate new tests alongside features and prefer `pytest` parametrization over loops.
- Test async flows with `pytest.mark.asyncio`; use `ctx.scope` in tests to isolate state and avoid leaking globals.
- Use `pyright`-style type assertions (e.g., `reveal_type`) only locally and delete them before committing.

### Self-verification

- Ensure type checking soundness as a part of the workflow
- Do not mute or ignore errors, double-check correctness and seek for solutions
- Verify code correctness with unit tests or by running ad-hoc scripts
- Capture tricky edge cases in regression tests before fixing them to prevent silent behaviour changes.

## Documentation

- Public symbols: add NumPy-style docstrings. Include Parameters/Returns/Raises sections and rationale
- Internal and private helpers: avoid docstrings, keep names self-explanatory
- If behavior/API changes, update relevant docs under `docs/` and examples if applicable
- Skip module docstrings
- Add usage snippets that exercise async scopes; readers should see how to wire states through `ctx`.

### Docs (MkDocs)

- Site is built with MkDocs + Material and `mkdocstrings` for API docs.
- Author pages under `docs/` and register navigation in `mkdocs.yml` (`nav:` section).
- Lint `make docs-lint` and format `make docs-format` after editing.
- Keep docstrings high‑quality; `mkdocstrings` pulls them into reference pages.
- When adding public APIs, update examples/guides as needed and ensure cross-links render.

## Security & Secrets

- Never log secrets or full request bodies containing keys/tokens
- Use environment variables for credentials, resolve via helpers like `getenv_str`

## Contribution Checklist

- Build: `make format` succeeds
- Quality: `make lint` is clean (Ruff, Bandit, Pyright strict)
- Tests: `make test` passes, add/update tests if behavior changes
- Types: strict, no ignores, no loosening of typing
- API surface: update `__init__.py` exports and docs if needed
