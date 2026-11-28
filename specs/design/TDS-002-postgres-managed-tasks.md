# TDS-002 — PostgreSQL task store

Status: accepted

- Kind: technical design specification
- Version: 1.1, revision of the accepted v1.0 (TDS-001 D-108 to D-110 and D-112 to D-116 applied,
  status back to `proposed` by D-28, accepted by D-31; D-32 an implementation-review clarification
  and D-33 to D-38 corrections with the run of D-39, D-40, status kept; D-29 and D-30 from a run on
  PostgreSQL 16 and 18: `fail_unsupported`, the `unsupported` failure kind, listing indexes and
  query; D-25 to D-27). No question is open. v1.0 (review 2: claim statement form and indexes,
  migrations on the store's pool, store protocol changes of TDS-001 D-80 to D-84; D-11 to D-14;
  answers to OQ-7 to OQ-9 and TDS-001's questions: D-15 to D-18; v0.9: third review fixes, D-19;
  v1.0: statements verified on PostgreSQL 18.7 and 16.16, D-20 to D-24).
- Date: 2026-11-28
- Author: not stated in the brief
- Grounded at: ecb86c8 (clean working tree; `specs/` untracked, added by this change)
- Implements: the task store protocol of [TDS-001](TDS-001-managed-tasks.md) (TDS-001 FR-78).

## Purpose

This document specifies `PostgresTaskStore`, the PostgreSQL implementation of haiway's task store
protocol (TDS-001 FR-78). It stores registered tasks, their statuses and attempts, their origin
links, and the leases and due moments the protocol needs, so that every runtime on one database
forms one shared work queue (D-1) and stored tasks survive a process restart. It also fails the
unsupported tasks of a runtime that asks for it (FR-23) and lists stored tasks (FR-24). Everything
else (claim rounds, renewal, retries, time limits, recovery, close) is the one runtime's (TDS-001
D-66, FR-80); where this store differs from the in-memory store is listed once, in TDS-001 FR-80.

Its readers are the haiway maintainers, the implementers, and operators running it.

## Normative language

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, NOT
RECOMMENDED, MAY and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC
2119, RFC 8174) when, and only when, they appear in all capitals.

## Glossary

Terms defined in [TDS-001](TDS-001-managed-tasks.md#glossary) (runtime, task store, store session,
runner id, lease, claim, registered task, registered name, stored task, attempt, due moment, retry
policy, outcome, failure, time limit, origin) keep their meaning here.

