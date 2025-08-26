# Repository Guidelines

## Project Structure & Modules
- `src/haiway/`: Library code (e.g., `context/`, `state/`, `types/`, `utils/`, `helpers/`, `httpx/`, `opentelemetry/`).
- `tests/`: Pytest suite (`test_*.py`), includes async tests.
- `docs/` + `mkdocs.yml`: User/developer docs; built to `site/`.
- `config/pre-push`: Git hook run by `make venv` to block WIP commits and enforce linting.
- `.github/workflows/`: CI for lint, tests, build, docs.

## Build, Test, and Development Commands
- `make venv`: Install uv, create `.venv`, install all extras, prepare git hooks.
- `make sync` / `make update`: Sync or upgrade dependencies via uv.
- `make format`: Auto-fix and format with Ruff.
- `make lint`: Security (bandit), lint (ruff), strict typing (pyright).
- `make test`: Run pytest with coverage for `src/`.
- `make docs-server` / `make docs`: Serve or build MkDocs site.
- `make release`: Lint + test, then `uv build` and publish.
  Example: `uv run pytest --rootdir= ./tests --doctest-modules` (matches CI).

## Coding Style & Naming
- Python 3.12+, line length 100, Ruff for lint/format (includes import sorting).
- Strict typing enforced by Pyright; prefer explicit types and TypedDict/Protocol where useful.
- Names: modules `snake_case`, classes `PascalCase`, functions/vars `snake_case`, constants `UPPER_CASE`.

## Testing Guidelines
- Framework: `pytest` (+ `pytest-asyncio`, `pytest-cov`).
- Location/pattern: place tests under `tests/` and name `test_*.py`.
- Async: mark with `@pytest.mark.asyncio`.
- CI also runs doctests; keep examples executable.
  Run locally: `make test` or `uv run pytest -v`.

## Commit & Pull Request Guidelines
- Commits: imperative, concise subject (≤72 chars), meaningful body; avoid "WIP" (pre-push hook blocks it).
- PRs: clear description, linked issues (`#123`), include tests/docs for behavior changes; add screenshots for docs when useful.
- CI must pass across OS matrix and Python 3.12/3.13; document any breaking changes in PR.

## Security & Configuration Tips
- Do not commit secrets; `.env` is optional for local use.
- Use `make venv` to install the pre-push hook and ensure consistent tooling.
