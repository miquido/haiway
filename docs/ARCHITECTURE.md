# Architecture

## Package layout

The package lives in `src/haiway/`; tests in `tests/` mirror it.

- `attributes/`: attribute annotations, state objects and validation helpers.
- `context/`: structured-concurrency context management, scopes and lifecycle.
- `helpers/`: configuration, async orchestration, retries, throttling, HTTP adapters, files and
  message queues.
- `types/`: fundamental typed primitives such as `Missing` and immutable containers.
- `utils/`: async utilities, environment helpers, logging bootstrap, metadata helpers.
- Integrations (optional extras): `fastapi/`, `starlette/`, `httpx/`, `postgres/`, `rabbitmq/` and
  `opentelemetry/`.

Core public exports are centralized in `src/haiway/__init__.py`; integrations export their own
public APIs from their subpackages.

## Context, state and concurrency

- State is modeled as immutable, validated state objects (`attributes/`).
- The context (`context/`) is a scope that carries state and dependencies. Scopes nest; entering one
  makes its state available to the code running inside it without explicit passing.
- Concurrency is structured: tasks are spawned within a scope (`ctx.spawn`) and are cleaned up when
  the scope exits. Work that must outlive the scope uses `ctx.spawn_background`.
- All I/O is async; unavoidable sync calls are bridged with the `@asynchronous` helper.

## Integrations

Each integration plugs the context into a third-party library (ASGI request handling, HTTP client,
database, message broker, observability) and translates provider errors into typed exceptions.
FastAPI reuses the Starlette integration pieces.

## Tooling and CI

- Python 3.15 by default, 3.14 supported; uv (pinned in `pyproject.toml` and the `Makefile`) manages
  the environment in `./.venv`.
- `make` targets wrap `uv run --frozen`: formatting with Ruff, linting with Ruff, Bandit and Pyright
  (strict), tests with pytest, documentation with MkDocs and mdformat/pymarkdown.
- GitHub Actions (`.github/workflows/`) run CI, build the documentation and publish releases.

## Open

- CI does not yet use `--frozen` or the same pytest command as `make test` (see
  `docs/changes/CR-001-dev-environment.md`).