| Term           | Definition                                                                                                                                 |
| -------------- | ------------------------------------------------------------------------------------------------------------------------------------------ |
| Task row       | The row of `haiway_managed_tasks` holding one stored task ([Data model](#data-model)).                                                     |
| Attempt row    | The row of `haiway_managed_task_attempts` holding one counted, ended attempt.                                                              |
| Attempt number | The task row's `attempts` plus 1, which equals the number of its attempt rows plus 1 (TDS-001 FR-78, D-35).                                |
| Due row        | A `queued` or `retry_pending` task row whose `due_at <= statement_timestamp()`.                                                            |
| Expired row    | A `running` task row whose `lease_expires_at < statement_timestamp()`; the lease stays its owner's until a claim takes it (TDS-001 FR-78). |
| Server clock   | `statement_timestamp()` of the PostgreSQL server; every stored time and every comparison uses it (FR-20).                                  |

## Decisions

| ID   | Question                   | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | Source                                                                             | Date       |
| ---- | -------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------------------------------------------- | ---------- |
| D-1  | OQ-1                       | Shared work queue: any open runner claims any due task whose registered name it knows, with `FOR UPDATE SKIP LOCKED`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                               | 2026-11-26 |
| D-2  | OQ-5                       | Shared: no namespaces; registered names must be unique per database. The consequence is documented: a task may run under another block's snapshot.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                               | 2026-11-26 |
| D-3  | OQ-2                       | Restored on this store by D-18. Superseded by TDS-001 D-71: the three timings are required settings of the runtime for every store. Lease, renewal and polling intervals are required settings, with no defaults.                                                                                                                                                                                                                                                                                                                                                                                                                          | user                                                                               | 2026-11-26 |
| D-4  | OQ-3                       | Its own `migrate()`, with its own version table and lock.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                               | 2026-11-26 |
| D-5  | OQ-4                       | Rows are kept; the application deletes them.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | user                                                                               | 2026-11-26 |
| D-6  | status                     | The status is `draft` while the body is unfinished, and `proposed` once complete; the catalog lifecycle rules over the earlier brief.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | caller (coordinator, relaying the user)                                            | 2026-11-26 |
| D-7  | OQ-6                       | Counted, as a new failure: an attempt row of kind `interrupted`, plus a new `TaskInterrupted` failure in TDS-001 that carries no exception; the retry policy decides, and with no retries the task fails.                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                               | 2026-11-26 |
| D-8  | round 4                    | This document becomes the PostgreSQL task store: the only part that differs between stores is the store; scheduling, claims, leases, retries, time limits, recovery and close belong to the one runtime (TDS-001 D-66).                                                                                                                                                                                                                                                                                                                                                                                                                    | user                                                                               | 2026-11-26 |
| D-9  | round 4                    | A stored task holds only the registered function name, the argument State (JSON), the status and attempts, the retry policy, the time limit, and the result or failure record (TDS-001 D-67).                                                                                                                                                                                                                                                                                                                                                                                                                                              | user                                                                               | 2026-11-26 |
| D-10 | round 5                    | The stored task keeps the origin link (trace id, scope id, trace context), so every attempt links back to the spawning span whichever runtime runs it; it is telemetry metadata, not context state (TDS-001 D-72).                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                               | 2026-11-26 |
| D-11 | review 2 S4                | `claim` locks its rows in a `WITH` query and updates them from it; every statement reads the server clock once, so `due_at` and `lease_expires_at` comparisons can be index conditions; a partial index on `lease_expires_at` of `running` rows serves expired-row claims.                                                                                                                                                                                                                                                                                                                                                                 | caller (review 2), derived: the brief names the fix                                | 2026-11-27 |
| D-12 | review 2 S12               | `migrate` is an instance method running on the store's own pool, and `open` checks the recorded schema version before yielding a session.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | caller (review 2), derived: the brief names the fix                                | 2026-11-27 |
| D-13 | review 2 B4, S6, S9        | The store follows TDS-001 D-81 to D-84: a lease is held by its owner until another claim takes it, expired or not; claims return the caller's own expired rows and do not count expired rows against `limit`; `renew` returns one mapping; `record` takes one `AttemptEnd`; `read` takes a set of identifiers.                                                                                                                                                                                                                                                                                                                             | caller (review 2), derived from TDS-001 D-81 to D-84                               | 2026-11-27 |
| D-14 | brief, round 6             | Ad-hoc tasks are removed from TDS-001 (TDS-001 D-77); this store never held them, and its non-goal on them is dropped.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | user                                                                               | 2026-11-27 |
| D-15 | OQ-7                       | Option 1, caller pool, documented: the constructor keeps taking the caller's pool; documentation requires at least 2 connections and a pool used by nothing else. With TDS-001 D-88, the store constructor also takes the three timings.                                                                                                                                                                                                                                                                                                                                                                                                   | user                                                                               | 2026-11-27 |
| D-16 | OQ-8                       | Option 1, JSONB: unchanged; the U+0000 rule stays.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                               | 2026-11-27 |
| D-17 | OQ-9                       | Option 1, open: later migrations are additive and compatible by rule, stated in the documentation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                               | 2026-11-27 |
| D-18 | TDS-001 OQ-41              | TDS-001 D-88 applied here: `PostgresTaskStore` takes `lease`, `lease_renewal` and `polling`, required, with no defaults, and yields `LeasedCoordination`; `claim` and `renew` use its own lease.                                                                                                                                                                                                                                                                                                                                                                                                                                           | user, through TDS-001 D-88                                                         | 2026-11-27 |
| D-19 | review 3, 9                | `record` is one statement: a `WITH` query locks the row and reads its old `started_at` and `cancel_requested_at`, and two data-modifying `WITH` queries update the task row and insert the attempt row from it; `RETURNING OLD` is not used, so no minimum server version is set; every operation is one statement (NFR-1).                                                                                                                                                                                                                                                                                                                | caller (review 3), derived from NFR-1 and FR-12                                    | 2026-11-27 |
| D-20 | experiment                 | `claim` tags each locked `id` in a `WITH` query `SELECT id, 'attempt' AS claim FROM due UNION SELECT id, 'interrupted' FROM expired`, and that tag drives the `SET` `CASE` expressions and `RETURNING`, since the two cases write different columns and `RETURNING OLD` is ruled out (D-19).                                                                                                                                                                                                                                                                                                                                               | caller, derived from an Experimenter run on PostgreSQL 18.7 and 16.16 (2026-11-27) | 2026-11-27 |
| D-21 | experiment                 | `cancel` locks the row in a `WITH` query used only for locking and the return value; its update reads only the task table's own columns. A statement must not mix values from a locked `WITH` row with kept task-table columns where the provisional row could violate a `CHECK`: the mixed form failed with a check violation when it waited on a concurrent claim.                                                                                                                                                                                                                                                                       | caller, derived from an Experimenter run on PostgreSQL 18.7 and 16.16 (2026-11-27) | 2026-11-27 |
| D-22 | experiment                 | Amended by D-35: the attempt number is `attempts + 1` of the task row. The readings the experiment verified become normative: `UNION`; due is `due_at <= now`, expired is `lease_expires_at < now`; the clock is inline `statement_timestamp()` everywhere; durations are `make_interval(secs => $n)`; `limit` is `LIMIT $n` with `NULL` meaning all; the attempt number is the count of attempt rows plus 1.                                                                                                                                                                                                                              | caller, derived from an Experimenter run on PostgreSQL 18.7 and 16.16 (2026-11-27) | 2026-11-27 |
| D-23 | experiment                 | PostgreSQL 16 is the minimum verified server version; the store uses nothing version-specific. The user's answers fix no version.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | caller, derived from an Experimenter run on PostgreSQL 18.7 and 16.16 (2026-11-27) | 2026-11-27 |
| D-24 | status                     | Amended by D-28 for v1.1. The design is finalized: status `accepted`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user, through the caller                                                           | 2026-11-27 |
| D-25 | TDS-001 D-110              | TDS-001 D-110 applied: a `fail_unsupported` statement fails due rows of unlisted names (FR-23), and `failure_kind` admits `unsupported`, in schema version 1, since no version was released.                                                                                                                                                                                                                                                                                                                                                                                                                                               | user, through TDS-001 D-110                                                        | 2026-11-27 |
| D-26 | TDS-001 D-109              | TDS-001 D-109 applied: a `query` statement lists rows (FR-24), served by indexes on `(created_at, id)` and `(name, created_at, id)` in schema version 1; its form follows D-27.                                                                                                                                                                                                                                                                                                                                                                                                                                                            | user, through TDS-001 D-109                                                        | 2026-11-27 |
| D-27 | TDS-001 OQ-53 to OQ-57     | TDS-001 D-112 to D-116 applied: `query` returns `TaskSummary` rows oldest first, after a `(created_at, id)` keyset, up to a limit of 1 to 1,000 (FR-24); `fail_unsupported` takes due rows only (FR-23).                                                                                                                                                                                                                                                                                                                                                                                                                                   | user, through TDS-001 D-112 to D-116                                               | 2026-11-27 |
| D-28 | status                     | Amended by D-31: accepted. Amends D-24 for this revision: v1.1 returns to `proposed` until the user accepts it (D-6), as TDS-001 v1.6 does (TDS-001 D-119).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | caller                                                                             | 2026-11-27 |
| D-29 | experiment                 | A filter with exactly one name uses `name = $name`, and one with several names `name = ANY($names)`; the documentation states that pages with several names read and sort the matching rows past the keyset; the names index stays.                                                                                                                                                                                                                                                                                                                                                                                                        | caller, derived from an Experimenter run on PostgreSQL 18.7 and 16.16 (2026-11-27) | 2026-11-27 |
| D-30 | experiment                 | Verified on PostgreSQL 16.16 and 18.7 (aarch64 containers, asyncpg 0.32.0, `READ COMMITTED`): FR-23 acceptance 2, 3 and 6 and FR-23 concurrent with `cancel`; FR-24's keyset row comparison as an index condition of `haiway_managed_tasks_created` in custom and generic plans; the `IS NULL OR` and multi-name plan behaviors of FR-24's rationale; plan shapes identical on both versions.                                                                                                                                                                                                                                              | caller, derived from an Experimenter run (2026-11-27)                              | 2026-11-27 |
| D-31 | status                     | The user accepted this revision: status `accepted` (TDS-001 D-122 for TDS-001 v1.6).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | user                                                                               | 2026-11-27 |
| D-32 | implementation review      | TDS-001 D-123 applied: `insert` of an `id` the table holds fails with the primary-key violation, raised as `PostgresException`, and writes nothing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | caller, implementation review                                                      | 2026-11-27 |
| D-33 | implementation review 4a   | Corrected by D-35: the count is the locked row's `attempts`. TDS-001 D-127 applied: `record` and `release` take `$attempt` and match the row only while its attempt number, the count of its attempt rows plus 1, equals it, besides the lease owner.                                                                                                                                                                                                                                                                                                                                                                                      | caller, implementation review 4a                                                   | 2026-11-27 |
| D-34 | implementation review 6    | `record` and `fail_unsupported` write an attempt row's `ended_at` as `greatest(statement_timestamp(), started_at)`, so a server clock stepping back between claim and record (or a failover to a lagging standby) cannot make the `CHECK (ended_at >= started_at)` fail every retry of `record`; the check stays.                                                                                                                                                                                                                                                                                                                          | caller, implementation review 6                                                    | 2026-11-28 |
| D-35 | real-database verification | The task row gains `attempts`, the number of counted attempts, kept in the same statement as every attempt row insert; `record` and `release` fence on `attempts + 1 = $attempt` read from the locked row, `claim` returns `attempts + 1`, and attempt rows are numbered `attempts + 1`. Corrects D-33: under `READ COMMITTED` the locked row is re-checked after a lock wait, but an attempt-count subquery reads the statement's snapshot, so a stale `record` of attempt 1 raised a unique violation (23505) and a stale `release` moved a task held as attempt 2 back to `queued`, each in 20 of 20 runs on PostgreSQL 16.16 and 18.7. | caller, real-database verification                                                 | 2026-11-28 |
| D-36 | real-database verification | `haiway_managed_tasks_open` is partial on `status IN ('queued', 'retry_pending')`: on FR-5 acceptance 4's data, the `<> 'finished'` form let the expired-lease query use it with `status = 'running'` as index condition instead of `haiway_managed_tasks_leases` (PostgreSQL 16 in 12 of 12 plans, 18 in up to 6 of 12); restricted, `_leases` served 24 of 24 and the due query kept `_open` 12 of 12 on both.                                                                                                                                                                                                                           | caller, real-database verification                                                 | 2026-11-28 |
| D-37 | real-database verification | NFR-1 asks one statement per operation, not one round trip: asyncpg's pool runs its reset (`SELECT pg_advisory_unlock_all(); CLOSE ALL; UNLISTEN *; RESET ALL;`) when a connection is released, and a statement-cache miss adds Parse and Describe; the reset stays, since it backs `migrate`'s unlock and timeout reset.                                                                                                                                                                                                                                                                                                                  | caller, real-database verification                                                 | 2026-11-28 |
| D-38 | real-database verification | `record`'s update returns `status`, `outcome` and `result`; `due_at` is not needed and is dropped from FR-12.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | caller, real-database verification                                                 | 2026-11-28 |
| D-39 | real-database verification | Verified on PostgreSQL 16.16 and 18.7 (asyncpg 0.32.0, `READ COMMITTED`), 20 of 20 runs per version: with D-35's forms, a stale `record` or `release` of attempt 1 waiting on the lock of a transaction that records attempt 1 and claims attempt 2 returns `None` and changes nothing; D-36's index plans; the `release` and `fail_unsupported` statements of FR-6 and FR-23.                                                                                                                                                                                                                                                             | caller, derived from a real-database run (2026-11-28)                              | 2026-11-28 |
| D-40 | real-database verification | FR-24's `query` reads the attempt count from the task row's `attempts`, the single source since D-35, instead of counting attempt rows; its plans are checked by the next real-database run.                                                                                                                                                                                                                                                                                                                                                                                                                                               | caller, real-database verification                                                 | 2026-11-28 |

## Context and scope

### Current system (as found)

Read at `ecb86c8`; lines opened by me unless marked "per CodeExplorer".

- **Package surface.** `haiway.postgres` exports `Postgres`, `PostgresConnection`,
  `PostgresConnectionPool`, `PostgresConfigurationRepository`, `PostgresErrorCode`,
  `PostgresException`, `PostgresRow`, `PostgresTransactionIsolation` and `PostgresValue`
  (`src/haiway/postgres/__init__.py:10-31`). Importing it without the `postgres` extra raises
  `ImportError` (`__init__.py:1-8`). The extra pins `asyncpg~=0.32.0` (per CodeExplorer:
  `pyproject.toml:39`, `uv.lock:64-65`).
- **Pool.** `PostgresConnectionPool` is a disposable yielding `Postgres`. Entering it while it is
  entered raises `PostgresException` (`client.py:335-339`); its exit resets it, so it can be entered
  again after a close (`client.py:386-394`). Its settings default from `POSTGRES_*` variables,
  `POSTGRES_CONNECTIONS` defaulting to 1 and the acquire timeout to 30 s (per CodeExplorer:
  `client.py:253-299`).
- **Connections.** `Postgres.acquire_connection()` raises `PostgresException` when a
  `PostgresConnection` is already in the context, and otherwise returns a context manager
  (`src/haiway/postgres/state.py:381-400`) whose `__aenter__` acquires a pooled connection and
  returns a `PostgresConnection` without installing it into the context
  (`src/haiway/postgres/client.py:637-699`); the docstring's "installs" (`state.py:388-389`)
  overstates this. A connection enters the context as a scope's or `ctx.disposables`' disposable
  state (`src/haiway/context/scope.py:152-182`, `src/haiway/context/disposables.py:321-324`), or as
  explicit state of `ctx.scope` or `ctx.updating` (`src/haiway/context/access.py:153-158`,
  `:256-258`). `Postgres.fetch_one`, `fetch` and `execute` reuse a `PostgresConnection` in the
  context, or acquire one per call (`state.py:590-594`; `:627-631`, `:666-670` per CodeExplorer).
  Hence, with `POSTGRES_CONNECTIONS=1`, code holding the pool's one connection outside the context
  that calls `Postgres.execute` waits for the acquire timeout and then raises; and tasks spawned
  with `ctx.spawn` under a scope holding a connection share it through `copy_context()`
  (`tasks.py:295-300`), while an asyncpg connection is not safe for concurrent use.
- **Statements.** `PostgresConnection` holds exactly three functions (`state.py:355-369`) and offers
  `fetch_one`, `fetch`, `execute` and `transaction(isolation=..., readonly=..., deferrable=...)`
  (per CodeExplorer: `state.py:168-353`). Parameters are positional `$n`, forwarded to asyncpg as
  given (`state.py:591`; `client.py:648`, `:670`).
- **Rows and JSON.** `PostgresRow` has typed accessors for UUID, datetime, str, int, float, decimal
  and bool, none for JSON (per CodeExplorer: `types.py:174-660`). No codec is registered; the
  configuration repository writes `to_json()` into a `JSONB` parameter and reads the text back with
  `from_json` (per CodeExplorer: `configuration.py:128-149`, `:212-214`).
- **Errors.** Driver errors are wrapped in `PostgresException` with their `sqlstate` where they
  occur, for example at pool creation and connection acquisition (`client.py:356-359`, `:628-631`);
  the exception type is defined in `types.py:99-153` (per CodeExplorer).
- **Migrations.** `Postgres.execute_migrations` runs one numbered sequence, versioned by the row
  count of a single `migrations` table, under a session advisory lock with key 1264456023 and a
  `lock_timeout` set with `set_config`, each migration in its own transaction; each recorded row
  keeps the migration's identifier and source checksum, and a recorded count above the available
  migrations raises `ValueError` (`state.py:402-557`, `:486-497`, `:681-746`).
- **Connection reset.** Releasing a pooled asyncpg connection runs, as the server supports them,
  `SELECT pg_advisory_unlock_all();`, `CLOSE ALL;`, `UNLISTEN *;` and `RESET ALL;`
  (`.venv/lib/python3.15/site-packages/asyncpg/connection.py:1763-1790`). The package exposes
  neither LISTEN/NOTIFY nor `FOR UPDATE SKIP LOCKED` (per CodeExplorer).
- **State JSON.** `to_json()` raises `ValueError` for a value without a JSON spelling, including
  non-finite floats (`src/haiway/attributes/state.py:1510-1532`); float mapping keys are written as
  `"nan"`, `"inf"` and `"-inf"` (per CodeExplorer: `tests/test_state_json.py:199-231`). PostgreSQL
  states "The jsonb type also rejects \\u0000" (PostgreSQL 18, JSON types, per WebExplorer); TDS-001
  FR-63 rejects the character before any store sees it.
- **Tests.** The postgres tests use fakes, except `tests/test_postgres_connection_pool.py:314-325`,
  which calls the real `create_pool` without network (per CodeExplorer); AGENTS.md forbids network
  in unit tests.

### Consequences for this design

- The store uses nothing version-specific; PostgreSQL 18.7 and 16.16 are verified, so PostgreSQL 16
  is the minimum verified version (D-23).
- The store uses a pool of its own and never a connection found in a context (FR-18): task bodies
  cannot starve it, and it never shares a connection with them.
- Leases are columns of the task row compared with the server clock, not session advisory locks,
  which end with the acquired connection.
- Without notifications, runtimes learn of rows other runtimes wrote by polling at the store's
  `polling` interval (TDS-001 FR-85; this document's FR-4).
- With the shared queue (D-1) and no concurrency limit by default (TDS-001 D-21), the first runtime
  to claim takes every due row it can run; operators who want tasks spread across runtimes set
  `TaskRuntime(concurrency_limit=...)`. TDS-001 D-90 keeps that default and has the documentation
  recommend a limit.
- The store's pool serves claims, renewals, records and polls of one runtime; with the pool default
  of one connection (`client.py:82-84`, `:265-268`) they queue behind each other, so the
  documentation requires at least 2 (FR-18, D-15).

### In scope

- the schema and its installation; the store operations of TDS-001 FR-78 in SQL, with the columns
  each writes; the store's connections and clock; storage errors.

### Non-goals

- Anything TDS-001 FR-80 gives the runtime: claim rounds, renewal timing, retries, time limits,
  recovery decisions, close.
- Exactly-once execution (TDS-001 non-goals); namespaces (D-2); deleting rows (D-5).
- Storing any context of a task beyond TDS-001 D-67 and the origin of D-10.
- LISTEN/NOTIFY; changing `haiway.postgres` beyond adding `PostgresTaskStore`.

## Components and responsibilities

| Component                 | Responsibility                                                                                                                                                    |
| ------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `PostgresTaskStore` (new) | The store object: its own pool (FR-4, FR-18); `open` checks the schema (FR-22) and yields a `TaskStoreSession`; `migrate` installs the schema on its pool (FR-1). |
| Store session (private)   | The operations of TDS-001 FR-78, each one transaction ([Column writes](#column-writes), FR-5 to FR-16, FR-23, FR-24).                                             |
| Schema                    | `haiway_managed_tasks`, `haiway_managed_task_attempts`, `haiway_managed_tasks_migrations` ([Data model](#data-model)).                                            |

## Interfaces

```python
@final
class PostgresTaskStore:
    def __init__(
        self,
        pool: PostgresConnectionPool,
        /,
        *,
        lease: float,  # seconds, required (D-18)
        lease_renewal: float,  # seconds, required, below lease
        polling: float,  # seconds, required
    ) -> None: ...

    def open(self) -> AbstractAsyncContextManager[TaskStoreSession]: ...

    async def migrate(self, *, timeout: int = 300) -> None: ...
```

- `pool` is a `PostgresConnectionPool` owned by the store: `open` enters it and the session's exit
  exits it (FR-18). Opening the store while its pool is entered raises the pool's
  `PostgresException` (`client.py:335-339`); after the session closed, the store can be opened again
  (`client.py:386-394`). Each runtime uses a store object of its own on the same database (TDS-001
  FR-46).
- `migrate` enters the store's own pool, runs on one of its connections and exits the pool; it
  raises the pool's `PostgresException` while the store is open (`client.py:335-339`). Its `timeout`
  has `execute_migrations`' meaning and default (`state.py:402-435`) (D-12, review 2 S12).
- The pool is the caller's; it needs at least 2 connections and is used by nothing else (FR-18,
  D-15).
- `PostgresTaskStore` is exported from `haiway.postgres` (NFR-4).

### Errors

| Operation                | Error               | Condition                                                                                 | State left                              |
| ------------------------ | ------------------- | ----------------------------------------------------------------------------------------- | --------------------------------------- |
| `PostgresTaskStore(...)` | `ValueError`        | A timing is out of the range of `LeaseTimings` (FR-4)                                     | Nothing created                         |
| `open`                   | `PostgresException` | The pool cannot be created, or it is entered already (FR-18)                              | Nothing opened                          |
| `open`                   | `PostgresException` | The schema version is not the store's latest (FR-22)                                      | Nothing opened; the pool exited         |
| Any session operation    | `PostgresException` | A statement fails, or no connection is acquired in time (FR-17)                           | The operation's transaction rolled back |
| `insert`                 | `PostgresException` | The table already holds the `id`: primary-key violation (FR-5, D-32)                      | Nothing written                         |
| `migrate`                | `PostgresException` | A statement fails, the lock is not acquired within `timeout`, or the store is open (FR-1) | The failed migration rolled back        |

## Data model

The schema is normative. Columns that matter only in some statuses are constrained to be present
exactly then, so that no stored row holds an invalid combination (AGENTS.md Golden Rule). Every time
is the server clock's. The task row's `attempts` is the number of counted attempts: the source of
the attempt number (`attempts + 1`, TDS-001 FR-78) and the fencing token of `record` and `release`.
The attempt rows MUST always be numbered 1 to `attempts`, so every statement that inserts an attempt
row updates `attempts` in the same statement. It is stored, not counted, because `READ COMMITTED`
re-checks a locked row after a lock wait while a subquery over the attempt rows keeps the
statement's snapshot, so a counted fence let a stale `record` or `release` act on a newer attempt
(D-35).

```sql
CREATE TABLE haiway_managed_tasks (
    id UUID PRIMARY KEY,
    name TEXT NOT NULL CHECK (name <> ''),
    argument JSONB NOT NULL CHECK (jsonb_typeof(argument) = 'object'),
    time_limit DOUBLE PRECISION CHECK (time_limit > 0 AND time_limit < 'Infinity'),
    retry_delays DOUBLE PRECISION[] NOT NULL
        CHECK (
            array_position(retry_delays, NULL) IS NULL
            AND 0 <= ALL (retry_delays)
            AND 'Infinity' > ALL (retry_delays)
        ),
    origin JSONB CHECK (jsonb_typeof(origin) = 'object'),  -- ObservabilityLink
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),  -- counted attempts (D-35)
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'retry_pending', 'finished')),
    due_at TIMESTAMPTZ,
    lease_owner UUID,
    lease_expires_at TIMESTAMPTZ,
    started_at TIMESTAMPTZ,
    cancel_requested_at TIMESTAMPTZ,
    outcome TEXT CHECK (outcome IN ('completed', 'failed', 'cancelled')),
    result JSONB,
    created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
    finished_at TIMESTAMPTZ,
    CHECK ((status = 'finished') = (due_at IS NULL)),
    CHECK ((status = 'running') = (lease_owner IS NOT NULL)),
    CHECK ((status = 'running') = (lease_expires_at IS NOT NULL)),
    CHECK ((status = 'running') = (started_at IS NOT NULL)),
    CHECK (cancel_requested_at IS NULL OR status = 'running'),
    CHECK ((status = 'finished') = (outcome IS NOT NULL)),
    CHECK ((status = 'finished') = (finished_at IS NOT NULL)),
    CHECK ((outcome IS NOT DISTINCT FROM 'completed') = (result IS NOT NULL))
);

CREATE INDEX haiway_managed_tasks_open ON haiway_managed_tasks (status, due_at)
    WHERE status IN ('queued', 'retry_pending');  -- D-36

CREATE INDEX haiway_managed_tasks_leases ON haiway_managed_tasks (lease_expires_at)
    WHERE status = 'running';

CREATE INDEX haiway_managed_tasks_created ON haiway_managed_tasks (created_at, id);

CREATE INDEX haiway_managed_tasks_names ON haiway_managed_tasks (name, created_at, id);

CREATE TABLE haiway_managed_task_attempts (
    task_id UUID NOT NULL REFERENCES haiway_managed_tasks (id) ON DELETE CASCADE,
    number INTEGER NOT NULL CHECK (number >= 1),
    started_at TIMESTAMPTZ NOT NULL,
    ended_at TIMESTAMPTZ NOT NULL CHECK (ended_at >= started_at),
    result TEXT NOT NULL CHECK (result IN ('completed', 'failed', 'cancelled')),
    failure_kind TEXT
        CHECK (failure_kind IN ('raised', 'preparation', 'time_limit', 'interrupted', 'unsupported')),
    exception_type TEXT,
    time_limit DOUBLE PRECISION,
    PRIMARY KEY (task_id, number),
    CHECK ((result = 'failed') = (failure_kind IS NOT NULL)),
    CHECK (((failure_kind IN ('raised', 'preparation')) IS TRUE) = (exception_type IS NOT NULL)),
    CHECK (((failure_kind = 'time_limit') IS TRUE) = (time_limit IS NOT NULL))
);

CREATE TABLE haiway_managed_tasks_migrations (
    version INTEGER PRIMARY KEY CHECK (version >= 0),
    executed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
);
```

| Column or row                     | Meaning, and the TDS-001 data it holds                                                                                                                                                          |
| --------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `id`, `name`, `argument`          | `NewTask.identifier`, `name`, `argument` (TDS-001 FR-63).                                                                                                                                       |
| `time_limit`, `retry_delays`      | `NewTask.time_limit` (`NULL` when none) and `retry.delays` (TDS-001 FR-27, FR-66).                                                                                                              |
| `origin`                          | `NewTask.origin` as `ObservabilityLink.to_json()`, `NULL` for a spawn outside any scope; bounded by TDS-001 FR-87 (D-10).                                                                       |
| `status`, `outcome`, `result`     | The `StoredStatus` variant; for `finished`, the `StoredFinished` outcome, and `result` as the JSON of the value or `null`.                                                                      |
| `cancel_requested_at`             | `StoredRunning.cancel_requested`, as the moment it was requested (audit, TDS-001 FR-78).                                                                                                        |
| `due_at`                          | Bookkeeping: the due moment of the next attempt (TDS-001 FR-33, glossary "Due moment").                                                                                                         |
| `lease_owner`, `lease_expires_at` | Bookkeeping: the lease.                                                                                                                                                                         |
| `started_at`                      | Bookkeeping: when the running attempt was claimed; its attempt row's start.                                                                                                                     |
| `attempts`                        | The number of counted attempts: the attempt number minus 1 and the fence of `record` and `release`; equal to the number of attempt rows (D-35).                                                 |
| `created_at`, `finished_at`       | Bookkeeping: ordering, audit and listing timestamps (TDS-001 FR-78, FR-96).                                                                                                                     |
| attempt row                       | One `StoredAttempt`, with its start and end for audit; its `number` is the row key and its position in `StoredTask.attempts` (TDS-001 D-84); a `failed` task's failure is its last attempt row. |

### Column writes

Each operation is one transaction and writes exactly these columns of the task row; every column not
listed keeps its value. "Now" is the server clock read once for the statement (FR-20); "clear" sets
`NULL`. An inserted attempt row takes `started_at` as each row says and `ended_at` as
`greatest(statement_timestamp(), started_at)` (D-34).

| Operation and case                                    | `status`        | `due_at`    | `lease_owner`, `lease_expires_at` | `started_at` | `cancel_requested_at` | `outcome`, `result`     | `finished_at` | `attempts` | Attempt row                          |
| ----------------------------------------------------- | --------------- | ----------- | --------------------------------- | ------------ | --------------------- | ----------------------- | ------------- | ---------- | ------------------------------------ |
| `insert` (also `created_at`: now)                     | `queued`        | now         | clear                             | clear        | clear                 | clear                   | clear         | 0          | none                                 |
| `claim`, due row                                      | `running`       | kept        | runner, now + lease               | now          | clear                 | kept (clear)            | kept (clear)  | kept       | none                                 |
| `claim`, expired row (any owner, the caller included) | `running`       | kept        | runner, now + lease               | kept         | kept                  | kept (clear)            | kept (clear)  | kept       | none                                 |
| `renew`, held row                                     | kept            | kept        | kept, now + lease                 | kept         | kept                  | kept                    | kept          | kept       | none                                 |
| `record`, cancel requested                            | `finished`      | clear       | clear                             | clear        | clear                 | `cancelled`, clear      | now           | +1         | inserted, `cancelled` (TDS-001 D-98) |
| `record`, `AttemptRetried`                            | `retry_pending` | now + delay | clear                             | clear        | clear                 | clear                   | clear         | +1         | inserted, `failed` with the failure  |
| `record`, `AttemptFailed`                             | `finished`      | clear       | clear                             | clear        | clear                 | `failed`, clear         | now           | +1         | inserted, `failed` with the failure  |
| `record`, `AttemptCompleted`                          | `finished`      | clear       | clear                             | clear        | clear                 | `completed`, the result | now           | +1         | inserted, `completed`                |
| `record`, `AttemptCancelled`                          | `finished`      | clear       | clear                             | clear        | clear                 | `cancelled`, clear      | now           | +1         | inserted, `cancelled`                |
| `release`, cancel requested                           | `finished`      | clear       | clear                             | clear        | clear                 | `cancelled`, clear      | now           | kept       | none                                 |
| `release`, otherwise                                  | `queued`        | now         | clear                             | clear        | clear                 | clear                   | clear         | kept       | none                                 |
| `cancel`, `queued` or `retry_pending`                 | `finished`      | clear       | clear                             | clear        | clear                 | `cancelled`, clear      | now           | kept       | none                                 |
| `cancel`, `running` without a request                 | kept            | kept        | kept                              | kept         | now                   | kept                    | kept          | kept       | none                                 |
| `fail_unsupported`, due row of an unlisted name       | `finished`      | clear       | kept (clear)                      | kept (clear) | kept (clear)          | `failed`, kept (clear)  | now           | +1         | inserted, `failed`, `unsupported`    |

## Behavior requirements

### FR-1 — Schema installation

**Statement:** `await store.migrate(timeout=...)` MUST raise the pool's `PostgresException` when the
store is open; otherwise it MUST enter the store's pool and, on one connection acquired from it: set
`lock_timeout` to `timeout` seconds; take a session advisory lock with a key constant distinct from
`execute_migrations`' key; create `haiway_managed_tasks_migrations` if absent; run, each in its own
transaction together with the insertion of its version, every schema migration whose version is not
recorded, in version order; then release the lock, reset `lock_timeout` and exit the pool, also when
a migration fails.

**Rationale:** D-4, D-12. It follows `execute_migrations` (`state.py:446-557`) without sharing its
table, whose row count is the application's version. Running on the store's pool installs the schema
in the database the store uses, which a `Postgres` found in the context need not be (review 2 S12).
A session advisory lock is "held until explicitly released or the session ends" (PostgreSQL 18,
explicit locking, per WebExplorer), and the whole run holds one connection.

**Acceptance:**

1. On an empty database, `migrate()` creates the three tables and four indexes of
   [Data model](#data-model); a second call changes nothing.
1. Two concurrent `migrate()` calls on two store objects both return without error, and each version
   is recorded once.
1. `migrate()` does not add a row to `migrations`, and needs no `Postgres` in the context.
1. `migrate()` on an open store raises `PostgresException` and changes nothing.

**Source:** D-4, D-12.

### FR-2 — Task rows

**Statement:** Every task row MUST satisfy the constraints of [Data model](#data-model), and every
operation MUST write exactly the columns of [Column writes](#column-writes).

**Rationale:** AGENTS.md Golden Rule; TDS-001 D-67 and D-72 list what a row holds. A `CHECK` is
satisfied by `NULL` (PostgreSQL 18, constraints, per WebExplorer), so every pairing is written as an
equality of truth values, `NOT NULL` is separate, and the array check rejects `NULL` elements
explicitly. A transition leaving `running` that kept `cancel_requested_at`, or a finish that kept
`due_at`, would violate a constraint, fail its `record` for good, and send the task through lease
expiry and recovery without end.

**Acceptance:**

1. One test per row of [Column writes](#column-writes) checks every listed column after the
   operation, against a PostgreSQL server (NFR-3).
1. Inserting a `running` row without `lease_owner` or `started_at`, a `finished` row with
   `outcome = 'completed'` and no `result`, or `retry_delays` holding `NULL`, fails with a check
   violation.

**Source:** TDS-001 D-67, D-72, FR-78; AGENTS.md; D-35.

### FR-3 — Attempt rows

**Statement:** `record` MUST insert the attempt row (number, `started_at` from the task row,
`ended_at` the server clock, or `started_at` when that is later (D-34), result and failure) in the
same statement as the task row's update, and only when that update matches the row (FR-12);
`release` MUST NOT insert one.

**Rationale:** TDS-001 FR-65, FR-78, D-56. One statement keeps the two tables consistent (D-19).

**Acceptance:**

1. A task failing once and then completing under `TaskRetryPolicy(delays=(0.0,))` has attempt rows 1
   (`failed`, `raised`, `builtins.ValueError`) and 2 (`completed`).

**Source:** TDS-001 FR-65, FR-78; D-19; D-34.

### FR-4 — Settings

**Statement:** `PostgresTaskStore(pool, /, *, lease, lease_renewal, polling)` MUST take the
`PostgresConnectionPool` it owns and the three timings in seconds, all required, with no default; it
MUST build `LeaseTimings(lease=lease, renewal=lease_renewal, polling=polling)` at construction,
raising its `ValueError` for a value out of range (TDS-001 FR-45), and every session MUST yield
`LeasedCoordination` with those timings. Its `claim` and `renew` MUST use that `lease`.

**Rationale:** TDS-001 D-88 gives the timings to the stores that need them, superseding TDS-001
D-71, which had superseded D-3; D-3's "required, with no defaults" holds again, now on this store
(D-18). The pool is the caller's (FR-18, D-15).

**Acceptance:**

1. `PostgresTaskStore(pool, lease=1.0, lease_renewal=1.0, polling=0.1)` raises `ValueError` naming
   the renewal; `PostgresTaskStore(pool)` raises `TypeError`.
1. A store whose pool is entered elsewhere fails at `open` with `PostgresException`; after its
   session closed, the store opens again.
1. An opened session's coordination is `LeasedCoordination` with the given timings.

**Source:** D-15, D-18; TDS-001 D-88.

### FR-5 — Insert and claim

**Statement:**

- `insert` MUST write the task row as [Column writes](#column-writes) lists, in one statement; for
  an `id` the table already holds it MUST fail with the primary-key violation, raised as
  `PostgresException`, and write nothing (D-32, TDS-001 D-123).

- `claim` MUST be one statement of this form:

  1. a `WITH` query `due` selecting the `id` of rows that are `queued` or `retry_pending` with
     `due_at <= statement_timestamp()` and `name = ANY($names)`, ordered by `due_at` and then
     `created_at`, with `LIMIT $limit` (a `NULL` limit taking all, `0` taking none) and
     `FOR UPDATE SKIP LOCKED` written inside it;
  1. a `WITH` query `expired` selecting the `id` of rows that are `running` with
     `lease_expires_at < statement_timestamp()` and `name = ANY($names)`, with no limit and
     `FOR UPDATE SKIP LOCKED` written inside it;
  1. a `WITH` query `claimed` of
     `SELECT id, 'attempt' AS claim FROM due UNION SELECT id, 'interrupted' FROM expired`;
  1. an `UPDATE` of the task table `FROM claimed`, joined on `id`, whose `SET` writes each column as
     [Column writes](#column-writes) lists through `CASE` expressions on `claimed.claim`
     (`started_at` the clock for `'attempt'` and kept for `'interrupted'`; `cancel_requested_at`
     cleared for `'attempt'` and kept for `'interrupted'`), with
     `lease_expires_at = statement_timestamp() + make_interval(secs => $lease)`;
  1. `RETURNING` `claimed.claim`, the row's task columns, the post-update `cancel_requested_at`, and
     the attempt number as `t.attempts + 1` (D-35).

  It MUST NOT update the rows through `WHERE id IN (SELECT ... FOR UPDATE SKIP LOCKED)`, and MUST
  NOT use `RETURNING OLD` (D-19).

- `claim` MUST return a row tagged `'attempt'` as `ClaimedAttempt` with the attempt number, and a
  row tagged `'interrupted'` as `ClaimedInterrupted` with the attempt number of the interrupted
  attempt and whether `cancel_requested_at` is set.

**Rationale:** D-1, D-11, D-13, D-20 and TDS-001 FR-78. With `SKIP LOCKED`, "any selected rows that
cannot be immediately locked are skipped", the documented use being "multiple consumers accessing a
queue-like table"; a locking clause applies to a `WITH` query only when written inside it
(PostgreSQL 18 and 19, `SELECT`, per WebExplorer). FOR UPDATE with SKIP LOCKED "makes the
sub-select's output unstable by definition", and the recommended fix is "to put the FOR UPDATE into
a WITH to guarantee single execution" (Tom Lane, pgsql-general, 2019-04-15, per WebExplorer). The
two groups need two locking queries because one limit cannot apply to only part of a query, and a
locking clause "cannot be specified either for a `UNION` result or for any input of a `UNION`"
(PostgreSQL 18, `SELECT`, per WebExplorer, 2026-11-27); the union is taken over the locked `id`s.
The two cases write different columns, and `RETURNING` gives post-update values, so the branch tag
is the only way the `SET` and the result can tell an attempt from an interruption without
`RETURNING OLD`. The order is best effort across runtimes: at `READ COMMITTED` a locking `SELECT`
with `ORDER BY` can return rows out of order (same page), as TDS-001 FR-80 lists. Expired rows need
no slot and include the caller's own (TDS-001 D-81, D-105). An Experimenter ran this form on
PostgreSQL 18.7 and 16.16 (asyncpg 0.32.0, `READ COMMITTED`, autocommit; per Experimenter,
2026-11-27): the deterministic checks passed, 8 rounds per version of 8 concurrent claimers with 0.3
s and 60 s leases and random cancels showed no invariant violation, the plans kept each locking
`WITH` query as a CTE Scan whose LockRows and Limit ran once (also under a forced nested loop), and
`limit=0` returned only expired leases with the due index scan "never executed".

**Acceptance:**

1. Two runtimes claiming concurrently from 100 due rows get disjoint sets whose union is the 100
   rows.

1. A row whose name is not in `names` is never returned; the caller's own expired row is returned as
   `ClaimedInterrupted`.

1. `claim` with `limit=2` over 5 due rows and 3 expired rows returns 2 `ClaimedAttempt` and 3
   `ClaimedInterrupted`, and updates exactly 5 rows; with `limit=0` it returns the 3 only.

1. `EXPLAIN` of the claim on a table of 200,000 finished rows and 100 due rows, for custom and
   generic plans with `limit` 10, `NULL` and 0, shows `due_at <= statement_timestamp()` as an index
   condition of `haiway_managed_tasks_open` and `lease_expires_at < statement_timestamp()` as an
   index condition of `haiway_managed_tasks_leases`.

1. An expired row claimed again keeps its `started_at` and `cancel_requested_at`; a due row gets
   `started_at` equal to the statement's clock and no `cancel_requested_at`.

1. `insert` of a task whose `id` is `running` in the table raises `PostgresException` and leaves the
   row unchanged.

**Source:** D-1, D-11, D-13, D-20, D-22, D-32; TDS-001 FR-78, D-90, D-105, D-123; D-35, D-36.

### FR-6 — Release

**Statement:** `release(runner, identifier, attempt)` MUST, in one statement and only when the row
is `running`, its `lease_owner` is the runner and its `attempts + 1` equals `$attempt` (D-33, D-35),
all read from the row the `UPDATE` locks, in this form (verified, D-39):

```sql
UPDATE haiway_managed_tasks
SET status = CASE WHEN cancel_requested_at IS NOT NULL THEN 'finished' ELSE 'queued' END,
    due_at = CASE WHEN cancel_requested_at IS NOT NULL THEN NULL ELSE statement_timestamp() END,
    lease_owner = NULL, lease_expires_at = NULL, started_at = NULL, cancel_requested_at = NULL,
    outcome = CASE WHEN cancel_requested_at IS NOT NULL THEN 'cancelled' ELSE NULL END,
    finished_at = CASE WHEN cancel_requested_at IS NOT NULL THEN statement_timestamp() ELSE NULL END
WHERE id = $2::UUID AND status = 'running' AND lease_owner = $1::UUID
  AND attempts + 1 = $3::BIGINT
RETURNING status, outcome;
```

It MUST write the row as [Column writes](#column-writes) lists: `finished` with outcome `cancelled`
when `cancel_requested_at` is set, `queued` otherwise; and return the status it fixed, or `None`
when it matched no row.

**Rationale:** TDS-001 D-56, D-88, FR-78. Releasing at once lets another runtime resume the task
without waiting for the lease to expire.

**Acceptance:**

1. After a close with a running stored task, its row is `queued` with no lease and no attempt row,
   and a second runtime claims it.

**Source:** TDS-001 D-56, D-88, FR-29, FR-78, D-127; D-33; D-35, D-39.

### FR-7 — Expired rows

**Statement:** An expired row MUST be claimable by any runtime listing its name, its lease's owner
included (FR-5), returned as `ClaimedInterrupted`; the runtime then records the interrupted attempt
through `record` (TDS-001 FR-84), whose attempt row takes the `started_at` the claim kept, or
discards the claim when it still runs that attempt.

**Rationale:** D-7 and TDS-001 D-65, D-66, D-81: the store reports the interruption, the runtime
decides.

**Acceptance:**

1. A runtime stopped without closing while running a task: after the lease expires, a second
   runtime's claim returns it as `ClaimedInterrupted` with attempt number 1, and attempt row 1 is
   recorded as `interrupted`.

**Source:** D-7, D-13; TDS-001 FR-78, FR-84.

### FR-8 — Lease renewal

**Statement:** `renew(runner, identifiers)` MUST, in one statement, set `lease_expires_at` to the
server clock plus the store's `lease` for the rows among `identifiers` that are `running` with
`lease_owner` the runner, expired or not, and return them in `LeaseRenewal.leases`, each mapped to
`"cancel_requested"` when `cancel_requested_at` is set and to `"held"` otherwise.

**Rationale:** TDS-001 FR-78, FR-83, D-84: one mapping, so a cancel can only be reported for a held
lease; TDS-001 D-88: the lease is the store's.

**Acceptance:**

1. A row whose `lease_owner` a test changed is absent from `leases`; a held row with
   `cancel_requested_at` set maps to `"cancel_requested"`.

**Source:** D-13, D-18; TDS-001 FR-78, FR-83.

### FR-9 — Stored data on each attempt (withdrawn)

Withdrawn by D-8: the runtime decodes the argument (TDS-001 FR-63); the store returns the text.

### FR-10 — Registered names

**Statement:** `claim` MUST return only rows whose `name` is in the given names. The documentation
MUST state that a row no runtime lists stays as it is while no runtime with `unsupported="fail"` is
open, and that registered names are unique per database (D-2): runtimes of different applications
sharing a database run each other's tasks of one name, each under its own root snapshot. It MUST
give a query listing the unfinished rows whose name a given set of registered names does not hold,
with their name, status and `created_at`, and state that a runtime with `unsupported="fail"` fails
such rows once due (FR-23, TDS-001 FR-92).

**Rationale:** D-1, D-2; TDS-001 D-96 asks for unlisted rows to be documented with a query.

**Acceptance:**

1. A row named `unknown` stays `queued` through 3 claims that do not list it, and the documented
   query run with the names `{"r"}` returns it.

**Source:** D-1, D-2, D-25; TDS-001 D-96.

### FR-11 — Read

**Statement:** `read(identifiers)` MUST, in one statement, return a mapping holding, for each of
`identifiers` that has a row, its `StoredTask`, with its attempt rows in number order; a `finished`
row with outcome `failed` MUST be returned as `StoredFailed`, its failure and count being those of
the attempt rows (TDS-001 FR-78).

**Rationale:** TDS-001 FR-21, FR-70, FR-78, D-83 (one read per polling tick); D-5 (rows stay until
the application deletes them).

**Acceptance:**

1. A finished task read in a second runtime has the status, attempts and result the first recorded,
   and survives a `StoredTask` JSON round trip with its status variant.
1. `read` of 50 identifiers, 2 of them unknown, returns 48 entries in one statement.

**Source:** D-5, D-13; TDS-001 FR-78.

### FR-12 — Record

**Statement:** `record(runner, identifier, attempt, end)` MUST be one statement of this form:

1. a `WITH` query `held` selecting, with `FOR UPDATE`, the task row's `id`, `started_at`,
   `cancel_requested_at` and `attempts`, only while it is `running` with `lease_owner` the runner
   (expired or not) and `attempts + 1 = $attempt` (D-33, D-35):
   `SELECT id, started_at, cancel_requested_at, attempts FROM haiway_managed_tasks WHERE id = $2::UUID AND status = 'running' AND lease_owner = $1::UUID AND attempts + 1 = $3::BIGINT FOR UPDATE`;
1. a data-modifying `WITH` query that updates the task row `FROM held`, writing every column as
   [Column writes](#column-writes) lists, each with a `CASE` on `held.cancel_requested_at` choosing
   the "cancel requested" case when it is set and the case of `end`'s variant otherwise, setting
   `attempts = held.attempts + 1`, and `RETURNING` the new `status`, `outcome` and `result` (D-38);
1. a data-modifying `WITH` query that inserts the attempt row `FROM held`: number
   `held.attempts + 1`, `started_at` from `held`, `ended_at`
   `greatest(statement_timestamp(), held.started_at)` (D-34), and the result `cancelled` when
   `held.cancel_requested_at` is set, otherwise the result and failure `end` gives;
1. a main query returning the updated row's new values.

When `held` matches no row, nothing is written and the statement returns no row, and `record` MUST
return `None`; otherwise it MUST return the status the returned values give. A statement MUST NOT
combine values read from a locked `WITH` row with task-table columns it keeps, in a way that can
make the provisional row the update writes violate a `CHECK` constraint; `record` writes every
mutable column from `held` or its parameters, so it keeps none (FR-14).

**Rationale:** TDS-001 FR-78: a cancel the store accepted wins at `record`, with the attempt
recorded as `cancelled` (TDS-001 D-98); matching on the owner and the attempt number (D-33) makes a
repeated `record` after a lost reply return `None` and write nothing (TDS-001 D-81). The update
clears `cancel_requested_at` and `started_at`, and "by default, the new (post-update) values of the
table's columns are used" in `RETURNING` (PostgreSQL 18, `UPDATE`, per WebExplorer); reading the old
values once in `held` and handing them to both writes avoids `RETURNING OLD`, so no minimum server
version is needed (D-19), and haiway's postgres integration states none (search of `docs/` and
`src/haiway/postgres/`, 2026-11-27). The sub-statements of a `WITH` run on one snapshot and "cannot
see one another's effects on the target tables", and "trying to update the same row twice in a
single statement is not supported" (PostgreSQL 18, "Data-Modifying Statements in WITH", per
WebExplorer): here each table is written once, and both writes read only `held`. `held`'s
`FOR UPDATE` makes a concurrent `cancel` or `claim` wait. An `UPDATE ... FROM` a `WITH` query is the
form Tom Lane recommends for the claim (FR-5), and it ran on PostgreSQL 16.16 and 18.7 (per
Experimenter, 2026-11-27).

**Acceptance:**

1. `record` by a runner whose lease was claimed by another runner returns `None` and writes nothing.
1. `record` of attempt 1 after the same runner's `record` of attempt 1 committed `AttemptRetried`
   and its `claim` took attempt 2 returns `None` and writes nothing; `release` of attempt 1 then
   does the same.
1. `record` by a runner whose lease expired unclaimed fixes the status and inserts the attempt row.
1. After `cancel` marked a running row, `record` with `AttemptRetried` returns `StoredFinished` with
   `StoredCancelled`, and the attempt row is `cancelled` with the claim's `started_at`.
1. A second identical `record` after a committed one returns `None` and inserts nothing.
1. A `record` of an attempt whose `started_at` a test set 1 h after the server clock succeeds, and
   its attempt row's `ended_at` equals that `started_at`.
1. A statement counter records one statement per `record`, and it prepares as one statement under
   the extended protocol.

**Source:** D-13, D-19, D-21; TDS-001 FR-66, FR-78, D-98, D-127; D-33; D-34; D-35, D-38, D-39.

### FR-13 — Claim rounds (withdrawn)

Withdrawn by D-8: claim rounds are the runtime's (TDS-001 FR-85).

### FR-14 — Cancel

**Statement:** `cancel(identifier)` MUST be one statement of this form:

1. a `WITH` query `target` selecting the row's `id` and `status` by `id` with `FOR UPDATE`;
1. a data-modifying `WITH` query updating the task table `FROM target`, joined on `id`, whose
   `WHERE` and `SET` `CASE` expressions read only the task table's own columns (`t.status`,
   `t.cancel_requested_at`), never `target`'s: a `queued` or `retry_pending` row is written as
   [Column writes](#column-writes) lists for that case, a `running` row gets
   `cancel_requested_at = COALESCE(t.cancel_requested_at, statement_timestamp())`, and a `finished`
   row is not updated;
1. a main query returning, from `target`, `"cancelled"` when it was `queued` or `retry_pending`,
   `"pending"` when it was `running`, and `"finished"` when it was `finished`; no row gives `None`.

A statement MUST NOT combine values read from a locked `WITH` row with task-table columns it keeps,
in a way that can make the provisional row the update writes violate a `CHECK` constraint.

**Rationale:** TDS-001 FR-26, FR-78, FR-86, D-91; D-21. `target` serves only for locking and for the
returned value. When the update waits on a concurrent claim, PostgreSQL checks constraints on a
provisional row before re-evaluating it; a form whose `CASE` expressions test `target.status` while
keeping `t.*` columns built a row `queued`, without a lease, with `cancel_requested_at` set, and
failed with `CheckViolationError` on `haiway_managed_tasks_check4` in 20 of 20 runs on PostgreSQL
18.7 and 16.16, while the form above passed 20 of 20 (per Experimenter, 2026-11-27).

**Acceptance:**

1. Cancelling a `running` row twice returns `"pending"` both times and leaves the first
   `cancel_requested_at`.
1. Cancelling a `queued` row returns `"cancelled"` and leaves it `finished`, `cancelled`, with
   `due_at` cleared and `finished_at` set.
1. Cancelling a `finished` row returns `"finished"`; an unknown identifier returns `None`.
1. A `cancel` of a due row that waits on a concurrent `claim` of it returns `"pending"`, marks the
   claimed row, and raises no check violation, in 20 of 20 runs.

**Source:** TDS-001 FR-26, FR-78, D-91; D-21.

### FR-15 — Retention of rows

**Statement:** The store MUST NOT delete rows. The documentation MUST give the statement that
deletes finished tasks older than a given time
(`DELETE FROM haiway_managed_tasks WHERE status = 'finished' AND finished_at < $1`, attempt rows
following by `ON DELETE CASCADE`), and state that a deleted task is no longer found by
`ManagedTasks.task`.

**Rationale:** D-5.

**Acceptance:**

1. After the documented statement deletes a finished task, its identifier is absent from the mapping
   `read` returns, and no attempt row of it remains.

**Source:** D-5; TDS-001 FR-70.

### FR-16 — Due moments on the server clock (withdrawn)

Withdrawn by TDS-001 D-89: the time limit counts from the claim, so `ClaimedAttempt.waited` is
removed.

### FR-17 — Storage errors

**Statement:** Every session operation MUST run as one transaction, and a failure MUST raise
`PostgresException` to the runtime with the transaction rolled back; the runtime handles it as
TDS-001 FR-82 says.

**Rationale:** AGENTS.md (typed errors at boundaries); the package wraps driver errors where they
occur (`client.py:356-359`, `:628-631`). This is the store's error type in TDS-001 FR-80's table.

**Acceptance:**

1. A `claim` interrupted by a failing statement leaves no row `running` under the runner.

**Source:** derived from AGENTS.md; TDS-001 FR-80, FR-82.

### FR-18 — Connections

**Statement:** The store MUST run its operations only on connections of its own `pool`, one acquired
for each operation and released at its end, never on a `PostgresConnection` found in any context; it
MUST work whatever connection the calling task holds. The docstring of `PostgresTaskStore` and the
documentation MUST state that its pool needs at least 2 connections and MUST be used by nothing
else, neither entered by the caller nor shared with task bodies.

**Rationale:** A connection in the context is shared by every task spawned under it
(`tasks.py:295-300`) and asyncpg connections are not safe for concurrent use;
`Postgres.acquire_connection()` raises inside such a context (`state.py:397-398`); and a pool shared
with task bodies can be held by them while a lease needs renewing. Review 2 S6 shows renewals,
records and polls of one runtime queueing on a single connection, and a caller entering the pool
makes `open` fail (`client.py:335-339`); D-15 keeps the caller's pool and documents both rules,
since the pool's size cannot be read from it (`client.py:290-299`).

**Acceptance:**

1. A task holding `async with ctx.scope("t", disposables=(Postgres.acquire_connection(),))` on the
   application's pool of one connection: the task's lease is renewed and its outcome recorded
   through the store's pool of 2 connections.
1. The docstring of `PostgresTaskStore` contains "2 connections".

**Source:** derived from `state.py:381-400`, `client.py:637-699`, `scope.py:152-182`; D-15.

### FR-19 — Runner observability (withdrawn)

Withdrawn by D-8: the runtime records claims, renewals and closes (TDS-001 FR-24, FR-61, FR-83).

### FR-20 — Server clock

**Statement:** Every statement of a session operation MUST read the server clock as inline
`statement_timestamp()`, for every time it stores (an attempt row's `ended_at` raised to its
`started_at`, D-34) and every comparison of `due_at` or `lease_expires_at`; it MUST NOT use
`clock_timestamp()` or the runtime's clock, and MUST add a duration given in seconds as
`make_interval(secs => $n)`. A row is due when `due_at <= statement_timestamp()`, and a lease
expired when `lease_expires_at < statement_timestamp()`. `insert` MUST write `created_at` from that
value. The column defaults of [Data model](#data-model) serve only rows written outside the store.

**Rationale:** Runtimes in several processes have unsynchronized clocks. `clock_timestamp()`
"returns the actual current time, and therefore its value changes even within a single SQL
statement" (PostgreSQL 18, date/time functions, per WebExplorer), and "it is not valid to use a
VOLATILE function in an index scan condition" (PostgreSQL 18, function volatility categories, per
WebExplorer). `statement_timestamp()` is STABLE (`provolatile = 's'`) and `clock_timestamp()`
VOLATILE (`'v'`) on PostgreSQL 18.7 and 16.16 (per Experimenter, 2026-11-27), so the comparisons
above are index conditions (FR-5 acceptance 4). Review 2 S4 reported, on PostgreSQL 18.7, 32.4 ms
for a claim comparing with inline `clock_timestamp()` against 0.95 ms with the clock read once and
0.28 ms with the lease index (D-11); these are review 2's single measurements, not reproduced.

**Acceptance:**

1. A runtime whose local clock is set 1 h ahead claims and renews as an unchanged one does.
1. A statement-text test finds no `clock_timestamp()` in the SQL of the session operations (the
   schema of `migrate` excluded).

**Source:** derived from D-1; D-11, D-22; D-34.

### FR-21 — Stored JSON

**Statement:** `insert` and `record` MUST store the given JSON text in `JSONB` columns, and `read`
and `claim` MUST return text equal to it as JSON (TDS-001 FR-63). A value the server rejects MUST
fail the operation with `PostgresException`, so a spawn stores nothing (TDS-001 FR-82).

**Rationale:** TDS-001 D-67; `JSONB` normalizes the text but keeps its value. The runtime rejects
U+0000 and values that do not round-trip before the store sees them (TDS-001 FR-63).

**Acceptance:**

1. An argument with nested mappings read back decodes to an equal State.

**Source:** TDS-001 D-67, FR-63; D-16.

### FR-22 — Schema check at open

**Statement:** `open` MUST, after entering the pool and before yielding a session, read the highest
version of `haiway_managed_tasks_migrations`, and MUST exit the pool and raise `PostgresException`
naming both versions when the table is absent or that version is below the store's latest migration;
a higher version MUST open. The documentation MUST state that every later migration of these tables
is additive and compatible with stores of earlier versions.

**Rationale:** D-12 (review 2 S12): without the check, a store on an unmigrated or outdated schema
opens and fails on its first statement, inside a claim round where TDS-001 FR-82 only logs it. D-17:
a newer replica may migrate first during a rolling deploy, and older replicas keep opening, which
holds only while migrations stay additive.

**Acceptance:**

1. On an empty database, `open` raises `PostgresException` and the pool is no longer entered; after
   `migrate()`, `open` yields a session.
1. With a version one above the store's latest recorded, `open` yields a session.

**Source:** D-12, D-17.

### FR-23 — Failing unsupported rows

**Statement:** `fail_unsupported(names)` MUST be one statement of this form:

1. a `WITH` query `unsupported` selecting the `id` and `attempts` of rows that are `queued` or
   `retry_pending` with `due_at <= statement_timestamp()` and `NOT (name = ANY($names))`, with no
   limit and `FOR UPDATE SKIP LOCKED` written inside it;
1. a data-modifying `WITH` query `failed` updating the task table `FROM unsupported`, joined on
   `id`, writing the columns as [Column writes](#column-writes) lists for `fail_unsupported` from
   constants, `statement_timestamp()` and `attempts = t.attempts + 1` only, and `RETURNING` the
   row's `id` and `name`;
1. a data-modifying `WITH` query inserting, `FROM unsupported`, one attempt row per task: number
   `unsupported.attempts + 1`, `started_at` `statement_timestamp()` and `ended_at`
   `greatest(statement_timestamp(), started_at)` (D-34), which equals it, result `failed`,
   `failure_kind` `unsupported`;
1. a main query returning `id` and `name` from `failed`, which `fail_unsupported` returns as a
   mapping.

The form verified on PostgreSQL 16.16 and 18.7 (D-39):

```sql
WITH unsupported AS (
    SELECT id, attempts FROM haiway_managed_tasks
    WHERE status IN ('queued', 'retry_pending')
      AND due_at <= statement_timestamp()
      AND NOT (name = ANY($1::TEXT[]))
    FOR UPDATE SKIP LOCKED
),
failed AS (
    UPDATE haiway_managed_tasks AS t
    SET status = 'finished', due_at = NULL, attempts = t.attempts + 1,
        outcome = 'failed', finished_at = statement_timestamp()
    FROM unsupported WHERE t.id = unsupported.id
    RETURNING t.id, t.name
),
attempt AS (
    INSERT INTO haiway_managed_task_attempts
        (task_id, number, started_at, ended_at, result, failure_kind)
    SELECT u.id, u.attempts + 1, statement_timestamp(), statement_timestamp(), 'failed', 'unsupported'
    FROM unsupported AS u
)
SELECT id, name FROM failed;
```

Its `ended_at` is `statement_timestamp()`, which equals
`greatest(statement_timestamp(), started_at)` here (D-34).

`$names` is a `TEXT[]` and MUST NOT be `NULL`: a runtime listing no task passes an empty array,
which matches every due row, while a `NULL` array would make `NOT (name = ANY(NULL))` `NULL` and
match nothing. `read` and `claim` MUST return an attempt row of kind `unsupported` as
`TaskUnsupported()`.

**Rationale:** TDS-001 FR-78, FR-93, D-110; D-25. One statement (NFR-1). `SKIP LOCKED` skips rows a
concurrent `claim`, `cancel` or `fail_unsupported` holds (PostgreSQL 18, `SELECT`, per WebExplorer,
as in FR-5), so a row a runtime listing its name is claiming is not failed, and two runtimes failing
unsupported rows never finish one row twice; a row that a concurrent transaction changed before the
lock is taken has its `WHERE` re-evaluated on the updated version ("the would-be updater will wait
... The search condition of the command (the `WHERE` clause) is re-evaluated", PostgreSQL 16 and 18,
Read Committed, per WebExplorer, 2026-11-27), so a row claimed or cancelled first is no longer due
and is left alone. The update reads nothing from `unsupported` but the join key, so it keeps D-21's
rule; the columns it keeps (`lease_owner`, `lease_expires_at`, `started_at`, `cancel_requested_at`,
`result`) are `NULL` on every `queued` or `retry_pending` row by the constraints of
[Data model](#data-model). A task failed as unsupported has no claim, hence no `started_at`, so its
attempt row starts and ends at the statement's clock. Running rows are untouched, so no pending
cancel can meet `fail_unsupported` (TDS-001 D-116). The due condition is that of FR-5, served by
`haiway_managed_tasks_open`. No limit, as for the `expired` query of FR-5. The attempt insert reads
`unsupported`, not `failed`: every row `unsupported` locks is updated, so both hold the same rows.
Verified on PostgreSQL 16.16 and 18.7 (D-30): acceptance 2 and 3, and this statement concurrent with
`cancel`, in 20 of 20 rounds on 100 rows and 5 of 5 on 5,000 rows each, with both sessions taking
rows in 17 to 20 of the 20 rounds and in every 5,000-row round; acceptance 6 on both versions.

**Acceptance:**

1. With 3 due `queued` rows of `x`, 1 due row of `r`, 1 `retry_pending` row of `x` due in 1 h and 1
   expired `running` row of `x`, `fail_unsupported({"r"})` returns the 3, each `finished`, `failed`,
   with `due_at` cleared, `finished_at` set and one attempt row `failed`, `unsupported`, numbered
   after its earlier attempt rows, whose `started_at` equals its `ended_at`; the other rows are
   unchanged.
1. Two concurrent `fail_unsupported({"r"})` calls over 100 due rows of `x` return disjoint sets
   whose union is the 100 rows, with exactly one `unsupported` attempt row each.
1. A `fail_unsupported({"r"})` concurrent with a `claim` listing `x`, over 100 due rows of `x`: each
   row ends either `running` under the claimer or `finished` by `fail_unsupported`, never both, with
   no check violation, in 20 of 20 runs.
1. `read` of a task failed as unsupported returns `StoredFinished(StoredFailed())` whose last
   attempt is `TaskUnsupported()`.
1. `fail_unsupported(set())` finishes every due row.
1. `EXPLAIN` of the statement on a table of 200,000 finished rows and 100 due rows shows
   `due_at <= statement_timestamp()` as an index condition of `haiway_managed_tasks_open`.

**Source:** D-25, D-27, D-30; TDS-001 FR-78, FR-93, D-110, D-111, D-116; D-34; D-35, D-39.

### FR-24 — Query

**Statement:** `query(matching, after=..., limit=...)` MUST be one statement of this form:

1. `SELECT` from `haiway_managed_tasks` the row's `id`, `name`, `status`, `outcome`, `created_at`,
   `finished_at` and `attempts`, the number of counted attempts (D-35, D-40), reading no attempt
   row;
1. a `WHERE` holding, for each part of `matching` that is given and only for those: for `names`
   holding exactly one name, `name = $name`, and for several, `name = ANY($names)`; for
   `TasksUnfinished`, `status = ANY($statuses)`; for `TasksFinished`,
   `status = 'finished' AND outcome = ANY($outcomes)`, and for its `finished` range
   `finished_at >= $start` for `TaskTimeFrom`, `finished_at < $end` for `TaskTimeBefore`, and both
   for `TaskTimeBetween`; for `created`, the same conditions on `created_at`; and, when `after` is
   given, `(created_at, id) > ($after_created, $after_id)`;
1. `ORDER BY created_at, id` and `LIMIT $limit`.

The statement text MUST contain only the conditions of the given parts, never a `$n IS NULL OR ...`
form. It MUST return each row as a `TaskSummary` (TDS-001 FR-98): status `queued`, `running` or
`retry_pending` as `TaskSummaryUnfinished`, and `finished` as `TaskSummaryFinished` with `outcome`
and `finished_at`; `attempts` the count; `created` the `created_at`; times as UTC `datetime` values.
The schema MUST hold the indexes `haiway_managed_tasks_created` on `(created_at, id)` and
`haiway_managed_tasks_names` on `(name, created_at, id)`, in schema version 1.

**Rationale:** TDS-001 D-109, D-112 to D-115, FR-95, FR-97, FR-98; D-26, D-27. A row comparison
compares "left-to-right, stopping as soon as an unequal or null pair of elements is found"
(PostgreSQL 16 and 18, row constructor comparison, per WebExplorer), so `(created_at, id) > (c, i)`
is the keyset of the order; neither column is ever `NULL`. Rows are kept until deleted (D-5), so
without an index on the order key every page would sort the table; a B-tree on `(x, y)` satisfies
`ORDER BY x, y` scanning forward (PostgreSQL 16 and 18, indexes and `ORDER BY`, per WebExplorer).
With one name, `name = $name` lets the planner scan `haiway_managed_tasks_names` in order with the
name and the keyset both as index conditions; with `name = ANY($names)` it never scanned that index
in order (a bitmap scan and a sort, also with the created index dropped and `enable_sort` off), so a
page of a filter with several names reads and sorts every row of those names after the keyset: 5,000
rows read for 50 returned in the run (D-29). Whether the planner picks the names index or the
created index with a filter depends on the name's share of rows: for a name of 5% it chose the
created index. Building the text from the given parts keeps the keyset and the name in the index
conditions: with a `$n IS NULL OR` form, generic plans still scan `haiway_managed_tasks_created` for
the order, but the keyset and name conditions become a filter (100,000 rows removed by filter, 0
with the form above). These planner facts are from the run of D-30 on PostgreSQL 16.16 and 18.7,
with identical plan shapes. Both indexes cover immutable columns and go into version 1, since no
version was released (Compatibility and migration).

**Acceptance:**

1. On an empty database, `migrate()` creates `haiway_managed_tasks_created` and
   `haiway_managed_tasks_names`.
1. Over 5 rows created in order A to E, `query(TaskFilter(), after=None, limit=2)` returns A, B, and
   with `after` the keyset of B returns C, D.
1. A task with 2 attempt rows, `finished`, `failed`, is returned as
   `TaskSummaryFinished(outcome="failed")` with `attempts == 2` and its `finished_at`.
1. The statement text of `query` does not name `haiway_managed_task_attempts`, and a summary's
   `attempts` equals the task row's `attempts`.
1. `TaskFilter(names={"r"}, status=TasksFinished(outcomes={"failed"}, finished=TaskTimeFrom(start=T)))`
   returns only failed rows of `r` finished at or after `T`.
1. `EXPLAIN` on a table of 200,000 rows, for custom and generic plans, shows for `TaskFilter()` with
   `after` given an index scan of `haiway_managed_tasks_created` with the row comparison as its
   index condition and no sort node.
1. `EXPLAIN` on a table of 200,000 rows spread over 200 names of 1,000 rows each, for custom and
   generic plans, shows for `TaskFilter(names={"r"})` with `after` given an index scan of
   `haiway_managed_tasks_names` with `name = $name` and the row comparison as its index conditions
   and no sort node.
1. A statement counter records one statement per `query`.

**Source:** D-5, D-26, D-27, D-29, D-30; TDS-001 FR-95, FR-97, FR-98, D-109, D-112 to D-115; D-40.

### NFR-1 — One statement per operation

**Statement:** Every session operation MUST execute as exactly one statement, `record` (FR-12),
`read` of any set of identifiers (FR-11), `fail_unsupported` (FR-23) and `query` (FR-24) included.

**Rationale:** Bounds the store's database load by the runtime's call rate (TDS-001 FR-83, FR-85).
It is not one round trip: asyncpg's pool runs its reset
(`SELECT pg_advisory_unlock_all(); CLOSE ALL; UNLISTEN *; RESET ALL;`) when the operation releases
its connection, and a statement-cache miss adds Parse and Describe; the reset stays, since it backs
`migrate`'s unlock and `lock_timeout` reset (D-37).

**Acceptance:**

1. A statement counter on a test database records one statement per operation.

**Source:** derived from TDS-001 FR-83, FR-85; D-19, D-37.

### NFR-2 — Documentation

**Statement:** `docs/features/postgres.md` MUST gain a "Task store" section: `migrate()`, the
store's own pool and its two rules (FR-18), the shared queue, unique registered names and the query
for unlisted rows (FR-10), what `unsupported="fail"` does to them (FR-23), listing, the server clock
of its times and the cost of filters with several names (FR-24, TDS-001 FR-95), row deletion
(FR-15), additive migrations (FR-22), the minimum verified PostgreSQL version, 16 (D-23), and an
example entering
`ctx.managed_tasks(TaskRuntime(store=PostgresTaskStore(PostgresConnectionPool(connection_limit=2), lease=30.0, lease_renewal=10.0, polling=1.0), tasks=(...)))`,
whose three timings are the example's own values, required by D-18.

**Rationale:** AGENTS.md Documentation; D-15, D-17, D-18; TDS-001 D-96.

**Acceptance:**

1. `make docs` builds with `--strict`, and the section contains the example.

**Source:** AGENTS.md; D-15, D-17, D-18, D-25, D-26.

### NFR-3 — Tests

**Statement:** The TDS-001 store contract tests (TDS-001 FR-78, NFR-5) and FR-1 to FR-24 MUST run
against a PostgreSQL server whose DSN a test environment variable gives, and MUST be skipped when it
is absent; no CI job provides one (TDS-001 D-76, D-97). FR-4 and FR-17 MUST also have unit tests
with fakes, which `make test` runs.

**Rationale:** `SKIP LOCKED`, constraints, plans and the server clock cannot be faked faithfully;
AGENTS.md keeps unit tests free of network.

**Acceptance:**

1. Without the variable, `make test` passes and reports the server tests as skipped.

**Source:** AGENTS.md Testing; TDS-001 D-76, D-97.

### NFR-4 — Exports

**Statement:** `PostgresTaskStore` MUST be exported from `haiway.postgres`.

**Rationale:** AGENTS.md: integration packages export their APIs.

**Acceptance:**

1. `from haiway.postgres import PostgresTaskStore` succeeds with the `postgres` extra.

**Source:** AGENTS.md.

## Security and failure modes

- Stored arguments and results are data at rest in the application's database, as JSON; failures are
  stored as type names only (TDS-001 FR-65); origins hold two identifiers and the W3C trace headers,
  which W3C keeps free of personal information but which can be sensitive (TDS-001 FR-87).
- Anyone who can write the tables can make a runtime run a listed registered task with an argument
  that passes validation (TDS-001 FR-63); no stored code runs (TDS-001 D-46).
- Runtimes of different applications sharing a database run each other's tasks of one name (D-2).

| Failure                                  | Behavior                                                                                    |
| ---------------------------------------- | ------------------------------------------------------------------------------------------- |
| Database unreachable during an operation | `PostgresException`; the runtime handles it (FR-17)                                         |
| Runtime process crashes                  | Its rows expire and are claimed as interrupted (FR-7)                                       |
| Schema not migrated                      | `open` raises (FR-22)                                                                       |
| Repeated `record` after a lost reply     | Returns `None`, writes nothing (FR-12)                                                      |
| Lease reassigned while running           | `record` writes nothing (FR-12)                                                             |
| Cancel accepted before the attempt ends  | `record` finishes the task cancelled (FR-12)                                                |
| No runtime lists a row's name            | The row waits (FR-10), or is failed once due by a runtime with `unsupported="fail"` (FR-23) |

## Compatibility and migration

- New tables only; `migrate()` versions them in their own table (FR-1), and `open` refuses a schema
  below the store's version (FR-22). Later schema changes are new versions of `migrate()`; the
  `haiway_managed_tasks_leases` index is part of the first version, since none was released.
- `migrate` changed from a class method using the context's `Postgres` to an instance method on the
  store's pool (D-12), and the constructor gained the three timings (D-18); nothing was released.
- Later schema versions are additive and compatible with stores of earlier versions, so a newer
  replica may migrate first during a rolling deploy (FR-22, D-17).
- Earlier versions of this schema were never released; nothing migrates from them.
- Server versions: the statements were verified on PostgreSQL 18.7 and 16.16 and use nothing
  version-specific (no `RETURNING OLD`); PostgreSQL 16 is the minimum verified version (D-23), and
  the documentation states it (NFR-2).
- v1.1 (D-25, D-26): `unsupported` in `failure_kind` and the two listing indexes are part of schema
  version 1, since no version was released; nothing migrates.
- Rollback: the tables stay; dropping them is the operator's choice.

## Coverage

Columns as in [TDS-001 Coverage](TDS-001-managed-tasks.md#coverage). Shared `N/A` reasons: (a) no
input beyond the row's; (b) no setting applies; (c) new API, nothing released; (d) records nothing
of its own; (e) owns no resource; (f) the runtime's (TDS-001 FR-80).

| Row                   | N     | In      | Err          | Conc              | Int             | St                    | Lim            | Sec      | Cfg     | Obs     | Comp          | Life    |
| --------------------- | ----- | ------- | ------------ | ----------------- | --------------- | --------------------- | -------------- | -------- | ------- | ------- | ------------- | ------- |
| `migrate`             | FR-1  | FR-1    | FR-1         | FR-1              | FR-1            | FR-1                  | N/A: one run   | N/A (a)  | FR-1    | N/A (d) | Compatibility | FR-1    |
| Settings and pool     | FR-4  | FR-4    | FR-4, FR-22  | FR-18             | FR-18           | N/A (f)               | FR-18          | N/A (a)  | FR-4    | N/A (d) | N/A (c)       | FR-18   |
| Insert                | FR-5  | FR-21   | FR-17, FR-21 | FR-5              | FR-17           | FR-2                  | NFR-1          | Security | N/A (b) | N/A (d) | N/A (c)       | FR-15   |
| Claim                 | FR-5  | FR-10   | FR-17        | FR-5              | FR-7            | FR-2                  | FR-5, NFR-1    | FR-10    | N/A (f) | N/A (d) | N/A (c)       | FR-8    |
| Renew                 | FR-8  | N/A (a) | FR-17        | FR-8              | FR-7            | FR-2                  | NFR-1          | N/A (a)  | FR-4    | N/A (d) | N/A (c)       | FR-8    |
| Record                | FR-12 | FR-21   | FR-17        | FR-12             | FR-12           | FR-2, FR-12           | NFR-1          | FR-3     | N/A (f) | N/A (d) | N/A (c)       | FR-15   |
| Release               | FR-6  | N/A (a) | FR-17        | FR-6              | FR-6            | FR-6                  | NFR-1          | N/A (a)  | N/A (b) | N/A (d) | N/A (c)       | FR-6    |
| Cancel                | FR-14 | N/A (a) | FR-17        | FR-12             | FR-14           | FR-14                 | NFR-1          | Security | N/A (b) | N/A (d) | N/A (c)       | FR-14   |
| Read                  | FR-11 | FR-11   | FR-17        | FR-11             | FR-11           | FR-2                  | FR-11, NFR-1   | Security | N/A (b) | N/A (d) | N/A (c)       | FR-15   |
| Attempt rows          | FR-3  | N/A (a) | FR-2         | FR-12             | FR-7            | FR-3                  | FR-15          | FR-3     | N/A (b) | N/A (d) | N/A (c)       | FR-15   |
| Clock and due moments | FR-20 | FR-20   | FR-17        | FR-20             | FR-20           | FR-2                  | N/A: one clock | N/A (a)  | N/A (b) | N/A (d) | N/A (c)       | N/A (e) |
| Schema check at open  | FR-22 | FR-22   | FR-22        | N/A: one per open | FR-22           | N/A: refuses or opens | N/A: one read  | N/A (a)  | FR-22   | N/A (d) | Compatibility | FR-22   |
| Failing unsupported   | FR-23 | FR-23   | FR-17        | FR-23             | FR-17           | FR-2, FR-23           | FR-23, NFR-1   | Security | N/A (f) | N/A (d) | Compatibility | FR-15   |
| Query                 | FR-24 | FR-24   | FR-17        | FR-24             | N/A: reads only | N/A: reads only       | FR-24, NFR-1   | Security | N/A (f) | N/A (d) | N/A (c)       | FR-15   |

Count: 14 rows, 168 cells: 114 covered by requirements or sections, 54 `N/A` with a reason; none
empty.

## Open questions

None open. OQ-1 to OQ-9 are resolved; this revision's questions were TDS-001 OQ-53 to OQ-58,
answered as TDS-001 D-112 to D-117.

### OQ-7 — Pool of the store (resolved)

Resolved by [D-15](#decisions).

- **Header:** Store pool
- **Question:** Should the store keep taking a caller-built `PostgresConnectionPool`, or build its
  own pool from connection settings, and should it require at least 2 connections or reserve one
  connection for renewals?
- **Why it matters:** Blocks FR-4, FR-18 and the constructor (review 2 S6, M5). The pool default is
  one connection (`client.py:82-84`, `:265-268`), on which polls and records queue in front of
  renewals, so a lease can expire while `record` waits; a caller-built pool can also be entered by
  the caller, which makes `open` fail (`client.py:335-339`); the resolved size is not readable from
  the pool (`client.py:290-299`), so a size check needs the store to build the pool or the pool to
  expose its limit.
- **Options:**
  1. **Caller pool, documented** — unchanged constructor; documentation requires at least 2
     connections and a pool used by nothing else.
  1. **Own pool, at least 2** — `PostgresTaskStore(dsn or settings, connections=...)` builds its
     pool; `ValueError` below 2 connections; the caller cannot enter it.
  1. **Reserved renewal connection** — the store holds one connection for renewals and serves the
     rest from the pool; works with any pool size of at least 2.
- **Recommendation:** none.

### OQ-8 — TEXT or JSONB columns (resolved)

Resolved by [D-16](#decisions).

- **Header:** JSON type
- **Question:** Should `argument`, `origin` and `result` stay `JSONB`, or become `TEXT` holding the
  JSON exactly as given?
- **Why it matters:** Blocks the schema, FR-21 and TDS-001 FR-63's U+0000 rule (review 2 M5). The
  store never queries inside the JSON; `JSONB` costs a parse per write, rejects `\u0000` (the only
  reason for TDS-001 FR-63's rule) and normalizes the text, and its `jsonb_typeof` checks are the
  only database-side validation.
- **Options:**
  1. **JSONB** — unchanged; the U+0000 rule stays.
  1. **TEXT** — exact text kept; the `jsonb_typeof` checks go; TDS-001 FR-63's U+0000 rule is
     withdrawn.
- **Recommendation:** none.

### OQ-9 — A schema newer than the store (resolved)

Resolved by [D-17](#decisions).

- **Header:** Newer schema
- **Question:** When `open` finds a schema version above the store's latest (a newer replica already
  migrated during a rolling deploy), should it open or refuse?
- **Why it matters:** Blocks FR-22. Refusing stops older replicas from restarting during a deploy;
  opening relies on every later migration staying compatible with older stores.
- **Options:**
  1. **Open** — later migrations are additive and compatible by rule, stated in the documentation.
  1. **Refuse** — `open` raises `PostgresException`; deploys migrate after the last old replica
     stopped.
- **Recommendation:** none.

### OQ-6 — Attempts interrupted by a crash (resolved)

Resolved by [D-7](#decisions).

- **Header:** Crash count
- **Question:** When a runner stops without closing (a crash, or a lost connection beyond the lease)
  and another runner resumes the task, should the interrupted attempt be left uncounted, as a close
  leaves it (TDS-001 D-56), or be counted as a failed attempt under the retry policy?
- **Why it matters:**
  - Blocks FR-7, and under option 2 the failure union of TDS-001 FR-20.
  - A task that crashes its own process (memory exhaustion, for example) is resumed without end
    under option 1.
  - Under option 2 the failure has no exception: it needs a new failure variant in TDS-001.
- **Options:**
  1. **Not counted** — the attempt number stays; no attempt row; a task that kills its process runs
     again after every lease expiry.
  1. **Counted, new failure** — an attempt row with failure kind `interrupted`; TDS-001 gains a
     `TaskInterrupted` failure; the retry policy decides, so with no retries the task fails.
- **Recommendation:** none.

### OQ-1 — Which runner runs a stored task (resolved)

Resolved by [D-1](#decisions).

- **Header:** Claim model
- **Question:** Should any open runner of the database claim any due stored task whose registered
  name it knows (a shared work queue), or should a runner run only the tasks it spawned, with other
  runners taking over a task only once its lease has expired?
- **Why it matters:** Blocks FR-5, FR-7, FR-10. A shared queue spreads tasks across processes, so a
  task can run in a process other than the one whose ManagedTasks spawned it; the in-memory
  implementation never does that, and TDS-001's concurrency limit then counts per runner.
- **Options:**
  1. **Shared work queue** — every runner polls for due rows and claims them with
     `FOR UPDATE SKIP LOCKED`; a task runs wherever a runner is free.
  1. **Spawner first, takeover on expiry** — a runner runs what it spawned; other runners claim a
     row only when its lease has expired (a crashed or closed runner).
  1. **Takeover at open only** — as option 2, but orphaned rows are claimed only when a runner
     opens, not while it runs.
- **Recommendation:** none.

### OQ-2 — Lease and polling timings (resolved)

Resolved by [D-3](#decisions).

- **Header:** Lease times
- **Question:** What should the default lease duration, lease renewal interval and polling interval
  be?
- **Why it matters:** Blocks FR-7, FR-8, FR-13, NFR-1. The lease duration bounds how long an
  orphaned task waits before another runner resumes it; the renewal and polling intervals set the
  database load per runner.
- **Options:**
  1. **30/10/1 s** — lease 30 s, renewal 10 s, polling 1 s; orphans resumed within about 30 s.
  1. **60/20/5 s** — lease 60 s, renewal 20 s, polling 5 s; fewer statements; orphans resumed within
     about 60 s.
  1. **Required settings** — no defaults; every runner states its timings.
- **Recommendation:** none.

### OQ-3 — Installing the schema (resolved)

Resolved by [D-4](#decisions).

- **Header:** Schema setup
- **Question:** How should the task tables be created: by a `migrate()` method of the implementation
  with its own version table and advisory lock, as migration callables the application appends to
  its own `execute_migrations` sequence, or both?
- **Why it matters:** Blocks FR-1. `execute_migrations` versions one sequence by the row count of
  `migrations` (`state.py:478-486`), so haiway's tables cannot be a second sequence in it.
  `PostgresConfigurationRepository.migrate()` creates its table on its own (per CodeExplorer).
- **Options:**
  1. **Own `migrate()`** — the implementation versions its schema in a table of its own, under its
     own advisory lock; the application calls it at startup.
  1. **Application sequence** — haiway exports the migration callables; the application appends them
     to its sequence; later schema changes are new callables it appends.
  1. **Both** — the callables are exported, and `migrate()` runs them under its own version table.
- **Recommendation:** none.

### OQ-4 — Retention of finished rows (resolved)

Resolved by [D-5](#decisions).

- **Header:** Row cleanup
- **Question:** How long should finished task rows and their attempt rows stay in the database: the
  same retention period as the in-memory implementation (3600 s, TDS-001 D-20) with deletion by the
  runners, kept until the application deletes them, or a required retention setting?
- **Why it matters:** Blocks FR-15. Without deletion the tables grow with every task; lookup by
  identifier (TDS-001 FR-70) returns a finished task only while its row exists.
- **Options:**
  1. **3600 s, runners delete** — the default of TDS-001 D-20; runners delete rows finished longer
     ago than the period while polling.
  1. **Kept, application deletes** — no deletion by haiway; a documented statement deletes old rows.
  1. **Required setting** — every runner states its retention period; runners delete older rows.
- **Recommendation:** none.

### OQ-5 — Scope of stored tasks (resolved)

Resolved by [D-2](#decisions).

- **Header:** Task scope
- **Question:** Should stored tasks belong to a namespace given when the implementation is opened,
  so that runners claim and look up only tasks of their own namespace, or should every runner of a
  database share all stored tasks?
- **Why it matters:** Blocks FR-5, FR-10 and the schema. Without a namespace, two applications
  sharing one database, or two ManagedTasks blocks of one process, claim each other's tasks when
  they register the same name, and a task can run under another block's root snapshot and presets
  registry than the one it was spawned in (TDS-001 D-39).
- **Options:**
  1. **Required namespace** — a namespace string is a required setting, stored on every row; claims
     and lookups match it.
  1. **Optional namespace** — as option 1, with a default namespace shared by runners that give
     none.
  1. **Shared** — no namespace; registered names are expected to be unique per database.
- **Recommendation:** none.

## Unverified

- v1.1, D-40: the listing plans with `attempts` read from the task row were not run.
- v1.1, D-34: `greatest(statement_timestamp(), started_at)` for `ended_at` was not run on
  PostgreSQL.
- v1.1, D-33: its counted fence failed on PostgreSQL (D-35); D-35's stored fence was verified
  (D-39).
- v1.1: not run on PostgreSQL (D-30 lists what was): FR-23 acceptance 4; FR-24 acceptance 1 to 4, 6
  and 7 (the one-name plan on the distribution acceptance 6 names was not part of the run, whose
  distribution was not relayed); plans under the default `plan_cache_mode` of `auto`; data with many
  distinct names. D-30's run was relayed by the coordinator; I did not see it. That a locking
  `SELECT` under `READ COMMITTED` re-checks its `WHERE` on a row a concurrent transaction updated is
  from WebExplorer (PostgreSQL 16 and 18, "Read Committed Isolation Level", 2026-11-27); what
  follows a failed re-check is shown there only by example. The `OFFSET`, index-ordering and
  `timestamptz` facts are from WebExplorer, 2026-11-27.
- Facts marked "per CodeExplorer", not opened by me (listed inline).
- Facts marked "per Experimenter, 2026-11-27" come from the coordinator's relay of an Experimenter
  run of this document's v0.9 statements on PostgreSQL 18.7 and 16.16 (asyncpg 0.32.0,
  `READ COMMITTED`, autocommit): 56 deterministic checks per version, 8 rounds per version of 8
  concurrent claimers, the `EXPLAIN` checks of FR-5 acceptance 4, the `provolatile` classes, the
  single-statement `record` (prepared under the extended protocol, while a two-command control was
  rejected), `UPDATE ... FROM` a `WITH` query, and the FR-14 check violation of the mixed form. I
  did not see the run.
- Review 2 S4's timings (32.4 ms, 0.95 ms, 0.28 ms) are review 2's single measurements, not
  reproduced; the index conditions of FR-5 acceptance 4 were verified instead.
- The scan of `haiway_managed_tasks_leases` shows `status = 'running'` in its Filter despite the
  partial index, while the lease condition stays its Index Cond (per Experimenter); no cause is
  stated.
- Not tested by the run: `migrate()` (lock, version table, concurrent runs), FR-22's schema check at
  open, decoding into haiway types, the rejection of U+0000 (FR-21), FR-20 acceptance 1 (clock
  skew), the query of FR-10, and FR-15's delete and cascade.
- That asyncpg returns `JSONB` as text without a registered codec is inferred from the configuration
  repository's code, not run (per CodeExplorer).
- PostgreSQL documentation facts are from WebExplorer (2026-11-26 and 2026-11-27), quoted from
  postgresql.org as returned by its fetch step, not opened by me: `SKIP LOCKED` and the `WITH` rule
  (versions 17, 18, 19); `clock_timestamp()` (18, 19); `NaN` treated as greater than all non-`NaN`
  values (17, 18), from which the rejection of `NaN` by the `CHECK` constraints is inferred; a
  `CHECK` passing on `NULL` (18); `IS` binding looser than `=` (18), which the schema's explicit
  parentheses make irrelevant; session advisory locks held until released or the session ends (17,
  18); `jsonb` rejecting `\u0000` (18, relayed by the review); the `WITH`, `UPDATE` and `UNION`
  quotes of FR-5 and FR-12. PostgreSQL 19 is the current release
  (<https://www.postgresql.org/support/versioning/>).

## Traceability

| Requirement | Implements (TDS-001)  | Source                                                                                |
| ----------- | --------------------- | ------------------------------------------------------------------------------------- |
| FR-1        | FR-78                 | D-4, D-12                                                                             |
| FR-2        | FR-78                 | TDS-001 D-67, D-72, FR-78; AGENTS.md; D-35                                            |
| FR-3        | FR-65, FR-78          | TDS-001 FR-65, FR-78; D-19; D-34                                                      |
| FR-4        | FR-45, FR-78, FR-85   | D-15, D-18; TDS-001 D-88                                                              |
| FR-5        | FR-78, FR-84          | D-1, D-11, D-13, D-20, D-22, D-32; TDS-001 FR-78, D-90, D-105, D-123; D-35, D-36      |
| FR-6        | FR-29, FR-78          | TDS-001 D-56, D-88, FR-29, FR-78, D-127; D-33; D-35, D-39                             |
| FR-7        | FR-78, FR-84          | D-7, D-13; TDS-001 FR-78, FR-84                                                       |
| FR-8        | FR-78, FR-83          | D-13, D-18; TDS-001 FR-78, FR-83                                                      |
| FR-9        | none                  | withdrawn, D-8                                                                        |
| FR-10       | FR-62, FR-70, FR-78   | D-1, D-2, D-25; TDS-001 D-96                                                          |
| FR-11       | FR-21, FR-70, FR-78   | D-5, D-13; TDS-001 FR-78                                                              |
| FR-12       | FR-66, FR-78, FR-82   | D-13, D-19, D-21; TDS-001 FR-66, FR-78, D-98, D-127; D-33; D-34; D-35, D-38, D-39     |
| FR-13       | none                  | withdrawn, D-8                                                                        |
| FR-14       | FR-26, FR-78, FR-86   | TDS-001 FR-26, FR-78, D-91; D-21                                                      |
| FR-15       | FR-70                 | D-5; TDS-001 FR-70                                                                    |
| FR-16       | none                  | withdrawn, TDS-001 D-89                                                               |
| FR-17       | FR-80, FR-82          | derived from AGENTS.md; TDS-001 FR-80, FR-82                                          |
| FR-18       | FR-78                 | derived from `state.py:381-400`, `client.py:637-699`, `scope.py:152-182`; D-15        |
| FR-19       | none                  | withdrawn, D-8                                                                        |
| FR-20       | FR-78                 | derived from D-1; D-11, D-22; D-34                                                    |
| FR-21       | FR-63                 | TDS-001 D-67, FR-63; D-16                                                             |
| FR-22       | FR-55                 | D-12, D-17                                                                            |
| FR-23       | FR-78, FR-93          | D-25, D-27, D-30; TDS-001 FR-78, FR-93, D-110, D-111, D-116; D-34; D-35, D-39         |
| FR-24       | FR-78, FR-94 to FR-98 | D-5, D-26, D-27, D-29, D-30; TDS-001 FR-95, FR-97, FR-98, D-109, D-112 to D-115; D-40 |
| NFR-1       | none                  | derived from TDS-001 FR-83, FR-85; D-19, D-37                                         |
| NFR-2       | NFR-6                 | AGENTS.md; D-15, D-17, D-18, D-25, D-26                                               |
| NFR-3       | NFR-5                 | AGENTS.md Testing; TDS-001 D-76, D-97                                                 |
| NFR-4       | NFR-7                 | AGENTS.md                                                                             |

## References

- haiway [TDS-001](TDS-001-managed-tasks.md), Managed tasks.
- asyncpg 0.32.0, `Connection.get_reset_query`
  (`.venv/lib/python3.15/site-packages/asyncpg/connection.py:1763-1790`).
- PostgreSQL 18 documentation, per WebExplorer (2026-11-26):
  <https://www.postgresql.org/docs/18/sql-select.html> (locking clause, `SKIP LOCKED`),
  <https://www.postgresql.org/docs/18/functions-datetime.html> (`clock_timestamp()`),
  <https://www.postgresql.org/docs/18/datatype-numeric.html> (`NaN`),
  <https://www.postgresql.org/docs/18/datatype-json.html> (`jsonb` and `\u0000`),
  <https://www.postgresql.org/docs/18/ddl-constraints.html> (`CHECK`),
  <https://www.postgresql.org/docs/18/sql-syntax-lexical.html> (operator precedence),
  <https://www.postgresql.org/docs/18/explicit-locking.html> (advisory locks); the same `SELECT` and
  date/time text in version 19.
- Tom Lane, "Re: Inexplicable UPDATE...RETURNING behaviour", pgsql-general, 2019-04-15:
  <https://www.postgresql.org/message-id/25988.1555372729%40sss.pgh.pa.us> (per WebExplorer,
  2026-11-27).
- PostgreSQL 18, `WITH` queries and `UPDATE`:
  <https://www.postgresql.org/docs/18/queries-with.html>,
  <https://www.postgresql.org/docs/18/sql-update.html> (per WebExplorer, 2026-11-27).
- PostgreSQL 18, function volatility categories:
  <https://www.postgresql.org/docs/18/xfunc-volatility.html> (per WebExplorer, 2026-11-27).
- RFC 2119, RFC 8174 (BCP 14).
