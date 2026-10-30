# CR-001 — Development environment, agent guidance and documentation structure (v0.59.2)

## Problem

Haiway is a mature, released library (0.59.x). Version v0.59.2 is a maintenance and cleanup version,
including support for Python 3.15 (briefing goals). It is also the project's setup version: a clean
clone must offer the standard Makefile interface (`setup`, `format`, `build`, `lint`, `test`),
agents need one authoritative guidance file, and the default documentation set (`docs/PRD.md`,
`docs/ARCHITECTURE.md`, `docs/changes/`) is missing.

Baseline, measured on `5d9fb3c` before planning (all green, no bugs to report):

- `make lint` — bandit, ruff and pyright (0 errors) pass.
- `make test` — 1205 passed.
- `make docs-lint` and `make docs` (mkdocs `--strict`) pass.
- `uv build` could not be checked in the planning sandbox (it cannot write outside the worktree);
  the build task verifies it.

Gaps and contradictions found in the code:

- The Makefile has `venv` and `sync` but no `setup` and no `build` target.
- `config/.env.example` is referenced by the Makefile and the briefing but does not exist (`config/`
  holds only `pre-push`).
- `.gitignore` lacks `/.hq/`, `site/` and `.shared`-style local artifact directories.
- `.claude/settings.json` runs `source .venv/bin/activate && make format`, which contradicts the
  stack update (never activate the venv; targets use `uv run --frozen`).
- CI uses `uv run` without `--frozen` and a `pytest` command that differs from `make test`. Out of
  scope here (recorded for a later version).
- `CLAUDE.md`, `docs/PRD.md`, `docs/ARCHITECTURE.md`, `docs/changes/README.md` do not exist.
- The stack briefing (N-2: uv 0.12.x, `make sync`) is superseded by the stack update (N-6): uv
  0.13.5, Python 3.15 default with 3.14 supported, `UV_PYTHON=3.14` fallback, dependency groups
  synced by default, all tools via `uv run --frozen`. This document follows N-6.

## Decisions

- D1 — Existing `venv` and `sync` targets stay (`sync` documented, `venv` kept); `setup` is the new
  entry point and is idempotent. No question was needed: the briefing and Makefile settle it.
