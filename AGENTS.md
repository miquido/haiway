# Repository Guidelines

## Project Structure & Module Organization
- Source: `src/haiway/` (modules: `context/`, `state/`, `types/`, `utils/`, `helpers/`, `httpx/`, `opentelemetry/`).
- Tests: `tests/` with `test_*.py` (async supported); doctests run in CI.
- Docs: `docs/` with `mkdocs.yml` → built to `site/`.
- Tooling: `config/pre-push` Git hook; CI in `.github/workflows/`.

## Architecture Overview
- Pillars: Context (scoped execution, events, variables), State (immutable, validated), Helpers (async utilities, HTTPX, observability).
- Key APIs: `src/haiway/context/access.py` exposes `ctx`; state core in `src/haiway/state/{structure,validation,path}.py`.
- State priority: explicit > disposables > presets > parent context.
 - State access: `ctx.state(T)` resolves by type only; for hybrid class/instance helpers use `@statemethod` (from `haiway.helpers`), which always passes an instance (class calls resolve from context, instance calls use the instance).

## Build, Test, and Development Commands
- `make venv`: Install `uv`, create `.venv`, install extras, set hooks.
- `make sync` / `make update`: Sync/upgrade deps via `uv`.
- `make format`: Format + autofix with Ruff; import sort included.
- `make lint`: Bandit (security), Ruff (lint), Pyright (strict types).
- `make test`: Pytest with coverage for `src/`.
- `make docs-server` / `make docs`: Serve or build MkDocs.
- Example: `uv run pytest --rootdir= ./tests --doctest-modules`.

## Coding Style & Naming Conventions
- Python 3.12+, line length 100. Strict typing is non-negotiable.
- Types: annotate every function param/return and all `State` attributes; prefer `Sequence`/`Mapping`/`Set` over `list`/`dict`/`set`.
- Unions: use `T | None` style; avoid `Optional[T]`. Keep generics explicit; avoid `Any`.
- Imports: prefer absolute `haiway...`; export via package `__init__.py` where appropriate.
- Naming: modules `snake_case`; classes `PascalCase`; funcs/vars `snake_case`; constants `UPPER_CASE`.
 - State helpers: avoid `@classmethod` for accessing contextual state; prefer `@statemethod` so methods work from class or instance while always operating on an instance.

## Testing Guidelines
- Frameworks: `pytest`, `pytest-asyncio`, `pytest-cov`.
- Async: mark with `@pytest.mark.asyncio`; use `ctx.scope(...)` with protocol impls to mock dependencies.
- Pattern: place tests in `tests/` as `test_*.py`; keep examples executable for doctests.
- Run: `make test` or `uv run pytest -v`.

## Commit & Pull Request Guidelines
- Commits: imperative, concise subject (≤72 chars) + meaningful body; avoid "WIP" (hook blocks).
- PRs: clear description, link issues (e.g., `#123`), include tests/docs for behavior changes; screenshots for docs when useful.
- CI: must pass on OS matrix, Python 3.12/3.13; call out breaking changes.

## Security & Configuration Tips
- Never commit secrets; `.env` optional for local use.
- Use `make venv` to install hooks and ensure consistent tooling.
