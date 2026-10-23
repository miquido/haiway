# Product Requirements: Haiway

## Product

Haiway is a Python framework for building high-quality codebases. It focuses on strict typing and
functional programming principles extended with structured concurrency. It is opinionated: strict
rules and patterns result in modular, safe and maintainable applications. It is published on PyPI as
`haiway` under the MIT license.

## Users

- Python developers building async services and tools who want immutable state, context-based
  dependency injection and automatic resource cleanup.
- Teams building on FastAPI, Starlette, httpx, Postgres, RabbitMQ or OpenTelemetry who want typed
  integrations aligned with Haiway's abstractions.
- Coding agents working on Haiway or on Haiway-based codebases (see `AGENTS.md`).

## Goals

- Provide immutable, validated state objects and a scoped context for dependencies and state.
- Provide structured concurrency with guaranteed cleanup.
- Keep strict typing throughout (Pyright strict, no untyped public APIs).
- Offer optional integrations as extras without burdening the core package.
- Version v0.59.2: maintenance and cleanup, including support for Python 3.15 (Python 3.14 stays
  supported).

## Requirements

- The core package depends only on `typing-extensions`; integrations are optional extras.
- Supported Python versions: 3.14 and 3.15.
- A clean clone supports `make setup`, `make format`, `make build`, `make lint` and `make test`.
- Public APIs are exported from `src/haiway/__init__.py` or the integration subpackage.
- Documentation is kept current with the code (`make docs`, `make docs-lint`).

## Success measures

- `make lint`, `make test`, `make docs` and `make docs-lint` pass.
- Open: numeric targets such as coverage, performance or adoption were not stated in the briefing.

## Open

- Support policy for future Python versions beyond 3.15.
- Quantitative quality targets.