- D2 — Nothing is vendored and there are no native libraries or model weights, so no artifact goes
  into `$HQ_SHARED`. The Makefile reads `HQ_SHARED` only for the optional uv cache location
  (`UV_CACHE_DIR` default `$HQ_SHARED/uv-cache` when set, else uv's default). AGENTS.md says so.
- D3 — "Minimal program exercising every core dependency": the core runtime dependency is
  `typing-extensions`; each optional extra is an integration dependency. `make build` builds the
  wheel with `uv build` and runs a smoke program from the built wheel (see R2).
- D4 — No product features, no version bump, no `uv.lock` change (briefing rules).

### correct-the-env-fallback

`.env` is read by make only; docs state that direct `uv sync`/`uv run` needs `UV_PYTHON` exported.

### fix-three-review-findings-on

`venv` is an alias of `setup` again. The Makefile puts `$UV_INSTALL_DIR` (default `~/.local/bin`)
first on `PATH`, so a uv installed during `uv_check` serves `setup` and every later recipe line.

## Requirements

### R1 — Makefile `setup` and environment files

Goal: on a clean clone, `make setup` yields a working environment.

In scope: `setup` target (uv check/pin to 0.13.5, `uv sync --frozen` with all groups and extras,
copies `config/.env.example` to `.env` if absent, installs the git pre-push hook if absent); new
`config/.env.example` (commented `UV_PYTHON=3.14` fallback, no secrets); `.gitignore` additions
`/.hq/`, `site/`, `.env`, local artifact directories; `.PHONY` and help comment updated; every
target keeps using `uv run --frozen`. Out of scope: changing dependencies or pins; CI changes.

Acceptance criteria:

- `make setup` exits 0 twice in a row and `uv run --frozen python --version` prints 3.15.x (or
  3.14.x with `UV_PYTHON=3.14 make setup`).
- `uv --version` prints `uv 0.13.5` after `make setup`.
- `test -f config/.env.example && ! grep -Ei 'key|token|password' config/.env.example` succeeds.
- `git check-ignore .hq site .env .venv .coverage` lists all five.
- `git status --short` is empty after `make setup`.

### R2 — Makefile `build` with a dependency smoke program

Goal: `make build` proves the package builds and every dependency links.

In scope: `build` target running `uv build --out-dir dist` (dist is ignored), then installing the
built wheel with all extras into a throwaway venv in a temp directory and running
`scripts/smoke.py`: it imports `haiway` and each integration (`haiway.httpx`, `haiway.postgres`,
`haiway.rabbitmq`, `haiway.starlette`, `haiway.fastapi`, `haiway.opentelemetry`), creates a `State`
and runs a `ctx.scope`, and prints `haiway smoke ok: <version>` as its last line. No network. Out of
scope: publishing (`make release` is never run by agents).

Acceptance criteria:

- `make build` exits 0, creates `dist/haiway-*.whl` and `dist/haiway-*.tar.gz`, and its last output
  line matches `haiway smoke ok: 0\.59\.\d+`.
- Running it with `UV_PYTHON=3.14` also exits 0.
- `git status --short` is empty afterwards (dist/ ignored).
- `make lint` stays clean with the new script (no ignores).

### R3 — Dependency smoke tests

Goal: `make test` contains a real smoke test of each core dependency.

In scope: `tests/test_dependencies.py`, parametrized over `typing_extensions` and the integration
modules (`httpx2`, `asyncpg`, `pika`, `starlette`, `fastapi`, `opentelemetry.sdk`,
`opentelemetry.exporter.otlp.proto.grpc`): each imports and performs one minimal offline call (for
example creating a Starlette `Request` scope, an OpenTelemetry `TracerProvider` span, a
`pika.URLParameters`), plus the matching haiway wrapper module import. No network.

Acceptance criteria:

- `uv run --frozen pytest tests/test_dependencies.py -v` shows one passing case per dependency
  listed above and no skips.
- `make test` is green and the total passed count is 1205 plus the new cases.
- `make lint` is clean (no `# type: ignore`, `# noqa`).

### R4 — Agent guidance

Goal: agents find the stack, layout, commands, conventions and rules in one place.

In scope: update `AGENTS.md` (commands `make setup`, `format`, `build`, `lint`, `test`, `docs`,
`docs-lint`; the "never activate the venv, use `uv run --frozen`" rule; the briefing's rules: no
secrets, no `make release`/`uv publish`/push, no version or `uv.lock` edits outside dependency
tasks, no new core runtime dependencies, no muted errors, short imperative commit messages,
`git diff --check` clean; where `$HQ_SHARED` is used (D2); the documentation structure); create
`CLAUDE.md` containing `@AGENTS.md`; fix `.claude/settings.json` hook so it runs `make format`
without activating the venv. Project skills under `.agents/skills/` only where they help; none are
required.

Acceptance criteria:

- `grep -c '@AGENTS.md' CLAUDE.md` prints 1.
- `grep -E 'make (setup|format|build|lint|test)' AGENTS.md` matches all five targets.
- `grep -n 'venv/bin/activate' .claude/settings.json AGENTS.md` prints nothing.
- `make docs-lint` passes; `git diff --check` is clean.

### R5 — Documentation structure

Goal: the default documents exist and are linked.

In scope: `docs/PRD.md` (what Haiway is, users, goals, requirements, from the briefing; unknowns
marked "Open"), `docs/ARCHITECTURE.md` (package layout, context/state/concurrency model,
integrations, tooling and CI), `docs/changes/README.md` (how change requests are named
`CR-NNN-<short-name>.md`, their sections, anchors `R1 — …`, decisions, open questions; lists
CR-001), README.md link section. New pages are not added to the MkDocs `nav` unless
`mkdocs build --strict` requires it; existing docs are kept.

Acceptance criteria:

- `test -f docs/PRD.md -a -f docs/ARCHITECTURE.md -a -f docs/changes/README.md` succeeds.
- `grep -E 'docs/(PRD|ARCHITECTURE)\.md|docs/changes' README.md` matches all three links.
- `make docs-lint` and `make docs` pass.

## Open questions

- Should CI move to the Makefile targets (`--frozen`, same pytest command)? Deferred; not part of
  this version.
