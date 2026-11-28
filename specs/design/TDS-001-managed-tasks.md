# TDS-001 — Managed tasks

Status: accepted

- Kind: technical design specification
- Version: 1.6, revision of the accepted v1.5 (default registered name, listing of stored tasks,
  failing unsupported tasks: D-108 to D-111; answers to OQ-53 to OQ-58 as D-112 to D-117; D-118 to
  D-121; accepted, D-122; implementation-review clarifications D-123 to D-126 and corrections D-127
  to D-130, runtime-design clarifications D-131 to D-140 and D-141 to D-145, status kept). No
  question is open. v1.5 (ad-hoc tasks and `ctx.spawn_background` removed; review 2 fixes; D-77 to
  D-87; answers to OQ-41 to OQ-52 as D-88 to D-99; v1.4: third review fixes, D-100 to D-106; v1.5:
  finalized, D-107).
- Date: 2026-11-28
- Author: not stated in the brief
- Grounded at: haiway ecb86c8 (clean working tree; `specs/` untracked, added by this change); draive
  5080cbc and draive-platform 6e01b8f for the downstream code (the cited source and test files are
  unmodified in both working trees); draive's specifications are untracked there (see Consumer)
- Store implemented by: [TDS-002](TDS-002-postgres-managed-tasks.md) (PostgreSQL task store).
- Consumer: draive TDS-004 "Deferred tool results", section "Required from haiway managed tasks",
  items HM-1 to HM-13 of its v0.8 (`draive/specs/design/TDS-004-deferred-tools.md:1138-1159`), and
  draive TDS-003 "MCP Tasks extension" FR-30, FR-31 and OQ-9
  (`draive/specs/design/TDS-003-mcp-tasks.md:688-733`, `:971-995`). Cited as plain text, per D-19;
  read from draive's working tree, where `specs/` is untracked, so not part of draive 5080cbc.
  `HM-n` alone means draive TDS-004 HM-n (D-101).

## Purpose

This document specifies managed tasks for haiway. A managed task runs out of the call context that
spawns it: it outlives the spawning scope, and every attempt builds its context fresh, as a separate
subtree whose root is the ManagedTasks runtime's root snapshot and presets registry (D-39, D-68). It
is linked to the spawning trace where the origin is known (D-41, D-67), isolated from every other
managed task (D-49), and retried under a retry policy (D-47). Its status and outcome are read
through its handle and the ManagedTasks API (D-42).

There is one runtime (D-66). It owns scheduling, the queue, concurrency, retries, time limits,
attempt scopes, telemetry links, close and the handle API. The only part that can be swapped is the
task store, which holds what D-67 lists and nothing else: haiway ships an in-memory store, the
default, and a PostgreSQL store ([TDS-002](TDS-002-postgres-managed-tasks.md)); custom stores
implement the same protocol. Only the store-dependent behaviors listed in FR-80's table differ
between stores; everything else is the same (D-75, narrowed by D-88).

Every managed task is a registered task: stored, shared by every runtime on one store, retried and
resumed (D-77). Stored tasks can be listed with filters (D-109), and a runtime can be set to fail
due tasks it cannot run (D-110). `ctx.spawn_background` and the background task group are removed
(D-78).

The framing principle of D-32 holds: each managed task is a separate entrypoint, a task tree of its
own, whose lifetime the runtime owns.

Its readers are the haiway maintainers, the implementers of task stores, and the draive maintainers
who consume it.

## Normative language

The key words MUST, MUST NOT, REQUIRED, SHALL, SHALL NOT, SHOULD, SHOULD NOT, RECOMMENDED, NOT
RECOMMENDED, MAY and OPTIONAL in this document are to be interpreted as described in BCP 14 (RFC
2119, RFC 8174) when, and only when, they appear in all capitals.

## Glossary

Terms no longer used (task manager, manager owner, manager scope, completion callback, supervisor
stage, implementation, persistent implementation, spawn state, spawn disposables, live and restored
outcome, ad-hoc task, task call of an ad-hoc task, background task) appear only in withdrawn
requirements, superseded decisions, resolved questions, and rationale explaining their removal.

| Term               | Definition                                                                                                                                                                                                              |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| ManagedTasks       | The `State` through which code spawns, looks up and checks managed tasks; its functions are bound to one open runtime.                                                                                                  |
| Runtime            | The one implementation of managed tasks (D-66): a `TaskRuntime` opened by a ManagedTasks block, with its settings, the attempts it runs and its store session (FR-80, FR-81).                                           |
| ManagedTasks block | The body of `async with ctx.managed_tasks(runtime)` (FR-55), or the same block entered by `ServerContext` (FR-35).                                                                                                      |
| Owner              | The `ManagedTasks` of the runtime running an attempt.                                                                                                                                                                   |
| Task store         | An object satisfying the `TaskStore` protocol (FR-78): `InMemoryTaskStore` (FR-79), TDS-002's store, or a custom one. The only extension point (D-66).                                                                  |
| Store session      | The `TaskStoreSession` a store yields when a runtime opens it; its operations are those of FR-78.                                                                                                                       |
| Runner id          | A `UUID` a runtime draws at open, identifying its leases in the store.                                                                                                                                                  |
| Store coordination | How a store tells runtimes when to act: `LeasedCoordination` with the store's lease timings, or `LocalCoordination`, which wakes runtimes on every change (FR-78, FR-85, D-88).                                         |
| Lease              | A runtime's hold on a running task in a store with lease timings, expiring on the store's clock unless renewed; in the in-memory store a lease never expires (FR-78, FR-83, D-88).                                      |
| Claim              | Taking due tasks from the store for a runtime, atomically, under a lease (FR-78, FR-85).                                                                                                                                |
| Root snapshot      | What a block captures when entered: the `ContextState` instances and the presets registry current at that moment (FR-56).                                                                                               |
| Spawning scope     | The haiway scope current when a task is spawned. Nothing of its state reaches the task (D-40, D-68).                                                                                                                    |
| Origin             | The trace position of the spawning scope at the spawn call, held as an `ObservabilityLink` and stored with a registered task (FR-12, FR-87).                                                                            |
| Registered task    | A coroutine function registered with `managed_task(...)`, taking one `State` argument and returning a `State` or `None` (FR-62, FR-76); stored, retried and resumed. The only kind of managed task (D-77).              |
| Registered name    | The string a registered task is known by: the `name` given to `managed_task`, or else the function's `__name__` (FR-91, D-108); never an import path (D-46). Unique per runtime (FR-45) and per database (TDS-002 D-2). |
| Unlisted name      | A registered name of a stored task that the runtime in question does not list in `TaskRuntime(tasks=...)` (FR-62, FR-70).                                                                                               |
| Unsupported task   | A due stored task with an unlisted name, as a runtime with `unsupported="fail"` sees it; such a runtime fails it with `TaskUnsupported` (FR-92, FR-93).                                                                 |
| Task filter        | A `TaskFilter`: the conditions on registered name, status or outcome, and creation and finish time that a listed task meets (FR-95).                                                                                    |
| Task summary       | A `TaskSummary`: what a listing returns per task: identifier, registered name, status, attempt count and listing timestamps (FR-98).                                                                                    |
| Page token         | The opaque string a listing returns for its next page: the JSON of the `TaskKeyset` of the last task listed, its position in the listing order (FR-97).                                                                 |
| Listing timestamps | The creation and finish times a store keeps for listing, in UTC: the server clock in TDS-002, the process wall clock in `InMemoryTaskStore` (FR-96).                                                                    |
| Task call          | A registered task bound to its encoded argument, ready to be spawned: `RegisteredTaskCall` (FR-62, D-80).                                                                                                               |
| Stored task        | What a store holds for a registered task: `StoredTask` (D-67, [Data model](#data-model)).                                                                                                                               |
| Task scope         | The root scope of one task in one runtime, entered in the task context; it spans the task's time in that runtime (FR-74).                                                                                               |
| Attempt scope      | The scope nested in the task scope in which one attempt runs, named like the task, which resolves the task's preset (FR-74, D-60).                                                                                      |
| Runtime scope      | The root scope in which a runtime's claim rounds, renewals, recoveries and their records run, through the runtime's backend (FR-88, D-87).                                                                              |
| Task context       | The new `contextvars.Context` a task runs in, holding exactly the task's `ContextState` and the snapshot's presets registry (FR-10).                                                                                    |
| Task tree          | The structured-concurrency tree rooted at a task scope (FR-54).                                                                                                                                                         |
| Task state         | The state of an attempt scope, in the priority order of FR-9.                                                                                                                                                           |
| Attempt            | One counted try of a task, numbered from 1 (D-47): a run of its function in its own attempt scope, or the attempt `fail_unsupported` records for a task failed as unsupported, which runs nothing (FR-93).              |
| Due moment         | When an attempt may be claimed: the spawn for attempt 1, the end of the retry delay for a later attempt, the release for an attempt left resumable (FR-29, FR-33).                                                      |
| Retry policy       | A `TaskRetryPolicy`: the delays before each retry; its length is the number of retries (FR-66).                                                                                                                         |
| Task handle        | The `ManagedTask` returned by a spawn or a lookup (FR-70).                                                                                                                                                              |
| Task status        | `queued`, `running`, `retry_pending` or `finished` ([Data model](#data-model)).                                                                                                                                         |
| Outcome            | The final result of a task: `TaskCompleted`, `TaskFailed` or `TaskCancelled` (FR-20).                                                                                                                                   |
| Failure            | Why an attempt failed: `TaskFailureRecord`, `TaskTimeLimitExceeded`, `TaskInterrupted` (the retryable `AttemptFailure`), or `TaskUnsupported` (FR-20, FR-93).                                                           |
| Time limit         | A duration in seconds per attempt, counted from the claim that starts the attempt; exceeding it fails the attempt (D-48, D-89, FR-27).                                                                                  |
| Grace period       | A duration in seconds for which a close lets running attempts finish before cancelling them (D-10, FR-30).                                                                                                              |
| Retention period   | A duration in seconds for which `InMemoryTaskStore` keeps a finished task (D-11, FR-32).                                                                                                                                |
| Concurrency limit  | The largest number of attempts one runtime runs at the same time; unlimited by default (D-12, D-21, D-90, FR-33).                                                                                                       |

## Decisions

| ID    | Question                 | Decision                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | Source                                                                                         | Date       |
| ----- | ------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------- | ---------- |
| D-1   | OQ-1                     | Superseded by D-22 for managed tasks. Option 1, raise when used after exit: `ctx.state(T)` returns the state while its scope is open, and raises a new `ContextStateDisposed` (a subclass of `ContextStateMissing`) once that scope has exited.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | user                                                                                           | 2026-11-26 |
| D-2   | OQ-2                     | Option 2, new trace with a link: the task gets a new trace id; in OpenTelemetry its span carries a `Link` added at creation; the logger records the origin trace id and scope id once; request traces end with the request.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | user                                                                                           | 2026-11-26 |
| D-3   | OQ-3                     | Superseded by D-42. Option 1, per-task async callback given at start; manager exit waits for it; a failure in the callback is logged and changes nothing else.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | user                                                                                           | 2026-11-26 |
| D-4   | OQ-4                     | Option 1, class methods on the manager (`TaskManager.spawn(...)`, `TaskManager.available()`, or names this document specifies). The disposable of D-4 is superseded by D-39; class methods on the state remain.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | user                                                                                           | 2026-11-26 |
| D-5   | OQ-5                     | Option 1, raise `ContextStateMissing` and start nothing.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | user                                                                                           | 2026-11-26 |
| D-6   | OQ-6                     | Option 2, re-resolve by name: fresh preset resources are entered for each task. Made the primary source of task state by D-22.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | user                                                                                           | 2026-11-26 |
| D-7   | OQ-7                     | Superseded in part by D-77: with no ad-hoc tasks, every scope is named by the registered name (FR-16). Option 2, the function's qualified name, kind `internal`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                                           | 2026-11-26 |
| D-8   | OQ-8                     | Option 1, a UUID that is not a credential; draive generates its own credential-grade MCP task ids.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | user                                                                                           | 2026-11-26 |
| D-9   | OQ-9                     | Option 1, per-task time limits only. Its outcome `TaskTimedOut` is superseded by D-48; per-task limits remain.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | user                                                                                           | 2026-11-26 |
| D-10  | OQ-10                    | Option 2, a grace period setting, 0 s by default; tasks still running when it elapses are cancelled.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | user                                                                                           | 2026-11-26 |
| D-11  | OQ-11                    | Option 3, lookup by identifier plus retention: finished tasks are kept for a retention period in seconds; this document specifies the default and what happens after it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | user                                                                                           | 2026-11-26 |
| D-12  | OQ-12                    | Superseded in part by D-89: where the limit counts from; the limit and its queue stay (OQ-42, review 2 S1 and M1). Option 3, a limit plus a queue: extra tasks wait for a slot, and their time limit counts from the start call; this document specifies the default limit, queue order, cancelling a queued task, and its effect on HM-1, HM-7, HM-8.                                                                                                                                                                                                                                                                                                                                                                                                                                                   | user                                                                                           | 2026-11-26 |
| D-13  | OQ-13                    | Option 1, listed by the application. Its mechanism (a listed disposable) is superseded by D-39; FR-35 keeps the opt-in.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                                           | 2026-11-26 |
| D-14  | OQ-14                    | Superseded by D-43. Option 1, keep `ctx.spawn_background` unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | user                                                                                           | 2026-11-26 |
| D-15  | OQ-15                    | Option 1, allowed: the task captures partly prepared state; this document specifies exactly what is captured. Narrowed by D-22: only the origin is captured.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | user                                                                                           | 2026-11-26 |
| D-16  | OQ-16                    | Superseded by D-42. Option 2, owner scope: the callback runs in a new root scope whose state holds only the manager, with the manager's observability backend.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | user                                                                                           | 2026-11-26 |
| D-17  | OQ-17                    | Option 1, an empty `contextvars.Context`; only haiway's own state is passed in.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | user                                                                                           | 2026-11-26 |
| D-18  | OQ-18                    | Superseded by D-39. Option 2, the manager exits first: `ctx.scope` and `ServerContext` treat the manager's disposable specially and exit it before the others; the mechanism is specified here, or raised as a question if the design needs one.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                                           | 2026-11-26 |
| D-19  | review                   | Documents of draive are cited as plain text with their path in code formatting, not as relative links.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | caller                                                                                         | 2026-11-26 |
| D-20  | D-11                     | The default retention period is 3600 s, the default `ttlMs` of draive's MCP tasks server (draive TDS-003 D-11: 3,600,000 ms).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | caller, delegated to this document                                                             | 2026-11-26 |
| D-21  | D-12                     | Kept by D-90. Challenged by review 2 S5 (OQ-43). No concurrency limit by default; queued tasks start in the order of their `TaskManager.spawn` calls.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | caller, delegated to this document ("unset or unlimited, unless you raise a question")         | 2026-11-26 |
| D-22  | round 2 correction       | "about empty context and presets - since detached tasks work out of current context we can't carry on contextually captured state - although we can utilize scope presets to make 'from scratch', detached context state": a managed task captures none of the starting scope's state; it runs in an empty `contextvars.Context` (D-17) and its state comes from entering scope presets, with fresh state and disposables owned by the task scope; D-6 becomes the primary mechanism.                                                                                                                                                                                                                                                                                                                    | user                                                                                           | 2026-11-26 |
| D-23  | OQ-22                    | Superseded by D-60. Presets are spawn arguments: a sequence of `ContextPresets` given to `TaskManager.spawn`; no registry lookup by name and no manager default (draive keeps its own default and passes it).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | derived from draive TDS-004 HM-2 (revised), forwarded by the caller                            | 2026-11-26 |
| D-24  | OQ-23                    | Its ad-hoc part is moot by D-77. Superseded by D-68 (FR-9 applies it to ad-hoc tasks too). Explicit `State` instances are accepted by `TaskManager.spawn` and are part of the task state.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | derived from draive TDS-004 HM-2 (revised), forwarded by the caller                            | 2026-11-26 |
| D-25  | OQ-25                    | A preset preparation failure is the task's outcome, distinguishable from a failure of the task body; never raised at spawn.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | derived from draive TDS-004 HM-3 (revised), forwarded by the caller                            | 2026-11-26 |
| D-26  | OQ-24                    | Option 2, the manager's backend: one backend serves all tasks and callbacks, configured once on the manager.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | user                                                                                           | 2026-11-26 |
| D-27  | OQ-19                    | Superseded by D-39. Option 1, a stage protocol: a runtime-checkable sub-protocol of `Disposable`; `Disposables`, `ctx.scope`, `ctx.disposables` and `ServerContext` detect it with `isinstance`, enter such disposables after all others and exit them before the others; the application lists the manager like any other disposable.                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | user                                                                                           | 2026-11-26 |
| D-28  | OQ-26                    | Option 1, included: the `TaskManager` the task was started through is in the task scope's state at the lowest priority. Superseded in part by D-39: FR-9 and FR-58 restate the priority of the owning ManagedTasks.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-26 |
| D-29  | OQ-20                    | Superseded by D-42. Option 1, link to the origin: the callback scope and the scope recording a queued task's end both get a new trace with one link to the task's origin, recorded as in FR-14 and FR-15.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | user                                                                                           | 2026-11-26 |
| D-30  | OQ-27                    | Amended by D-70 and D-85: the outcome is `TaskFailed` with a `TaskFailureRecord` naming the release error's type, not the error itself. Option 1, body failure: the outcome is `TaskFailed` carrying the release error; the returned value is lost.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-26 |
| D-31  | review                   | Superseded by D-91. Kept by D-82: a registered task's cancel always reaches the store, so `False` means only "already finished"; a richer result is open (OQ-44). `ManagedTask.cancel()` returns `bool`: `True` when this call caused the cancellation of a queued or running task, `False` when the task had already finished.                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | caller, from the draive review; draive TDS-004 HM-6 (v0.6)                                     | 2026-11-26 |
| D-32  | principle                | "we kind of have structured concurrency in haiway right? I believe in that model managed tasks must be treated as kind of separate entrypoint/task tree which has separate lifetime (although we will link it to root scope so it won't be fully separate)". Clarified: the lifetime anchor is any enclosing scope the manager is entered in (root or nested), whose exit cancels the trees; the trace link points to the starting scope.                                                                                                                                                                                                                                                                                                                                                                | user                                                                                           | 2026-11-26 |
| D-33  | review                   | The default and `Logger`-wrapped manager backends use `debug_context=False`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | main agent                                                                                     | 2026-11-26 |
| D-34  | review                   | haiway TDS-001 owns the mapping of draive TDS-004 HM items to requirements; draive points to its Traceability table.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | main agent                                                                                     | 2026-11-26 |
| D-35  | review                   | Superseded by D-85 (review 2 M1): FR-40 re-raises interpreter exits with nothing recorded, and `TaskTimedOut` no longer exists (D-48). On `KeyboardInterrupt` or `SystemExit` the runner fixes `TaskFailed(exception)` synchronously, then re-raises the exception unchanged. A `BaseExceptionGroup` from the task scope: any non-cancellation error gives `TaskFailed` with the group; a group of cancellations only gives `TaskCancelled`, or `TaskTimedOut` when the time limit caused it.                                                                                                                                                                                                                                                                                                            | main agent                                                                                     | 2026-11-26 |
| D-36  | review                   | Superseded by D-43. FR-36 keeps `ctx.spawn_background` and its documentation unchanged (D-14); managed-task documentation can mention it as "see also".                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | main agent                                                                                     | 2026-11-26 |
| D-37  | review                   | A value lost to a `cancel()` that arrives after the coroutine returned, while presets are released, is logged at WARNING.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | main agent                                                                                     | 2026-11-26 |
| D-38  | review                   | Superseded by D-39. State of a supervisor disposable keeps the priority of its group: one from a preset stays at preset priority, below the scope's disposables.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | main agent                                                                                     | 2026-11-26 |
| D-39  | U1, round 3              | ManagedTasks is its own `ctx` API, outside scope disposables (chosen over the supervisor-stage disposable), entered as a dedicated `async with ctx.<...>(...)` block. When entered it snapshots its parent context's state (`ContextState`) and presets (presets registry) as the root context of every task it spawns.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                                           | 2026-11-26 |
| D-40  | U2, round 3              | Superseded in part by D-68: no spawn state or spawn disposables. A managed task is spawned out of its call context: it does not inherit the caller's scope state. Its state is the ManagedTasks snapshot, presets, and all disposables and state passed explicitly at spawn.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | user                                                                                           | 2026-11-26 |
| D-41  | U3, round 3              | Tasks get links: the spawning span is linked to the task's inner span (a new trace with a link); keep what works of the existing OpenTelemetry and logger link design.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | user                                                                                           | 2026-11-26 |
| D-42  | U4, round 3              | `on_completion` is removed entirely. Outcomes and status are accessed through the task handle and the ManagedTasks API (lookup by identifier, status, outcome, cancel).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                                           | 2026-11-26 |
| D-43  | U5, round 3              | Superseded by D-78. `ctx.spawn_background` is removed as a separate mechanism; the `ctx` API stays as a proxy to the ManagedTasks in context, and raises `ContextStateMissing` when there is none. `BackgroundTaskGroup` and `ctx.shutdown_background_tasks` are removed as appropriate; a breaking change, on which nothing released in `src/` depends.                                                                                                                                                                                                                                                                                                                                                                                                                                                 | user                                                                                           | 2026-11-26 |
| D-44  | U6, round 3              | Narrowed by FR-61: queued and retry-pending tasks the closing runtime was not running stay in the store for any runtime, are not cut off, and are not reported. Superseded in part by D-66: the in-memory implementation becomes the in-memory store; its close records apply to the one runtime. A default in-memory implementation is provided. Closing it with tasks in progress, pending or queued logs errors (no secrets, no arguments), and closing the ManagedTasks block closes (cancels) its tasks.                                                                                                                                                                                                                                                                                            | user                                                                                           | 2026-11-26 |
| D-45  | U7, round 3              | Superseded by D-66. Custom implementations with persistence are possible; the protocol is the extension point. A PostgreSQL implementation is provided (TDS-002): it stores tasks and statuses and allows resuming them.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | user                                                                                           | 2026-11-26 |
| D-46  | U8, round 3              | Amended by D-108: without `name`, the registered name is the function's `__name__`. Resumable task identity: functions are registered under a stable registered name (for example a decorator or registry), and arguments are haiway `State`s stored as JSON, validated on resume. Not import paths.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | user                                                                                           | 2026-11-26 |
| D-47  | U9, round 3              | Task failures are recorded, and retries are allowed under a retry policy owned by the ManagedTasks implementation.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | user                                                                                           | 2026-11-26 |
| D-48  | U10, round 3             | A task past its time limit is failed, not a separate "timed out" outcome, and can be retried under the retry policy.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | user                                                                                           | 2026-11-26 |
| D-49  | U11, round 3             | Failures are isolated: one task's failure never affects any other managed task.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | user                                                                                           | 2026-11-26 |
| D-50  | U12, round 3             | Its open point is settled by D-56. Closing the ManagedTasks block closes its tasks: the in-memory implementation cancels them; for a persistent implementation, what happens to stored running tasks (left resumable or marked) was left open.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | user                                                                                           | 2026-11-26 |
| D-51  | OQ-28                    | Superseded by D-77. Registered and ad-hoc: two spawn forms; ad-hoc tasks run in-process only and are never resumed; `ctx.spawn_background(fn, *args, **kwargs)` keeps its shape.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                                           | 2026-11-26 |
| D-52  | OQ-29                    | Superseded by D-70. Live, with a record after restart: in-process outcomes carry the live exception; the outcome union gets a restored variant for outcomes read back after a restart.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | user                                                                                           | 2026-11-26 |
| D-53  | OQ-30                    | Superseded by D-68. Explicit States are stored as JSON and re-applied; disposables are for ad-hoc tasks only.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | user                                                                                           | 2026-11-26 |
| D-54  | OQ-31                    | The policy is set per registered task: the implementation default can be overridden per registered task, and the override is stored with the task.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | user                                                                                           | 2026-11-26 |
| D-55  | OQ-32                    | No retries by default; once retries are configured, preparation failures are retried too.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | user                                                                                           | 2026-11-26 |
| D-56  | OQ-33                    | Narrowed by D-88 and D-100: the in-memory store cancels running tasks at close instead. On close, stored running, queued and retry-pending tasks are left resumable, and the interrupted attempt is not counted.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                                           | 2026-11-26 |
| D-57  | OQ-34                    | Await the handle: no new haiway API; draive watches each handle itself. The impact on draive HM-7, HM-11, HM-12 and HM-13 is recorded under Conflicts.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | user                                                                                           | 2026-11-26 |
| D-58  | OQ-35                    | `ctx.spawn` and `ContextTaskGroup.run` outside any scope raise `ContextMissing`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | user                                                                                           | 2026-11-26 |
| D-59  | OQ-36                    | Time-only retention; the memory growth is documented (retained exceptions keep their frames alive).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-26 |
| D-60  | OQ-37                    | Registry only: spawn has no presets argument; a task's preset is the registry entry named like its task scope. D-23 is superseded.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | user                                                                                           | 2026-11-26 |
| D-61  | status                   | The status is `draft` while the body is unfinished, and `proposed` once complete; the catalog lifecycle rules over the earlier brief.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | caller (coordinator, relaying the user)                                                        | 2026-11-26 |
| D-62  | OQ-38                    | Simplified by D-70: `TaskFailureRecord` is the failure form, not a restored-only variant. Restored failure record: `TaskFailure` gains `TaskFailureRecord(kind, exception_type)`; completed, cancelled and time-limit outcomes have one shape whether live or restored; `TaskInterrupted`, which has no exception, has the same shape live or restored.                                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                                           | 2026-11-26 |
| D-63  | OQ-39                    | Superseded by D-68. Declared per task (option 1, as drafted): `managed_task(name=..., state=(A, B))`; spawn rejects other types with `TypeError`; stored elements are keyed by their declared position.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                                           | 2026-11-26 |
| D-64  | async API                | Its ad-hoc and `ctx.spawn_background` parts are superseded by D-77 and D-78; every remaining operation stays a coroutine. Everything async: ad-hoc spawn becomes async too, for one uniform API, and `ctx.spawn_background` becomes async and returns the handle rather than an `asyncio.Task`. Overrides the synchronous `spawn_ad_hoc`, `available` and `ctx.spawn_background` derived in v0.7.                                                                                                                                                                                                                                                                                                                                                                                                        | user                                                                                           | 2026-11-26 |
| D-65  | TDS-002 OQ-6             | Counted, as a new failure: an attempt row of kind `interrupted`, and a new `TaskInterrupted` failure here, carrying no exception; the retry policy decides, and with no retries the task fails.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | user                                                                                           | 2026-11-26 |
| D-66  | round 4, A1              | Narrowed by D-88: the in-memory store has no lease timings and cancels at close. Challenged for the in-memory store by review 2 S7 (OQ-41). One runtime implementation of ManagedTasks: scheduling, the queue, concurrency, retries, time limits, attempt scopes, telemetry links, close behavior and the handle API. The only extension point is a store protocol, narrow and focused; haiway ships an in-memory store (the default) and a PostgreSQL store; custom stores implement the same protocol; every behavior other than durability is identical across stores. User: "I would like to have single implementation for this the only part we can swap is store - in-memory by default, PostgreSQL we provide or custom according to interface, all the rest should work the same all the time." | user                                                                                           | 2026-11-26 |
| D-67  | round 4, A2              | Amended by D-72: the origin link is stored too. A stored task holds only: the registered function name, the argument State (JSON), the status and attempts, the retry policy, the time limit, and the result or failure record. Nothing else of the context is stored.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | user                                                                                           | 2026-11-26 |
| D-68  | round 4, A3              | Kept by D-95. Every attempt, retries and tasks claimed from the queue included, builds its context fresh: a separate subtree whose root is the manager's root (the ManagedTasks snapshot and its presets registry, as they are at that attempt). Context changes come only from code inside the function or from the serializable argument State. Explicit spawn state and spawn disposables of registered tasks are removed; presets come from the registry only (D-60 stays).                                                                                                                                                                                                                                                                                                                          | user                                                                                           | 2026-11-26 |
| D-69  | round 4, A4              | Superseded by D-77. Ad-hoc tasks are kept as runtime-local: the same runtime, but they never reach the store, live only in this process, are never retried or resumed, and keep the `ctx.spawn_background` proxy (async, returns the handle); an accepted exception to "all the rest works the same", kept minimal.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-26 |
| D-70  | round 4, A5              | Registered functions return a State (or None). Failures are stored as kind plus exception type name; the live exception is only logged. Every store returns identical outcomes, so a local handle and a handle read back from the store see the same outcome. Supersedes D-52 and simplifies D-62: `TaskFailureRecord` is the failure form for registered tasks, not a restored-only variant.                                                                                                                                                                                                                                                                                                                                                                                                            | user                                                                                           | 2026-11-26 |
| D-71  | OQ-40                    | Superseded by D-88. Challenged by review 2 S7 (OQ-41). Runtime settings, required: `TaskRuntime(lease=..., lease_renewal=..., polling=...)` applies to every store, the in-memory default included; all three required, with no defaults; every store goes through the same paths. Supersedes TDS-002 D-3, which moves to the runtime.                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | user                                                                                           | 2026-11-26 |
| D-72  | round 5                  | Store the origin link: the stored task keeps the origin link (trace id, scope id, trace context), so every attempt links back to the spawning span whichever runtime runs it; it is telemetry metadata, not context state. Amends D-67 and resolves the D-41 against D-67 conflict.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-26 |
| D-73  | round 5                  | Superseded by D-77. Ad-hoc state: the derived choice is confirmed; ad-hoc tasks take arguments only, with no explicit state or disposables.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | user                                                                                           | 2026-11-26 |
| D-74  | review M1                | Its ad-hoc part is superseded by D-77; the registered part stays. A registered task that fails during the grace period of a close records its normal retry (`retry_pending`, resumable by another runtime or the next start); ad-hoc tasks are never retried, so they end `TaskFailed`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                                           | 2026-11-26 |
| D-75  | review M4                | Narrowed by D-88: FR-80's table also lists coordination and release at close. Retention counts as durability and is store-specific: each store states its own; in-memory drops finished tasks after its retention; PostgreSQL keeps rows until the application deletes them. One table lists every store-dependent behavior (retention, opening many times, ordering across runtimes, error types); everything else is identical.                                                                                                                                                                                                                                                                                                                                                                        | user                                                                                           | 2026-11-26 |
| D-76  | review M13               | Kept by D-97. Challenged by review 2 S13 (OQ-50). No PostgreSQL CI job; the tests against PostgreSQL run only when a DSN is set locally.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | user                                                                                           | 2026-11-26 |
| D-77  | brief, round 6           | Ad-hoc tasks are removed entirely. Managed tasks are only registered tasks: a coroutine function decorated with `managed_task(name=...)`, taking exactly one positional `State` argument and returning a `State` or `None`. `AdHocTaskCall`, `ManagedTasks.spawn_ad_hoc`, the `AdHocTaskSpawning` protocol and field, ad-hoc records and `TaskRuntime(retention=...)`, and every ad-hoc branch are removed; FR-73 is withdrawn. Supersedes D-51, D-69 and D-73, the ad-hoc parts of D-24, D-64 and D-74, and D-7 in part.                                                                                                                                                                                                                                                                                | user                                                                                           | 2026-11-27 |
| D-78  | brief, round 6           | `ctx.spawn_background` is removed entirely, with `BackgroundTaskGroup`, `ContextTaskGroup.background_run` and `ctx.shutdown_background_tasks` (FR-68); FR-67 is withdrawn; a breaking change. Supersedes D-43. Correction: D-43's "nothing released in `src/` depends on it" was checked only in haiway's `src/`; released downstream code calls it (Compatibility and migration).                                                                                                                                                                                                                                                                                                                                                                                                                       | user                                                                                           | 2026-11-27 |
| D-79  | brief, round 6           | The fixes of the second independent design review ("review 2") are applied to what remains; a fix contradicting a recorded decision is returned as an open question with its costs; draive TDS-004 items served only by ad-hoc tasks are recorded as not met, and ad-hoc tasks are not reintroduced.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | user                                                                                           | 2026-11-27 |
| D-80  | review 2 B3              | `RegisteredTaskCall[Result]` holds the registered name, the encoded argument and the result class, and drops `Argument`, so that a `RegisteredTask[A, R]` is assignable to the `RegisteredTask` type `TaskRuntime(tasks=...)` takes (with `Never` as argument and `State` or `None` as result) under pyright strict; `RegisteredTask.__call__` encodes and checks the argument (FR-63).                                                                                                                                                                                                                                                                                                                                                                                                                  | caller (review 2), derived: the brief names the fix                                            | 2026-11-27 |
| D-81  | review 2 B4              | Amended by D-127: a repeated `record` is idempotent only with the attempt check. A failed `record` is retried at every renewal tick while the lease keeps being renewed; a runtime may claim its own expired lease and discards that claim for an attempt it still runs; after a `record` error, local awaiters fall back to polling the store.                                                                                                                                                                                                                                                                                                                                                                                                                                                          | caller (review 2), derived: the brief names the fix; tied to `lease_renewal` to add no setting | 2026-11-27 |
| D-82  | review 2 S2              | A registered task's `cancel()` always calls the store's `cancel`, whatever the calling runtime requested locally; a cancel the store accepts wins at `record` and at `release`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | caller (review 2), derived: agrees with D-31                                                   | 2026-11-27 |
| D-83  | review 2 S6              | Outcome polling reads every polled task of a runtime in one store `read` of a set of identifiers per polling tick; renewal of a task continues until its `record` returns.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | caller (review 2), derived: the brief names the fix                                            | 2026-11-27 |
| D-84  | review 2 S9              | `record` takes one sum type `AttemptEnd` (completed with result, failed and retried with failure and delay, failed for good with failure, cancelled); `StoredTask` validates its invariants at construction; `StoredAttempt.number` is dropped (the position is the number); `LeaseRenewal` is one mapping from identifier to lease state; retention is set only on `InMemoryTaskStore`.                                                                                                                                                                                                                                                                                                                                                                                                                 | caller (review 2), derived: AGENTS.md Golden Rule                                              | 2026-11-27 |
| D-85  | review 2 M1              | Stale decisions resolved: D-35 is superseded by FR-40; D-30 is read with D-70 (a release error after a returned value gives a `raised` failure naming its type); D-12's counting is open (OQ-42); FR-67's rationale is moot with FR-67 withdrawn.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        | caller (review 2), derived from D-48, D-70, D-78                                               | 2026-11-27 |
| D-86  | review 2 M2              | The task scope is entered with an explicit empty preset so it resolves no registry entry; the root snapshot reads the registry through the private `ContextPresetsRegistry._context` inside haiway; `ctx.managed_tasks` yields its `ManagedTasks`; `ServerContext` enters `ctx.updating` and `ctx.presets` around the block so the snapshot holds its state and presets.                                                                                                                                                                                                                                                                                                                                                                                                                                 | caller (review 2), derived: feasibility on `scope.py:84-89`, `presets.py:265-280`              | 2026-11-27 |
| D-87  | review 2 M3              | A runtime runs its claim rounds, renewals, recoveries and their records in a runtime scope of its own, recorded through its backend, instead of outside any scope (root logger).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | caller (review 2), derived: `observability.py:862-875`                                         | 2026-11-27 |
| D-88  | OQ-41                    | Option 2, timings with the store: a store that needs leases takes the three timings (TDS-002 store constructor); the in-memory store has none and never expires a lease; at close it fixes `TaskCancelled` for running tasks and their handles return it. Supersedes D-71, narrows D-66.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | user                                                                                           | 2026-11-27 |
| D-89  | OQ-42                    | Option 2, from the start: the limit counts from the claim; queue wait and downtime never count; `ClaimedAttempt.waited` and FR-41's "fails without running" are removed. Supersedes D-12 in part (where the limit counts from; D-12's limit and queue stay, D-85).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | user                                                                                           | 2026-11-27 |
| D-90  | OQ-43                    | Option 1, keep D-21: unlimited by default; documentation recommends a limit.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | user                                                                                           | 2026-11-27 |
| D-91  | OQ-44                    | Option 2, result type: `cancel` returns `Literal["cancelled", "pending", "finished"]`. Supersedes D-31.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | user                                                                                           | 2026-11-27 |
| D-92  | OQ-45                    | Option 1, document the limit: no change; FR-4 and the documentation warn; applications spawn after commit.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | user                                                                                           | 2026-11-27 |
| D-93  | OQ-46                    | Option 1, built by ServerContext: `ctx.managed_tasks` gains an optional fallback backend; `ServerContext` passes `observability(traceparent=None, tracestate=None)`; the runtime's own setting wins.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | user                                                                                           | 2026-11-27 |
| D-94  | OQ-47                    | Option 2, lenient, count: decode with `ignore_extra=True`; a remaining failure stays a preparation failure under the retry policy.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | user                                                                                           | 2026-11-27 |
| D-95  | OQ-48                    | Option 1, neither: keys travel in the argument State; D-68 unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | user                                                                                           | 2026-11-27 |
| D-96  | OQ-49                    | Option 2, status only: lookup returns a handle whose `status()` works and whose `outcome()` raises `ValueError` for an unlisted name; a handle whose task disappears raises `LookupError` naming the identifier; unlisted rows are documented with a query.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | user                                                                                           | 2026-11-27 |
| D-97  | OQ-50                    | Option 1, keep D-76: local runs only.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | user                                                                                           | 2026-11-27 |
| D-98  | OQ-51                    | Option 1, recorded as cancelled: as today.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | user                                                                                           | 2026-11-27 |
| D-99  | OQ-52                    | Option 2, bound by a duration, the grace period: `record` retries during a close continue until `grace_period` has elapsed from the close, then stop; the task is then recovered as interrupted. With the default `grace_period` of 0 s, retries stop at once; the requirement and documentation say so.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | user                                                                                           | 2026-11-27 |
| D-100 | review 3, 8              | A close always cancels the running tasks of an in-memory store, also when the store object is shared by other runtimes; this is deliberate, and the closing runtime's queued and retry-pending tasks remain for the other runtimes.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-27 |
| D-101 | review 3, 16             | In this document `HM-n` stands for draive TDS-004 HM-n, the items of draive TDS-004 "Required from haiway managed tasks"; every other draive identifier keeps its `draive` prefix.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | caller (review 3), form only                                                                   | 2026-11-27 |
| D-102 | review 3, 5              | When `ServerContext`'s observability setting is a `Logger` (the default included), the fallback backend it passes to `ctx.managed_tasks` is `LoggerObservability(logger, debug_context=False)`, as D-33 requires of manager backends; any other setting is called once as `ObservabilityPreparing`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | caller (review 3), derived from D-33, D-93                                                     | 2026-11-27 |
| D-103 | review 3, 6              | Amended by D-137: the cancelled wait's task is awaited until it ends. `LocalCoordination.changed(seen)` works on a change counter: it returns the store's current counter at once when it differs from `seen`, and otherwise waits for the next change or the earliest due moment; a close cancels a pending wait.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | caller (review 3), derived from D-88: neither an edge- nor a level-triggered wait is sound     | 2026-11-27 |
| D-104 | review 3, 7              | Only `InMemoryTaskStore` yields `LocalCoordination`; every other store yields `LeasedCoordination`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | caller (review 3), derived from D-88 ("absent for the in-memory store")                        | 2026-11-27 |
| D-105 | review 3, 23             | A runtime with no free slot still runs claim rounds with `limit=0`, which claim only expired leases, so a saturated runtime recovers interrupted attempts.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | caller (review 3), derived from FR-78 (expired leases need no slot) and FR-80 (shared queue)   | 2026-11-27 |
| D-106 | review 3, 15             | `StoredTask` checks its invariants in its own `__init__`, which calls the generated one first, so field validation stays and the check runs on construction and on every decoding; a `validate` override is not called on direct construction (probe, 2026-11-27).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | caller (review 3), derived from D-84 and a probe                                               | 2026-11-27 |
| D-107 | status                   | Amended by D-119 for v1.6. The design is finalized: status `accepted`; TDS-002's statements were verified on PostgreSQL 18.7 and 16.16 (TDS-002 D-20 to D-23).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | user, through the caller                                                                       | 2026-11-27 |
| D-108 | brief, round 7           | `managed_task` takes `name` as an optional override; when it is absent, the registered name is the function's `__name__`, the bare name, not `__qualname__` and not a module path. Renaming the function then changes the stored identity, and an explicit `name` keeps it stable. Amends D-46.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | user                                                                                           | 2026-11-27 |
| D-109 | brief, round 7           | Stored tasks can be listed beyond lookup by identifier: a `ManagedTasks` class method (for example `ManagedTasks.tasks(...)`) with filters on registered name, status or outcome, and created and finished time ranges, with pagination; it is backed by a new store session function, a small focused protocol like the others, implemented by `InMemoryTaskStore` (within its retention) and the PostgreSQL store, which keeps finished rows until the application deletes them (TDS-002 D-5).                                                                                                                                                                                                                                                                                                         | user                                                                                           | 2026-11-27 |
| D-110 | brief, round 7           | Entering the block starts executing due stored tasks at once (the claim round at open, FR-85, made explicit). A new opt-in runtime setting, `TaskRuntime(unsupported=...)` with `"leave"` (the default: today's behavior, unlisted names are never claimed) or `"fail"`: with `"fail"`, a due stored task whose registered name the runtime does not list is finished as failed with a new `TaskFailure` variant meaning "unsupported task", never retried, while a cancel request still wins; the default and the shared queue are unchanged; the documentation warns that enabling it with heterogeneous workers on one database fails the other workers' tasks.                                                                                                                                       | user                                                                                           | 2026-11-27 |
| D-111 | D-110                    | `TaskUnsupported` carries only its `kind`, not the `name` of the brief's example: the registered name already has its home in `NewTask.name` and `ManagedTask.name` (AGENTS.md Golden Rule, single source of truth).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | derived from AGENTS.md Golden Rule                                                             | 2026-11-27 |
| D-112 | OQ-53                    | Option 1, summary: a new `State` per listed task holding the identifier, registered name, status with the outcome kind once finished, attempt count, and created and finished times; no argument, result or origin.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-27 |
| D-113 | OQ-54                    | Option 1, haiway's existing `Pagination` and `Paginated` types (`utils/pagination.py`) with an opaque keyset token.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-27 |
| D-114 | OQ-55                    | Oldest first: creation time ascending, then identifier; no direction parameter.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | user                                                                                           | 2026-11-27 |
| D-115 | OQ-56                    | Option 1, a required limit of 1 to 1,000; `ValueError` outside that range.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | user                                                                                           | 2026-11-27 |
| D-116 | OQ-57                    | Option 1, due tasks only: `queued` and due `retry_pending` tasks, as written.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | user                                                                                           | 2026-11-27 |
| D-117 | OQ-58                    | Option 2, check the task: each call carries an opaque registration identity, typed without `Argument` so D-80's variance holds; `spawn` raises `ValueError` unless it matches the listed task; spawns from other processes stay unchecked.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | user                                                                                           | 2026-11-27 |
| D-118 | review gap               | `ManagedTasks.task` on a `closed` runtime raises `RuntimeError`, as handles do after close (FR-77) and as listing does (FR-94).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | caller, derived from consistency with FR-77 and FR-94                                          | 2026-11-27 |
| D-119 | status                   | Amended by D-122: accepted. Amends D-107 for this revision: v1.6 returns to `proposed` until the user accepts it (D-61); TDS-002 v1.1 likewise (TDS-002 D-28).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                           | caller                                                                                         | 2026-11-27 |
| D-120 | review finding           | `ManagedTasks.spawn` checks a call against the listed task by registration only (FR-99); the separate result-class check of FR-62 is dropped, since an equal registration means the same `RegisteredTask` and so the same result class (AGENTS.md Golden Rule).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          | caller                                                                                         | 2026-11-27 |
| D-121 | experiment               | `TaskCompleted.value` is a read-only property, not a plain attribute: only a property keeps `Result` covariant, so that a handle of `ManagedTask[R]` widens to a handle of `State` or `None` (D-80); verified with pyright 1.1.414 strict, `pythonVersion` 3.15.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | caller, derived from an Experimenter run (2026-11-27)                                          | 2026-11-27 |
| D-122 | status                   | The user accepted this revision: status `accepted` (TDS-002 D-31 for TDS-002 v1.1).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      | user                                                                                           | 2026-11-27 |
| D-123 | implementation review    | `insert` of an identifier the store already holds raises and changes nothing: `InMemoryTaskStore` raises `ValueError` naming the identifier, `PostgresTaskStore` its `PostgresException` (TDS-002 D-32).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | caller, implementation review                                                                  | 2026-11-27 |
| D-124 | implementation review    | A negative `limit` given to a store's `claim` or `query` raises `ValueError` in `InMemoryTaskStore` (PostgreSQL raises for a negative `LIMIT`); `claim`'s absent limit stays unlimited and `0` stays the round of expired leases only.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | caller, implementation review                                                                  | 2026-11-27 |
| D-125 | implementation review    | The Data model claims only the combinations FR-90 lists are rejected by `StoredTask`; FR-90 is not extended (an unfinished task with more attempts than its retries allow, or a time-limit failure of a task without a time limit, still constructs).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    | caller, implementation review                                                                  | 2026-11-27 |
| D-126 | implementation review    | Value types check at construction: `TaskFailed` rejects `attempts < 1`, `ClaimedAttempt` and `ClaimedInterrupted` reject `attempt < 1`, `AttemptRetried` rejects a `delay` that is not finite or below 0; every type with construction checks applies them also in `updating(...)` and `copy.replace`, overriding `updating`, which the native `State` runs without a custom `__init__`.                                                                                                                                                                                                                                                                                                                                                                                                                 | caller, implementation review                                                                  | 2026-11-27 |
| D-127 | implementation review 4a | `record` and `release` take the number of the attempt they end, `record(runner, identifier, attempt, end, /)` and `release(runner, identifier, attempt, /)`, and a store applies them only when `runner` holds the lease and the task's attempt number equals `attempt`; otherwise they return `None` and change nothing. A lease-owner check alone let a delayed `record` of attempt 1 land on attempt 2 after the same runner claimed the retry. Amends D-81.                                                                                                                                                                                                                                                                                                                                          | caller, implementation review 4a                                                               | 2026-11-27 |
| D-128 | implementation review 4a | At close step 3 the runtime delivers a cancellation to every attempt still running, those whose termination was already requested included; the first request still decides the ending (a time limit stays `TaskTimeLimitExceeded`), and FR-61's record is logged only when the close's request decides it.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | caller, implementation review 4a                                                               | 2026-11-27 |
| D-129 | implementation review 4a | The change counter of a `LocalCoordination` store also grows by 1 at every `claim` that claimed at least one task.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | caller, implementation review 4a                                                               | 2026-11-27 |
| D-130 | implementation review 4a | `NewTask` checks `time_limit` (absent, or finite and above 0) at construction and in `updating(...)` and `copy.replace`, raising `ValueError`, since the public `ManagedTasks.spawning` field bypasses `ManagedTasks.spawn`'s check.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | caller, implementation review 4a                                                               | 2026-11-27 |
| D-131 | runtime design           | Entering the task scope raising fails the attempt as `TaskFailureRecord(kind="preparation")` under the retry policy, logged at ERROR in the runtime scope (FR-47, FR-74).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                | caller, runtime design                                                                         | 2026-11-27 |
| D-132 | runtime design           | Losing a lease is not a termination reason: the attempt is cancelled and its end discarded, a state distinct from a termination requested for `cancel`, `time_limit` or `close` (FR-83).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 | caller, runtime design                                                                         | 2026-11-27 |
| D-133 | runtime design           | A `record` returning `None` discards the end with one WARNING; no attempt is left to cancel, and the outcome is learned from the store (FR-21, FR-83).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | caller, runtime design                                                                         | 2026-11-27 |
| D-134 | runtime design           | Amended by D-141: a newer attempt claimed during a discard counts too. "The caller's own lease" in FR-84 means that this runtime still runs that attempt (entering, running the function, or recording it) or is already recording its interruption; `ClaimedInterrupted` carries no previous owner.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     | caller, runtime design                                                                         | 2026-11-27 |
| D-135 | runtime design           | One task scope per task and runtime is best effort: a task can enter a new task scope in the same runtime after a lease loss, or after its retry was seen run elsewhere (FR-74).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         | caller, runtime design                                                                         | 2026-11-27 |
| D-136 | runtime design           | During a close, a `ClaimedInterrupted` that a claim in progress returns is recorded, bounded by the grace period as `record` retries are (D-99); a `ClaimedAttempt` is released and never run (FR-29).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | caller, runtime design                                                                         | 2026-11-27 |
| D-137 | runtime design           | At close, a pending `changed()` wait is cancelled and its task awaited until it ends, not for its result (amends D-103's "not waited for").                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | caller, runtime design                                                                         | 2026-11-27 |
| D-138 | runtime design           | When the caller of a spawn is cancelled during the store's `insert`, the insert continues: the task can be stored although the caller got no handle; `ManagedTasks.spawn`'s docstring and the documentation say so (FR-4).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               | caller, runtime design                                                                         | 2026-11-27 |
| D-139 | runtime design           | Clarified by D-142. Handle operations, lookups and listings are served while the runtime is `closing` and raise `RuntimeError` once it is `closed`; those in progress can prolong the close until they return (FR-29).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   | caller, runtime design                                                                         | 2026-11-27 |
| D-140 | runtime design           | Renewals run at a fixed delay after each `renew` returns, so the gap between lease extensions is the renewal interval plus the duration of `renew`; the documentation states that `lease - renewal` has to exceed the expected duration of `renew` (FR-83).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              | caller, runtime design                                                                         | 2026-11-27 |
| D-141 | implementation review    | A newer attempt of a task that this runtime claims while it still discards the attempt whose lease it lost (D-132) is held by this runtime: it is renewed with the others (FR-83), counts as the caller's own lease for FR-84, and its time limit counts from that claim (FR-27); when its renewal reports the lease missing, it is forgotten with one WARNING. Amends D-134.                                                                                                                                                                                                                                                                                                                                                                                                                            | caller, implementation review                                                                  | 2026-11-27 |
| D-142 | implementation review    | In D-139, `closed` is the runtime's own state: a call queued behind the last step of the close is refused with `RuntimeError` although its caller issued it while the runtime was `closing`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                             | caller, implementation review                                                                  | 2026-11-27 |
| D-143 | implementation review 4b | Under `LocalCoordination`, which has no polling tick, a failed `claim` or `fail_unsupported` call, and a failed `changed()` wait, are retried after one fixed short delay, not only at the next store change.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            | caller, implementation review 4b                                                               | 2026-11-27 |
| D-144 | implementation review 4b | At interpreter exit, store calls cancelled by the event loop's shutdown end the runtime with nothing recorded, and the exit propagates once, unchanged.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  | caller, implementation review 4b                                                               | 2026-11-27 |
| D-145 | docs review              | The managed-tasks documentation lives in its own guide, `docs/guides/managed-tasks.md`, registered in `mkdocs.yml`; `docs/guides/concurrent.md` replaces its background-task text with a short pointer to it, so the content has one home (NFR-6).                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                       | caller, docs review                                                                            | 2026-11-28 |

## Context and scope

### Current system (as found)

Read at `ecb86c8`; lines opened by me unless marked "per CodeExplorer".

- **Scoped tasks.**
  - `ctx.spawn` runs in the current scope's `ScopeTaskGroup` with `copy_context()`
    (`src/haiway/context/access.py:403`, `src/haiway/context/tasks.py:295-300`).
  - The scope joins its tasks on exit, and cancels them when the body fails (`tasks.py:349-368`).
  - After a scope's join, spawning into it raises
    `RuntimeError("Context scope tasks are already joined, spawn rejected")` (`tasks.py:183-185`).
  - Spawning while disposables are prepared or disposed raises `RuntimeError` (`tasks.py:286-293`,
    `scope.py:193`, `:201`).
- **Background tasks.**
  - `ctx.spawn_background` runs `ContextTaskGroup.background_run`, which starts the task in an empty
    `Context()` in the process-wide `BackgroundTaskGroup` (`access.py:460`, `tasks.py:469-484`).
  - Their failures are logged at ERROR with no context (`tasks.py:71-90`).
    `ctx.shutdown_background_tasks()` only cancels (`tasks.py:93-123`).
    - The group is loop-bound and single-threaded by design (`tasks.py:36-38`); it is removed
      (FR-68).
  - `ctx.spawn_background` has no caller in haiway's `src/` (per CodeExplorer), but released
    downstream code calls it (see Downstream callers below).
- **Scope entry order.** Identifier, observability, presets, state (with disposables), task group,
  event bus (root or isolated scopes only), closing future. Unwinding pops these in reverse
  (`scope.py:59-133`, `:279-313`). Preset disposables are entered before scope disposables, as two
  sequential groups (`scope.py:152-182`).
- **State.**
  - `ContextState` is a flat mapping from exact type to instance, copied on update
    (`src/haiway/context/state.py:102-113`, `:150-156`). Merging puts presets first, then
    disposables, then explicit state, over the current state (`scope.py:176-182`).
  - `ContextState.snapshot()` is a public classmethod returning the current instances
    (`state.py:15-21`).
  - `ctx.state(T)` for a missing type with no default constructs `T()` and stores it; a failing
    constructor raises `ContextStateMissing`; outside any scope it raises `ContextMissing`
    (`state.py:42-71`).
  - `ctx.contains_state(T)` checks presence without constructing, and is false outside any scope
    (`access.py:549`, `state.py:23-33`).
  - `ContextStateMissing` and `ContextMissing` are both direct subclasses of `ContextException`
    (`src/haiway/context/types.py:8-23`).
- **Disposable state has no provenance.**
  - Scope and preset disposables are merged into `ContextState` by type (`scope.py:152-205`), and
    `ContextDisposables` merges the same way (`src/haiway/context/disposables.py:317-324`).
  - `DisposableState.of(...)` wraps plain state in a disposable whose exit does nothing
    (`disposables.py:35-78`).
  - Presets wrap async-factory state the same way (`presets.py:100-117`).
- **Disposal.**
  - `Disposables.__aexit__` gathers every exit concurrently and is not abandoned on cancellation
    (`disposables.py:200-217`).
  - `ContextDisposables` joins its tasks before disposing (`disposables.py:340-366`).
- **Presets.**
  - The registry is the context variable `ContextPresetsRegistry._context`, holding an immutable
    name-to-preset mapping (`src/haiway/context/presets.py:265-306`).
  - A scope uses a directly passed preset, or else the registry entry for its own name
    (`scope.py:84-89`).
  - Preset disposable factories run on every resolution (`presets.py:46-52`).
- **Events.** Only a root or an `isolated=True` scope creates an event bus (`scope.py:115-118`).
  Sending on a closed bus is a silent no-op: closing drops every watched future
  (`src/haiway/context/events.py:272-273`), and a send finding none returns (`events.py:210-211`);
  subscribing to a closed bus ends at once (`events.py:234-242`).
- **Identifiers.**
  - `ContextIdentifier` holds `name`, `kind`, `scope_id`, `parent_id` and `path`, and carries no
    trace id. A root has `scope_id == parent_id` (`src/haiway/context/identifier.py:78-138`,
    `:151-153`).
  - `random_identifier()` draws 122 random bits of a version-4 UUID from a non-cryptographic
    generator, and is documented as "not secrets" (`identifier.py:21-62`).
- **Observability protocol.**
  - `ObservabilityScopeEntering.__call__(scope, /) -> str` receives only the new scope's identifier
    (`src/haiway/context/observability.py:205-216`).
  - Its implementations are `LoggerObservability` (`observability.py:608-648`) and the OpenTelemetry
    adapter (`src/haiway/opentelemetry/observability.py:857-894`); `ContextObservability` calls it
    at `observability.py:1040`.
  - Without an explicit backend, a nested scope inherits the current one and a root gets a new
    `LoggerObservability` (`observability.py:751-790`).
- **`LoggerObservability`.**
  - A scope whose parent store is present joins its trace and increments the parent's pending count;
    otherwise it starts a new random trace (`observability.py:608-648`).
  - Every record is prefixed with `[<trace hex>] [<name>] [<scope id>]`
    (`observability.py:393-402`).
  - A scope completes only once it has exited and no nested scope is pending
    (`observability.py:409-425`).
  - Scope lifecycle is recorded at DEBUG (`observability.py:459-464`).
- **OpenTelemetry adapter.**
  - A tracked parent gives the span's parent context. An untracked root uses the adapter's remote
    context, built from `traceparent`; an untracked nested scope uses the ambient context
    (`opentelemetry/observability.py:857-894`).
  - The trace context is encoded from the scope's own context
    (`opentelemetry/observability.py:702-715`).
  - Nothing in `src/` creates span links (per CodeExplorer, `rg`).
  - Pinned `opentelemetry-api` and `opentelemetry-sdk` are 1.45.2 (`uv.lock:789-790`, `:856-857`).
- **Starlette.**
  - The `ServerContext` lifespan enters one bare `Disposables` and stores the produced state, with
    the declared state last; it enters no scope (`src/haiway/starlette/context.py:182-207`).
  - Every request enters a new root scope with that state as explicit state, and a per-request
    backend built from the request's `traceparent` (`src/haiway/starlette/middleware.py:184-190`,
    `starlette/context.py:264-272`, `opentelemetry/observability.py:1321-1327`).
  - A mounted sub-app never receives the lifespan message (per CodeExplorer, Starlette 1.10.0
    `routing.py:405`, `Mount.matches` handling only `http` and `websocket`; `Router.app` runs the
    lifespan at `:690-692`).
- **State methods.** `statemethod` lets a `State` method be called on the class, resolving the
  instance from the context (`src/haiway/helpers/statemethods.py:12`; used by `Postgres`,
  `src/haiway/postgres/state.py:381`).
- **Probe** (Experimenter probe, 2026-11-26; haiway 0.67.0, CPython 3.15.1, 20 runs per
  configuration).
  - A task started with a copied context and owned outside the starting scope survives that scope.
  - Afterwards `ctx.state` works, and disposable state stays readable after its `__aexit__`.
  - `ctx.spawn` raises `RuntimeError`, and `ctx.subscribe` ends at once.
  - Both backends drop `log_*` and `record_*`, and `ctx.trace_id()` returns zeros.
  - Observability survived only when ancestor scopes were kept pending.

### Current system, further facts

Read at `ecb86c8`; lines opened by me unless marked "per CodeExplorer".

- **Root state.** `ContextState.updating` merges into the `ContextState` current in the context
  variable and only starts from nothing when none is current
  (`src/haiway/context/state.py:102-113`). A scope, root or nested, merges its presets, disposables
  and explicit state that way (`scope.py:91-100`, `:176-182`). `ContextState.snapshot()` returns the
  current instances, or `()` when none is current (`state.py:15-21`). There is no
  `ContextState.current()` (per CodeExplorer).

- **State priority.** `ctx.scope` documents four layers, from highest to lowest: explicit state,
  explicit disposables, preset state, contextual state from parent contexts
  (`src/haiway/context/access.py:170-177`).

- **Presets registry.** `ctx.presets(*presets)` enters a `ContextPresetsRegistry` that shadows any
  outer one for its block, with no fallback (`access.py:86-150`); entering none is a no-op. A scope
  without a direct preset selects the registry entry named like itself
  (`src/haiway/context/scope.py:84-89`, `src/haiway/context/presets.py:268-277`). The registry is a
  `ClassVar[ContextVar]` (`presets.py:280`); only `ContextPresets` is exported (per CodeExplorer).

- **`ctx.updating`.** Merges state into the current context without a scope (`access.py:256-280`).

- **Trace identifiers.** `ctx.trace_id()` returns 32 lower-case hex characters (`access.py:46-62`),
  and raises `ContextMissing` outside any context (`src/haiway/context/observability.py:799-801`,
  per CodeExplorer). The logger backend draws it from `random_identifier()`, the OpenTelemetry
  adapter from the span's trace id (per CodeExplorer: `observability.py:480-491`, `:627`;
  `src/haiway/opentelemetry/observability.py:688-700`). `ctx.trace_context()` is `{}` under the
  logger backend (per CodeExplorer: `tests/test_logger_observability.py:277`).

- **Scope errors are already logged.** `LoggerObservability` logs every `Exception` leaving a scope
  at ERROR with `exc_info`, once per scope it passes through (`observability.py:684-696`). The
  OpenTelemetry adapter records it once per span chain (per CodeExplorer:
  `opentelemetry/observability.py:896-924`).

- **Scope task errors.** A failed `ctx.spawn` task is logged at ERROR when it fails
  (`src/haiway/context/tasks.py:211-216`). On join, a single error is raised on its own, several as
  an `ExceptionGroup` or `BaseExceptionGroup` (`tasks.py:401-422`).

- **Background tasks, full surface.**

  - `ctx.spawn_background` accepts a coroutine object, or a coroutine function with its arguments,
    and returns `asyncio.Task[Result]` (`access.py:407-460`). It runs
    `ContextTaskGroup.background_run`, which starts the task in an empty `Context()` in the
    process-wide `BackgroundTaskGroup` (`tasks.py:469-484`).
  - `ctx.shutdown_background_tasks()` calls `BackgroundTaskGroup.shutdown_all()`
    (`access.py:462-470`).
    - `ctx.spawn` outside any scope falls back to `ContextTaskGroup.background_run`, logged at DEBUG
      (`tasks.py:270-284`); `ContextTaskGroup.run` falls back to `background_run` with no record
      (`tasks.py:453-459`).
  - Spawning while disposables are prepared or disposed raises a `RuntimeError` whose message points
    to `ctx.spawn_background` (`tasks.py:286-293`).
  - None of `BackgroundTaskGroup`, `ContextTaskGroup` or `ScopeTaskGroup` is exported (per
    CodeExplorer).
  - Other mentions: `tests/conftest.py:12-23` (an autouse fixture calling
    `ctx.shutdown_background_tasks()` after every test), `tests/test_background_tasks.py`,
    `tests/test_background_scope.py`, `tests/test_scope_concurrency.py:219-228`,
    `tests/test_context_lifecycle_fixes.py:171`, `tests/test_context.py:383-388`;
    `docs/guides/concurrent.md:59-79`, `docs/getting-started/functional-programming-guide.md:238`,
    `AGENTS.md:70`, `llms.txt:172-175`, and five files under `skills/haiway/` (per CodeExplorer).

- **`ServerContext`.** Its lifespan enters its `Disposables` and assigns
  `self.state = (*disposable_state, *self._state)`; it puts nothing into the context variables
  (`src/haiway/starlette/context.py:182-207`). Each request enters `ctx.presets(*context.presets)`
  and a root scope with `request_state` as explicit state
  (`src/haiway/starlette/middleware.py:184-190`). Its observability can be a per-request factory
  (`starlette/context.py:122-145`).

- **State JSON.** `State.to_json()` encodes and raises `ValueError` on failure;
  `State.from_json(value, /, decoder=json.JSONDecoder, *, ignore_extra=False)` decodes and
  validates, raising `ValueError` whose cause is the `ValidationError`; `State.json_schema()`
  returns the schema (`src/haiway/attributes/state.py:1288-1404`, `:1480-1532`, per CodeExplorer;
  `docs/guides/state.md:456-460`). A State is identified by its class; there is no name registry of
  States, and States refuse pickling (per CodeExplorer: `state.py:~1650-1670`).

- **Root-logger fallback.** A record made outside any scope goes to the root `Logger`
  (`src/haiway/context/observability.py:862-875`); the `ctx.spawn_background` docstring says the
  same of background tasks (`access.py:437-439`).

- **Empty preset.** `ContextPresets.of(name)` with no state and no disposables has no static state
  and nothing to prepare (`presets.py:66-72`, `:212-220`); a scope given a preset uses it and does
  not select the registry entry of its name (`scope.py:84-89`).

- **Spawn rejection message.** `ScopeTaskGroup.run` rejects spawns during disposable preparation or
  disposal with a `RuntimeError` ending "use ctx.spawn_background instead" (`tasks.py:286-293`).

- **Request backends.** `ObservabilityPreparing.__call__(*, traceparent, tracestate)` returns an
  `Observability` (`src/haiway/starlette/types.py:9-38`); `ServerContext` wraps a plain backend or a
  `Logger` into such a function and otherwise keeps the given one (`starlette/context.py:122-145`).

- **Pool size.** `PostgresConnectionPool(connection_limit=...)` defaults to `POSTGRES_CONNECTIONS`
  or 1 (`src/haiway/postgres/client.py:82-84`, `:265-268`); the resolved limit is kept only inside
  the pool's factory (`client.py:290-299`).

- **Retry helper.**
  `retry(limit=1, delay=None | float | int | Callable[[int, Exception], float], catching=Exception | Callable[[Exception], bool])`
  retries raised exceptions up to `limit` more times and always propagates `CancelledError`
  (`src/haiway/helpers/retries.py:35-74`).

- **Pagination types.** haiway exports `Pagination` (a `State` with
  `token: UUID | str | int | None`, `limit: int` and `arguments: BasicObject`) and
  `Paginated[Element]` (a read-only sequence with that `Pagination`), whose `has_next_page` is true
  when a token is present or the page holds at least `limit` items
  (`src/haiway/utils/pagination.py:16-62`, `:186-212`; `src/haiway/__init__.py:100-102`). Nothing in
  `src/` uses them otherwise (`rg`).

- **Datetimes in State.** A `datetime` attribute accepts a naive value: the JSON writer emits no
  offset for it (`src/haiway/_native/convert.h:105-108`), and the schema pattern makes the offset
  optional (`src/haiway/attributes/specification.py:765-771`). CPython raises `TypeError` ("can't
  compare offset-naive and offset-aware datetimes") on ordering a naive against an aware datetime
  (run by me, CPython 3.12.4, 2026-11-27).

- **Lint.** Ruff runs the `A` rules (`pyproject.toml:93-94`), which flag attributes and parameters
  shadowing builtins such as `list` and `filter`.

- **Function names.** For a function defined inside another, `__name__` is the bare name (`inner`)
  and `__qualname__` the dotted path (`outer.<locals>.inner`) (run by me, CPython 3.12.4,
  2026-11-27; PEP 3155, per WebExplorer).

- **Clocks.** "The reference point of the returned value \[of `time.monotonic()`\] is undefined, so
  that only the difference between the results of two calls is valid"; `datetime.now(tz)` converts
  the current time to `tz`, and `datetime.UTC` is `timezone.utc` (Python 3.15 documentation, per
  WebExplorer, 2026-11-27).

Downstream callers of `ctx.spawn_background`, opened by me (draive at 5080cbc, draive-platform at
6e01b8f; the cited files are unmodified in both working trees):

- `draive/src/draive/aws/observability.py:705` calls it on the event loop, and `:714` schedules it
  from a worker thread through `loop.call_soon_threadsafe` (`:698-716`).
- `draive-platform/src/draive_platform/telemetry/observability.py:425` stores a trace in the
  background.
- `draive-platform/src/draive_platform/evaluation/runner.py:170` keeps the returned `asyncio.Task`,
  adds a done callback (`:172`) and cancels it (`:174-182`).
- `draive/tests/test_aws_state_observability.py:44`, `:104`, `:191`, `:281` and `:327` monkeypatch
  it with `monkeypatch.setattr`; `:72`, `:151` and `:249` define the local fakes patched in.
- draive's four callers at `aws/observability.py:462`, `:485`, `:524` and `:563` all go through the
  helper `_spawn_background` (`:698-716`), so migrating that helper covers them.
- State JSON with extra keys: `State.from_json(..., ignore_extra=True)` skips keys that are neither
  attribute names nor aliases, in every nested State except below a class with its own `__init__` or
  `validate` (`attributes/state.py:1344-1370`).

Type check (pyright 1.1.414 strict, 2026-11-27, run by me on a stand-in of the interfaces): with
`RegisteredTaskCall[Argument, Result]` exposing `task` and `argument`, passing a declared
`RegisteredTask[A, R]` to a parameter of type `Iterable[RegisteredTask[Never, State | None]]` fails
("Type parameter "Argument@RegisteredTask" is invariant"); with `RegisteredTaskCall[Result]` holding
the name, the encoded argument and the result class, the same call passes with 0 errors.

External facts (per WebExplorer, fetched 2026-11-26):

- **OpenTelemetry Trace API, specification v1.63.0.**
  - A span is linked to zero or more causally related spans "inside a single Trace or across
    different Traces".
  - Links carry attributes.
  - Links added at span creation are preferred to `AddLink`, because samplers see only creation-time
    data.
- **opentelemetry-python 1.45.2.**
  - `Tracer.start_span(..., links=...)` accepts `Sequence[Link]`, and
    `Link(context, attributes=None)`.
  - `TraceContextTextMapPropagator.extract` returns the given context unchanged for a missing or
    malformed `traceparent`; otherwise the context holds a remote `SpanContext`.
- **W3C Trace Context Level 1** (Recommendation, 2021-11-23). `traceparent` is
  `version-trace-id-parent-id-trace-flags`, of 2, 32, 16 and 2 lower-case hex digits.

### In scope

- the `ctx.managed_tasks(...)` block, the root snapshot, nesting and `ServerContext` integration;
- the runtime (`TaskRuntime`) and its settings; the `ManagedTasks` state and `TaskRoot`;
- the `TaskStore` protocol and `InMemoryTaskStore`; the split between runtime and store;
- registered tasks, their registered name and its default, their argument and result as JSON;
- listing stored tasks with filters and pages; failing unsupported tasks;
- the handle, status, outcome, failure, lookup, cancellation, time limits, retries, leases, claims
  and recovery of interrupted attempts;
- close; the task scope, attempt scopes, task context, observability and the `ObservabilityLink`
  change;
- the removal of `ctx.spawn_background` and the background task group; `ctx.spawn` outside a scope;
- tests and documentation.

### Non-goals

- Completion callbacks or any completion notification beyond awaiting a handle (D-42, D-57).
- Storing any context of a task beyond D-67's list and the origin of D-72: no spawn state,
  disposables or presets.
- Resuming a coroutine where it stopped: a resumed or retried attempt runs the function again.
- Exactly-once execution: an attempt interrupted after its side effects runs again (D-56, D-65).
- Spawning a coroutine function that is not a registered task, or arguments that are not one `State`
  (D-77); in-process callables and closures cannot be spawned.
- A replacement for `ctx.spawn_background`: fire-and-forget work outside managed tasks runs in loop
  tasks the caller owns (D-78).
- Presets passed at spawn (D-60).
- Using one runtime from another thread or event loop (HM-10; FR-34). haiway's loop-bound objects
  record their loop and are reached from elsewhere only by scheduling onto it
  (`src/haiway/utils/loops.py:12-45`).
- Detecting use of a disposed resource that a task received as an argument.
- Changing `ctx.spawn` inside a scope, or `ContextTaskGroup.run` inside a task group.
- A `links` parameter of `ctx.scope`; only `TaskRoot.task_scope` enters linked scopes (FR-74).
- Task identifiers usable as credentials (D-8).
- `managed_task` applied bare, as `@managed_task` without a call: it takes keyword arguments only,
  so such a use raises `TypeError` (FR-91); D-108 does not ask for it.
- Failing stored tasks of unlisted names in a runtime with `unsupported="leave"` (D-110), and
  failing any stored task that is not due (FR-93).

## Components and responsibilities

| Component                                                                                                         | Responsibility                                                                                                                                                                       |
| ----------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `ctx.managed_tasks(runtime)` (new)                                                                                | Enters the block: takes the root snapshot, opens the runtime, puts its `ManagedTasks` into the body's context and yields it, closes the runtime on exit (FR-55, FR-56).              |
| `TaskRuntime` (new)                                                                                               | The one runtime (D-66): settings (FR-81), runner id, the attempts it runs, origins, the runtime scope (FR-88), claim rounds, renewal, recovery, retries, time limits, close (FR-80). |
| `ManagedTasks` (`State`, new)                                                                                     | The entry points `available`, `spawn`, `task` (D-4) and the listing of FR-94 (D-109), all coroutines (D-64); validates inputs and captures the origin, then calls the runtime.       |
| `TaskRoot` (new)                                                                                                  | Owns the root snapshot of one block; builds task contexts (FR-10) and enters task scopes with their link (FR-74).                                                                    |
| `TaskStore`, `TaskStoreSession` (protocols, new)                                                                  | The extension point (D-66): the operations of FR-78.                                                                                                                                 |
| `InMemoryTaskStore` (new)                                                                                         | The default store (FR-79).                                                                                                                                                           |
| Task filter types (new)                                                                                           | Immutable values selecting listed tasks (FR-95).                                                                                                                                     |
| `managed_task(...)`, `RegisteredTask`, `RegisteredTaskCall` (new)                                                 | Registration under a registered name, with the argument and result classes read from the signature; a call encodes its argument (FR-62, FR-63, FR-76, D-80).                         |
| `ManagedTask[Result]` (new)                                                                                       | The handle: identifier, name, status, outcome, cancel (FR-70); owns nothing.                                                                                                         |
| Outcome and failure types, `TaskRetryPolicy`, stored-task types (new)                                             | Immutable values (FR-20, FR-66, [Data model](#data-model)).                                                                                                                          |
| `ObservabilityLink` (new), `ObservabilityScopeEntering` and its implementations (changed)                         | The origin of a task (FR-12, FR-71); links passed to and recorded by backends (FR-13 to FR-15).                                                                                      |
| `ServerContext` (changed)                                                                                         | Enters a block in its lifespan after its disposables and passes the `ManagedTasks` to requests (FR-35).                                                                              |
| `ctx.spawn`, `ScopeTaskGroup.run`, `ContextTaskGroup.run` (changed)                                               | Raise `ContextMissing` outside any scope or task group (FR-69).                                                                                                                      |
| `ctx.spawn_background`, `BackgroundTaskGroup`, `ContextTaskGroup.background_run`, `ctx.shutdown_background_tasks` | Removed (FR-68, D-78).                                                                                                                                                               |
| PostgreSQL task store                                                                                             | [TDS-002](TDS-002-postgres-managed-tasks.md).                                                                                                                                        |

## Interfaces

Signatures are normative for names, parameters, types and defaults. Docstrings follow AGENTS.md
(NumPy style). Failure types, `TaskRetryPolicy`, `ObservabilityLink`, the attempt ends and the
stored-task types are `State`s: every field has a JSON form, and stores keep them. `TaskCompleted`,
`TaskFailed` and `TaskCancelled` are `@final` immutable classes that reject attribute assignment and
deletion, as `ContextPresets` does (`src/haiway/context/presets.py:245-263`): they are never stored
(a store holds `StoredTask`), and their value was already validated when the runtime decoded it
(FR-76). `ManagedTask`, `TaskRoot`, `TaskRuntime`, `RegisteredTask`, `RegisteredTaskCall` and the
stores are `@final` classes holding functions or settings.

```python
# --- observability ---------------------------------------------------------------

@final
class ObservabilityLink(State):
    trace_id: UUID  # UUID(hex=ctx.trace_id()) of the origin (FR-71)
    scope_id: UUID
    trace_context: Mapping[str, str]  # bounded by FR-87; can be empty

    @classmethod
    def current(cls) -> Self: ...  # ContextMissing outside any scope (FR-12)

class ObservabilityScopeEntering(Protocol):
    def __call__(
        self,
        scope: ContextIdentifier,
        /,
        *,
        links: Sequence[ObservabilityLink],
    ) -> str: ...

# --- outcomes ----------------------------------------------------------------------

type TaskStatus = Literal["queued", "running", "retry_pending", "finished"]

type TaskCancellation = Literal["cancelled", "pending", "finished"]  # FR-26

@final
class TaskFailureRecord(State):
    kind: Literal["raised", "preparation"]
    exception_type: str  # "<module>.<qualname>" of the exception

@final
class TaskTimeLimitExceeded(State):
    kind: Literal["time_limit"] = "time_limit"
    time_limit: float  # seconds, as given at spawn

@final
class TaskInterrupted(State):
    kind: Literal["interrupted"] = "interrupted"  # the attempt's runtime stopped (FR-84)

@final
class TaskUnsupported(State):
    kind: Literal["unsupported"] = "unsupported"  # failed by a runtime not listing it (FR-93, D-111)

type AttemptFailure = TaskFailureRecord | TaskTimeLimitExceeded | TaskInterrupted  # retryable (FR-66)

type TaskFailure = AttemptFailure | TaskUnsupported

@final
class TaskCompleted[Result: State | None]:
    @property
    def value(self) -> Result: ...  # decoded from the store (FR-76); read-only keeps Result covariant

@final
class TaskFailed:
    failure: TaskFailure  # the failure of the last attempt
    attempts: int  # counted attempts, at least 1

@final
class TaskCancelled: ...

type TaskOutcome[Result: State | None] = TaskCompleted[Result] | TaskFailed | TaskCancelled

@final
class TaskRetryPolicy(State):
    delays: Sequence[float] = ()  # seconds before retry 1, 2, ...; len(delays) retries

# --- listing filters (FR-95) --------------------------------------------------------

@final
class TaskTimeFrom(State):
    bound: Literal["from"] = "from"
    start: datetime  # inclusive; timezone-aware

@final
class TaskTimeBefore(State):
    bound: Literal["before"] = "before"
    end: datetime  # exclusive; timezone-aware

@final
class TaskTimeBetween(State):
    bound: Literal["between"] = "between"
    start: datetime  # inclusive; timezone-aware
    end: datetime  # exclusive; timezone-aware, later than start

type TaskTimeRange = TaskTimeFrom | TaskTimeBefore | TaskTimeBetween

@final
class TasksUnfinished(State):
    status: Literal["unfinished"] = "unfinished"
    statuses: Set[Literal["queued", "running", "retry_pending"]]

@final
class TasksFinished(State):
    status: Literal["finished"] = "finished"
    outcomes: Set[Literal["completed", "failed", "cancelled"]]
    finished: TaskTimeRange | None = None  # absent: any finish time

type TaskStatusFilter = TasksUnfinished | TasksFinished

@final
class TaskFilter(State):
    names: Set[str] | None = None  # absent: every registered name
    status: TaskStatusFilter | None = None  # absent: every status
    created: TaskTimeRange | None = None  # absent: any creation time

# --- listing results (FR-97, FR-98) ------------------------------------------------

@final
class TaskSummaryUnfinished(State):
    status: Literal["queued", "running", "retry_pending"]

@final
class TaskSummaryFinished(State):
    status: Literal["finished"] = "finished"
    outcome: Literal["completed", "failed", "cancelled"]
    finished: datetime  # UTC listing timestamp (FR-96)

@final
class TaskSummary(State):  # invariants checked at construction (FR-98)
    identifier: UUID
    name: str
    status: TaskSummaryUnfinished | TaskSummaryFinished
    attempts: int  # counted attempts, at least 0
    created: datetime  # UTC listing timestamp (FR-96)

@final
class TaskKeyset(State):  # its JSON is the page token; naive times rejected (FR-97)
    created: datetime
    identifier: UUID

class TaskListing(Protocol):
    async def __call__(
        self,
        pagination: Pagination,
        /,
        *,
        matching: TaskFilter,
    ) -> Paginated[TaskSummary]: ...

# --- tasks -------------------------------------------------------------------------

@final
class RegisteredTask[Argument: State, Result: State | None]:
    @property
    def name(self) -> str: ...
    @property
    def retry(self) -> TaskRetryPolicy | None: ...  # absent: the runtime default (D-54)
    @property
    def result(self) -> type[Result]: ...  # the annotated return class; NoneType for None
    @property
    def registration(self) -> UUID: ...  # drawn when managed_task is applied (FR-99)
    def __call__(self, argument: Argument, /) -> RegisteredTaskCall[Result]: ...  # FR-63
    def bind(
        self,
        argument: str,  # stored JSON of the argument
        /,
    ) -> Callable[[], Coroutine[None, None, Result]]: ...  # ValueError: decoding failed (FR-63)

@final
class RegisteredTaskCall[Result: State | None]:
    @property
    def name(self) -> str: ...  # the registered name
    @property
    def argument(self) -> str: ...  # the argument's JSON, checked by __call__ (FR-63)
    @property
    def result(self) -> type[Result]: ...
    @property
    def registration(self) -> UUID: ...  # its RegisteredTask's (FR-99)

def managed_task[Argument: State, Result: State | None](
    *,
    name: str | None = None,  # absent: the function's __name__ (FR-91, D-108)
    retry: TaskRetryPolicy | None = None,
) -> Callable[
    [Callable[[Argument], Coroutine[None, None, Result]]],
    RegisteredTask[Argument, Result],
]: ...

@final
class ManagedTask[Result: State | None]:
    @property
    def identifier(self) -> UUID: ...
    @property
    def name(self) -> str: ...
    async def status(self) -> TaskStatus: ...
    async def outcome(self) -> TaskOutcome[Result]: ...
    async def cancel(self) -> TaskCancellation: ...

class TaskAvailability(Protocol):
    async def __call__(self) -> bool: ...

class RegisteredTaskSpawning(Protocol):
    async def __call__[Result: State | None](
        self,
        call: RegisteredTaskCall[Result],
        /,
        *,
        time_limit: float | None,
        origin: ObservabilityLink | None,
    ) -> ManagedTask[Result]: ...

class TaskLookup(Protocol):
    async def __call__(
        self,
        identifier: UUID,
        /,
    ) -> ManagedTask[State | None] | None: ...  # unlisted names: FR-70

@final
class ManagedTasks(State):
    availability: TaskAvailability  # no field has a default (FR-2)
    spawning: RegisteredTaskSpawning
    lookup: TaskLookup
    listing: TaskListing  # FR-94

    @classmethod
    async def available(cls) -> bool: ...

    @classmethod
    async def spawn[Result: State | None](
        cls,
        call: RegisteredTaskCall[Result],
        /,
        *,
        time_limit: float | None = None,
    ) -> ManagedTask[Result]: ...

    @classmethod
    async def task(cls, identifier: UUID, /) -> ManagedTask[State | None] | None: ...

    @classmethod
    async def tasks(
        cls,
        pagination: Pagination,  # limit 1 to 1,000; token None or from a listing (FR-94, FR-97)
        /,
        *,
        matching: TaskFilter = TaskFilter(),
    ) -> Paginated[TaskSummary]: ...

@final
class TaskRoot:
    @property
    def state(self) -> Sequence[State]: ...
    def context(self, owner: ManagedTasks, /) -> Context: ...  # FR-10
    def task_scope(
        self,
        name: str,
        /,
        *,
        link: ObservabilityLink | None,
        observability: Observability,
    ) -> AbstractAsyncContextManager[str]: ...  # FR-74

# --- the runtime ------------------------------------------------------------------------

@final
class TaskRuntime:
    def __init__(
        self,
        *,
        store: TaskStore | None = None,  # absent: InMemoryTaskStore()
        tasks: Iterable[RegisteredTask[Never, State | None]] = (),
        retry: TaskRetryPolicy = TaskRetryPolicy(),
        grace_period: float = 0.0,
        concurrency_limit: int | None = None,  # absent: unlimited (D-90)
        observability: Observability | Logger | None = None,
        unsupported: Literal["leave", "fail"] = "leave",  # FR-92, D-110
    ) -> None: ...

# --- the store protocol -------------------------------------------------------------------

@final
class StoredAttempt(State):
    result: Literal["completed", "cancelled"] | TaskFailure  # its number: position + 1

@final
class StoredQueued(State):
    status: Literal["queued"] = "queued"

@final
class StoredRunning(State):
    status: Literal["running"] = "running"
    cancel_requested: bool

@final
class StoredRetryPending(State):
    status: Literal["retry_pending"] = "retry_pending"

@final
class StoredCompleted(State):
    outcome: Literal["completed"] = "completed"
    result: str  # JSON of the returned State, or "null"

@final
class StoredFailed(State):
    outcome: Literal["failed"] = "failed"  # failure and count: from StoredTask.attempts

@final
class StoredCancelled(State):
    outcome: Literal["cancelled"] = "cancelled"

@final
class StoredFinished(State):
    status: Literal["finished"] = "finished"
    outcome: StoredCompleted | StoredFailed | StoredCancelled

type StoredStatus = StoredQueued | StoredRunning | StoredRetryPending | StoredFinished

@final
class NewTask(State):
    identifier: UUID
    name: str
    argument: str  # JSON of the argument State (FR-63)
    retry: TaskRetryPolicy  # the effective policy (FR-66)
    time_limit: float | None  # absent, or finite and above 0 (D-130)
    origin: ObservabilityLink | None  # absent for a spawn outside any scope (FR-12, FR-87)

@final
class StoredTask(State):  # invariants validated at construction (FR-90)
    task: NewTask
    status: StoredStatus
    attempts: Sequence[StoredAttempt]  # counted attempts, in number order

@final
class ClaimedAttempt(State):
    claim: Literal["attempt"] = "attempt"
    task: NewTask
    attempt: int  # len(attempts) + 1

@final
class ClaimedInterrupted(State):
    claim: Literal["interrupted"] = "interrupted"
    task: NewTask
    attempt: int  # the interrupted attempt: len(attempts) + 1
    cancel_requested: bool

type ClaimedTask = ClaimedAttempt | ClaimedInterrupted

type LeaseState = Literal["held", "cancel_requested"]

@final
class LeaseRenewal(State):
    leases: Mapping[UUID, LeaseState]  # the requested leases still held; absent: not held

@final
class AttemptCompleted(State):
    end: Literal["completed"] = "completed"
    result: str  # JSON of the returned State, or "null"

@final
class AttemptRetried(State):
    end: Literal["retried"] = "retried"
    failure: AttemptFailure
    delay: float  # seconds until the next attempt is due

@final
class AttemptFailed(State):
    end: Literal["failed"] = "failed"  # no retry follows
    failure: AttemptFailure

@final
class AttemptCancelled(State):
    end: Literal["cancelled"] = "cancelled"

type AttemptEnd = AttemptCompleted | AttemptRetried | AttemptFailed | AttemptCancelled

@final
class LeaseTimings(State):
    lease: float  # seconds a claim or renewal holds a lease
    renewal: float  # seconds between renewals, below lease
    polling: float  # seconds between claim rounds and outcome polls

@final
class LeasedCoordination(State):
    coordination: Literal["leased"] = "leased"
    timings: LeaseTimings  # owned by the store (D-88)

class ChangeWaiting(Protocol):
    async def __call__(
        self,
        seen: int,  # the counter last returned; -1 before the first call
        /,
    ) -> int: ...  # the store's change counter, once it differs from seen (FR-78)

@final
class LocalCoordination:  # leases never expire; runtimes wake on every change
    @property
    def coordination(self) -> Literal["local"]: ...
    @property
    def changed(self) -> ChangeWaiting: ...

type StoreCoordination = LeasedCoordination | LocalCoordination

class TaskInserting(Protocol):
    async def __call__(self, task: NewTask, /) -> None: ...

class TaskClaiming(Protocol):
    async def __call__(
        self,
        runner: UUID,
        names: Set[str],
        *,
        limit: int | None,
    ) -> Sequence[ClaimedTask]: ...

class LeaseRenewing(Protocol):
    async def __call__(
        self,
        runner: UUID,
        identifiers: Set[UUID],
    ) -> LeaseRenewal: ...

class AttemptRecording(Protocol):
    async def __call__(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,  # the number of the attempt it ends (D-127)
        end: AttemptEnd,
        /,
    ) -> StoredStatus | None: ...  # the status fixed, or None: no lease, or another attempt

class TaskReleasing(Protocol):
    async def __call__(
        self,
        runner: UUID,
        identifier: UUID,
        attempt: int,  # the number of the attempt it releases (D-127)
        /,
    ) -> StoredStatus | None: ...  # the status fixed, or None: no lease, or another attempt

class CancelRequesting(Protocol):
    async def __call__(
        self,
        identifier: UUID,
        /,
    ) -> TaskCancellation | None: ...  # None: the store does not hold it

class UnsupportedFailing(Protocol):
    async def __call__(
        self,
        names: Set[str],  # the listed registered names
        /,
    ) -> Mapping[UUID, str]: ...  # identifier to registered name of each task it failed (FR-78)

class TaskQuerying(Protocol):
    async def __call__(
        self,
        matching: TaskFilter,
        /,
        *,
        after: TaskKeyset | None,  # absent: from the start
        limit: int,  # 1 to 1,000
    ) -> Sequence[TaskSummary]: ...  # in listing order (FR-78, FR-97)

class TaskReading(Protocol):
    async def __call__(
        self,
        identifiers: Set[UUID],
        /,
    ) -> Mapping[UUID, StoredTask]: ...  # absent: the store does not hold it

@final
class TaskStoreSession:
    def __init__(
        self,
        *,
        insert: TaskInserting,
        claim: TaskClaiming,
        renew: LeaseRenewing,
        record: AttemptRecording,
        release: TaskReleasing,
        cancel: CancelRequesting,
        read: TaskReading,
        fail_unsupported: UnsupportedFailing,
        query: TaskQuerying,
        coordination: StoreCoordination,
    ) -> None: ...
    @property
    def insert(self) -> TaskInserting: ...
    @property
    def claim(self) -> TaskClaiming: ...
    @property
    def renew(self) -> LeaseRenewing: ...
    @property
    def record(self) -> AttemptRecording: ...
    @property
    def release(self) -> TaskReleasing: ...
    @property
    def cancel(self) -> CancelRequesting: ...
    @property
    def read(self) -> TaskReading: ...
    @property
    def fail_unsupported(self) -> UnsupportedFailing: ...
    @property
    def query(self) -> TaskQuerying: ...
    @property
    def coordination(self) -> StoreCoordination: ...

class TaskStore(Protocol):
    def open(self) -> AbstractAsyncContextManager[TaskStoreSession]: ...

@final
class InMemoryTaskStore:
    def __init__(self, *, retention: float = 3600.0) -> None: ...
    def open(self) -> AbstractAsyncContextManager[TaskStoreSession]: ...

# --- ctx and ServerContext ------------------------------------------------------------

class ctx:
    @staticmethod
    def managed_tasks(
        runtime: TaskRuntime,
        /,
        *,
        observability: Observability | None = None,  # fallback backend (FR-52, D-93)
    ) -> AbstractAsyncContextManager[ManagedTasks]: ...

class ServerContext:
    def __init__(
        self,
        *state: State | None,
        disposables: Iterable[Disposable | None] = (),
        presets: Iterable[ContextPresets] = (),
        observability: Observability | Logger | ObservabilityPreparing = DEFAULT_LOGGER,
        tasks: TaskRuntime | None = None,  # FR-35
    ) -> None: ...
```

- **Type parameters.** `TaskRuntime(tasks=...)` takes `RegisteredTask[Never, State | None]`, relying
  on the variance PEP 695 infers. `Argument` MUST appear only as a parameter (`__call__`), so it is
  contravariant: `RegisteredTaskCall` carries no `Argument` (D-80), and `RegisteredTask` stores no
  attribute whose type mentions `Argument`, binding the argument class and the function in a closure
  behind `bind`, because pyright counts private attributes in inferred variance (a private
  `type[Argument]` attribute made `Argument` invariant in the check). `Result` appears only in
  results and read-only properties, so it is covariant. Checked with pyright 1.1.414 strict on a
  stand-in of these signatures: a declared `RegisteredTask[A, R]` and `RegisteredTask[A, None]` pass
  to `TaskRuntime(tasks=...)`, `ManagedTasks.spawn(t(A(n=1)))` gives `ManagedTask[R]`, and a handle
  widens to `ManagedTask[State | None]`, with 0 errors; the v1.1 shape failed with "Type parameter
  "Argument@RegisteredTask" is invariant" (review 2 B3, confirmed). Re-checked on v1.6 with
  `registration` present (pyright 1.1.414 strict, `pythonVersion` 3.15, a stand-in with the real
  `haiway.State`, per Experimenter, 2026-11-27): the same three uses give 0 errors once
  `TaskCompleted.value` is a read-only property (D-121). `Result` MUST appear in no plain attribute
  of a generic class: as a plain attribute of `TaskCompleted`, as a `@dataclass(frozen=True)` field
  on 3.15 and as a `State` field it made `Result` invariant in `TaskCompleted` and through it in
  `ManagedTask`, and widening a handle gave 2 errors. No other generic class here has a
  `Result`-typed attribute.
- **Task calls.** `RegisteredTaskCall` has no public constructor: calls are created only by
  `RegisteredTask.__call__`, so every call's argument has passed FR-63's checks.
- **Discriminated unions.** Every union carried through a store has a `Literal` tag with a distinct
  value per variant (`kind`, `status`, `outcome`, `claim`, `end`, `coordination`, and in filters
  `bound` and `status`), because union decoding takes the first alternative that validates, and
  untagged variants of one shape would change on a JSON round trip.
- **`ManagedTasks` fields** are protocol-typed functions with no defaults, as
  `ConfigurationRepository` holds its functions (`src/haiway/helpers/configuration.py:266-290`), so
  `ManagedTasks()` fails (FR-2).
- **Coordination.** `LocalCoordination` is not stored and not a `State`: it holds a function. Only
  `InMemoryTaskStore` MUST yield it; every other store MUST yield `LeasedCoordination` (FR-78,
  D-104). Each variant carries only what its runtime path needs (D-88).
- **Construction checks.** `NewTask` (its `time_limit`, D-130), `TaskFailed`, `ClaimedAttempt`,
  `ClaimedInterrupted`, `AttemptRetried` and every other type whose requirements state checks
  (FR-90, FR-95, FR-97, FR-98) apply them on direct construction, on decoding, in `updating(...)`
  and in `copy.replace`; each overrides `updating`, since the native `State` runs it without the
  class's own `__init__` (D-126).
- **Failure unions.** `AttemptFailure` holds the failures an attempt run by a runtime can end with,
  each retryable; `TaskFailure` adds `TaskUnsupported`, which only `fail_unsupported` records
  (FR-93), so an `AttemptRetried` carrying it cannot be built (FR-66).
- **Filters.** Each filter part carries only its own data: a finish-time range exists only inside
  `TasksFinished`, and a time range has one variant per kind of bound instead of two optional
  bounds. An empty set in `names`, `statuses` or `outcomes` matches no task (FR-95).
- **Absent parameters.** A parameter defaulting to `None` (`store`, `name` and `retry` of
  `managed_task`, `time_limit`, `concurrency_limit`, both `observability` parameters, and `tasks` of
  `ServerContext`) means the option is not given, as `ctx.scope(observability=None)` does
  (`access.py:153-161`).
- **Class methods, not `statemethod`s** (D-4): the `statemethod` descriptor is generic over the
  method as a whole (`src/haiway/helpers/statemethods.py:12-56`).
- **Everything async** (D-64): every operation of `ManagedTasks`, `ManagedTask` and the store
  session is a coroutine.
- **Exports.** Every public name above is exported from `src/haiway/__init__.py`, and the context
  types also from `src/haiway/context/__init__.py` (NFR-7).

### Errors

| Operation                                                                                                                                                              | Error                                                           | Condition                                                                                                                                                                      | State left                      |
| ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------- |
| `ctx.managed_tasks(...)` entry                                                                                                                                         | `RuntimeError`                                                  | The `TaskRuntime` object was entered before (FR-46)                                                                                                                            | Nothing entered                 |
| `ctx.managed_tasks(...)` entry                                                                                                                                         | any                                                             | The store's `open` raises (FR-55)                                                                                                                                              | Nothing entered                 |
| `ManagedTasks.spawn`, `task`, `tasks`                                                                                                                                  | `ContextStateMissing`                                           | No `ManagedTasks` in the context, including outside any scope (FR-5)                                                                                                           | Nothing started; nothing called |
| `ManagedTasks.spawn`                                                                                                                                                   | `RuntimeError`                                                  | The runtime is closing or closed (FR-31), or another event loop (FR-34)                                                                                                        | Nothing started                 |
| `ManagedTasks.spawn`                                                                                                                                                   | `ValueError`                                                    | `time_limit` is not a finite number above 0 (FR-27); the call's name is not listed, or its registration differs from the listed task's (FR-62, FR-99)                          | Nothing stored                  |
| `ManagedTasks.spawn`, `task`, `tasks`, handle operations                                                                                                               | the store's                                                     | A store operation fails (FR-82)                                                                                                                                                | What the store left             |
| `RegisteredTask.__call__`                                                                                                                                              | `TypeError`                                                     | The argument is not an instance of the parameter's class (FR-62)                                                                                                               | Nothing created                 |
| `RegisteredTask.__call__`                                                                                                                                              | `ValueError`                                                    | The argument fails to encode or to decode back, or holds U+0000 in a string (FR-63)                                                                                            | Nothing created                 |
| `managed_task(...)` application                                                                                                                                        | `TypeError`                                                     | The function does not take exactly one positional `State` parameter without a default, or does not return a `State` class or `None` (FR-62, FR-76)                             | Nothing registered              |
| `managed_task(...)`                                                                                                                                                    | `ValueError`                                                    | `name` is given and empty (FR-62)                                                                                                                                              | Nothing registered              |
| `managed_task(...)` application                                                                                                                                        | `TypeError`                                                     | `name` is absent and the function has no `__name__` of type `str` (FR-91)                                                                                                      | Nothing registered              |
| `managed_task(...)` application                                                                                                                                        | `ValueError`                                                    | `name` is absent and the function's `__name__` is empty (FR-91)                                                                                                                | Nothing registered              |
| `TaskTimeFrom(...)`, `TaskTimeBefore(...)`, `TaskTimeBetween(...)`                                                                                                     | `ValueError`                                                    | A bound is a naive `datetime`, or `start` is not earlier than `end` (FR-95)                                                                                                    | Nothing created                 |
| `ManagedTasks.tasks`                                                                                                                                                   | `RuntimeError`                                                  | Another event loop (FR-34), or the runtime is `closed` (FR-94)                                                                                                                 | Nothing changed                 |
| `ManagedTasks.tasks`                                                                                                                                                   | `ValueError`                                                    | `pagination.limit` outside 1 to 1,000, a token that is not `None` and that `TaskKeyset.from_json` rejects (a naive time included), or non-empty `pagination.arguments` (FR-94) | No store call made              |
| `TaskRuntime(...)`, `InMemoryTaskStore(...)`, `TaskRetryPolicy(...)`, `LeaseTimings(...)`                                                                              | `ValueError`                                                    | A setting is out of its range (FR-45)                                                                                                                                          | Nothing created                 |
| `NewTask(...)`, its `updating(...)` and `copy.replace`                                                                                                                 | `ValueError`                                                    | `time_limit` is given and is not a finite number above 0 (D-130)                                                                                                               | Nothing created                 |
| `TaskFailed(...)`, `ClaimedAttempt(...)`, `ClaimedInterrupted(...)`, `AttemptRetried(...)`, and `updating(...)` or `copy.replace` of any type with construction checks | `ValueError`                                                    | `attempts` below 1, `attempt` below 1, a `delay` not finite or below 0, or another check of its class failing (D-126)                                                          | Nothing created                 |
| A store's `insert`                                                                                                                                                     | `ValueError` (`InMemoryTaskStore`), the store's error (TDS-002) | The store already holds the identifier (FR-78, D-123)                                                                                                                          | Nothing changed                 |
| A store's `claim`, `query`                                                                                                                                             | `ValueError`                                                    | `limit` is negative (FR-78, D-124)                                                                                                                                             | Nothing changed                 |
| `StoredTask(...)`                                                                                                                                                      | `ValueError`                                                    | An invariant of FR-90 does not hold                                                                                                                                            | Nothing created                 |
| `StoredTask.from_json(...)`                                                                                                                                            | `ValueError`                                                    | An invariant of FR-90 does not hold                                                                                                                                            | Nothing created                 |
| `ManagedTask.status`, `outcome`, `cancel`                                                                                                                              | `RuntimeError`                                                  | Another event loop (FR-34), or after its runtime closed, except for an outcome the close fixed (FR-77)                                                                         | Nothing changed                 |
| `ManagedTask.status`, `outcome`, `cancel`                                                                                                                              | `LookupError`                                                   | The store no longer holds the task (FR-70)                                                                                                                                     | Nothing changed                 |
| `TaskSummary(...)`, `TaskSummaryFinished(...)`, `TaskKeyset(...)`                                                                                                      | `ValueError`                                                    | An invariant of FR-98 does not hold, or a time is naive (FR-97, FR-98)                                                                                                         |                                 |
| `ManagedTasks.task`                                                                                                                                                    | `RuntimeError`                                                  | The runtime is `closed` (FR-70, D-118)                                                                                                                                         | Nothing changed                 |
| `ManagedTask.outcome`                                                                                                                                                  | `ValueError`                                                    | The task's registered name is not listed in this runtime (FR-70)                                                                                                               | Nothing changed                 |
| `ctx.spawn`, `ContextTaskGroup.run`                                                                                                                                    | `ContextMissing`                                                | Called outside any scope or task group (FR-69)                                                                                                                                 | A given coroutine closed        |

`await ManagedTasks.available()` never raises (FR-3). `ManagedTask.outcome()` never raises the
task's exception (FR-21).

## Data model

### Stored task

A store holds, per registered task, exactly `StoredTask` (D-67, D-72) and its own bookkeeping
(FR-78). Each status variant carries only its own data, tagged by a `Literal` field (AGENTS.md
Golden Rule). The attempt number, each attempt's number (its position plus 1), a failed task's
failure and its count of attempts are derived from `StoredTask.attempts`, their one source (D-84).
`StoredTask` rejects at construction the combinations of status and attempts that FR-90 lists; other
combinations no transition produces are not checked (D-125).

| Variant              | Data                                                               |
| -------------------- | ------------------------------------------------------------------ |
| `StoredQueued`       | Nothing; due on the store's clock (store bookkeeping).             |
| `StoredRunning`      | Whether a cancel was requested; leased in the store's bookkeeping. |
| `StoredRetryPending` | Nothing; due after its retry delay (store bookkeeping).            |
| `StoredFinished`     | The outcome: a completed result as JSON, failed, or cancelled.     |

An attempt's end, as the runtime reports it to `record`, is one `AttemptEnd` variant carrying
exactly its data (D-84):

| Variant            | Data                                               | Status `record` fixes without a pending cancel |
| ------------------ | -------------------------------------------------- | ---------------------------------------------- |
| `AttemptCompleted` | The returned value as JSON                         | `finished`, `StoredCompleted`                  |
| `AttemptRetried`   | The failure, and the delay before the next attempt | `retry_pending`                                |
| `AttemptFailed`    | The failure; no retry follows                      | `finished`, `StoredFailed`                     |
| `AttemptCancelled` | Nothing; a cancel requested in the running runtime | `finished`, `StoredCancelled`                  |

A task failed as unsupported reaches no `record`: the store's `fail_unsupported` records one attempt
ending in `TaskUnsupported` and fixes `finished`, `StoredFailed` in one operation (FR-78, FR-93).
That attempt runs nothing (glossary "Attempt"); as for every failed task, the failure and the count
are derived from `StoredTask.attempts` (D-84, FR-90).

Each store also keeps the listing timestamps of every task it holds, as store bookkeeping (FR-78,
FR-96); a listed `TaskSummary` shows them (FR-98).

### Runtime records

The runtime keeps, for each task whose attempt it runs: the task call's registered task, the origin
(when known), the local attempt with its termination (none requested, or requested with its reason
`cancel`, `time_limit` or `close`, FR-26, FR-40) or, apart from those, discarding after a lost lease
or a `record` returning `None` (FR-83, D-132), and whether its `record` is pending or failed
(FR-82). Every status and outcome is read from the store (FR-70).

### Runtime state

`open` (accepting spawns), `closing` (exit begun), `closed` (exit returned). `available()` returns
`True` only in `open`; spawning raises `RuntimeError` in `closing` and `closed` (FR-31). Handle
operations, lookups and listings are served in `closing`, possibly prolonging the close until they
return, and raise `RuntimeError` in `closed`, the runtime's own state, so a call queued behind the
close's last step is refused although issued while `closing` (FR-29, FR-77, D-139, D-142).

### Task status transitions

| Event                                                                                          | `queued`                                              | `running` (no termination requested here)                                                                                                                   | `running` (termination requested here)                                                                                                               | `retry_pending`                                       | `finished`   |
| ---------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- | ------------ |
| A runtime has a slot and claims it (FR-85)                                                     | → `running`                                           | Impossible                                                                                                                                                  | Impossible                                                                                                                                           | → `running` once due                                  | Impossible   |
| Attempt returns (FR-20)                                                                        | Impossible                                            | → `finished`, `TaskCompleted`                                                                                                                               | Per the request (FR-40)                                                                                                                              | Impossible                                            | Impossible   |
| Attempt fails, retries remain, before a close's request (FR-66, FR-29)                         | Impossible                                            | → `retry_pending`                                                                                                                                           | Request `time_limit`: as the cell to the left; otherwise per request                                                                                 | Impossible                                            | Impossible   |
| Attempt fails, no retry remains                                                                | Impossible                                            | → `finished`, `TaskFailed`                                                                                                                                  | Request `time_limit`: → `finished`, `TaskFailed`; otherwise per request                                                                              | Impossible                                            | Impossible   |
| A cancel is pending in the store when `record` or `release` runs (FR-78)                       | Impossible                                            | → `finished`, `TaskCancelled`                                                                                                                               | → `finished`, `TaskCancelled`                                                                                                                        | Impossible                                            | Impossible   |
| `KeyboardInterrupt` or `SystemExit` (FR-40)                                                    | Impossible                                            | Re-raised; nothing recorded                                                                                                                                 | Same                                                                                                                                                 | Impossible                                            | Impossible   |
| `cancel()` (FR-26, FR-86)                                                                      | `"cancelled"`; → `finished`, `TaskCancelled`          | `"pending"`; cancel requested in the store and delivered to the attempt                                                                                     | `"pending"`; cancel requested in the store unless already requested there                                                                            | `"cancelled"`; → `finished`, `TaskCancelled`          | `"finished"` |
| Time limit of the attempt elapses, counted from its claim (FR-27)                              | Impossible: not claimed                               | → `running`, request `time_limit`                                                                                                                           | No change                                                                                                                                            | Impossible: not claimed                               | No change    |
| Close begins (FR-29)                                                                           | Unchanged in the store                                | Grace period, then request `close`                                                                                                                          | Grace period, then cancellation delivered again (FR-29 step 3); the earlier request decides the ending (D-128)                                       | Unchanged in the store                                | No change    |
| Attempt ends after request `close` (FR-29)                                                     | Impossible                                            | Impossible                                                                                                                                                  | Released: TDS-002 store → `queued`, or `finished`, `TaskCancelled` when a cancel was requested; in-memory store → `finished`, `TaskCancelled` (D-88) | Impossible                                            | Impossible   |
| `record` fails (FR-82)                                                                         | Impossible                                            | Stays `running`; `record` retried at each renewal tick while the lease is renewed; during a close, until the grace period has elapsed from the close (D-99) | Same                                                                                                                                                 | Impossible                                            | Impossible   |
| Lease expires, claimed by another runtime (FR-84; stores with lease timings only)              | Impossible                                            | Interrupted attempt recorded: → `retry_pending` or `finished`, `TaskFailed(TaskInterrupted)`; `TaskCancelled` when a cancel was requested                   | Same                                                                                                                                                 | Impossible                                            | Impossible   |
| Lease expires, claimed by the runtime holding it (FR-84, D-81; stores with lease timings only) | Impossible                                            | Still running there: claim discarded, the attempt continues under the new lease; otherwise as the row above                                                 | Same                                                                                                                                                 | Impossible                                            | Impossible   |
| A runtime with `unsupported="fail"` not listing its name runs a claim round (FR-93)            | Once due: → `finished`, `TaskFailed(TaskUnsupported)` | No change (D-116)                                                                                                                                           | No change (D-116)                                                                                                                                    | Once due: → `finished`, `TaskFailed(TaskUnsupported)` | No change    |

## Behavior requirements

Acceptance criteria that name no store run with the in-memory store under `make test`, and with
TDS-002's store only when a PostgreSQL DSN is set locally (TDS-002 NFR-3); no CI job runs them
against PostgreSQL (D-76). `TaskRuntime(...)` in an acceptance criterion stands for a runtime with
the listed registered tasks and the other settings the criterion names. TDS-002's store stands for
`PostgresTaskStore(pool, lease=1.0, lease_renewal=0.3, polling=0.1)`, and "a leased test store" for
an in-process test double of FR-78 yielding
`LeasedCoordination(timings=LeaseTimings(lease=1.0, renewal=0.3, polling=0.1))`, unless the
criterion gives other timings; these are test values, not defaults (D-88).

### FR-1 — Task manager entered through a disposable (withdrawn)

Withdrawn by D-39: ManagedTasks is entered as its own block, not a disposable (FR-55).

### FR-2 — No ManagedTasks is never constructed

**Statement:** `ctx.state(ManagedTasks)` in a context without a ManagedTasks block MUST raise
`ContextStateMissing`, and MUST NOT store any `ManagedTasks` in the scope's state.

**Rationale:** `ctx.state` constructs and stores a default when the constructor succeeds
(`state.py:50-65`); no `ManagedTasks` field has a default, so construction fails.

**Acceptance:**

1. Given `ctx.scope("s")` with no block, when `ctx.state(ManagedTasks)` is called, then it raises
   `ContextStateMissing`, and afterwards `ctx.contains_state(ManagedTasks)` is `False`.

**Source:** derived from D-5 and `state.py:50-58`: D-5's error holds only if reading never creates
one.

### FR-3 — Availability check

**Statement:** `await ManagedTasks.available()` MUST return `True` exactly when the context holds a
`ManagedTasks` whose runtime is `open`, and `False` otherwise, without raising.

**Rationale:** D-4; draive uses it (draive TDS-004 HM-9, withdrawn there in favour of this check).

**Acceptance:**

1. Outside any scope and outside any block, it returns `False`.
1. In the body of `ctx.managed_tasks(TaskRuntime(...))`, it returns `True`.
1. In a task still running while its block closes with a grace period of 1 s, it returns `False`.

**Source:** D-4, D-64.

### FR-4 — Spawning a registered task

**Statement:** `await ManagedTasks.spawn(call, time_limit=...)`, called from any scope at any depth
inside a block, MUST return a `ManagedTask` once the store has recorded the task (FR-78), and MUST
NOT tie the task to the spawning scope: that scope's exit MUST NOT wait for, cancel or fail the
task. The docstring of `ManagedTasks.spawn` and the documentation MUST state that the store records
the task through its own connections, so a task spawned inside an application transaction is stored
at once, can start before that transaction commits and survives its rollback, and that applications
spawn after commit (D-92); and that a spawn whose caller is cancelled while the store records the
task lets the insert finish, so the task can be stored and run although the caller got no handle
(D-138).

**Rationale:** HM-1, D-40, D-66. Recording before returning lets the caller hand out the identifier,
and lets any runtime sharing the store run the task. D-92 keeps one spawn path and documents the
limit review 2 S3 found.

**Acceptance:**

1. Given a block, and a scope nested 5 levels below its body that spawns a registered task sleeping
   1 s, when the nested scope's body ends, then its `async with` returns before the task finishes,
   and the task's outcome is later `TaskCompleted`.
1. Once `spawn` returned, `TaskStoreSession.read({identifier})` holds the handle's identifier, for
   the in-memory store and for TDS-002.
1. The docstring of `ManagedTasks.spawn` contains "transaction".

**Source:** HM-1; D-40, D-46, D-66, D-77, D-92, D-138.

### FR-5 — Spawning without ManagedTasks

**Statement:** `ManagedTasks.spawn`, `ManagedTasks.task` and `ManagedTasks.tasks` (FR-94) MUST raise
`ContextStateMissing` when no `ManagedTasks` is in the context, including outside any scope, and
MUST start nothing and call no task function.

**Rationale:** D-5. Outside any scope with no `ContextState` set, `ctx.state` would raise
`ContextMissing` (`state.py:67-71`); one error covers both cases, as D-43 had asked and D-78 keeps
for the remaining entry points. A task call holds an encoded argument, not a coroutine, so nothing
needs closing.

**Acceptance:**

1. In a scope without a block, `await ManagedTasks.spawn(report(Request()))` raises
   `ContextStateMissing`, and `report`'s function was never called.
1. Outside any scope, `await ManagedTasks.task(uuid4())` raises `ContextStateMissing`.

**Source:** D-5, D-78, D-109.

### FR-6 — Captured state (withdrawn)

Withdrawn by D-22: a managed task captures none of the spawning scope's state. Its state is FR-9.

### FR-7 — Disposed state raises (withdrawn)

Withdrawn by D-22, which makes D-1 moot; `ContextStateDisposed` is not added.

### FR-8 — Disposal markers (withdrawn)

Withdrawn with FR-7 (D-22).

### FR-9 — Task state

**Statement:** The state of every attempt scope MUST be, from lowest to highest priority:

1. the root snapshot's state of the runtime running the attempt (FR-56);
1. the owner, that runtime's `ManagedTasks` (FR-58);
1. the state of the preset that the root snapshot's presets registry holds under the task's name
   (D-60), resolved anew for each attempt.

Nothing else MUST be present: no state of the spawning scope, and no state passed at spawn. A task
receives data only through its argument.

**Rationale:**

- D-68: every attempt builds its context fresh from the runtime's root; context changes come from
  code inside the function or from the argument State.
- `ctx.scope` orders contextual state below preset state (`access.py:170-177`); the owner sits above
  the snapshot so that in nested blocks the task's own block wins (FR-58, restating D-28).
- Presets are resolved per attempt, since preparation failures are retried (D-55) and preset
  factories run on every resolution (`presets.py:46-52`).
- The runtime adds no state exposing the task's identifier or attempt; keys a task needs travel in
  its argument (D-95).

**Acceptance:**

1. Given an enclosing scope holding `A(n=0)` when the block is entered and a registry preset named
   like the task yielding `A(n=1)` and `B()`, an attempt reads `A(n=1)` and `B()`; without the
   preset it reads `A(n=0)`.
1. A non-default-constructible `C` set only in the spawning scope raises `ContextStateMissing` in
   the task.
1. A preset whose disposable counts its entries is entered once for each of three attempts.

**Source:** D-60, D-68, D-77, D-95; derived from `access.py:170-177`; D-28 restated.

**Depends on:** FR-56, FR-58, FR-74.

### FR-10 — Task context

**Statement:** The runtime MUST run each attempt's task scope in a new `contextvars.Context` built
by `TaskRoot.context(owner)`, which MUST hold, before the task scope is entered, exactly:

- a `ContextState` holding the root snapshot's state and the owner, in the order of FR-9;
- the presets registry of the root snapshot, when the snapshot had one;

and no other context variable, haiway's or the application's.

**Rationale:** D-17 (only haiway's own state passed in) and D-39, D-68 (the root is the task's
context). The registry is set as a value in the new context, not entered: entering one registry
object twice fails its reentrance assertion (`presets.py:300-303`).

**Acceptance:**

1. A `contextvars.ContextVar` set by the application before the block reads as unset in a task.
1. In a task, `ContextIdentifier.current()` is a scope whose root is the task scope
   (`scope_id == parent_id` for the task scope).
1. Two tasks running at once each resolve the registry's preset without an `AssertionError`.

**Source:** D-17, D-39, D-68.

### FR-11 — Task backend

**Statement:** Every task scope and attempt scope MUST record through the runtime's backend,
whatever backend the spawning scope uses.

**Rationale:** D-26: one backend, configured once (now on the runtime, D-66). Presets carry no
backend (`presets.py:67`, `:172`).

**Acceptance:**

1. Given a runtime with a recording backend, and a spawning scope using another recording backend,
   when the task calls `ctx.log_info("x")` after the spawning scope exited, then only the runtime's
   backend receives it, with the attempt scope's identifier.

**Source:** D-26, D-66.

### FR-12 — Origin link

**Statement:**

- `ManagedTasks.spawn` MUST capture the origin with `ObservabilityLink.current()` at the call:
  `trace_id` per FR-71, `scope_id` of the current scope, and `trace_context` as
  `ctx.trace_context()` returns it, bounded by FR-87. Outside any scope, inside a block, the origin
  MUST be absent.
- The origin MUST be stored with the task (`NewTask.origin`).
- Every task scope, in whichever runtime runs the attempt, MUST be entered with the origin as its
  only link when there is one, and with no link otherwise, and MUST start a new trace.

**Rationale:** D-2, D-41 and HM-4; D-72 stores the origin as telemetry metadata so that every
attempt links back to the spawning span. The trace context is the backend's own encoding
(`opentelemetry/observability.py:702-715`).

**Acceptance:**

1. With a recording backend, the task scope's entering receives `links` of length 1 whose `scope_id`
   equals the spawning scope's id, and in the task `ctx.trace_id()` differs from the spawning
   scope's.
1. A task spawned in the block's body outside any scope enters its task scope with `links == ()`.
1. With TDS-002, a task claimed by a second runtime enters its task scope with the same link as the
   spawning runtime would have used.

**Source:** D-2, D-41, D-72, D-77; HM-4.

### FR-13 — Protocol change for links

**Statement:** `ObservabilityScopeEntering.__call__` MUST take a keyword-only parameter
`links: Sequence[ObservabilityLink]`. Every scope MUST be entered with an empty sequence, except a
task scope entered through `TaskRoot.task_scope` (FR-74), whose links are those of FR-12.

**Rationale:** The protocol receives only the identifier today (`observability.py:205-216`). An
empty sequence stands for "no links". With completion callbacks withdrawn (D-42), no scope other
than the task scope has an origin to link to, which removes v0.5's contradiction between "only task
scopes" and its linked manager scopes.

**Acceptance:**

1. Pyright strict passes with both built-in backends implementing the new signature.
1. A backend recording `links` sees `()` for every `ctx.scope(...)` and for every attempt scope.

**Source:** derived from D-41 and `observability.py:205-216`: a link cannot reach a backend
otherwise.

### FR-14 — Link in `LoggerObservability`

**Statement:** When a scope is entered with links, `LoggerObservability` MUST start a new trace and
log exactly one record at INFO, with the scope's prefix, carrying each link's trace id and scope id
as hex. It MUST NOT repeat them on later records of that scope.

**Rationale:**

- D-2: "the logger records the origin trace id and scope id once".
- Later records carry the task's trace id in their prefix (`observability.py:393-402`), so they join
  the link record by trace id.
- INFO is the lowest level a default INFO logger emits; scope lifecycle is DEBUG
  (`observability.py:459-464`). See [Conflicts](#conflicts).

**Acceptance:**

1. Given a logger at INFO, when a task scope is entered and its attempt logs twice, then exactly one
   INFO record contains the spawning trace id and scope id in hex, and all three records carry the
   task's trace id prefix.

**Source:** D-2, D-41; HM-4.

### FR-15 — Link in the OpenTelemetry adapter

**Statement:** When a scope is entered with links, the OpenTelemetry adapter MUST:

- start its span with an empty parent context, ignoring the adapter's remote context and the ambient
  context;
- pass to `start_span` one `Link` per link whose `trace_context` yields a valid remote
  `SpanContext`;
- for a link that does not yield one, log a WARNING without the carrier values and create no `Link`.

**Rationale:**

- D-2, and links added at creation (OpenTelemetry specification v1.63.0, per WebExplorer).
- The per-request adapter would otherwise parent a root on the caller's `traceparent`
  (`opentelemetry/observability.py:869-873`).

**Acceptance:**

1. With an OpenTelemetry backend and an in-memory span exporter, a task spawned in a request scope
   that has already ended exports a task span whose `parent` is `None`, whose `links[0].context` has
   the request span's trace id and span id, and whose trace id differs from the request's.
1. A link with an empty `trace_context` produces a span without links and one WARNING.

**Source:** D-2, D-41; HM-4.

### FR-16 — Scope names and kind

**Statement:** The task scope and every attempt scope of a task MUST be named by the task's
registered name, and their kind MUST be `internal`. `ManagedTask.name` MUST return that name.

**Rationale:** The registered name is the stable identity (D-46), and the attempt scope's name
selects the preset (D-60). D-7's qualified name served ad-hoc tasks only, which D-77 removes.

**Acceptance:**

1. A task of `managed_task(name="reports.build")` has `name == "reports.build"`, and its records are
   prefixed with `[reports.build]`.

**Source:** D-7 (in part), D-46, D-60, D-77.

### FR-17 — Scoped work inside a task

**Statement:** Inside an attempt scope:

- `ctx.spawn` MUST run tasks in the attempt scope's task group, joined at the attempt's end;
- `ctx.send` and `ctx.subscribe` MUST use the task scope's event bus;
- `ManagedTasks.spawn` MUST spawn through the owner.

**Rationale:** Every scope owns a task group, and a root scope owns an event bus
(`scope.py:103-118`); the owner is in the task state (FR-9).

**Acceptance:**

1. In a task, `await ctx.spawn(helper)` returns `helper`'s result.
1. A subscription made in the task receives an event sent in the task.
1. `await ManagedTasks.spawn(other(Request()))` in a task returns a handle whose outcome is
   `TaskCompleted`.

**Source:** derived from FR-9, FR-74 and `scope.py:103-118`.

### FR-18 — Task identifier

**Statement:** Each `ManagedTask.identifier` MUST be a `UUID` from `random_identifier()`, unique
among the tasks a runtime can look up (FR-70). Documentation and docstrings MUST state that it is
not a credential.

**Rationale:** D-8. The generator is not cryptographic (`identifier.py:49-62`).

**Acceptance:**

1. 10,000 spawned tasks have 10,000 distinct identifiers.
1. The docstring of `ManagedTask.identifier` contains "not a credential".

**Source:** D-8.

### FR-19 — Task status

**Statement:** `await ManagedTask.status()` MUST return the status of the task's record
([Data model](#data-model)): `"queued"` while an attempt waits for a slot, `"running"` while an
attempt runs, `"retry_pending"` during a retry delay, and `"finished"` once the outcome is fixed.

**Rationale:** D-42 (status through the handle); D-47 adds the retry delay.

**Acceptance:**

1. With `concurrency_limit=1` and one running task, a second task reports `"queued"`, then
   `"running"` after the first finished, then `"finished"`.
1. A task failing its first attempt under `TaskRetryPolicy(delays=(0.2,))` reports `"retry_pending"`
   0.1 s after the failure.

**Source:** D-42, D-47.

### FR-20 — Outcome

**Statement:** Every task MUST end with exactly one outcome of `TaskOutcome[Result]`, fixed once:

| Outcome         | When                                                                                                                                                                      |
| --------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TaskCompleted` | An attempt returned and its attempt scope exited without error, with the value                                                                                            |
| `TaskFailed`    | The last attempt failed and no retry remains (FR-66), or a runtime failed the task as unsupported (FR-93), with that attempt's failure and the number of counted attempts |
| `TaskCancelled` | A cancellation was requested through `cancel()` (FR-25, FR-26)                                                                                                            |

An attempt's failure MUST be:

- `TaskFailureRecord(kind="raised", exception_type=...)`, when the function raised, or its attempt
  scope's exit raised after it, a returned value then being lost (D-30 read with D-70, D-85); a
  single error raised by the scope's task group is that error, several are its exception group
  (`tasks.py:401-422`);
- `TaskFailureRecord(kind="preparation", exception_type=...)`, when preparing the attempt raised
  (FR-47);
- `TaskTimeLimitExceeded` with the limit, when its time limit elapsed first (FR-27);
- `TaskInterrupted`, when the runtime that ran the attempt stopped without closing (FR-84); it
  carries nothing;
- `TaskUnsupported`, when a runtime with `unsupported="fail"` that does not list the task's
  registered name found it due (FR-93); it carries nothing, the name being the task's (D-111).

`exception_type` MUST be the exception's `__module__` and `__qualname__` joined by `.`. The live
exception MUST only be logged (FR-24). An attempt ending with `CancelledError` while no termination
was requested MUST give `TaskCancelled`.

The value MUST be the result decoded from the store (FR-76), so that every handle, in any runtime,
sees equal outcomes.

**Rationale:** D-25, D-30, D-48, D-65 and D-70: failures are stored as kind plus type name, and
every store returns identical outcomes; `TaskFailureRecord` is the one failure form, not a restored
variant (D-62 simplified).

**Acceptance:**

1. A registered task returning `R(n=5)` yields `TaskCompleted` whose value equals `R(n=5)`, with the
   in-memory store and with TDS-002, and a handle obtained by lookup in a second runtime sharing the
   store yields an equal outcome.
1. A task raising `ValueError("x")` with no retries yields `TaskFailed` whose failure is
   `TaskFailureRecord(kind="raised", exception_type="builtins.ValueError")`, and `attempts == 1`.
1. A task whose `ctx.spawn` subtask raises `KeyError` yields
   `exception_type == "builtins.KeyError"`, not a group type.
1. A task whose function returns and whose preset disposable's `__aexit__` raises `OSError` yields
   `TaskFailureRecord(kind="raised", exception_type="builtins.OSError")`.

**Source:** D-25, D-30, D-48, D-65, D-70, D-85, D-110, D-111; HM-3, HM-7, HM-8.

### FR-21 — Awaiting the outcome

**Statement:** `await ManagedTask.outcome()` MUST return the outcome once it is fixed, MUST NOT
raise the task's exception, and MUST NOT affect the task when the awaiting coroutine is cancelled.
Every awaiter MUST receive an equal outcome. For a task whose attempt runs in this runtime and whose
`record` has not failed, the awaiter MUST be woken when `record` returns the status it fixed.
Otherwise, including after a failed `record` or a `record` that returned `None` (FR-82), the runtime
MUST learn the outcome by reading the store: at every polling tick of a `LeasedCoordination`, and
after every change a `LocalCoordination` reports (FR-85), it MUST call the store's `read` once with
the set of every identifier any of its awaiters waits on, and wake the awaiters of each task the
read returns finished.

**Rationale:** D-42, D-57: awaiting the handle is how consumers learn of completion; D-70: outcomes
are equal whatever reads them. D-81: a `record` whose commit succeeded but whose reply was lost
leaves only the store knowing the outcome. D-83: one read per tick bounds the store load by the
polling interval, not by the number of awaiters (review 2 S6). D-88: the in-memory store has no
polling interval and reports its changes instead.

**Acceptance:**

1. Two awaiters of a failing task both receive equal `TaskFailed` outcomes.
1. Cancelling an awaiter leaves the task `running`; it later yields `TaskCompleted`.
1. With a leased test store and 50 awaiters of 50 tasks run by another runtime, a store counting
   calls records at most one `read` per polling interval from the awaiting runtime.
1. With a leased test store whose `record` commits and then raises, the awaiter is woken with
   `TaskCompleted` within two polling intervals.
1. With two runtimes on one `InMemoryTaskStore`, an awaiter in the runtime that did not run the task
   receives its outcome within 0.1 s of the `record`.

**Source:** D-42, D-57, D-70, D-81, D-83, D-88; HM-7.

### FR-22 — Completion callback (withdrawn)

Withdrawn by D-42: completion callbacks are removed.

### FR-23 — Failure isolation

**Statement:** A task's failure, cancellation, time limit, retry or interruption MUST NOT cancel,
fail or delay any other managed task, the runtime, the block or the spawning scope.

**Rationale:** D-49.

**Acceptance:**

1. Of two tasks spawned together, one raising `ValueError` and one returning, the first yields
   `TaskFailed` and the second `TaskCompleted`; the spawning scope and the block exit without error.
1. A task in a retry delay does not delay another task's start under `concurrency_limit=1`.

**Source:** D-49.

### FR-24 — Attempt and task failure records

**Statement:**

- When an attempt fails and a retry follows, the runtime MUST log one WARNING record in the task
  scope, after the attempt scope exited, carrying the task identifier, the attempt number, the
  failure kind, the exception type name (or the time limit) and the retry delay.
- When a task ends with `TaskFailed`, it MUST log one ERROR record in the task scope, after the last
  attempt scope exited, with the same fields and the number of attempts; a task failed as
  unsupported has no task scope and is logged by FR-93 instead.
- Neither record MUST carry the exception's message, arguments or traceback; the backend records the
  live exception when it leaves the attempt scope (`observability.py:684-696`), which is the only
  place it is logged (D-70).

**Rationale:** AGENTS.md (surface errors, never log secrets); D-70.

**Acceptance:**

1. Under `TaskRetryPolicy(delays=(0.01,))`, a task raising `ValueError("secret")` twice produces
   exactly one WARNING and one ERROR record of the runtime, both naming `builtins.ValueError` and
   the task identifier, neither containing `"secret"`.

**Source:** derived from AGENTS.md Logging and Security; D-47, D-70, D-110.

### FR-25 — Cancelling a running task

**Statement:** `await ManagedTask.cancel()` on a `running` task with no cancel requested in the
store MUST request cancellation of the attempt and return `"pending"`; the outcome MUST be
`TaskCancelled` once the attempt ends (FR-40), and no retry follows.

**Rationale:** HM-6, D-91; a cancellation the caller asked for is not undone by a retry.

**Acceptance:**

1. A task sleeping 10 s, cancelled after 0.01 s, returns `"pending"` from `cancel()` and yields
   `TaskCancelled` within 0.1 s, under `TaskRetryPolicy(delays=(0.0,))`.

**Source:** HM-6; D-91.

### FR-26 — Result of `cancel` and the race with completion

**Statement:** `await ManagedTask.cancel()` MUST call the store's `cancel` (FR-78) and return its
result, whatever the calling runtime requested locally for the task (a time limit or a close):

- `"cancelled"` when the task was `queued` or `retry_pending`: it finishes with `TaskCancelled` at
  once;
- `"pending"` when the task was `running`: the store records the cancel request, or keeps the one
  already recorded, and the task finishes with `TaskCancelled` when its attempt ends;
- `"finished"`, changing nothing, when the task was `finished`;
- for a task the store no longer holds, `cancel()` MUST raise `LookupError` naming the identifier
  (FR-70).

A cancel the store accepted MUST win: the next `record` or `release` of that task fixes
`TaskCancelled` (FR-78), whatever the attempt's own end, so a `"pending"` result always ends in
`TaskCancelled`. A call that lands after an attempt's function returned and before its `record`
fixed the outcome (while the attempt scope releases its presets) MUST therefore end in
`TaskCancelled`, and the lost value MUST be logged at WARNING in the task scope, without the value
(D-37).

**Rationale:** D-91 replaces D-31's `bool`, which gave `True` both for a task finished at once and
for one whose cancellation was only requested. D-82: a runtime's local time limit or close is
invisible to the store and to other runtimes, so every cancel goes to the store and every runtime
gets one answer (review 2 S2).

**Acceptance:**

1. After `TaskCompleted`, `cancel()` returns `"finished"`, and `outcome()` returns an equal outcome.
1. Two consecutive `cancel()` calls on a running task both return `"pending"`, and the outcome is
   `TaskCancelled`.
1. With `concurrency_limit=1`, `cancel()` of a queued task returns `"cancelled"`.
1. A task whose preset disposable's `__aexit__` awaits an event, cancelled after its function
   returned, returns `"pending"`, yields `TaskCancelled`, and one WARNING record names the task.
1. A task with `time_limit=0.05` whose cleanup takes 0.2 s after cancellation: `cancel()` at 0.1 s,
   through a handle in the runtime that runs it, returns `"pending"`, and the outcome is
   `TaskCancelled`.
1. With two runtimes on one store, a `cancel()` from the runtime not running the task returns
   `"pending"` and yields `TaskCancelled` even when the attempt returns before the cancel reaches
   the running runtime.

**Source:** D-37, D-48, D-82, D-91.

### FR-27 — Time limit

**Statement:**

- `time_limit` MUST be a finite number of seconds above 0; any other value MUST raise `ValueError`
  at spawn.
- The limit MUST apply to each attempt, counted from the moment the runtime claims that attempt;
  time spent queued, in a retry delay, or released by a close MUST NOT count.
- When it elapses before the attempt ends, the attempt MUST have its cancellation requested with
  reason `time_limit`; its failure MUST be `TaskTimeLimitExceeded`, and the retry policy then
  decides (FR-66).

**Rationale:** D-48 makes exceeding the limit a retryable failure, which is only possible per
attempt. D-89: counting from the claim keeps queue wait and downtime out of the limit, so a deploy
longer than the limit no longer fails released tasks without running them (review 2 S1).

**Acceptance:**

1. A task with `time_limit=0.05` sleeping 1 s yields
   `TaskFailed(TaskTimeLimitExceeded(time_limit=0.05))` between 0.05 s and 0.5 s after its claim
   (NFR-3).
1. Under `TaskRetryPolicy(delays=(0.0,))`, a task with `time_limit=0.05` that sleeps 1 s in attempt
   1 and returns at once in attempt 2 yields `TaskCompleted`.
1. With `concurrency_limit=1`, a task with `time_limit=0.05` queued 0.2 s behind a busy slot and
   returning after 0.01 s yields `TaskCompleted`.
1. `time_limit=0`, `-1`, `float("nan")` and `float("inf")` each raise `ValueError`.

**Source:** D-9, D-48, D-89.

### FR-28 — Manager default time limit (withdrawn)

Withdrawn by D-9: time limits are per task only.

### FR-29 — Close

**Statement:** When a block exits, its runtime MUST, in this order, for every store:

1. set its state to `closing`, stop claiming (FR-80) and reject spawns (FR-31);
1. wait up to the grace period for running attempts to end; an attempt that fails in that period is
   recorded as any failed attempt is, and goes to `retry_pending` when its policy allows another
   attempt (FR-66), resumable by another runtime or by the next start;
1. deliver a cancellation to every attempt still running, those whose termination was already
   requested included; the first request still decides the ending (FR-40), so an attempt past its
   time limit stays `TaskTimeLimitExceeded`; log FR-61's record for each task whose ending the
   close's request decides (D-128);
1. release each cancelled task to the store (FR-78, `release`): a store with lease timings leaves it
   resumable with the attempt not counted (D-56), and the in-memory store finishes it with
   `TaskCancelled` (D-88); either finishes it with `TaskCancelled` when a cancel had been requested;
1. leave tasks that this runtime was not running (queued or retry-pending in the store) unchanged;
1. wait until every attempt it ran has ended and been recorded or released, and until every store
   call already in progress (spawns, lookups, listings, handle operations, claims,
   `fail_unsupported` calls, renewals, polls) has returned; a `record` being retried (FR-82) is
   waited for only until the grace period has elapsed from the start of the close, and then
   abandoned (D-99); a pending `changed()` wait is cancelled at once and its task awaited until it
   ends, not for its result (D-103, D-137); a `ClaimedInterrupted` that a claim in progress returns
   is recorded, bounded by the grace period as a retried `record` is, and a `ClaimedAttempt` it
   returns is released and never run (D-136); handle operations, lookups and listings admitted while
   `closing` are waited for until they return (D-139);
1. close the store session, exit the runtime scope (FR-88), set its state to `closed`, and return.

A cancellation of the exit itself MUST NOT abandon these steps; it MUST be re-raised after the last.
The docstring of `TaskRuntime(grace_period=...)` and the documentation MUST state that with the
default grace period of 0 s a failing `record` is abandoned at once when a close begins, and that an
abandoned task is recovered as `TaskInterrupted` once its lease expires (FR-84).

**Rationale:** D-50 and D-56 (closing closes the tasks; stored work stays resumable where another
process can resume it), D-88 and D-100 (the in-memory store cancels, deliberately also when shared;
on an unshared store no process could reach a released task), D-44 (errors logged), D-10 (grace
period), D-66 (one close for every store), D-74 (a failure in the grace period keeps its normal
retry), D-99 (a close is bounded even when the store is down). No attempt starts during a close
(FR-31). The session closes only once nothing uses it. Disposal is never abandoned
(`disposables.py:200-217`). Only `InMemoryTaskStore` yields `LocalCoordination` (D-104), and it
raises no error on its own loop (FR-80), so a `record` without expiring leases is never retried.

**Acceptance:**

1. With the in-memory store, exiting a block with 3 running tasks sleeping 0.5 s returns within 0.2
   s, and all 3 yield `TaskCancelled`.
1. With `grace_period=0.5`, a running task needing 0.1 s yields `TaskCompleted`, and one needing 1 s
   is cut off after about 0.5 s.
1. With `grace_period=0.5` and `TaskRetryPolicy(delays=(0.0,))`, a task failing during the grace
   period is `retry_pending` in the store afterwards.
1. With TDS-002, a task running at close is `queued` in the store with no attempt recorded
   afterwards, and a new process completes it.
1. With a leased test store whose `record` always raises and `grace_period=0.2`, the close returns
   between 0.2 s and 0.5 s after it began; with the default grace period it returns within 0.1 s.
1. A `ManagedTask.status()` call in progress when the close begins returns before the session
   closes.

**Source:** D-10, D-44, D-50, D-56, D-66, D-74, D-77, D-88, D-99, D-100, D-103, D-104, D-109, D-110,
D-128, D-136, D-137, D-139.

### FR-30 — Grace period setting

**Statement:** `TaskRuntime(grace_period=...)` MUST default to `0.0` s, and MUST be a finite number
of seconds of at least 0.

**Rationale:** D-10. With 0, a close cancels at once (HM-6).

**Acceptance:**

1. `TaskRuntime(...)` without `grace_period` cancels a running task at close without waiting.
1. `TaskRuntime(..., grace_period=-1)` raises `ValueError`.

**Source:** D-10.

### FR-31 — Spawning during or after close

**Statement:** The runtime MUST make `ManagedTasks.spawn` raise `RuntimeError` while it is `closing`
or `closed`, and start nothing.

**Rationale:** A scope task group rejects spawns once joined (`tasks.py:183-185`); accepting tasks
during a close would extend it without bound.

**Acceptance:**

1. A task still running during a close with `grace_period=1.0` calls
   `await ManagedTasks.spawn(other(Request()))` and gets `RuntimeError`; the store holds no new
   task.

**Source:** derived from FR-29 and `tasks.py:183-185`.

### FR-32 — Retention

**Statement:**

- The in-memory store MUST keep a finished task for `InMemoryTaskStore(retention=...)` seconds after
  it finished, and drop it afterwards; this is the only retention setting (D-84).
- It MUST default to `3600.0` and be a finite number of seconds of at least 0, and MUST bound
  retention by time only; its docstring and the documentation MUST state that memory grows with the
  number of tasks finished within one period.
- Once the store no longer holds a task, its handles behave as FR-70 states.

**Rationale:** D-11, D-20, D-59, D-75. With ad-hoc tasks removed (D-77),
`TaskRuntime(retention=...)` has nothing left to retain, so retention is set in one place (review 2
S9). Failures hold no live exceptions (D-70), so the frames named by D-59 are not retained.

**Acceptance:**

1. With `InMemoryTaskStore(retention=0.05)`, a finished task is found by lookup at once, and lookup
   returns `None` 0.2 s after it finished.

**Source:** D-11, D-20, D-59, D-70, D-75, D-84, D-96.

### FR-33 — Concurrency limit and queue

**Statement:**

- `concurrency_limit`, when given, MUST be an integer of at least 1; when absent, every attempt MUST
  start as soon as it is due (D-90).
- With a limit, an attempt that becomes due while that many attempts are `running` MUST wait as
  `queued`.
- Queued attempts MUST start in the order of their due moments, one per freed slot; tasks spawned in
  sequence keep the order of their spawn calls.
- The spawning scope MUST NOT wait for a queued task.
- The docstring of `TaskRuntime(concurrency_limit=...)` and the documentation MUST state that,
  without a limit, the first runtime to claim from a shared store takes every due task, and MUST
  recommend a limit for runtimes sharing a store.

**Rationale:** D-12, D-21, kept by D-90, which asks the documentation to recommend a limit (review 2
S5). Attempts after a retry delay join the same queue, ordered by due moment.

**Acceptance:**

1. With `concurrency_limit=2` and 5 tasks spawned in order A to E, each sleeping 0.05 s, the tasks
   enter `running` in order A to E, and at most 2 are `running` at any time.
1. `concurrency_limit=0` raises `ValueError`.
1. The docstring of `TaskRuntime` contains "concurrency_limit" and "shared store".

**Source:** D-12, D-21, D-90; HM-1, HM-8.

### FR-34 — One event loop

**Statement:** The runtime and the in-memory store MUST serve spawns, lookups, listings (FR-94) and
handle operations only on the event loop the block was entered on, and MUST raise `RuntimeError` on
any other loop. Calls from any number of concurrent tasks on that loop MUST NOT lose, duplicate or
reorder spawns or outcomes.

**Rationale:** HM-10; asyncio tasks of one loop interleave only at `await` points, and haiway's
loop-bound objects are reached from another loop only by scheduling onto theirs
(`utils/loops.py:12-45`).

**Acceptance:**

1. See NFR-1.
1. `ManagedTasks.spawn` called from a second event loop in another thread raises `RuntimeError`.

**Source:** HM-10, D-109.

### FR-35 — `ServerContext` integration

**Statement:**

- When `ServerContext` is given `tasks: TaskRuntime`, its lifespan MUST, after its disposables have
  been entered and before it yields, enter `ctx.updating(*disposable_state, *declared_state)` and
  `ctx.presets(*presets)` and, inside both, `ctx.managed_tasks(tasks, observability=fallback)`,
  where `fallback`, built once per lifespan from its own observability setting, is
  `LoggerObservability(logger, debug_context=False)` for a `Logger` (the default `DEFAULT_LOGGER`
  included, D-102), the backend itself for an `Observability`, and
  `preparing(traceparent=None, tracestate=None)` for an `ObservabilityPreparing`, so that the root
  snapshot holds the state the lifespan prepared and the registry of its `presets` (D-86), and a
  runtime given no backend records through the server's (D-93).
- It MUST add the `ManagedTasks` the block yields to the state of every request scope, after that
  state.
- It MUST exit the block, then the presets registry and the state update, after it resumes from
  serving and before its disposables exit.
- Without `tasks`, no block is entered, and `await ManagedTasks.available()` returns `False` in
  requests.

**Rationale:** D-13, D-39, D-66. The lifespan enters no scope and puts nothing into the context
variables (`starlette/context.py:182-207`), and `ctx.managed_tasks` takes only the runtime and a
fallback backend, so the state and presets have to be current when the block is entered:
`ctx.updating` merges state without a scope (`access.py:256-280`) and `ctx.presets` sets the
registry (`access.py:86-150`). Requests receive that state as explicit state, with the presets
registry entered per request (`middleware.py:184-190`). A mounted sub-app has no lifespan of its own
(Starlette 1.10.0 `routing.py:405`, `Mount.matches`). `ServerContext` wraps its setting into an
`ObservabilityPreparing` (`starlette/context.py:122-145`), which takes the request's trace headers
(`starlette/types.py:33-38`); with none it starts a fresh backend for the runtime (D-93, review 2
S10). A `Logger` there becomes `LoggerObservability(observability)` with the default
`debug_context=__debug__` (`starlette/context.py:133-141`, `observability.py:428-432`), which D-33
rules out for manager backends, hence D-102.

**Acceptance:**

1. A host app with `ServerContext(Config(), disposables=(pool,), tasks=TaskRuntime(...))` and a
   route calling `await ManagedTasks.spawn(work(Request()))` returns a response before the task
   ends; the task reads `Config` and the pool's state, and its outcome is `TaskCompleted`.
1. At shutdown with a running task, the task is cut off before the pool's `__aexit__` starts.
1. A route of a mounted sub-app reaches the host's `ManagedTasks`.
1. A preset given to `ServerContext(presets=...)` and named like a registered task is entered by
   that task's attempt scope.
1. With `ServerContext(observability=preparing, tasks=TaskRuntime(...))` and a recording
   `preparing`, a task's records reach the backend `preparing` returned for
   `traceparent=None, tracestate=None`; with `TaskRuntime(observability=backend)` they reach
   `backend`.
1. With the default `ServerContext` observability, two tasks record through one backend and no tree
   summary is logged at their end (FR-52).

**Source:** D-13, D-39, D-66, D-86, D-93, D-102.

### FR-36 — `ctx.spawn_background` unchanged (withdrawn)

Withdrawn by D-43: `ctx.spawn_background` became a proxy (FR-67), itself withdrawn when D-78 removed
`ctx.spawn_background` (FR-68).

### FR-37 — Spawning during disposable preparation or disposal

**Statement:** `ManagedTasks.spawn` MUST be allowed while a scope's disposables are prepared or
disposed; the origin is then the scope being entered or exited, which is current then
(`scope.py:73-74`).

**Rationale:** D-15, narrowed by D-22. `ctx.spawn` raises there (`tasks.py:286-293`), and its
message points to `ManagedTasks.spawn` once `ctx.spawn_background` is removed (FR-89).

**Acceptance:**

1. A disposable whose `__aenter__` calls `await ManagedTasks.spawn(work(Request()))`, inside a
   block, gets a handle, and the task's link carries the id of the scope being entered.

**Source:** D-15, D-22, D-78.

### FR-38 — Completion callback scope (withdrawn)

Withdrawn by D-42: completion callbacks are removed.

### FR-39 — Callback failure (withdrawn)

Withdrawn by D-42: completion callbacks are removed.

### FR-40 — Termination requests and interpreter exits

**Statement:**

- Once a termination is requested for a running attempt in its runtime (`cancel`, `time_limit` or
  `close`), the attempt's end MUST be decided by the first such request, whatever the function does
  afterwards: `cancel` gives `AttemptCancelled`, `time_limit` gives the failure
  `TaskTimeLimitExceeded`, and `close` gives FR-29's release. A later local request MUST NOT change
  it. A value returned or an `Exception` raised after the request MUST be logged at WARNING in the
  task scope, without its content, and discarded.
- A cancel accepted by the store MUST still win over that end when `record` or `release` runs
  (FR-26, FR-78), so the outcome is then `TaskCancelled`.
- A `KeyboardInterrupt` or `SystemExit` raised by an attempt MUST be re-raised at once, unchanged,
  with nothing recorded: the attempt's end is then found by FR-84 as `TaskInterrupted` when its
  store outlives the process, and is lost with an in-memory store.
- At interpreter exit, store calls that the event loop's shutdown cancels MUST end the runtime with
  nothing recorded, and the `KeyboardInterrupt` or `SystemExit` MUST propagate once, unchanged
  (D-144).

**Rationale:** Cancellation is cooperative in asyncio. D-82 sends every cancel to the store, where
it wins at `record` and `release`; the local first-request rule decides only what the runtime passes
to them. The interpreter exits leave the event loop (CPython `Lib/asyncio/tasks.py`, unverified), so
the asynchronous store write of an outcome cannot complete before them; recording nothing keeps one
path for every store. This replaces D-35 (D-85).

**Acceptance:**

1. A task with `time_limit=0.05` that catches `CancelledError` and returns yields
   `TaskTimeLimitExceeded`, and one WARNING record.
1. A task cancelled through its handle, whose time limit then elapses during its cleanup, yields
   `TaskCancelled`.
1. A task whose time limit elapsed and that is then cancelled through its handle during its cleanup
   yields `TaskCancelled`.
1. With TDS-002, an attempt raising `SystemExit` in a runtime whose loop then stops is recorded by a
   second runtime as `TaskInterrupted` after the lease expires.

**Source:** derived from HM-6, HM-8; D-48, D-65, D-66, D-82, D-85, D-144.

### FR-41 — A queued or retry-pending task that is cancelled

**Statement:**

- A task that is `queued` or `retry_pending` and is cancelled (FR-26) MUST finish with
  `TaskCancelled` at once, with its function not called.
- This ending MUST be recorded at DEBUG in the scope of the `cancel()` caller, since no runtime
  holds a task scope for it.

**Rationale:** D-12, D-48. A task scope exists only from its claim (FR-74). D-89 counts the time
limit from the claim, so an unclaimed task never exceeds it and its "fails without running" case is
removed.

**Acceptance:**

1. With `concurrency_limit=1`, cancelling a queued task yields `TaskCancelled` at once and its
   function was never called.

**Source:** D-12, D-48, D-66, D-77, D-89.

### FR-42 — Spawn and lifecycle records

**Statement:**

- `ManagedTasks.spawn` MUST log at DEBUG in the spawning scope (when there is one), with the task's
  identifier and name.
- The task scope MUST log at DEBUG when each attempt starts and when the outcome is fixed; a task
  failed as unsupported has no task scope, and FR-93's record replaces both.
- No record of the runtime or a store MUST contain the task's argument, its value or an exception
  message.

**Rationale:** AGENTS.md Logging and Security.

**Acceptance:**

1. With DEBUG enabled, a task spawned with an argument `Secret(value="s3")` and returning
   `Secret(value="s4")` produces records that contain its identifier and never `"s3"` or `"s4"`.

**Source:** derived from AGENTS.md Logging, Security; D-110.

### FR-43 — Manager exits before other disposables (withdrawn)

Withdrawn by D-39: the block is not a disposable; its close precedes the release of enclosing
resources by lexical nesting (FR-57).

### FR-44 — Manager enters after other disposables (withdrawn)

Withdrawn by D-39, with FR-43.

### FR-45 — Settings validation

**Statement:** These constructors MUST raise `ValueError`, naming the setting, for a value out of
range:

| Setting                              | Range                                 |
| ------------------------------------ | ------------------------------------- |
| `TaskRuntime(grace_period=...)`      | finite, at least 0 s                  |
| `TaskRuntime(concurrency_limit=...)` | integer, at least 1                   |
| `TaskRuntime(tasks=...)`             | no two tasks with one registered name |
| `TaskRuntime(unsupported=...)`       | `"leave"` or `"fail"`                 |
| `InMemoryTaskStore(retention=...)`   | finite, at least 0 s                  |
| `TaskRetryPolicy(delays=...)`        | every element finite, at least 0 s    |
| `LeaseTimings(lease=...)`            | finite, above 0 s                     |
| `LeaseTimings(renewal=...)`          | finite, above 0 s, below `lease`      |
| `LeaseTimings(polling=...)`          | finite, above 0 s                     |

**Rationale:** FR-30, FR-32, FR-33 and FR-66 define the ranges; a renewal at or above the lease
would let a healthy lease expire. D-88 moves the timings from the runtime to the stores that need
them, which build a `LeaseTimings` (TDS-002 FR-4). Failing at construction fails before an
application starts.

**Acceptance:**

1. Each of `grace_period=-0.1`, `InMemoryTaskStore(retention=float("inf"))`, `concurrency_limit=0`,
   two tasks named `"r"`, `TaskRetryPolicy(delays=(float("nan"),))`, `LeaseTimings` with `lease=0`,
   with `polling=float("nan")`, and with `renewal` equal to `lease` raises `ValueError` whose
   message names the setting.
1. Two functions both named `process`, in two modules, each registered with `managed_task()` and no
   `name`, given together to `TaskRuntime(tasks=...)` raise `ValueError` naming `tasks` (FR-91).
1. `TaskRuntime(unsupported="drop")` raises `ValueError` naming `unsupported`.

**Source:** derived from FR-30, FR-32, FR-33, FR-66; D-88, D-108, D-110.

### FR-46 — One open per runtime and store object

**Statement:** Entering `ctx.managed_tasks(runtime)` with a `TaskRuntime` object that was entered
before MUST raise `RuntimeError` and leave its first opening unchanged. An `InMemoryTaskStore`
object MUST allow several runtimes to open it at once (FR-79); a store object that owns a resource
entered once at a time, such as TDS-002's pool, MAY reject an opening while it is open, and runtimes
then share the store's data through one object each (FR-80).

**Rationale:** One open per runtime keeps one owner of its local records. A store's data is shared
by design (D-66, TDS-002 D-1); the in-memory store's data lives in its object, so sharing it means
sharing the object. By analogy, `PostgresConnectionPool` rejects a second entry
(`client.py:335-339`, per CodeExplorer).

**Acceptance:**

1. Entering `ctx.managed_tasks(runtime)` twice with the same object, nested or in sequence, raises
   `RuntimeError` at the second entry.
1. Two runtimes opened on one `InMemoryTaskStore` object both run tasks from it.

**Source:** derived from D-66 and `client.py:335-339`.

### FR-47 — Preparation failure

**Statement:** When entering an attempt scope raises (a preset's disposable or factory raising), or
entering the task scope raises (FR-74, D-131), the attempt MUST:

- not call the task's function;
- release what it had entered, as a failed scope entry does (`scope.py:135-150`);
- fail with `TaskFailureRecord(kind="preparation", ...)`, unless a termination was requested first
  (FR-40);
- be retried under the retry policy like any failure (D-55).

Decoding the stored argument failing (FR-63) MUST be a preparation failure too.

**Rationale:** D-25, D-55, D-70.

**Acceptance:**

1. A preset whose disposable raises `OSError` in `__aenter__` yields a preparation failure naming
   `builtins.OSError`; the function never ran, and a second preset disposable entered alongside it
   was exited once.
1. Under `TaskRetryPolicy(delays=(0.0,))`, a preset failing only on its first entry yields
   `TaskCompleted`.

**Source:** D-25, D-55, D-70; draive TDS-004 HM-3; D-131.

### FR-48 — Preset release

**Statement:** For every attempt whose preparation completed, every preset disposable MUST be
entered exactly once before the function starts and exited exactly once after it ends, before the
attempt's result is decided, for every ending: returned, raised, cancelled, time limit or close. An
attempt whose preparation failed MUST exit only what it entered (FR-47).

**Rationale:** HM-3. v0.5 claimed release for tasks that never prepared; an attempt that never
entered its presets has nothing to release.

**Acceptance:**

1. A preset disposable counting enters and exits records `(1, 1)` for each of four tasks: one
   returning, one raising, one cancelled through its handle, one exceeding its time limit, each with
   no retries; in each case the exit is recorded before `outcome()` returns.

**Source:** draive TDS-004 HM-3.

### FR-49 — Manager exit awaits completion callbacks (withdrawn)

Withdrawn by D-42: completion callbacks are removed.

### FR-50 — Supervisor stage protocol (withdrawn)

Withdrawn by D-39: no supervisor stage of disposables is needed.

### FR-51 — Supervisor stage failures (withdrawn)

Withdrawn by D-39, with FR-50.

### FR-52 — Default backend

**Statement:** The runtime MUST record every task through, in this order of precedence: the backend
given as `TaskRuntime(observability=...)` (a `Logger` becoming one
`LoggerObservability(logger, debug_context=False)` created at open); else the backend given to
`ctx.managed_tasks(runtime, observability=...)`; else one `LoggerObservability(debug_context=False)`
created at open.

**Rationale:** D-26, D-33, D-93 (the runtime's own setting wins over the block's fallback).
`LoggerObservability()` defaults to `debug_context=__debug__` (`observability.py:428-433`) and then
keeps each scope tree until its root completes (`observability.py:446-450`).

**Acceptance:**

1. Two tasks of a runtime created with no `observability`, in a block given none, record through the
   same backend instance, and no tree summary is logged at their end.
1. A runtime with `observability=a` in a block given `observability=b` records only through `a`;
   without its own setting it records only through `b`.

**Source:** derived from D-26, D-33; `observability.py:426-450`; D-93.

### FR-53 — Wrapping the manager's disposable (withdrawn)

Withdrawn by D-39, with FR-50.

### FR-54 — Each managed task is a separate task tree

**Statement:**

- Each managed task MUST form a task tree of its own, rooted at its task scope, with its attempt
  scopes, their `ctx.spawn` tasks and their nested scopes inside it; the existing rules MUST hold
  inside it: a scope joins its `ctx.spawn` tasks on exit, and cancels them when its body fails or is
  cancelled (`tasks.py:349-368`), except that an isolated scope shields its tasks from a cancelled
  body (`tasks.py:362-367`).
- The task scope MUST NOT be `isolated=True`, so that a cancel, a time limit or a close ends the
  attempt's whole subtree.
- The tree's lifetime MUST be independent of the spawning scope's tree, in both directions.
- A task spawned from inside another managed task MUST form a further separate tree through the
  owner (FR-17); the spawning task's end or cancellation MUST NOT affect it.

**Rationale:** D-32. A root scope owns its task group and event bus (`scope.py:103-118`). With the
background task group removed (D-78), every detached task haiway runs is a managed task.

**Acceptance:**

1. A task whose attempt starts a `ctx.spawn` subtask sleeping 10 s, with `time_limit=0.05`, yields
   `TaskTimeLimitExceeded`, and the subtask has been cancelled when the outcome is fixed.
1. A spawning scope whose body is cancelled right after the spawn leaves the task running; it later
   yields `TaskCompleted`.
1. Task A spawns task B with `ManagedTasks.spawn` and is then cancelled; B later yields
   `TaskCompleted`, and closing the block while B runs cuts B off (FR-29).

**Source:** D-32, D-78; `scope.py:103-118`, `tasks.py:349-368`.

### FR-55 — ManagedTasks block

**Statement:** `async with ctx.managed_tasks(runtime) as tasks:` MUST, on entry:

1. take the root snapshot (FR-56) into a new `TaskRoot`;
1. open the runtime with that root, which opens its store session (FR-78);
1. merge the runtime's `ManagedTasks` into the context of the body, as `ctx.updating` does
   (`access.py:256-280`), and yield it as `tasks`.

A `TaskRuntime` given no `store` MUST use a new `InMemoryTaskStore()` (FR-81). Step 2 MUST start the
runtime's first claim round (FR-85) before the block yields, so that the due stored tasks whose
registered names the runtime lists start running at once, concurrently with the body and without any
spawn, and, with `unsupported="fail"`, its unsupported tasks are failed at once (FR-93); the body
does not wait for them. The docstring of `ctx.managed_tasks`, the docstring of
`ServerContext(tasks=...)` and the documentation MUST state that entering the block starts executing
stored tasks, so a task can start before the body's first statement and, under `ServerContext`,
before the first request is served (D-110). On exit it MUST restore the context state, then close
the runtime (FR-29). A failing store opening MUST propagate its exception and leave nothing entered.
The block MAY be entered outside any scope.

**Rationale:** D-39 (own `ctx` API); D-66 (one runtime, in-memory store by default). Yielding the
`ManagedTasks` lets code that owns no scope of its own, such as `ServerContext`'s lifespan, hand it
to the scopes it enters (D-86, review 2 M2).

**Acceptance:**

1. In the body, `ctx.state(ManagedTasks)` returns the yielded instance; after the block,
   `ctx.contains_state(ManagedTasks)` is `False`.
1. A store whose `open` raises `OSError` makes the `async with` raise that `OSError`.
1. An `InMemoryTaskStore` holds a due `queued` task of a listed name, left by a closed runtime,
   whose function sets an `asyncio.Event`: in a second block whose body spawns nothing and waits up
   to 0.1 s for that event, the event is set.
1. The docstring of `ctx.managed_tasks` contains "starts executing".

**Source:** D-39, D-66, D-86, D-110.

### FR-56 — Root snapshot

**Statement:** The root snapshot MUST be taken once, at block entry, before `open`, and MUST hold:

- the `State` instances of `ContextState.snapshot()` at that moment (`state.py:15-21`), in their
  order, or none outside any state;
- the innermost presets registry current at that moment, or none.

It MUST NOT change afterwards: state merged or presets entered inside the block's body, including by
scopes entered there, MUST NOT reach any task.

**Rationale:** D-39 ("when entered it snapshots its parent context's state and presets"). A scope's
state is a copied mapping (`state.py:102-113`), so a snapshot of its instances is stable.

**Acceptance:**

1. Given `ctx.scope("app", A())` around the block and `ctx.scope("inner", B())` inside its body
   spawning a task, the task reads `A` and raises `ContextStateMissing` for a
   non-default-constructible `B`.
1. A preset registered with `ctx.presets(...)` inside the body is not resolved by tasks.

**Source:** D-39.

### FR-57 — Lifetime of snapshotted resources

**Statement:**

- The block MUST close its runtime, ending every attempt it runs (FR-29), before its `async with`
  returns, so that state the snapshot took from enclosing scopes stays usable for every attempt the
  runtime runs.
- The docstring of `ctx.managed_tasks` and the documentation MUST state that this ordering holds
  only when the block is exited before the scopes and disposables around it, as `async with` nesting
  ensures, and that every task shares the snapshot's instances, so a snapshot holding a single
  connection (for example a `PostgresConnection`) hands that one connection to every task at once.

**Rationale:** Derived from D-39 and the scope exit order: a scope's body ends before its
`__aexit__` unwinds its disposables (`scope.py:207-245`, `:279-313`). Tasks share the root instances
by design (D-68); an asyncpg connection is not safe for concurrent use, and a `PostgresConnection`
reaches the context through a scope's disposables (`scope.py:152-182`), `ctx.disposables`
(`context/disposables.py:321-324`), or as explicit state of `ctx.scope` or `ctx.updating`
(`access.py:153-158`, `:256-258`).

**Acceptance:**

1. Given `ctx.scope("app", disposables=(pool,))` around a block with a running task holding the
   pool's state, the task is cut off before the pool's `__aexit__` starts.
1. The docstring of `ctx.managed_tasks` contains "connection".

**Source:** derived from D-39, D-68 and `scope.py:207-245`, `:279-313`.

### FR-58 — Nested blocks

**Statement:** A block entered inside another block's body, or inside a task, MUST be a separate
block: its `ManagedTasks` MUST be the one in its body's context, its tasks MUST have it as owner
above any `ManagedTasks` its root snapshot holds (FR-9), and closing it MUST NOT affect the tasks of
the outer block.

**Rationale:** Derived from D-39 and the type-keyed merge of `ContextState` (`state.py:102-113`): an
inner block's snapshot holds the outer `ManagedTasks`, which the owner overrides.

**Acceptance:**

1. In an inner block on its own store, a task spawned through `ManagedTasks.spawn` reads the inner
   `ManagedTasks`; closing the inner block cuts it off and leaves a task of the outer block running.

**Source:** derived from D-39, D-40.

### FR-59 — ManagedTasks protocol (withdrawn)

Withdrawn by D-66: implementations are no longer swapped; the store is the extension point (FR-78).

### FR-60 — In-memory implementation (withdrawn)

Withdrawn by D-66: one runtime (FR-80) with `InMemoryTaskStore` as the default store (FR-79).

### FR-61 — Close log

**Statement:** For every task whose attempt a close cuts off (FR-29, step 3) and whose ending the
close's request decides, no termination having been requested before it (D-128), the runtime MUST
log one ERROR record in the runtime scope (FR-88), carrying the task's identifier, name and status
when the close began, and nothing else of the task. Queued and retry-pending tasks that the runtime
was not running MUST NOT be reported.

**Rationale:** D-44 ("logs errors ... no secrets, no arguments"), applied by the one runtime to
every store (D-66). A task finishing within the grace period is not cut off.

**Acceptance:**

1. Closing a block with two running tasks, with the default grace period, produces exactly two such
   ERROR records, neither containing the tasks' arguments.
1. With `grace_period=1.0`, a task finishing after 0.1 s produces no such record.
1. With the default grace period, a task whose time limit has elapsed and whose function swallows
   every `CancelledError` and sleeps 3 s is cut off by the close: the exit returns within 0.2 s, the
   outcome is `TaskFailed(TaskTimeLimitExceeded(time_limit=...))`, and no FR-61 record names it.

**Source:** D-44, D-66, D-87, D-128.

### FR-62 — Registration

**Statement:**

- `managed_task(name=..., retry=...)` applied to a coroutine function MUST return a `RegisteredTask`
  with that registered name (the function's `__name__` when `name` is absent, FR-91), retry policy
  override and result class.
- The function MUST take exactly one parameter, positional, without a default, annotated with a
  `State` class (a specialized generic counts); `managed_task` MUST raise `TypeError` otherwise, and
  `ValueError` when `name` is given and empty.
- `RegisteredTask.__call__` MUST raise `TypeError` when its argument is not an instance of the
  parameter's class, and otherwise return a `RegisteredTaskCall` holding the registered name, the
  encoded argument (FR-63), the result class (D-80) and the registration (FR-99).
- A runtime MUST spawn and claim only the registered tasks listed in `TaskRuntime(tasks=...)`, and
  `ManagedTasks.spawn` MUST raise `ValueError` for a call whose name is not listed; a call of
  another task under a listed name is rejected by FR-99.

**Rationale:** D-46 and D-67: a stored task holds the registered name and "the argument State"; the
function picks that argument. One owner of the name-to-task map, the runtime's settings, keeps it
free of import side effects, and claiming requires it (FR-80). The call carries the name and not the
task (D-80); its registration ties it to the listed task, which keeps `ManagedTask[Result]` true to
that task's result class (FR-99, D-120).

**Acceptance:**

1. `managed_task(name="r")` over `async def f(a: A) -> R` returns a task whose `name` is `"r"`.
1. Over `async def f(a: int)`, `async def f(a: A, b: B)`, `async def f()` and
   `async def f(a: A = A())` it raises `TypeError`.
1. Spawning a registered task not listed in the runtime raises `ValueError`, and the store holds
   nothing; so does a call of another task registered under a listed name with another result class.

**Source:** D-46, D-54, D-67, D-80, D-108, D-117, D-120.

### FR-63 — Argument encoding

**Statement:**

- `RegisteredTask.__call__` MUST encode the argument with `State.to_json()`, decode that text again
  with `from_json` on the parameter's class, and raise `ValueError` when either fails or any string
  of the decoded argument (key or value) contains the character U+0000.
- Before each attempt, the runtime MUST decode the argument the store returns through
  `RegisteredTask.bind`, which uses `from_json(..., ignore_extra=True)` on the parameter's class; a
  failure MUST fail the attempt as a preparation failure (FR-47), under the retry policy.
- A store MUST return text that is equal, as JSON, to the text it was given; a store error at spawn
  MUST reach the caller (FR-82).

**Rationale:**

- D-67: the argument State, as JSON, is the only data a task has; D-80 moves the encoding into the
  call, where the argument's class is known.
- `to_json` raises `ValueError` for values without a JSON spelling, such as non-finite floats
  (`state.py:1510-1532`); float mapping keys are written as `"nan"`, `"inf"` and `"-inf"` strings
  (per CodeExplorer: `tests/test_state_json.py:199-231`); decoding at the call proves the argument
  survives the round trip the task depends on.
- D-94: during a rolling deploy an older replica skips fields a newer one added, except below a
  class with its own `__init__` or `validate` (`attributes/state.py:1344-1370`) instead of failing
  the task (review 2 S8); what still fails to decode stays a preparation failure.
- PostgreSQL states "The jsonb type also rejects \\u0000" (PostgreSQL 18, JSON types, per
  WebExplorer), and TDS-002 keeps `JSONB` (TDS-002 D-16); rejecting the character in the runtime
  makes every store behave alike (D-66). Checking the decoded strings, not the text, keeps a string
  holding the six characters backslash-u-0-0-0-0, written as `"\\u0000"`, valid.

**Acceptance:**

1. Calling a registered task with an argument holding `float("nan")` raises `ValueError`, and
   nothing reaches a store.
1. An argument holding the string `"a\x00b"` raises `ValueError` at the call; one holding the six
   characters `\u0000` is accepted.
1. A stored argument holding a key the parameter's class does not define decodes, and the attempt
   runs.
1. With TDS-002, a stored argument edited to violate its State's schema makes the next attempt fail
   with a preparation failure naming `builtins.ValueError`.

**Source:** D-46, D-66, D-67, D-80, D-94; TDS-002 D-16.

### FR-64 — Persistence contract (withdrawn)

Withdrawn by D-66: replaced by the store protocol (FR-78) and the runtime responsibilities (FR-80).

### FR-65 — Attempt records

**Statement:** For every counted attempt that ended, except the attempt `fail_unsupported` records
itself for a task failed as unsupported (FR-78, FR-93), the runtime MUST have the store record it
with one `AttemptEnd` (FR-78): `AttemptCompleted` with the encoded value, `AttemptRetried` or
`AttemptFailed` with its failure (FR-20, FR-66), or `AttemptCancelled`. No exception message or
traceback MUST be recorded, and no record MUST be made for an attempt released by a close (D-56).

**Rationale:** D-47 (failures recorded), D-67, D-70, D-84, AGENTS.md Security.

**Acceptance:**

1. A task failing twice with `ValueError("secret")` under `TaskRetryPolicy(delays=(0.0,))` leaves
   two attempt records naming `builtins.ValueError` and no `"secret"`, with every store.

**Source:** D-47, D-56, D-65, D-67, D-70, D-84, D-110.

### FR-66 — Retry policy

**Statement:**

- A task's effective policy MUST be its registered task's override when given, and otherwise
  `TaskRuntime(retry=...)`, which defaults to `TaskRetryPolicy()`, allowing no retry; the effective
  policy MUST be stored with the task at spawn.
- After a failed attempt `n`, when `n <= len(delays)` and no `cancel` or `close` was requested (a
  failure during a close's grace period included, D-74), the runtime MUST record `AttemptRetried`
  with delay `delays[n - 1]` seconds, so that the task goes to `retry_pending` and its attempt
  `n + 1` becomes due that much later; otherwise it MUST record `AttemptFailed`, and the task
  finishes with `TaskFailed`.
- Every `AttemptFailure` variant MUST be retryable; `TaskCancelled` MUST NOT be retried, and neither
  MUST a task failed with `TaskUnsupported` (FR-93), which finishes `TaskFailed` whatever retries
  its policy leaves: the one exception to retryable failures (D-110).

**Rationale:** D-47, D-48, D-54, D-55, D-65, D-67 (the retry policy is stored), D-74, D-84. D-110
excludes the unsupported failure from retries; it is not an `AttemptFailure`, so no `AttemptRetried`
can carry it.

**Acceptance:**

1. Under `TaskRetryPolicy(delays=(0.05, 0.1))`, a task raising every time runs 3 attempts and yields
   `TaskFailed` with `attempts == 3`.
1. A task with an override of one retry under a runtime default of none runs 2 attempts.

**Source:** D-47, D-48, D-54, D-55, D-65, D-67, D-74, D-84, D-110.

### FR-67 — `ctx.spawn_background` as a proxy (withdrawn)

Withdrawn by D-78: `ctx.spawn_background` is removed (FR-68). Its rationale about retries and
coroutine objects is moot (D-85).

### FR-68 — Removal of `ctx.spawn_background` and the background task group

**Statement:** `ctx.spawn_background`, `BackgroundTaskGroup`, `ContextTaskGroup.background_run` and
`ctx.shutdown_background_tasks` MUST be removed, together with their tests, the autouse fixture of
`tests/conftest.py:12-23`, and their mentions in `docs/`, `llms.txt`, `skills/haiway/` and
`AGENTS.md`; `docs/guides/concurrent.md` MUST point detached work to managed tasks (NFR-6).

**Rationale:** D-78, superseding D-43's proxy. None of the removed classes is exported (per
CodeExplorer); in haiway's `src/` nothing uses them other than the fallbacks that FR-69 replaces.
Released downstream code does call `ctx.spawn_background` (Compatibility and migration).

**Acceptance:**

1. `rg -n "spawn_background|BackgroundTaskGroup|background_run|shutdown_background_tasks" src tests docs llms.txt skills AGENTS.md`
   finds nothing.
1. `hasattr(ctx, "spawn_background")` and `hasattr(ctx, "shutdown_background_tasks")` are both
   `False`.

**Source:** D-78.

### FR-69 — `ctx.spawn` outside a scope

**Statement:** `ctx.spawn` (through `ScopeTaskGroup.run`) and `ContextTaskGroup.run`, called outside
any scope or task group, MUST raise `ContextMissing` and close a given coroutine. Their docstrings
and `docs/guides/concurrent.md` MUST say so in place of the background fallback.

**Rationale:** D-58. Today they fall back to a background task (`tasks.py:270-284`, `:449-456`);
with the background task group removed (D-78) no fallback target remains, so D-58's error is the
only behavior left.

**Acceptance:**

1. Outside any scope, `ctx.spawn(work())` raises `ContextMissing`, and the coroutine reports
   `cr_frame is None`.

**Source:** D-58, D-78.

### FR-70 — Lookup and missing tasks

**Statement:**

- `await ManagedTasks.task(identifier)` MUST return a handle of the task with that identifier while
  the store holds it (`read`), and `None` otherwise, whether or not this runtime lists its
  registered name.
- For a task whose registered name this runtime does not list, the handle's `status()` MUST work,
  and its `outcome()` MUST raise `ValueError` naming the registered name, since no result class is
  known.
- When the store no longer holds a handle's task, its `status()`, `outcome()` (also one awaiting)
  and `cancel()` MUST raise `LookupError` naming the identifier.
- Once its runtime is `closed`, `ManagedTasks.task` MUST raise `RuntimeError` (D-118).

**Rationale:** D-42; D-11 and D-75 for retention; TDS-002 D-5 for stored rows; D-96 for unlisted
names and vanished tasks (review 2 M4, M5).

**Acceptance:**

1. An unknown `UUID` returns `None`.
1. A handle found by lookup reports the same status and an equal outcome to the one `spawn`
   returned.
1. A runtime listing no task named `"r"` looks up a task of `"r"` spawned by another runtime on the
   same store: `status()` returns its status, and `outcome()` raises `ValueError` containing `"r"`.
1. With `InMemoryTaskStore(retention=0.05)`, `outcome()` of a handle 0.2 s after its task finished
   raises `LookupError` containing the identifier.
1. After the block exits, a kept `ManagedTasks` instance's `task(identifier)` raises `RuntimeError`.

**Source:** D-42, D-11, D-75, D-96, D-118.

### FR-71 — Origin trace id conversion

**Statement:** `ObservabilityLink.current()` MUST set `trace_id` to `UUID(hex=ctx.trace_id())`, and
backends MUST render a link's trace id as `link.trace_id.hex`.

**Rationale:** `ctx.trace_id()` returns 32 lower-case hex characters (`access.py:46-62`), which
`UUID(hex=...)` parses and `.hex` renders back unchanged. v0.5 left the conversion undefined.

**Acceptance:**

1. In a scope, `ObservabilityLink.current().trace_id.hex == ctx.trace_id()`.

**Source:** derived from `access.py:46-62`.

### FR-72 — Closing a persistent implementation (withdrawn)

Withdrawn by D-66: one close for every store (FR-29).

### FR-73 — Ad-hoc tasks (withdrawn)

Withdrawn by D-77: ad-hoc tasks are removed; every managed task is a registered task.

### FR-74 — Task scope and attempt scopes

**Statement:**

- Each task run by a runtime MUST run in a task scope entered with
  `TaskRoot.task_scope(name, link=origin, observability=backend)` in the task context (FR-10) when
  the runtime claims it. The task scope MUST be entered with an explicit empty preset,
  `ContextPresets.of(name)`, so that it resolves no registry entry.
- Each attempt MUST run in an attempt scope nested in the task scope, entered with the task's name
  and no preset so that it resolves the registry entry of that name (D-60), with no explicit state
  (FR-9).
- Waiting for a slot, retry delays and the records of FR-24, FR-26, FR-40 and FR-41 MUST happen in
  the task scope, outside any attempt scope.
- When entering the task scope raises, the attempt MUST fail as
  `TaskFailureRecord(kind="preparation", ...)` under the retry policy, as FR-47 states for attempt
  scopes, and the runtime MUST log one ERROR record in the runtime scope (FR-88) with the task's
  identifier and name (D-131).
- One task scope per task in a runtime is best effort: a task MAY enter a new task scope in the same
  runtime after its lease was lost (FR-83) or after its retry was seen run elsewhere (D-135).

**Rationale:** Derived: presets are resolved per attempt (D-55, D-60); one trace per task carries
the link once (D-41); D-68 leaves nothing else to enter. A scope without a direct preset selects the
registry entry of its own name (`scope.py:84-89`), and the task scope bears the task's name, so only
an explicit preset with nothing to prepare (`presets.py:212-220`) keeps it from entering the task's
preset (D-86, review 2 M2).

**Acceptance:**

1. With a recording backend, a task with two attempts enters one root scope and two nested scopes,
   all named after the task, when no lease is lost and no retry runs elsewhere.
1. A task scope whose entry raises fails attempt 1 with a `preparation` failure, with one ERROR
   record in the runtime scope.
1. A registry preset named like the task, whose disposable counts entries, is entered once by each
   attempt scope and never by the task scope.

**Source:** derived from D-41, D-55, D-60, D-68, D-86; D-131, D-135.

### FR-75 — Spawn disposables (withdrawn)

Withdrawn by D-68: tasks take no spawn disposables.

### FR-76 — Results of registered tasks

**Statement:** A registered task's function MUST be annotated to return a `State` class or `None`;
`managed_task` MUST raise `TypeError` otherwise. The runtime MUST encode a returned `State` with
`to_json()` (rejecting U+0000 as FR-63 does) and record it as `AttemptCompleted`; an encoding
failure MUST fail the attempt with
`TaskFailureRecord(kind="raised", exception_type="builtins.ValueError")`. Every handle MUST receive
the value decoded with `from_json` on the call's result class from what the store returns.

**Rationale:** D-67, D-70: the stored result is the one every handle sees.

**Acceptance:**

1. `managed_task(name="r")` over `async def f(a: A) -> int` raises `TypeError`.
1. A task returning `R(x=float("nan"))` yields a `raised` failure naming `builtins.ValueError`.

**Source:** D-46, D-67, D-70, D-84.

### FR-77 — Handles after close

**Statement:** After the block's exit returned, handles MUST raise `RuntimeError` from `status`,
`outcome` and `cancel`, except that for a task whose `release` at the close fixed `TaskCancelled`
(FR-29: always with the in-memory store, and with any store when a cancel was pending) `status()`
MUST return `"finished"`, `outcome()` `TaskCancelled` and `cancel()` `"finished"`. An `outcome`
awaiting at the close a task left resumable MUST raise `RuntimeError` then, and one awaiting a task
whose release fixed `TaskCancelled` MUST return it.

**Rationale:** The store session ends with the block, and a released task has no outcome in this
runtime. D-88: the in-memory store fixes `TaskCancelled` at close and its handles return it; the
runtime keeps only those outcomes, which `release` returned and which never change, since nothing
can change a finished task.

**Acceptance:**

1. With TDS-002, an `outcome()` awaiting a running task raises `RuntimeError` when the block closes.
1. With the in-memory store, an `outcome()` awaiting a running task returns `TaskCancelled` when the
   block closes, and a later `outcome()` of that handle returns `TaskCancelled` too.
1. With TDS-002, a running task cancelled from another runtime just before the close, whose attempt
   is still running when the close cancels it, yields `TaskCancelled` from an `outcome()` awaiting
   it and from a later `outcome()`.
1. After a close, `status()` of a handle of a task that had finished before the close raises
   `RuntimeError`.

**Source:** derived from D-50, D-56, D-66, D-77; D-88.

### FR-78 — Store protocol

**Statement:** A store MUST implement `TaskStore.open()`, an async context manager yielding a
`TaskStoreSession` whose operations behave as follows, each atomic. "The attempt number" is the
number of the task's counted attempts plus 1. A runner "holds" a task's lease while the task is
`running` with that runner as lease owner, whether or not the lease has expired, until another claim
takes it. A runner "holds attempt `n`" of a task when it holds the task's lease and the attempt
number is `n`; `record` and `release` act only then (D-127). "The lease" is `timings.lease` of a
store yielding `LeasedCoordination`; a store yielding `LocalCoordination` gives leases that never
expire.

| Operation                                  | Behavior                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `insert(task)`                             | Records a new task `queued`, with no attempts, due now on the store's clock; for an identifier the store already holds, raises and changes nothing (`ValueError` naming the identifier in `InMemoryTaskStore`, D-123).                                                                                                                                                                                                                                                                                                                                                                                                |
| `claim(runner, names, limit)`              | Takes up to `limit` (all when absent; none for `0`; a negative `limit` raises `ValueError`, D-124) `queued` or `retry_pending` tasks whose name is in `names` and that are due, returned as `ClaimedAttempt` with the attempt number; and, not counted against `limit`, `running` tasks whose name is in `names` and whose lease expired, the caller's own included, returned as `ClaimedInterrupted` with the attempt number of the interrupted attempt and whether a cancel was requested. Each returned task becomes `running`, leased to `runner` for the lease. No task is returned to two runners while leased. |
| `renew(runner, identifiers)`               | Extends by the lease the leases `runner` holds among `identifiers` (with `LocalCoordination`, extends nothing), and returns them in `LeaseRenewal.leases`, each mapped to `"cancel_requested"` when a cancel was requested and to `"held"` otherwise.                                                                                                                                                                                                                                                                                                                                                                 |
| `record(runner, identifier, attempt, end)` | If `runner` holds attempt `attempt`: when a cancel was requested, records the attempt as `cancelled` and fixes `StoredFinished` with `StoredCancelled`, whatever `end` says (D-98); otherwise records the attempt as `end` gives it and fixes the status of [Data model](#data-model) for that variant (`retry_pending` due after `AttemptRetried.delay`). Returns the status it fixed. Otherwise records nothing and returns `None`.                                                                                                                                                                                 |
| `release(runner, identifier, attempt)`     | If `runner` holds attempt `attempt`: fixes `finished` with `StoredCancelled` when a cancel was requested; otherwise, with `LeasedCoordination`, moves the task to `queued`, due now, with no attempt recorded, and with `LocalCoordination` fixes `finished` with `StoredCancelled` (D-88). Returns the status it fixed; otherwise changes nothing and returns `None`.                                                                                                                                                                                                                                                |
| `cancel(identifier)`                       | `queued` or `retry_pending`: finishes it with `StoredCancelled`, returns `"cancelled"`; `running`: marks the request when none is marked, returns `"pending"`; `finished`: returns `"finished"`; not held: returns `None`.                                                                                                                                                                                                                                                                                                                                                                                            |
| `fail_unsupported(names)`                  | Finishes every `queued` or `retry_pending` task that is due and whose name is not in `names`: records one attempt failed with `TaskUnsupported` and fixes `StoredFinished` with `StoredFailed`, whatever retries its policy leaves; skips a task that a concurrent operation of another runtime holds at that moment; returns a mapping from the identifier of each task it finished to its registered name. Changes no `running` or `finished` task (D-116).                                                                                                                                                         |
| `query(matching, after, limit)`            | Returns, as `TaskSummary` values (FR-98), the first `limit` tasks it holds that match `matching` (FR-95) and come after `after` in the listing order (FR-97), or from the start when `after` is absent. A negative `limit` raises `ValueError` (D-124).                                                                                                                                                                                                                                                                                                                                                               |
| `read(identifiers)`                        | Returns a mapping holding, for each of `identifiers` the store holds, its `StoredTask`.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |

A store MUST yield exactly one coordination for its session: `InMemoryTaskStore` yields
`LocalCoordination`, and every other store MUST yield `LeasedCoordination` with the timings it was
constructed with (D-104), since the recovery of a failed `record` (FR-82, FR-84) needs leases that
expire. A `LocalCoordination` store keeps one change counter, a non-negative integer that grows by 1
at every `insert`, `record`, `release` and `cancel`, at every `claim` that claimed and every
`fail_unsupported` that finished at least one task (D-129), and once whenever the due moment of a
`queued` or `retry_pending` task it holds passes; `changed(seen)` MUST return the counter at once
when it differs from `seen`, and otherwise wait until it changes (D-103). Times are measured on the
store's own clock; the runtime passes no time to the store, except the bounds of a task filter
(FR-95). A store MUST hold nothing of a task beyond `StoredTask` and its own bookkeeping: due
moments, leases, and timestamps for ordering and audit (creation, finish, cancel request, attempt
start and end), the creation and finish times among them kept as listing timestamps (FR-96) (D-67,
D-72). Every union the protocol carries is discriminated by a `Literal` field, so that a JSON round
trip keeps the variant.

Before calling `record` or `release`, the runtime MUST pass the number of the attempt it ends
(D-127) and, to `record`, the end its own record decides (FR-40): a local `cancel` gives
`AttemptCancelled`, a local time limit the failure `TaskTimeLimitExceeded`. A claim returning the
caller's own expired lease is handled by FR-84.

**Rationale:** D-66 (one runtime, the store the only extension point) and AGENTS.md (small, focused
interfaces: one function per operation). Every rule the runtime relies on (due moments, leases,
atomic claims, a pending cancel winning at `record`) is stated once here, so the in-memory store and
TDS-002 differ only where FR-80 lists. D-88 gives the timings to the stores that need them, so the
store owns them and `claim` and `renew` take no duration; the in-memory store shares one event loop
with its runtimes (FR-34), so leases there would expire only on loop stalls. The attempt number is
derived from the recorded attempts, so it has one source. `record` takes one `AttemptEnd` (D-84,
review 2 S9). Holding a lease until another claim takes it makes a retried `record` idempotent and
lets a single runtime recover its own expired leases (D-81). An expired lease needs no slot to be
recorded, so it does not count against `limit` (review 2 M5). `read` of a set serves the batched
polls of FR-21 (D-83). `cancel` returns D-91's result type.

**Acceptance:**

1. A contract test suite, parametrized over `InMemoryTaskStore`, a leased test store and TDS-002's
   store, checks every row of the table with one test per transition, including: two runners
   claiming one due task concurrently get it once; with lease timings, `claim` returns the caller's
   own expired lease as `ClaimedInterrupted`, and `claim` with `limit=1` returns one due task and
   every expired lease; with `InMemoryTaskStore`, a task leased 10 s ago without renewal is never
   returned by `claim`; `record` by a runner whose lease another runner claimed returns `None`;
   `record` of attempt `n` after the same runner claimed attempt `n + 1` returns `None` and changes
   nothing, and so does `release` of attempt `n`; `record` after an accepted `cancel` returns
   `StoredFinished(StoredCancelled)` for `AttemptRetried` and for `AttemptCompleted`; `release`
   records no attempt and returns `queued` with TDS-002 and `StoredFinished(StoredCancelled)` with
   `InMemoryTaskStore`; `cancel` returns `"cancelled"`, `"pending"`, `"pending"` again, `"finished"`
   and `None` in the matching states; `renew` maps a cancel-requested lease to `"cancel_requested"`;
   `read` of three identifiers of which one is unknown returns two entries; a `StoredTask` survives
   a JSON round trip with its status variant.
1. `InMemoryTaskStore` yields `LocalCoordination`, and an awaited `changed()` returns after another
   runtime's `insert`, after another runtime's `claim` of a due retry, and 0.2 s after a `record` of
   `AttemptRetried(delay=0.2)` with no other change.
1. `InMemoryTaskStore`'s `insert` of an identifier it holds as `running` raises `ValueError` naming
   the identifier, and the task stays `running` with its attempts and its place in listing order;
   its `claim(..., limit=-1)` and `query(..., limit=-1)` raise `ValueError`.

**Source:** D-56, D-66, D-67, D-72, D-81, D-83, D-84, D-88, D-91, D-98, D-103, D-104, D-105, D-109,
D-110.

### FR-79 — In-memory store

**Statement:** `InMemoryTaskStore(retention=...)` MUST implement FR-78 in process memory, on
`time.monotonic()` for due moments, leases and retention and on the wall clock for listing
timestamps (FR-96), MUST be shareable by every runtime opening it on the event loop it was first
opened on (FR-34, FR-46), MUST drop finished tasks after its retention (FR-32), and MUST yield
`LocalCoordination`: its leases never expire, and its `release` finishes a task with
`StoredCancelled`, also when other runtimes share the store object (D-100). It is the default store
(FR-81).

**Rationale:** D-88: every runtime on one in-memory store shares its event loop, so leases there
would expire only on loop stalls and produce false interruptions (review 2 S7). On a store no other
runtime opens, a released task would sit where no process can reach it; on a shared store the
cancellation is deliberate (D-100), while the closing runtime's queued and retry-pending tasks stay
for the other runtimes.

**Acceptance:**

1. The contract tests of FR-78 pass for `InMemoryTaskStore`.
1. A runtime whose loop is blocked for 2 s while running a task keeps it: no other runtime on the
   same store object records `TaskInterrupted`, and the task completes once.
1. With two runtimes on one `InMemoryTaskStore`, closing the first while it runs task A and holds
   queued task B ends A with `TaskCancelled`, and the second runtime runs B.

**Source:** D-66, D-88, D-100, D-109.

### FR-80 — Runtime and store responsibilities

**Statement:** The runtime MUST own, identically for every store: claim rounds (when and how many,
FR-85), lease renewal (FR-83), recovery of interrupted attempts (FR-84), cancel delivery (FR-86),
scheduling, the concurrency limit and queue, retries and their decision, time limits, attempt
scopes, links, encoding and decoding, outcomes, records and close. The store MUST own only what
FR-78 lists. Every runtime sharing a store MUST form one shared work queue: any open runtime claims
any due task whose name it lists. Only the behaviors in this table MAY depend on the store; every
other behavior MUST be the same with every store:

| Behavior                    | `InMemoryTaskStore` (FR-79)                                                                                              | TDS-002 store                                                                                |
| --------------------------- | ------------------------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------- |
| Durability                  | Lost with the process                                                                                                    | Survives the process                                                                         |
| Retention of finished tasks | Dropped after its `retention` (FR-32)                                                                                    | Kept until the application deletes them (TDS-002 D-5)                                        |
| Opening                     | One object opened by any number of runtimes on its event loop                                                            | One open per object while its pool is entered; any number of objects on one database (FR-46) |
| Coordination                | `LocalCoordination`: leases never expire; runtimes wake on changes and due moments (D-88)                                | `LeasedCoordination` with the timings given to the store (TDS-002 FR-4)                      |
| Release at close            | Finishes the task with `TaskCancelled` (D-88)                                                                            | Leaves it `queued`, resumable (D-56)                                                         |
| Claim order across runtimes | In due order, in one process                                                                                             | Best effort under concurrent claims (TDS-002 FR-5)                                           |
| Errors of store operations  | `RuntimeError` on another event loop (FR-34); `ValueError` for a duplicate `insert` or a negative `limit` (D-123, D-124) | `PostgresException` (TDS-002 FR-17)                                                          |
| Listing timestamps          | The process wall clock in UTC at insert and at finish (FR-96)                                                            | The server clock (TDS-002 FR-20)                                                             |
| Tasks a listing can return  | The tasks it still holds, within its `retention` (FR-32)                                                                 | Every row until the application deletes it (TDS-002 FR-15)                                   |

**Rationale:** D-66 ("all the rest should work the same all the time"), narrowed by D-88; D-75
(retention is durability and store-specific; one table lists every store-dependent behavior);
TDS-002 D-1 (shared queue), which the in-memory store gives the runtimes of one process.

**Acceptance:**

1. The acceptance criteria of this document that name no store pass with `InMemoryTaskStore` under
   `make test`; with TDS-002's store they run when a PostgreSQL DSN is set locally (TDS-002 NFR-3,
   D-76, D-97), and no CI job runs them.

**Source:** D-66, D-75, D-76, D-88, D-97; TDS-002 D-1.

### FR-81 — Runtime settings

**Statement:** `TaskRuntime(...)`, with the parameters [Interfaces](#interfaces) lists, MUST hold
the settings of one runtime and no timing of leases or polling, and MUST use a new
`InMemoryTaskStore()` when `store` is absent.

**Rationale:** D-88 moves the lease and polling timings to the stores that need them; `retention` is
removed with ad-hoc tasks (D-77, D-84).

**Acceptance:**

1. `TaskRuntime()` opens with an in-memory store, and `TaskRuntime(lease=1.0)` raises `TypeError`.

**Source:** D-66, D-77, D-88, D-110.

### FR-82 — Store errors

**Statement:**

- An exception raised by a store operation serving a caller (`spawn`, `task`, `tasks`, `status`,
  `outcome`, `cancel`) MUST reach that caller unchanged.
- One raised in a claim round (by its `claim` or its `fail_unsupported`, FR-93), a renewal or a poll
  MUST be logged at ERROR in the runtime scope (FR-88), and the step retried at its next tick
  (FR-85). Under `LocalCoordination`, which has no polling tick, a failed claim round and a failed
  `changed()` wait MUST be retried after one fixed delay, the same for both, and not only at the
  next store change (D-143); its value is deliberately left to the implementation as a short
  constant, since `InMemoryTaskStore` raises only on another event loop (FR-80).
- When `record` of an attempt's end raises, the runtime MUST log at ERROR in the task scope, keep
  renewing the task's lease (FR-83), retry the same `record` with the same `AttemptEnd` at each
  renewal tick until it returns, and switch the task's local awaiters to reading the store (FR-21).
  Once a close has begun, it MUST stop retrying when the grace period has elapsed from the start of
  the close (D-99), leaving the lease to expire. A retried `record` that returns `None` MUST be
  treated as FR-83 says, its awaiters keeping to reading the store.

**Rationale:** AGENTS.md (typed errors at boundaries). Review 2 B4: stopping renewal after a failed
`record` left a single runtime's task `running` for good, because a runtime never claimed its own
lease, and with several runtimes discarded finished work; a committed write whose reply was lost
never woke local handles. The lease-owner and attempt check makes a repeated `record` idempotent
(FR-78, D-127), and retrying at the renewal tick adds no setting (D-81). D-99 bounds a close by its
grace period, after which recovery counts the attempt as `TaskInterrupted` (FR-84). `record` fails
only with a store with lease timings: only `InMemoryTaskStore` lacks them (D-104), and it raises
nothing on its loop (FR-80).

**Acceptance:**

1. A store failing one claim round logs one ERROR, and the next round claims.
1. A leased test store whose `record` fails twice and then succeeds: the lease stays renewed
   throughout, the task's outcome is `TaskCompleted`, and its awaiter is woken.
1. A leased test store whose `record` commits and then raises: the retried `record` returns `None`,
   nothing is recorded twice, and the awaiter receives `TaskCompleted` by reading the store.

**Source:** derived from AGENTS.md, D-65; D-81, D-99, D-109, D-110, D-127, D-143.

### FR-83 — Lease renewal and loss

**Statement:** At every renewal tick (FR-85) the runtime MUST call `renew` for every task whose
attempt it runs or whose `record` it still retries. A task absent from `LeaseRenewal.leases` MUST
have its local attempt cancelled, its end discarded with nothing more recorded, and one WARNING
logged with its identifier; this discarding is not a termination reason, so no `cancel`,
`time_limit` or `close` record follows (D-132). A task mapped to `"cancel_requested"` MUST have its
local attempt's cancellation requested (FR-86). A `record` that returns `None` (lease taken, or
another attempt, D-127) MUST discard the end with one WARNING carrying the identifier; the attempt
has already ended, so nothing is cancelled, and its awaiters learn the outcome from the store
(FR-21, D-133). A newer attempt of the task that this runtime claims while it still discards the
lost one MUST be held by this runtime: renewed with the others, its time limit counted from that
claim (FR-27); when its renewal reports the lease missing, it MUST be forgotten with one WARNING
(D-141). Renewals MUST run at a fixed delay of `timings.renewal` after each `renew` returns, so the
gap between two extensions of a lease is that delay plus the duration of `renew`; the documentation
MUST state that `lease - renewal` has to exceed the expected duration of `renew` (D-140).

**Rationale:** A lease can expire under a long pause or a lost connection and the task be claimed by
another runtime (FR-84); discarding keeps one writer per task. Renewing until `record` returns keeps
a slow `record` (one waiting for a pooled connection, for example) from letting the lease expire
(D-83, review 2 S6); the lease-owner and attempt check of `record` makes the overlap harmless
(D-127). With `LocalCoordination`, `renew` extends nothing and serves cancel delivery only (FR-78).

**Acceptance:**

1. With a leased test store of `lease=0.3` and `renewal=0.1`, a task running 0.9 s keeps its lease
   and completes once.
1. A task whose lease a test hands to another runner is cancelled locally within one renewal
   interval and records nothing.
1. With the same store, a `record` that the store delays 0.9 s keeps the lease and fixes the
   outcome.

**Source:** derived from D-66, TDS-002 D-1; D-81, D-83, D-88, D-127, D-132, D-133, D-140, D-141.

### FR-84 — Recovery of interrupted attempts

**Statement:** For each `ClaimedInterrupted` task a claim returns, the runtime MUST:

- when it is the caller's own lease, meaning that this runtime still runs that attempt (entering it,
  running the function, or recording it, its `record` retries included) or is already recording its
  interruption (D-134), or holds a newer attempt it claimed while discarding a lost one (D-141),
  discard the claim, keeping that work under the renewed lease;
- otherwise record the interrupted attempt and decide what follows as for any failed attempt:
  `TaskCancelled` when a cancel was requested, otherwise the failure `TaskInterrupted` with the
  retry policy deciding between `AttemptRetried` and `AttemptFailed` (FR-66).

**Rationale:** D-65: the interrupted attempt is counted, as a failure without an exception, and the
retry policy decides; D-66: the runtime, not the store, applies the policy. D-81: a runtime may
claim its own expired lease, and an event loop stall must not run the task twice. Only stores with
lease timings return interrupted tasks (D-88).

**Acceptance:**

1. With TDS-002, a runtime stopped without closing while running a task with
   `TaskRetryPolicy(delays=(0.0,))`: a second runtime records attempt 1 as `TaskInterrupted` after
   the lease expires, runs attempt 2, and the task completes.
1. The same with no retries ends the task `TaskFailed(TaskInterrupted)`, `attempts == 1`.
1. With a leased test store, a runtime whose loop stalls past the lease while running an attempt
   claims its own expired lease, records no interruption, and the attempt completes once.

**Source:** D-65, D-66, D-81, D-88, D-134, D-141.

### FR-85 — Claim rounds and ticks

**Statement:** While open, the runtime MUST run a claim round once right after open, after each
spawn of a registered task, after each local attempt ends, when the earliest retry delay it recorded
elapses, and at each store tick. A round claims with `limit` set to the number of free slots (absent
without a concurrency limit), so a runtime with no free slot claims with `limit=0`, which returns
only expired leases (FR-78, D-105). A store's ticks are:

- with `LeasedCoordination`: a polling tick every `timings.polling` seconds, which also reads
  outcomes (FR-21), and a renewal tick every `timings.renewal` seconds (FR-83);
- with `LocalCoordination`: every return of `changed(seen)`, called again with the counter it
  returned, which is both the polling and the renewal tick; the first call passes `-1`.

**Rationale:** D-88 (the store owns its timings; the in-memory store has none and reports changes
instead); D-66 (the runtime claims). Derived from D-88 and FR-80's shared queue: without polling, a
runtime opening on an in-memory store that already holds queued tasks, or a retry another runtime
recorded, would never be claimed unless the open runs a round and the store, which owns the due
moments, reports when one passes. D-103: a change counter makes the wait miss no change that lands
between two waits, and since a passing due moment counts as one change, an overdue task with every
slot full wakes the runtime once, not in a loop. D-105: an expired lease needs no slot (FR-78), so a
saturated runtime still recovers other runtimes' interrupted attempts.

**Acceptance:**

1. With a leased test store of `polling=0.1`, a task inserted by another runtime starts here within
   0.3 s when a slot is free.
1. With two runtimes on one `InMemoryTaskStore`, a task spawned by a runtime with no free slot
   starts in the other runtime within 0.1 s.
1. A registered task spawned with a free slot starts without waiting for a tick.
1. A runtime opened on an `InMemoryTaskStore` that holds a queued task left by a closed runtime
   starts it within 0.1 s.
1. With two runtimes on one `InMemoryTaskStore`, a retry with delay 0.2 s recorded by a runtime that
   closes right after starts in the other runtime between 0.2 s and 0.4 s later.
1. A runtime with `concurrency_limit=1` and its slot busy, on one `InMemoryTaskStore` holding an
   overdue queued task, calls `changed` at most twice in 0.5 s.
1. With a leased test store, a runtime whose only slot is busy records `TaskInterrupted` for another
   runtime's expired lease within one polling interval after it expired.

**Source:** D-66, D-88, D-103, D-105; derived from FR-80 for the wake on due moments and the round
at open.

### FR-86 — Cancel across runtimes

**Statement:** `await ManagedTask.cancel()` MUST call the store's `cancel` and return its result
(FR-26); when this runtime holds the task's lease, it MUST request the attempt's cancellation in
process at once, and otherwise the holding runtime MUST request it at its next renewal tick (FR-83,
FR-85).

**Rationale:** D-82, D-91, FR-25, FR-26; with a shared store the attempt can run in another runtime.

**Acceptance:**

1. With a leased test store of `renewal=0.3`, a runtime cancelling a task run by a second runtime
   gets `"pending"`, and the outcome is `TaskCancelled` within 0.8 s.
1. With one `InMemoryTaskStore` and two runtimes, the same yields `TaskCancelled` within 0.1 s.
1. Cancelling a task this runtime runs yields `TaskCancelled` within 0.1 s.

**Source:** D-82, D-91; FR-25, FR-26.

### FR-87 — Bounds of the stored origin

**Statement:** The origin's `trace_context` MUST hold only the keys `traceparent` and `tracestate`,
each a string within W3C Trace Context Level 1's limits: `traceparent` in the version format of
section 3.2.2, and `tracestate` of at most 32 list-members whose keys and values are each at most
256 characters. When `ctx.trace_context()` returns other keys, or values outside these limits,
`ObservabilityLink.current()` MUST keep the trace id and scope id with an empty `trace_context`, and
the runtime MUST log one WARNING without the values.

**Rationale:** D-72 stores the origin as telemetry metadata; keeping only the W3C headers, within
the specification's own limits, bounds what is stored (32 members of at most 256 + 256 characters,
about 16 KiB, inferred) and stores nothing a backend did not already propagate. The specification
forbids personally identifiable information in `tracestate` but warns that the headers can carry
sensitive information (sections 6.2, 7.1; per WebExplorer), so stored origins are treated as such
([Security and failure modes](#security-and-failure-modes)). An empty `trace_context` gives a link
by ids only: `LoggerObservability` records the ids (FR-14), and the OpenTelemetry adapter creates no
span link and logs a WARNING (FR-15).

**Acceptance:**

1. A backend whose `trace_context()` returns `{"traceparent": <valid>, "x": "y"}` yields an origin
   with an empty `trace_context` and one WARNING not containing `"y"`.
1. A `tracestate` of 33 list-members yields an empty `trace_context`.

**Source:** D-72; W3C Trace Context Level 1, sections 3.2.2, 3.3.1 (per WebExplorer).

### FR-88 — Runtime scope

**Statement:** On open, the runtime MUST enter a root scope named `managed_tasks`, kind `internal`,
with the runtime's backend and no state, in a new `contextvars.Context`, and MUST run its claim
rounds (their `fail_unsupported` calls included), renewals, polls and recoveries, and the records of
FR-61, FR-82, FR-83 and FR-93 that belong to no task scope, inside it; it MUST exit that scope in
the last step of its close (FR-29).

**Rationale:** Without a scope, `ctx.log_*` and `ctx.record_*` fall back to the root `Logger`
(`observability.py:862-875`), bypassing the runtime's backend (D-26, D-87, review 2 M3). A context
of its own keeps the scope out of the block's body. The name repeats the API that enters the block.

**Acceptance:**

1. With a recording backend, a store failing one claim round produces one ERROR record whose scope
   is named `managed_tasks`, and the root logger receives nothing.

**Source:** derived from D-26 and `observability.py:862-875`; D-87, D-110.

### FR-89 — Spawn rejection message

**Statement:** The `RuntimeError` that `ScopeTaskGroup.run` raises while disposables are prepared or
disposed MUST NOT mention `ctx.spawn_background`, and MUST name `ManagedTasks.spawn` as the way to
start work that outlives the scope.

**Rationale:** The message ends "use ctx.spawn_background instead" (`tasks.py:286-293`), which D-78
removes.

**Acceptance:**

1. Calling `ctx.spawn(work)` inside a disposable's `__aenter__` raises `RuntimeError` whose message
   contains `ManagedTasks.spawn` and not `spawn_background`.

**Source:** derived from D-78 and `tasks.py:286-293`.

### FR-90 — Stored task invariants

**Statement:** Constructing a `StoredTask` (directly, with `from_json`, or nested in another decoded
value) MUST raise `ValueError` unless:

1. every attempt but the last is a failure;
1. a `StoredQueued` or `StoredRunning` task's attempts are all failures;
1. a `StoredRetryPending` task has at least one attempt, all failures;
1. a `StoredCompleted` task has at least one attempt, the last `"completed"`;
1. a `StoredFailed` task has at least one attempt, the last a failure;
1. an attempt `"cancelled"` is only the last attempt of a `StoredCancelled` task;
1. an attempt `"completed"` is only the last attempt of a `StoredCompleted` task (D-98);
1. an attempt failed with `TaskUnsupported` is only the last attempt of a `StoredFailed` task
   (FR-93).

Constructing a `StoredCompleted` or an `AttemptCompleted` MUST raise `ValueError` unless its
`result` is JSON text of an object or `null`. Each check MUST be implemented in the class's own
`__init__`, which calls the generated `__init__` first, so that field validation runs before it.

**Rationale:** Review 2 S9: the v1.1 types allowed a `StoredFailed` task with no attempts, a
`StoredCompleted` task after an interrupted last attempt and non-JSON result text; every store
returns `StoredTask`, so a check at construction keeps every store's output inside the transitions
of [Data model](#data-model) (D-84). The rules follow from FR-66 and FR-78: only the last attempt
can end otherwise than failed, and a cancel can finish a task whose last attempt failed. D-106: a
probe on haiway's `.venv` (CPython 3.15, 2026-11-27) showed that an own `validate` is not called on
direct construction and replaces field validation when decoding (`attributes/state.py:1858-1864`),
while an own `__init__` calling the generated one keeps field validation (a wrong `Literal` still
raises `ValidationError`) and runs on direct construction, on `from_json` (raising `ValueError`) and
for a nested value.

**Acceptance:**

1. `StoredTask(..., status=StoredFinished(outcome=StoredFailed()), attempts=())` raises
   `ValueError`.
1. A `StoredCompleted` task whose last attempt is `TaskInterrupted()` raises `ValueError`.
1. `StoredCompleted(result="{")` raises `ValueError`.
1. A `StoredCancelled` task whose single attempt failed is accepted.
1. `StoredTask.from_json` of a `StoredFailed` task with no attempts raises `ValueError`, and one
   with a status value outside the `Literal` raises `ValueError` whose cause is a `ValidationError`.
1. A `StoredRetryPending` task whose last attempt is `TaskUnsupported()` raises `ValueError`, and a
   `StoredFailed` task whose attempts are `TaskInterrupted()` then `TaskUnsupported()` is accepted.

**Source:** derived from D-84, D-98, D-106, D-110, FR-66, FR-78 (review 2 S9).

### FR-91 — Default registered name

**Statement:**

- `managed_task(...)` with `name` absent MUST, when applied to a function, take the function's
  `__name__`, read once at that moment, as the registered name: the bare name, never `__qualname__`
  and never a module path (D-108).
- With `name` absent, it MUST raise `TypeError` when the function has no `__name__` attribute of
  type `str`, and `ValueError` when that `__name__` is empty.
- A given `name` MUST be the registered name whatever the function's `__name__`, and an empty given
  `name` MUST raise `ValueError` (FR-62).
- The docstring of `managed_task` and the documentation (NFR-6) MUST state that: renaming the
  function changes its registered name, so stored tasks of the old name get an unlisted name in
  every runtime of the new code (FR-70; TDS-002 FR-10), where they wait with `unsupported="leave"`
  and are failed by a runtime with `unsupported="fail"` (FR-93); an explicit `name` keeps the
  identity stable across renames and moves; two functions of one `__name__` in one runtime are
  rejected (FR-45), and across runtimes on one store they share one registered name (TDS-002 D-2).

**Rationale:** D-108. FR-62 already raises `TypeError` for a callable unsuitable as a registered
task, so a callable that yields no name is rejected the same way; `name` stays non-empty because the
stored name is (TDS-002 `CHECK (name <> '')`). `managed_task` takes keyword arguments only, so it is
not usable bare; D-108 does not ask for that ([Non-goals](#non-goals)).

**Acceptance:**

1. `managed_task()` over `async def process(a: A) -> R` returns a task whose `name` is `"process"`.
1. Over `inner`, defined inside `outer` (`__qualname__` `"outer.<locals>.inner"`), the name is
   `"inner"`.
1. `managed_task(name="r")` over `process` returns a task whose `name` is `"r"`.
1. With `name` absent, a callable object without `__name__` raises `TypeError`, and a function whose
   `__name__` was set to `""` raises `ValueError`; `managed_task(name="")` raises `ValueError`.
1. `managed_task(process)` raises `TypeError`.
1. The docstring of `managed_task` contains "__name__" and "rename".

**Source:** D-108; derived from FR-62 for the `TypeError`.

### FR-92 — Unsupported setting

**Statement:**

- `TaskRuntime(unsupported=...)` MUST take `"leave"` or `"fail"`, defaulting to `"leave"`, and raise
  `ValueError` naming the setting for any other value (FR-45).
- With `"leave"`, the runtime MUST NOT call the store's `fail_unsupported`, and stored tasks of
  unlisted names stay as v1.5 leaves them: never claimed (FR-62, FR-80).
- The docstring of `TaskRuntime(unsupported=...)` and the documentation (NFR-6) MUST warn that with
  `"fail"` a runtime fails every due stored task of a name it does not list, also one that another
  runtime on the same store lists and would run: runtimes of different applications or worker kinds
  sharing one database fail each other's tasks; during a rolling deploy older replicas fail tasks of
  names only newer ones list; a runtime given `tasks=()` fails every due task; and tasks of a
  renamed function are failed (FR-91).

**Rationale:** D-110: opt-in, default unchanged. The shared queue (FR-80, TDS-002 D-1) makes every
runtime on a store see every due task, which is what turns the setting against heterogeneous
runtimes.

**Acceptance:**

1. With the default, an `InMemoryTaskStore` holding a due `queued` task of an unlisted name keeps it
   `queued` for 0.3 s, and a store counting calls records no `fail_unsupported`.
1. The docstring of `TaskRuntime` contains "unsupported" and "every due".

**Source:** D-110.

### FR-93 — Failing unsupported tasks

**Statement:**

- With `unsupported="fail"`, while `open`, the runtime MUST call the store's `fail_unsupported` with
  the set of its listed registered names in every claim round (FR-85), rounds with `limit=0`
  included, and MUST NOT call it while `closing` or `closed` (FR-29).
- For each task `fail_unsupported` returns, the runtime MUST log one ERROR record in the runtime
  scope (FR-88) carrying the task's identifier, its registered name and the failure kind
  `unsupported`, and nothing else of the task (NFR-4); it enters no task scope and no attempt scope
  for it.
- An exception raised by `fail_unsupported` MUST be handled as one raised in a claim round (FR-82).
- Between a `cancel` and `fail_unsupported` of one `queued` or `retry_pending` task, the operation
  that takes the task first decides: a task cancelled first stays `TaskCancelled`, and a `cancel`
  after `fail_unsupported` returns `"finished"` (FR-78). `fail_unsupported` takes no `running` task,
  so a cancel already requested on a running task always wins.
- Expired leases of unlisted names MUST stay as they are (D-116): `claim` returns only listed names
  (FR-78) and `fail_unsupported` takes no `running` task.

**Rationale:** D-110. "Due" is the glossary's due moment: `queued` and `retry_pending` tasks whose
next attempt has become claimable. The record level follows FR-24, which logs a task ending
`TaskFailed` at ERROR; there is no task scope to log it in, so it goes to the runtime scope (FR-88).
Calling `fail_unsupported` in every round, the full ones included, keeps it on the round's ticks
(FR-85): with `LocalCoordination` a passing due moment and every `insert` wake the round. A failure
is never retried (FR-66).

**Acceptance:**

1. A runtime listing `"x"` spawns a task of `"x"` with no free slot and closes; a runtime with
   `unsupported="fail"` listing only `"r"` opens on the same `InMemoryTaskStore`: within 0.1 s the
   task is `finished`, a third runtime listing `"x"` reads `TaskFailed(TaskUnsupported())` with
   `attempts == 1`, and one ERROR record carries its identifier and `"x"` and not its argument.
1. A `retry_pending` task of `"x"` with one failed attempt and retries left becomes due: it ends
   `TaskFailed(TaskUnsupported())` with `attempts == 2`, and no attempt 3 runs.
1. A `retry_pending` task of `"x"` with a delay of 0.3 s stays `retry_pending` for 0.2 s.
1. A due task of a listed name is claimed and run, never failed as unsupported.
1. A `running` task of `"x"` whose lease expired in a leased test store stays `running` through 3
   claim rounds of a runtime with `unsupported="fail"` not listing `"x"`.
1. A task of `"x"` cancelled before the runtime opens stays `TaskCancelled`.
1. `cancel()` of a task of `"x"` that `fail_unsupported` already finished returns `"finished"`, and
   its outcome stays `TaskFailed(TaskUnsupported())`.
1. A store whose `fail_unsupported` raises once logs one ERROR in the runtime scope, and the next
   round fails the task.

**Source:** D-110, D-111, D-116; derived from FR-24, FR-82, FR-85, FR-88.

### FR-94 — Listing stored tasks

**Statement:**

- `await ManagedTasks.tasks(pagination, matching=...)` MUST return a `Paginated[TaskSummary]` of the
  stored tasks that match `matching` (FR-95; absent: `TaskFilter()`, every task), in the order and
  pages of FR-97, one `TaskSummary` per task (FR-98), through one call of the store's `query` per
  page, whether or not this runtime lists their registered names (D-96, D-109, D-112).
- It MUST raise `ValueError`, calling no store operation, when `pagination.limit` is not an integer
  from 1 to 1,000 (D-115), when `pagination.token` is neither `None` nor a string that
  `TaskKeyset.from_json` accepts (a naive time is rejected, FR-97), or when `pagination.arguments`
  is not empty.
- `InMemoryTaskStore` MUST list only the tasks it still holds, within its retention (FR-32); TDS-002
  lists every row until the application deletes it (TDS-002 FR-15).
- It MUST raise `ContextStateMissing` as `ManagedTasks.task` does (FR-5), raise `RuntimeError` on
  another event loop (FR-34) and once the runtime is `closed`, be served while `closing` (FR-29,
  step 6), and pass store errors unchanged (FR-82).

**Rationale:** D-109; D-112 (summaries), D-113 (haiway's `Pagination` and `Paginated`), D-115 (a
required limit of 1 to 1,000: `Pagination.limit` has no default, `utils/pagination.py:60-62`).
Filters have their own typed parameter (FR-95), so `arguments` has no meaning here; rejecting a
non-empty one, derived from D-113 and the Golden Rule, keeps a filter passed there from being
dropped silently. Lookup already returns tasks of unlisted names (D-96). The closed state follows
FR-77 and D-118.

**Acceptance:**

1. Outside any scope, `ManagedTasks.tasks(Pagination.of(limit=10))` raises `ContextStateMissing`.
1. `ManagedTasks.tasks(...)` given each of `Pagination.of(limit=0)`, `Pagination.of(limit=1001)`,
   `Pagination.of(limit=10, token=7)`, `Pagination.of(limit=10, token="x")` and
   `Pagination.of(limit=10, kind="a")` raises `ValueError`, and a store counting calls records no
   `query`.
1. With `InMemoryTaskStore(retention=0.05)`, a task finished 0.2 s earlier is not listed.
1. A task of an unlisted name, spawned by another runtime on the same store, is listed.
1. With 3 tasks, `Pagination.of(limit=10)` returns 3 summaries whose page has `token is None` and
   `has_next_page` `False`.
1. After the block exits, a kept `ManagedTasks` instance's `tasks(...)` raises `RuntimeError`.
1. A token `TaskKeyset`-shaped but with a naive `created`, such as
   `'{"created": "2026-01-01T00:00:00", "identifier": "<uuid>"}'`, raises `ValueError`.

**Source:** D-96, D-109, D-112, D-113, D-115, D-118; derived from FR-5, FR-34, FR-77, and from D-113
with AGENTS.md Golden Rule for rejecting `arguments`.

### FR-95 — Task filters

**Statement:**

- A task MUST be listed only when it meets every given part of the `TaskFilter`: `names`, its
  registered name is in the set; `TasksUnfinished`, its status is in `statuses`; `TasksFinished`, it
  is `finished` with an outcome in `outcomes` and, when `finished` is given, its finish time is in
  that range; `created`, its creation time is in that range. An absent part MUST match every task,
  and an empty set MUST match none.
- A time range MUST contain a time `t` when `start <= t` (`TaskTimeFrom`), `t < end`
  (`TaskTimeBefore`), or both (`TaskTimeBetween`), compared as instants.
- `TaskTimeFrom`, `TaskTimeBefore` and `TaskTimeBetween` MUST raise `ValueError` for a naive
  `datetime`, and `TaskTimeBetween` also when `start` is not earlier than `end`, each check in the
  class's own `__init__` after the generated one, as FR-90 does (D-106).
- Creation and finish times are the store's listing timestamps (FR-96); the documentation MUST state
  that with TDS-002 they are the database server's clock, which can differ from the caller's.

**Rationale:** D-109 names the filters. The Golden Rule shapes them: a finish range exists only for
finished tasks, and bounds are variants instead of optional fields. A naive `datetime` has no
instant to compare, and CPython refuses to order it against an aware one (`TypeError`, run by me,
2026-11-27), while `State` accepts it ([Context and scope](#current-system-further-facts)), so the
check has to be the class's own. Half-open ranges let consecutive ranges cover time without overlap.

**Acceptance:**

1. Over tasks `a` (`queued`), `b` (`finished`, `completed`) and `c` (`finished`, `failed`), a filter
   `TaskFilter(status=TasksFinished(outcomes={"failed"}))` lists `c` only, and
   `TaskFilter(names=set())` lists none.
1. A task created at `T` is listed by `TaskTimeFrom(start=T)` and by `TaskTimeBefore(end=T + 1 µs)`,
   and not by `TaskTimeBefore(end=T)`.
1. `TaskTimeFrom(start=datetime(2026, 1, 1))` raises `ValueError`, and `TaskTimeBetween` with equal
   `start` and `end` raises `ValueError`.
1. `TaskFilter.from_json` of a `TasksFinished` with a `finished` range keeps its variants.

**Source:** D-106, D-109; derived from AGENTS.md Golden Rule.

### FR-96 — Listing timestamps

**Statement:** A store MUST keep, for every task it holds, its creation time, fixed by `insert`,
and, once `finished`, its finish time, fixed by the operation that finished it, as timezone-aware
UTC `datetime` values of microsecond resolution. `InMemoryTaskStore` MUST take them from
`datetime.now(UTC)`, and keep due moments, leases and retention on `time.monotonic()` (FR-79). A
listed `TaskSummary` shows them (FR-98).

**Rationale:** D-109 asks for created and finished ranges, so both stores need a clock a caller's
`datetime` compares with. `time.monotonic()` has an undefined reference point (Python 3.15
documentation, per WebExplorer), so it cannot be compared with a calendar time. TDS-002 already
keeps `created_at` and `finished_at` on the server clock (TDS-002 Data model, FR-20), whose
`timestamptz` resolution is 1 µs (PostgreSQL 16 and 18, per WebExplorer).

**Acceptance:**

1. With `InMemoryTaskStore`, a task spawned between two readings of `datetime.now(UTC)` is listed by
   a `TaskTimeBetween` of those two readings, end plus 1 µs.
1. A task finished while the process wall clock moves 1 h back still keeps its retention measured on
   `time.monotonic()` (FR-32).

**Source:** D-109; derived from FR-78 (timestamps are store bookkeeping) and `time.monotonic()`.

### FR-97 — Listing order and pages

**Statement:**

- Tasks MUST be listed oldest first: by creation time ascending, then by identifier ascending as a
  tie-break (D-114).
- A page MUST hold the first `pagination.limit` matching tasks after the position its token names,
  or from the start when the token is `None`.
- The returned `Paginated.pagination` MUST be the request's `Pagination` with its token replaced:
  when the page holds `limit` tasks, by the token naming the position of its last task; otherwise by
  `None`.
- A token MUST be the text of `TaskKeyset(created=..., identifier=...).to_json()` of that last
  task's creation time and identifier, produced and decoded only by the runtime; `TaskKeyset` MUST
  raise `ValueError` for a naive `created` in its own `__init__`, as FR-98 does (D-106); a string
  that `TaskKeyset.from_json` rejects MUST raise `ValueError` (FR-94). The documentation MUST call
  tokens opaque.
- A traversal from `token=None`, following each returned token while it is not `None`, MUST return
  every task the store holds and that matches from the traversal's start to its end exactly once,
  and no task twice.
- The store's `query` MUST receive the decoded `TaskKeyset` (absent for the first page) and the
  limit, and return the summaries strictly after that position in this order (FR-78).

**Rationale:** D-113: haiway's `Paginated.has_next_page` is true when a token is present or the page
holds at least `limit` items (`utils/pagination.py:211-212`); giving a token exactly for a full page
makes a full last page lead to one empty page, as OQ-54 option 1 stated, and makes a partial page
end the traversal with `has_next_page` `False`, so a loop on either `has_next_page` or the token
ends. D-114: oldest first, no direction parameter. Creation time and identifier are fixed at
`insert`, so the keyset never moves under a traversal; an offset would. The token is built from data
the summary already carries (FR-98), so its format has one owner, the runtime, and a store receives
typed values. Microsecond `datetime` text round-trips the store's microsecond timestamps (FR-96). A
task inserted during a traversal is returned only when its creation time is after the current
position, which the wall clock of `InMemoryTaskStore` does not guarantee.

**Acceptance:**

1. With 5 tasks created in order A to E and `limit=2`: page 1 is A, B with a token; page 2 is C, D
   with a token; page 3 is E with `token is None` and `has_next_page` `False`.
1. With 4 tasks and `limit=2`, page 2 has a token and `has_next_page` `True`, and page 3 is empty
   with `token is None`.
1. Two tasks with equal creation times are listed in identifier order, each once across pages of
   `limit=1`.
1. Tasks inserted, finished and deleted between the calls of a traversal over 50 tasks with
   `limit=7`: every task present throughout is returned exactly once, none twice.
1. A token from one listing continues a listing with another filter from the same position.

**Source:** D-109, D-113, D-114, D-115.

### FR-98 — Task summaries

**Statement:**

- A `TaskSummary` MUST hold the task's identifier, registered name, status (`TaskSummaryUnfinished`
  with `queued`, `running` or `retry_pending`, or `TaskSummaryFinished` with the outcome kind
  `completed`, `failed` or `cancelled` and the finish time), the number of its counted attempts and
  its creation time, the two times being the store's listing timestamps (FR-96), and nothing else of
  the task: no argument, result, failure or origin (D-112).
- Constructing a `TaskSummaryFinished` (directly or decoded) MUST raise `ValueError` when `finished`
  is naive. Constructing a `TaskSummary` MUST raise `ValueError` when `created` is naive, when
  `attempts` is below 0, or when `attempts` is 0 and its status is `retry_pending` or finished with
  outcome `completed` or `failed`. Each check MUST be in the class's own `__init__`, as FR-90 does
  (D-106).

**Rationale:** D-112. Every count matches FR-90: only `queued`, `running` and `cancelled` tasks can
have no counted attempt. The finish time exists only in the finished variant (AGENTS.md Golden
Rule). The outcome of a listed task comes from `ManagedTasks.task(identifier)` (FR-70).

**Acceptance:**

1. A task failed once and pending a retry is summarized as
   `TaskSummaryUnfinished(status="retry_pending")` with `attempts == 1`; after completing, as
   `TaskSummaryFinished(outcome="completed", finished=...)` with `attempts == 2` and a finish time.
1. A summary listed for a task spawned with `Secret(value="s3")` contains no `"s3"` in its JSON.
1. `TaskSummary(..., attempts=-1)`, a `TaskSummary` whose status is
   `TaskSummaryFinished(outcome="failed", finished=...)` with `attempts=0`, and a
   `TaskSummaryFinished` with a naive `finished` each raise `ValueError`.

**Source:** D-106, D-112; derived from FR-90 and AGENTS.md Golden Rule.

### FR-99 — Spawn checks the registered task

**Statement:** `managed_task` MUST give each `RegisteredTask` it returns a `registration`, a `UUID`
drawn with `uuid4()` when it is applied, and `RegisteredTask.__call__` MUST copy it into the
`RegisteredTaskCall`. `ManagedTasks.spawn` MUST raise `ValueError`, storing nothing, when the call's
`registration` differs from that of the task the runtime lists under the call's name; this is the
only check of a call against the listed task, and it also covers the result class, since an equal
registration means the same `RegisteredTask` (D-120). A stored task MUST NOT hold the registration,
so tasks spawned by other processes are not checked (D-117).

**Rationale:** D-117. With D-108 two functions of one `__name__` share a registered name, and a
check by name and result class alone would let one run the other's stored argument. The identity is
a `UUID`, a type that does not mention `Argument`, so `RegisteredTask` keeps `Argument`
contravariant and `RegisteredTaskCall` keeps none (D-80).

**Acceptance:**

1. Two functions named `process` in two modules, both returning `R`, registered without `name`: a
   runtime listing the first raises `ValueError` for a call of the second, and the store holds
   nothing.
1. A call of the listed task itself spawns.

**Source:** D-80, D-108, D-117, D-120.

### NFR-1 — Concurrent spawns

**Statement:** 1,000 tasks spawned from 100 concurrently running scopes on one loop (10 each)
through a runtime with the in-memory store MUST each yield exactly one outcome.

**Rationale:** HM-10.

**Acceptance:**

1. The test collects 1,000 distinct identifiers and 1,000 `TaskCompleted` outcomes.

**Source:** HM-10.

### NFR-2 — Spawning scope exit time

**Statement:** A scope that spawns a task sleeping 1 s through a runtime with the in-memory store
and then ends MUST complete its exit within 10 ms of its body ending, measured with
`time.monotonic()` on an otherwise idle loop, with the spawning scope on
`LoggerObservability(debug_context=False)` and the runtime on its default backend.

**Rationale:** HM-1.

**Acceptance:**

1. The measured exit takes less than 0.010 s in 20 of 20 runs.

**Source:** HM-1.

### NFR-3 — Time limit precision

**Statement:** A task of a runtime with the in-memory store with `time_limit=0.05` sleeping 1 s MUST
fix `TaskFailed(TaskTimeLimitExceeded(time_limit=0.05))` between 0.05 s and 0.5 s after the spawn
call, with a free slot, measured with `time.monotonic()` on an otherwise idle loop.

**Rationale:** HM-8, D-48; with a free slot the claim follows the spawn at once (FR-85), so the
bound holds although the limit counts from the claim (D-89).

**Acceptance:**

1. In 20 of 20 runs the measured time is within the bounds.

**Source:** HM-8, D-48, D-89.

### NFR-4 — Records without content

**Statement:** Every record and every stored attempt record produced by the runtime or a store
outside the task's own code MUST identify a task only by its identifier, name and attempt number,
and MUST NOT include arguments, returned values, exception messages or tracebacks; exceptions reach
logs only through the backend's scope-error record.

**Rationale:** AGENTS.md Security.

**Acceptance:**

1. See FR-24, FR-42 and FR-65 acceptance.

**Source:** AGENTS.md Security.

### NFR-5 — Tests

**Statement:**

- Every acceptance criterion MUST have a unit test: `tests/test_managed_tasks.py` (core, in-memory,
  logger, and the leased test store, an in-process test double of FR-78 yielding
  `LeasedCoordination`), `tests/test_managed_tasks_opentelemetry.py` (in-memory span exporter), and
  contract tests in `tests/test_managed_tasks_contract.py` parametrized over `InMemoryTaskStore`,
  the leased test store and the PostgreSQL store; the PostgreSQL parameter runs only when a DSN is
  set locally and is skipped otherwise, with no CI job for it (TDS-002 NFR-3, D-76, D-97).
- The tests use no network, no sleep over 1 s, and `pytest.mark.asyncio`.

**Rationale:** AGENTS.md Testing; D-76, D-97; the in-memory store has no leases (D-88), so lease
paths are tested in process through the leased test store.

**Acceptance:**

1. `make test` passes, and every acceptance item is referenced by a test name or comment.

**Source:** AGENTS.md Testing; D-88, D-97.

### NFR-6 — Documentation

**Statement:** Every public symbol of [Interfaces](#interfaces) MUST have a NumPy docstring with
Parameters, Returns and Raises. A dedicated guide, `docs/guides/managed-tasks.md`, registered in the
`nav` of `mkdocs.yml`, MUST show `ctx.managed_tasks` with a registered task, the root snapshot rule
(FR-56, FR-57), the retention growth (FR-32), the transaction limit (FR-4), the recommended
concurrency limit (FR-33), the close bound of a failing `record` (FR-29), that entering the block
starts executing stored tasks (FR-55), the default registered name and its rename hazard (FR-91),
listing stored tasks (FR-94, FR-95), the `unsupported` setting with its warnings (FR-92), and that
fire-and-forget work which is not a registered task runs in loop tasks the caller owns (D-78).
`docs/guides/concurrent.md` MUST replace its background-task text (`concurrent.md:59-79`) with a
short pointer to that guide and repeat none of its content (D-145). `docs/features/starlette.md`
MUST show `ServerContext(tasks=...)`; `make docs-lint` MUST pass.

**Rationale:** AGENTS.md Documentation; D-59, D-77, D-78, D-90, D-92, D-99, D-108, D-109, D-110,
D-145.

**Acceptance:**

1. `make docs` builds with `--strict`, and the pages contain the examples.
1. `mkdocs.yml` lists `guides/managed-tasks.md` in its `nav`, and `docs/guides/concurrent.md` links
   to it and contains no `ctx.managed_tasks` example.

**Source:** AGENTS.md Documentation; D-108, D-109, D-110, D-145.

### NFR-7 — Exports

**Statement:** The public names of [Interfaces](#interfaces) MUST be exported from
`src/haiway/__init__.py`, and the context types also from `src/haiway/context/__init__.py`.

**Rationale:** AGENTS.md Contribution checklist.

**Acceptance:**

1. `from haiway import ManagedTasks, TaskRuntime, InMemoryTaskStore, TaskStore, managed_task, RegisteredTaskCall, AttemptEnd, TaskOutcome, TaskRetryPolicy, ObservabilityLink, TaskRoot`
   succeeds, and `from haiway import AdHocTaskCall` raises `ImportError`.
1. `from haiway import TaskUnsupported, AttemptFailure, TaskFilter, TasksFinished, TasksUnfinished, TaskTimeFrom, TaskTimeBefore, TaskTimeBetween, TaskTimeRange, UnsupportedFailing, TaskSummary, TaskSummaryUnfinished, TaskSummaryFinished, TaskKeyset, TaskListing, TaskQuerying`
   succeeds.

**Source:** AGENTS.md; D-109, D-110.

### NFR-8 — No postponed annotations

**Statement:** New and changed modules MUST NOT contain `from __future__ import annotations`.

**Rationale:** Registration reads parameter annotations (FR-62), and postponed annotations empty
`State` fields (brief).

**Acceptance:**

1. `rg -n "from __future__ import annotations" src/haiway` finds no match in the changed files.

**Source:** brief (user preference).

### NFR-9 — Callback latency (withdrawn)

Withdrawn by D-42: completion callbacks are removed.

## Security and failure modes

- **Task identifiers are not credentials** (D-8; `identifier.py:53-55`). Anyone who can call
  `ManagedTasks.task` in a runtime, or read a shared store, can look a task up or cancel it. Code
  exposing tasks to remote callers maps them to its own credential-grade identifiers (draive TDS-003
  D-11).
- **The root snapshot is shared.** Every attempt a runtime runs, whoever spawned the task, reads the
  state of the scopes around that runtime's block, including clients and credentials held there
  (FR-56). With a shared store, a task can run in another runtime, under that runtime's snapshot
  (D-68, TDS-002 D-2).
- **Stored data** is the argument and result as JSON, failures as type names, never messages or
  tracebacks (FR-63, FR-65), and the origin: two identifiers and the W3C trace headers, bounded by
  FR-87, which W3C keeps free of personal information but which can be sensitive. The argument is
  validated before every attempt (FR-63), so data edited in the store fails the attempt instead of
  reaching the function unvalidated.
- **Trust boundary.** No stored code runs: a stored task names a registered task listed in the
  runtime's settings, never an import path (D-46, FR-62).
- **Records** carry no task content (NFR-4).
- **Listing** returns summaries without arguments, results or origins (FR-98), but enumerates the
  identifiers of stored tasks, which are not credentials: whoever holds a runtime's `ManagedTasks`
  can list every task of its store, of every registered name, and then look up or cancel any of them
  (FR-94, D-8).
- **Default names** (FR-91): bare function names collide more often than chosen names; on a shared
  database a collision makes runtimes run each other's tasks (TDS-002 D-2).
- **`unsupported="fail"`** lets one misconfigured runtime fail every due task of the names it does
  not list on its store (FR-92).
- **Spawn and application transactions.** The store writes through its own connections, so a task
  spawned inside an application transaction is stored and may start before that transaction commits,
  and survives its rollback (review 2 S3); the documentation says so (FR-4, D-92).

| Failure                                          | Behavior                                                                                                                                      |
| ------------------------------------------------ | --------------------------------------------------------------------------------------------------------------------------------------------- |
| Function raises                                  | `raised` failure; retried or `TaskFailed` (FR-20, FR-66); records (FR-24)                                                                     |
| Preset fails to prepare                          | `preparation` failure, retried (FR-47)                                                                                                        |
| Argument fails to decode                         | Extra keys skipped; any other failure is a `preparation` failure (FR-63, D-94)                                                                |
| Time limit elapses                               | `TaskTimeLimitExceeded`, retried (FR-27)                                                                                                      |
| `KeyboardInterrupt` or `SystemExit` in a task    | Re-raised, nothing recorded; recovered as `TaskInterrupted` when the store survives (FR-40)                                                   |
| Runtime stops without closing                    | With lease timings, after the lease expires, `TaskInterrupted`, retry policy decides (FR-84); with the in-memory store, lost with the process |
| Lease lost while running                         | Local attempt cancelled, nothing recorded (FR-83)                                                                                             |
| Store operation fails                            | Raised to the caller, or logged and retried by the loop (FR-82)                                                                               |
| `record` fails                                   | Retried at each renewal tick with the lease renewed; awaiters poll (FR-82, FR-21)                                                             |
| Own lease expires during a loop stall            | Claim discarded, attempt continues (FR-84)                                                                                                    |
| `record` still failing when a close waits for it | Abandoned once the grace period has elapsed from the close; recovered as `TaskInterrupted` (FR-29, FR-82, D-99)                               |
| Function ignores cancellation and never ends     | Close waits without limit, as a scope waits for its tasks (`tasks.py:242-249`)                                                                |
| Close cancelled                                  | Close completes, then re-raises (FR-29)                                                                                                       |
| Due task of an unlisted name                     | Left as it is with `unsupported="leave"`; failed with `TaskUnsupported`, not retried, with `"fail"` (FR-92, FR-93)                            |
| `fail_unsupported` fails                         | Logged and retried at the next claim round (FR-82, FR-93)                                                                                     |

## Compatibility and migration

- Managed tasks are new; nothing of them was released, so no compatibility shims are added (brief).

- **Breaking, `ctx` (D-78):** `ctx.spawn_background` and `ctx.shutdown_background_tasks` are removed
  (FR-68); `ctx.spawn` outside any scope raises `ContextMissing` (FR-69). D-43 judged the removal
  safe because nothing in haiway's `src/` used it; released downstream code does, and has to move
  before it upgrades:

  | Call site                                                                                                                                          | Use                                                                                                | Moves to                                                                                                                                                   |
  | -------------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
  | `draive/src/draive/aws/observability.py:705`, in the helper `_spawn_background` (`:698`) that the callers at `:462`, `:485`, `:524` and `:563` use | Fire-and-forget export of a record from the event loop                                             | A loop task the module owns (`asyncio` task kept until done), or a registered task when the work has a `State` argument                                    |
  | `draive/src/draive/aws/observability.py:714`                                                                                                       | The same from a worker thread, through `loop.call_soon_threadsafe`                                 | A loop task created by the scheduled callback on that loop; a managed task cannot be spawned from another thread (FR-34)                                   |
  | `draive-platform/src/draive_platform/telemetry/observability.py:425`                                                                               | Fire-and-forget storage of a trace                                                                 | A loop task the observability object owns, or a registered task taking the spans as a `State`                                                              |
  | `draive-platform/src/draive_platform/evaluation/runner.py:170`                                                                                     | Evaluation run kept as `asyncio.Task`, with `add_done_callback` (`:172`) and `cancel` (`:174-182`) | A loop task the runner owns, keeping its done callback and cancel; or a registered task whose handle replaces the `Task` (`ManagedTask.cancel`, `outcome`) |
  | `draive/tests/test_aws_state_observability.py:44`, `:104`, `:191`, `:281`, `:327` (with fakes defined at `:72`, `:151`, `:249`)                    | Monkeypatch `ctx.spawn_background`                                                                 | Patch whatever replaces it                                                                                                                                 |

  Line numbers are at draive 5080cbc and draive-platform 6e01b8f.

- **Breaking, internals:** `BackgroundTaskGroup` and `ContextTaskGroup.background_run` are removed
  (FR-68), with the autouse fixture of `tests/conftest.py:12-23` and the mentions listed in
  [Context and scope](#context-and-scope).

- **Ad-hoc tasks (D-77)** were never released; draive's specifications that relied on them are
  listed under [Conflicts](#conflicts).

- **Observability:** `ObservabilityScopeEntering` gains `links` (FR-13); 13 test doubles change:
  `tests/test_context.py:556`, `:608`, `:727`; `tests/test_starlette.py:219`, `:745`, `:788`,
  `:1237`, `:1648`; `tests/test_starlette_streaming.py:302`;
  `tests/test_logger_observability.py:358`; `tests/test_closing.py:99`;
  `tests/test_postgres_observability.py:102`; `tests/test_http_client.py:398`.

- **`ServerContext`** gains the optional `tasks` parameter (FR-35).

- **v1.6 revision (D-108 to D-110):** nothing of v1.5 was released, so no shim is added: `name` of
  `managed_task` becomes optional, `TaskFailure` gains `TaskUnsupported` and `AttemptRetried` and
  `AttemptFailed` take `AttemptFailure`, `ManagedTasks` gains the `listing` field, and
  `TaskStoreSession` gains `fail_unsupported` and `query`, which every custom store has to
  implement, and `RegisteredTask` and `RegisteredTaskCall` gain `registration`, which no store holds
  (FR-99). `unsupported="leave"` keeps v1.5's behavior. Code relying on `name` being required, or
  matching `TaskFailure` exhaustively, changes with it.

- **Renamed functions:** with the default name (FR-91), a rename between releases leaves the stored
  tasks of the old name unlisted; they wait, or are failed with `unsupported="fail"` (FR-93).
  Registering the function with `name` set to the old name keeps them running.

- **Stored data:** the JSON of an argument or result is decoded by the current registered task's
  classes (FR-63, FR-76); changing a State class in a way its stored JSON no longer validates
  against fails the stored tasks' next attempts as preparation failures. During a rolling deploy an
  older replica skips a field a newer one added to an argument and runs the task without it (review
  2 S8, D-94).

- **Rollback:** removing managed tasks again leaves stored tasks in TDS-002's tables (TDS-002
  Compatibility and migration); `ctx.spawn_background` is not restored.

- **draive:** see [Conflicts](#conflicts).

## Coverage

Columns: N normal path; In input classes; Err errors and failures; Conc concurrency and ordering;
Int interruption, crash and restart; St states and transitions; Lim limits and capacity; Sec
security; Cfg configuration; Obs observability; Comp compatibility, migration and rollback; Life
resource lifecycle.

Shared `N/A` reasons: (a) no input beyond those in the same row; (b) no setting applies; (c) new
API, nothing released (brief); (d) records nothing of its own; (e) synchronous and without shared
data; (f) owns no resource; (g) holds no lifecycle state of its own; (h) no input from outside the
process.

| Row                                   | N                   | In                  | Err                              | Conc                       | Int           | St                | Lim                      | Sec      | Cfg                             | Obs          | Comp                 | Life         |
| ------------------------------------- | ------------------- | ------------------- | -------------------------------- | -------------------------- | ------------- | ----------------- | ------------------------ | -------- | ------------------------------- | ------------ | -------------------- | ------------ |
| `ctx.managed_tasks` block             | FR-55               | FR-46, FR-55        | FR-55                            | FR-58                      | FR-57         | Runtime state     | N/A: one opening         | Security | FR-81                           | N/A (d)      | N/A (c)              | FR-57        |
| Root snapshot                         | FR-56               | FR-56               | N/A: cannot fail                 | FR-56                      | FR-57         | N/A (g)           | N/A: one per block       | Security | N/A (b)                         | N/A (d)      | N/A (c)              | FR-57        |
| `ServerContext`                       | FR-35               | FR-35               | FR-55                            | FR-35                      | FR-35         | N/A: lifespan     | N/A: one block           | Security | FR-35                           | FR-11        | Compatibility        | FR-35        |
| Runtime settings                      | FR-81               | FR-45               | FR-45                            | N/A (e)                    | N/A: no state | N/A (g)           | FR-33                    | N/A (h)  | FR-81, FR-45                    | FR-52        | N/A (c)              | FR-46        |
| Registration                          | FR-62               | FR-62, FR-76        | FR-62, FR-76                     | N/A (e)                    | N/A: code     | N/A (g)           | N/A: no bound            | Security | FR-66                           | N/A (d)      | N/A (c)              | N/A (f)      |
| Registered spawn                      | FR-4, FR-62         | FR-27, FR-62, FR-63 | FR-5, FR-31, FR-62, FR-63, FR-99 | FR-34, NFR-1               | FR-29         | Data model        | FR-33                    | NFR-4    | FR-27                           | FR-42        | Compatibility        | FR-78        |
| Handle status and outcome             | FR-19, FR-21        | N/A (a)             | FR-34, FR-77                     | FR-21                      | FR-77, FR-82  | Data model        | N/A: any awaiters        | FR-18    | FR-85                           | N/A (d)      | N/A (c)              | FR-32, FR-77 |
| Cancel                                | FR-25, FR-86        | N/A (a)             | FR-34, FR-77                     | FR-26, FR-40               | FR-29         | FR-26, FR-41      | N/A: one request         | Security | FR-83                           | FR-41        | N/A (c)              | FR-41        |
| Lookup                                | FR-70               | FR-70               | FR-5, FR-82                      | FR-34                      | FR-70         | FR-70             | FR-32                    | FR-18    | FR-32                           | N/A (d)      | N/A (c)              | FR-32        |
| Outcome and failure                   | FR-20               | N/A (a)             | FR-20, FR-47                     | FR-40                      | FR-40, FR-84  | Data model        | N/A: one per task        | NFR-4    | N/A (b)                         | FR-24        | N/A (c)              | N/A (f)      |
| Time limit                            | FR-27               | FR-27               | FR-27                            | FR-40                      | FR-27         | FR-41             | NFR-3                    | N/A (h)  | FR-27                           | FR-24        | N/A (c)              | N/A (f)      |
| Retries                               | FR-66               | FR-45               | FR-47                            | FR-23                      | FR-84         | Data model        | FR-66                    | N/A (h)  | FR-66                           | FR-24        | N/A (c)              | FR-74        |
| Queue and concurrency limit           | FR-33, FR-81        | FR-45               | FR-41                            | FR-33                      | FR-29         | Data model        | FR-33                    | N/A (h)  | FR-33                           | FR-41        | N/A (c)              | FR-41        |
| Claims and leases                     | FR-85               | N/A (a)             | FR-82                            | FR-78, FR-84               | FR-83, FR-84  | Data model        | FR-85                    | N/A (h)  | FR-81, FR-85                    | FR-83        | N/A (c)              | FR-83        |
| Recovery of interrupted attempts      | FR-84               | N/A (a)             | FR-82                            | FR-78                      | FR-84         | Data model        | N/A: one per lease       | N/A (h)  | FR-66                           | FR-24        | N/A (c)              | FR-84        |
| Close                                 | FR-29               | N/A (a)             | FR-29                            | FR-29, FR-31               | FR-29         | Runtime state     | FR-30                    | FR-61    | FR-30                           | FR-61        | N/A (c)              | FR-29, FR-77 |
| Store protocol                        | FR-78               | FR-78               | FR-82, FR-90                     | FR-78                      | FR-78         | FR-78, FR-90      | N/A: the store's         | FR-65    | N/A (b)                         | N/A (d)      | Compatibility        | FR-78        |
| In-memory store                       | FR-79               | FR-79               | FR-82                            | FR-79                      | FR-29         | FR-78             | FR-32                    | N/A (h)  | FR-32                           | N/A (d)      | N/A (c)              | FR-79        |
| Encoding                              | FR-63, FR-76        | FR-63               | FR-63, FR-76                     | N/A (e)                    | FR-63         | N/A (g)           | N/A: no bound            | FR-63    | N/A (b)                         | N/A (d)      | Compatibility        | N/A (f)      |
| Task context and scopes               | FR-10, FR-74, FR-88 | N/A (a)             | FR-47                            | FR-10                      | FR-48         | FR-74             | N/A: one per attempt     | FR-10    | FR-11                           | FR-11, FR-16 | N/A (c)              | FR-48        |
| Task state                            | FR-9                | FR-63               | FR-2                             | N/A: per attempt           | FR-9          | N/A (g)           | N/A: no bound            | Security | N/A: D-60                       | N/A (d)      | N/A (c)              | FR-57        |
| Link and `ObservabilityScopeEntering` | FR-12               | FR-15, FR-87        | FR-15, FR-87                     | N/A: on the scope's loop   | FR-12         | N/A (g)           | N/A: one link            | Security | FR-11                           | FR-14, FR-15 | FR-13                | N/A (f)      |
| Removals and `ctx.spawn`              | FR-68, FR-69, FR-89 | FR-69               | FR-69                            | N/A (e)                    | N/A: removed  | N/A (g)           | N/A: removed             | N/A (h)  | N/A (b)                         | FR-69        | FR-68, Compatibility | N/A (f)      |
| Task tree                             | FR-54               | N/A (a)             | FR-54                            | FR-54                      | FR-54         | FR-17             | N/A: one per task        | N/A (h)  | N/A (b)                         | FR-17        | N/A (c)              | FR-54        |
| Attempt end and stored task           | FR-65, FR-78        | FR-90               | FR-90                            | FR-78                      | FR-82         | Data model, FR-90 | N/A: one end per attempt | NFR-4    | N/A (b)                         | N/A (d)      | N/A (c)              | N/A (f)      |
| Runtime scope                         | FR-88               | N/A (h)             | FR-82                            | N/A: one scope per runtime | FR-29         | Runtime state     | N/A: one per runtime     | N/A (h)  | N/A (b)                         | FR-88        | N/A (c)              | FR-88        |
| Default registered name               | FR-91               | FR-91               | FR-91                            | N/A (e)                    | N/A: code     | N/A (g)           | N/A: no bound            | Security | FR-91                           | N/A (d)      | Compatibility        | N/A (f)      |
| Failing unsupported tasks             | FR-93               | FR-92               | FR-82, FR-93                     | FR-78, FR-93               | FR-29, FR-78  | Data model, D-116 | FR-78                    | Security | FR-92                           | FR-93        | Compatibility        | N/A (f)      |
| Listing                               | FR-94, FR-98        | FR-94, FR-95        | FR-94                            | FR-97                      | N/A: reads    | N/A (g)           | FR-94, FR-97             | Security | N/A: per-call limit, no setting | N/A (d)      | Compatibility        | FR-32, FR-94 |
| Task summary and keyset               | FR-98               | FR-98               | FR-98                            | N/A (e)                    | N/A: values   | FR-98             | N/A: one per task        | FR-98    | N/A (b)                         | N/A (d)      | Compatibility        | N/A (f)      |
| Listing timestamps                    | FR-96               | N/A (h)             | N/A: cannot fail                 | FR-96                      | FR-80         | N/A (g)           | N/A: one per event       | N/A (h)  | N/A (b)                         | N/A (d)      | Compatibility        | FR-32        |

Count: 31 rows, 372 cells: 247 covered by requirements, sections or decisions, 125 `N/A` with a
reason; none empty and none naming an open question. "N/A: reads" means the operation changes
nothing, so an interruption leaves nothing to recover.

## Open questions

None open. OQ-1 to OQ-20 and OQ-22 to OQ-58 are resolved; OQ-21 is withdrawn.

### OQ-53 — Items of a task listing (resolved)

Resolved by [D-112](#decisions).

- **Header:** List items
- **Question:** What should each item returned by `ManagedTasks.tasks(...)` be: a task handle, a
  summary of the task with its status and times, or the stored task with its times?
- **Why it matters:** Blocking. Fixes the result type of FR-94, the store's `query` (FR-78), what
  FR-96's timestamps are shown in, TDS-002 FR-24's selected columns, and NFR-7's exports. A handle
  of an unlisted name gives its status only (D-96); the stored task carries the argument JSON and
  the origin, which this document treats as possibly sensitive (FR-87, NFR-4).
- **Options:**
  1. **Summary (Recommended)** — a new `State` per task: identifier, registered name, status with
     the outcome kind when finished, the number of attempts, creation and finish time; no argument,
     result or origin; one store read per page; a handle comes from `ManagedTasks.task(identifier)`.
  1. **Handles** — `ManagedTask[State | None]` per task, as lookup returns; no times visible; each
     `status()` or `outcome()` is one more store read; `outcome()` raises for unlisted names.
  1. **Stored tasks** — `StoredTask` plus its creation and finish time; everything the store holds,
     the argument, result and origin JSON included; one read; callers decode the JSON themselves.
- **Recommendation:** option 1: it shows the status of a page in one read, as D-83 batches reads to
  bound store load, and keeps the argument and origin out of listings (FR-87, NFR-4).

### OQ-54 — Pages of a task listing (resolved)

Resolved by [D-113](#decisions).

- **Header:** Pagination
- **Question:** How should `ManagedTasks.tasks(...)` page through tasks: a keyset cursor in haiway's
  existing `Pagination` and `Paginated` types, a keyset cursor in typed types of its own, or an
  offset with a limit?
- **Why it matters:** Blocking. Fixes the parameters and result of FR-94 and FR-97, the store's
  `query`, and TDS-002 FR-24's SQL. PostgreSQL keeps finished rows until the application deletes
  them (TDS-002 D-5), so listings grow without bound.
- **Options:**
  1. **haiway `Pagination`** — the store returns an opaque string token of the last item's creation
     time and identifier; reuses exported types (`utils/pagination.py:16-62`); its `arguments` stay
     unused, and `has_next_page` is true for a full last page, so a client can fetch one empty page
     (`:211-212`).
  1. **Typed cursor** — own page type with the items and the cursor of the next page, absent on the
     last page; an exact end signal; two more exported types.
  1. **Offset** — `offset` and `limit`; an insert or delete between two calls shifts the pages, so a
     traversal can skip or repeat tasks; PostgreSQL computes every skipped row ("a large `OFFSET`
     might be inefficient", PostgreSQL 16 and 18, `LIMIT` and `OFFSET`, per WebExplorer).
- **Recommendation:** a keyset cursor (option 1 or 2) over option 3, for the cost and the stability
  above; the cited facts do not choose between options 1 and 2.

### OQ-55 — Order of a task listing (resolved)

Resolved by [D-114](#decisions).

- **Header:** List order
- **Question:** In which direction should `ManagedTasks.tasks(...)` return tasks by creation time:
  newest first, oldest first, or as the caller chooses per call?
- **Why it matters:** Completes FR-97 and TDS-002 FR-24. The key is fixed (creation time, then
  identifier, FR-97); a PostgreSQL index on it serves both directions.
- **Options:**
  1. **Newest first** — recent tasks on the first page; one fixed direction.
  1. **Oldest first** — a traversal follows creation order; one fixed direction.
  1. **Caller chooses** — a direction parameter, whose default the answer also names.
- **Recommendation:** none.

### OQ-56 — Page size of a task listing (resolved)

Resolved by [D-115](#decisions).

- **Header:** Page size
- **Question:** How many tasks should one call of `ManagedTasks.tasks(...)` return: a required limit
  of 1 to 1,000, a default of 100 with a maximum of 1,000, or a default of 100 with no maximum?
- **Why it matters:** Completes FR-94, FR-97 and TDS-002 FR-24, and bounds one `query` statement and
  its memory. Other numbers can be given instead.
- **Options:**
  1. **Required, 1 to 1,000** — every call states its page size; `ValueError` outside the range.
  1. **Default 100, max 1,000** — calls may omit it; `ValueError` above 1,000 or below 1.
  1. **Default 100, no max** — any size of at least 1; one call can read a whole table.
- **Recommendation:** none.

### OQ-57 — Expired leases under `unsupported="fail"` (resolved)

Resolved by [D-116](#decisions).

- **Header:** Fail scope
- **Question:** With `unsupported="fail"`, should a runtime also fail `running` tasks of unlisted
  names whose lease expired because their runtime stopped, or only `queued` and due `retry_pending`
  tasks?
- **Why it matters:** Completes FR-78's `fail_unsupported` row, FR-93 and TDS-002 FR-23. Only
  `running` tasks carry a pending cancel request, so this is where "a cancel request still wins"
  (D-110) has effect.
- **Options:**
  1. **Due tasks only (Recommended)** — as written; an interrupted task of an unlisted name waits
     for a runtime listing its name, which records the interruption under the retry policy (FR-84).
  1. **Expired leases too** — `fail_unsupported` also takes expired leases of unlisted names,
     records the interrupted attempt and finishes the task `TaskUnsupported`, or `TaskCancelled`
     when a cancel was requested; a task interrupted on the only node listing its name is failed by
     the others.
- **Recommendation:** option 1: D-110 says "due", which the glossary's due moment and TDS-002's due
  row define as `queued` or `retry_pending`.

### OQ-58 — Spawning a call of a same-named task (resolved)

Resolved by [D-117](#decisions).

- **Header:** Same name
- **Question:** Should `ManagedTasks.spawn` reject a call made by a registered task other than the
  one the runtime lists under that name, now that two functions of one `__name__` get one registered
  name by default?
- **Why it matters:** FR-62 checks only the name and the result class (D-80): a call of an unlisted
  `b.process` spawned through a runtime listing `a.process`, both returning `R`, is stored and runs
  `a.process` with `b.process`'s argument JSON, decoded by `a.process`'s argument class. D-108 makes
  such a collision likelier.
- **Options:**
  1. **Keep, document** — only name and result class are checked; FR-91's documentation warns.
  1. **Check the task** — a call carries an opaque registration identity, typed so that it does not
     mention `Argument` (D-80's variance holds), and `spawn` raises `ValueError` unless it is the
     listed task's; another process's spawn stays unchecked.
- **Recommendation:** none.

### OQ-41 — Timings and close of the in-memory store (resolved)

Resolved by [D-88](#decisions).

- **Header:** In-memory
- **Question:** Should the in-memory store keep the lease, renewal and polling timings and the
  close-time release of every store (D-71, D-66), or should the timings be given with a store that
  needs them, absent for the in-memory store, which then cancels running tasks at close with
  `TaskCancelled`?
- **Why it matters:**
  - Blocks the `TaskRuntime` and `InMemoryTaskStore` signatures, FR-29, FR-77, FR-79, FR-80, FR-81
    and FR-85, and TDS-002 FR-4; contradicts D-71 and the "same paths" of D-66, so it is a user
    decision (review 2 S7).
  - Every runtime on one in-memory store shares one event loop (FR-34), so its leases expire only
    when that loop stalls, which yields false interruptions; and with `store=None` a task running at
    close is released into a store no one can reach again, its handle raising `RuntimeError`: the
    task is silently lost.
- **Options:**
  1. **Keep D-71 as is** — three required timings for every store; a close releases with every
     store; tasks running at an in-memory close are lost with the process, as today.
  1. **Timings with the store** — a store that needs leases takes the three timings (TDS-002 store
     constructor); the in-memory store has none and never expires a lease; at close it fixes
     `TaskCancelled` for running tasks and their handles return it; supersedes D-71 and narrows
     D-66.
  1. **Keep timings, cancel in memory** — the timings stay on `TaskRuntime` (D-71), but the
     in-memory store finishes released tasks with `TaskCancelled`, so handles return an outcome;
     false interruptions on loop stalls remain.
- **Recommendation:** none; the cost of keeping D-71 is stated above, the cost of changing it is a
  store-dependent path that D-66 ruled out.

### OQ-42 — Where the time limit is counted from (resolved)

Resolved by [D-89](#decisions).

- **Header:** Time limit
- **Question:** Should an attempt's time limit keep counting from its due moment (the spawn, the end
  of a retry delay, or the close that released it), as D-12 and FR-27 say, or count from the moment
  the attempt is claimed and starts, with any start deadline a separate setting?
- **Why it matters:**
  - Blocks FR-27, FR-33 acceptance 2, FR-41, NFR-3, `ClaimedAttempt.waited` and TDS-002 FR-16;
    contradicts D-12 and the counting HM-8 asked for (review 2 S1 and M1).
  - Counting from the due moment counts queue wait and downtime: an attempt released at a close is
    due at the close, so a deploy longer than the limit fails released tasks without running them,
    for good under the default of no retries, which defeats D-56.
- **Options:**
  1. **Due moment (as D-12)** — unchanged; documentation warns that downtime counts.
  1. **From the start** — the limit counts from the claim; queue wait and downtime never count;
     `ClaimedAttempt.waited` and FR-41's "fails without running" are removed; D-12 superseded.
  1. **Start plus deadline** — the limit counts from the start, and a separate optional
     `start_deadline` at spawn fails an attempt not started in time; one more parameter and stored
     column.
- **Recommendation:** none.

### OQ-43 — Concurrency default and claim batch (resolved)

Resolved by [D-90](#decisions).

- **Header:** Concurrency
- **Question:** With no concurrency limit by default (D-21), the first runtime to claim takes every
  due task of a shared store; should a concurrency limit become required when a store other than the
  in-memory one is given, get a finite default, or stay unlimited, and should one claim return at
  most a fixed number of tasks?
- **Why it matters:**
  - Blocks `TaskRuntime(concurrency_limit=...)`, FR-33, FR-45, FR-78 (`claim`'s `limit` and the
    expired leases it returns beyond it) and TDS-002 FR-5; contradicts D-21 (review 2 S5).
  - Answer OQ-41 first: if timings move to the store, "a store other than in-memory" has a natural
    place for the limit.
- **Options:**
  1. **Keep D-21** — unlimited; a backlog lands on the first runtime; documentation recommends a
     limit.
  1. **Required with a store** — `concurrency_limit` required when `store` is given, optional for
     the in-memory default; claims are bounded by free slots.
  1. **Finite default** — a default limit for every runtime (the user gives the number) and a claim
     batch cap (the user gives the number) bounding expired leases too.
- **Recommendation:** none; the numbers of option 3 are not in the sources.

### OQ-44 — Result of `cancel` (resolved)

Resolved by [D-91](#decisions).

- **Header:** Cancel type
- **Question:** Should `ManagedTask.cancel()` keep returning `bool` (D-31, now always answered by
  the store, D-82), or return a small result type telling "cancelled now", "cancellation pending"
  and "already finished" apart?
- **Why it matters:** Blocks the `ManagedTask.cancel` signature, FR-26 and FR-86; contradicts D-31
  (review 2 S2). `True` today covers both a queued task finished at once and a running task whose
  cancellation is only requested.
- **Options:**
  1. **Keep `bool`** — D-31 as is; callers awaiting the outcome learn when it ends.
  1. **Result type** — `Literal["cancelled", "pending", "finished"]`; draive maps it to MCP
     `tasks/cancel`; supersedes D-31.
- **Recommendation:** none.

### OQ-45 — Spawning inside an application transaction (resolved)

Resolved by [D-92](#decisions).

- **Header:** Transaction
- **Question:** Should `ManagedTasks.spawn` be able to insert a task on the caller's
  `PostgresConnection`, inside its transaction, with the claim triggered after commit, or should the
  documentation state prominently that a task spawned inside a transaction is stored at once and
  survives its rollback?
- **Why it matters:** Blocks FR-4 and TDS-002's interface (review 2 S3). The store writes only
  through its own pool (TDS-002 FR-18), and a claim runs right after a spawn (FR-85), so a task can
  start before the transaction that spawned it commits.
- **Options:**
  1. **Document the limit** — no change; FR-4 and the documentation warn; applications spawn after
     commit.
  1. **Transactional insert** — a PostgreSQL-specific spawn path taking the caller's connection; the
     runtime claims only after the caller's commit; one store-specific behavior added to FR-80's
     table.
- **Recommendation:** none.

### OQ-46 — Backend of a runtime under `ServerContext` (resolved)

Resolved by [D-93](#decisions).

- **Header:** Backend
- **Question:** When `ServerContext(tasks=...)` is given a runtime created without `observability`,
  should the runtime record through a backend `ServerContext` builds from its own `observability`
  setting, or should `ServerContext` reject such a runtime?
- **Why it matters:** Blocks FR-35 and FR-52 (review 2 S10). `ServerContext(observability=...)` may
  be a per-request factory (`starlette/context.py:122-145`), while a runtime without a backend
  creates a plain `LoggerObservability` (FR-52), so under OpenTelemetry tasks log to a plain logger
  and FR-15's links never appear. A factory can be called once with
  `traceparent=None, tracestate=None` (`starlette/types.py:33-38`).
- **Options:**
  1. **Built by ServerContext** — `ctx.managed_tasks` gains an optional fallback backend;
     `ServerContext` passes `observability(traceparent=None, tracestate=None)`; the runtime's own
     setting wins.
  1. **Required** — `ServerContext` raises `ValueError` at construction for a runtime without
     `observability` when its own setting is not a plain `Logger` or backend.
  1. **Unchanged** — documented only.
- **Recommendation:** none.

### OQ-47 — Decoding across versions (resolved)

Resolved by [D-94](#decisions).

- **Header:** Versions
- **Question:** During a rolling deploy, should a runtime decode a stored argument with
  `ignore_extra=True` and release (not count) an attempt whose argument still fails to decode, or
  should argument shapes be versioned through the registered name, with decoding staying strict?
- **Why it matters:** Blocks FR-47 and FR-63 (review 2 S8). Strict decoding makes an older replica
  fail, as a preparation failure, a task a newer replica spawned with an added field;
  `from_json(..., ignore_extra=True)` skips such fields, except below a class with its own
  `__init__` or `validate` (`attributes/state.py:1344-1370`).
- **Options:**
  1. **Lenient, release** — decode with `ignore_extra=True`; a decode failure releases the attempt
     uncounted; a task no runtime can decode is released and claimed again without end.
  1. **Lenient, count** — decode with `ignore_extra=True`; a remaining failure stays a preparation
     failure under the retry policy.
  1. **Versioned names** — strict decoding; a changed argument shape gets a new registered name (for
     example `reports.build.v2`); old replicas never claim it.
- **Recommendation:** none.

### OQ-48 — Caller identifier and the task's own identity (resolved)

Resolved by [D-95](#decisions).

- **Header:** Idempotency
- **Question:** Should `ManagedTasks.spawn` accept an optional caller-supplied identifier stored
  only if absent, and should a task body be able to read its identifier and attempt number (through
  a state the runtime adds or a `ManagedTask.current()` call)?
- **Why it matters:** Blocks FR-4, FR-9, FR-18 and `TaskInserting` (review 2 S11). Execution is at
  least once (non-goals), yet a task cannot build an idempotency key from its own identity, and a
  caller retrying a spawn creates a second task. A runtime-added state widens FR-9 beyond D-68's
  "argument State only".
- **Options:**
  1. **Neither** — keys travel in the argument State; D-68 unchanged.
  1. **Identifier at spawn** — `spawn(call, identifier=...)`; when the identifier exists, `spawn`
     returns its handle if name and argument are equal and raises `ValueError` otherwise; nothing
     added to the task state.
  1. **Both** — option 2, plus a `ManagedTaskAttempt(identifier, attempt)` state at the owner's
     priority in every attempt scope (amends D-68).
- **Recommendation:** none.

### OQ-49 — Tasks a runtime cannot decode or no longer finds (resolved)

Resolved by [D-96](#decisions).

- **Header:** Unlisted
- **Question:** What should `ManagedTasks.task` and a handle do for a task whose registered name
  this runtime does not list, or that the store no longer holds, and should a task whose name no
  runtime lists be signalled anywhere?
- **Why it matters:** Blocks FR-32, FR-70 and the `TaskLookup` result (review 2 M4, M5). A lookup
  cannot decode a value without the result class; a row no runtime lists waits forever with no
  record (TDS-002 FR-10); a dropped in-memory task leaves pollers waiting.
- **Options:**
  1. **None and raise** — lookup returns `None` for unlisted names; a handle whose task disappears
     raises `LookupError` naming the identifier; unlisted rows are documented with a query.
  1. **Status only** — lookup returns a handle whose `status()` works and whose `outcome()` raises
     `ValueError` for an unlisted name; disappearance as option 1.
  1. **Warn on unlisted** — as option 1, and each claim round logs one WARNING per registered name
     it sees due but does not list (a store query added to FR-78).
- **Recommendation:** none.

### OQ-50 — CI job for the PostgreSQL store (resolved)

Resolved by [D-97](#decisions).

- **Header:** CI job
- **Question:** Should CI run the PostgreSQL contract tests against a PostgreSQL service container,
  reversing D-76?
- **Why it matters:** Blocks NFR-5, FR-80 and TDS-002 NFR-3 (review 2 S13). Today the contract tests
  of TDS-002 run only with a developer's DSN; a CI job with a service container is not a unit test,
  so AGENTS.md's no-network rule for unit tests does not forbid it.
- **Options:**
  1. **Keep D-76** — local runs only.
  1. **CI job** — a job with a PostgreSQL service container sets the DSN and runs the contract and
     TDS-002 tests on every pull request; supersedes D-76.
- **Recommendation:** none.

### OQ-51 — A completed attempt under a pending cancel (resolved)

Resolved by [D-98](#decisions).

- **Header:** Late cancel
- **Question:** When a cancel is pending in the store and the attempt has completed (or failed),
  should `record` keep recording the attempt as `cancelled`, or record it as it ended while the task
  still finishes with `TaskCancelled`?
- **Why it matters:** Blocks FR-26, FR-78 and FR-90 rules 6 and 7, and TDS-002 FR-12 (review 2 M5).
  Recording `cancelled` loses what the attempt did, which audit and side-effect reconciliation may
  need.
- **Options:**
  1. **Recorded as cancelled** — as today; the attempt history hides that the work completed.
  1. **Recorded as it ended** — the attempt row says `completed` or the failure; the task is
     `StoredCancelled`; FR-90 allows any last attempt under `StoredCancelled`.
- **Recommendation:** none.

### OQ-52 — A `record` still failing at close (resolved)

Resolved by [D-99](#decisions).

- **Header:** Close wait
- **Question:** When a close waits for an attempt whose `record` keeps failing (the database is down
  at shutdown), should the close wait until `record` succeeds, bound the retries by a duration and
  then abandon them, or stop retrying as soon as the close begins?
- **Why it matters:** Blocks FR-29 step 6 and FR-82. FR-82 retries `record` until it returns, and
  FR-29 waits for those retries, so as written a close with the store unreachable never returns and
  a deploy hangs. An abandoned `record` leaves the lease to expire, and the task is then recovered
  as `TaskInterrupted`, counting an attempt that completed.
- **Options:**
  1. **Wait without bound** — no completed work is lost; shutdown hangs while the store is down.
  1. **Bound by a duration** — retries continue until the grace period (or `lease`, the user says
     which) has elapsed from the close, then stop; the task is recovered as interrupted.
  1. **Stop at close** — the first failure during a close abandons the `record`; shutdown is never
     delayed by the store.
- **Recommendation:** none.

### OQ-40 — Where lease and polling timings come from (resolved)

Resolved by [D-71](#decisions).

- **Header:** Timings
- **Question:** The one runtime now claims, renews leases and polls for every store (D-66), while
  TDS-002 D-3 made lease, renewal and polling intervals required settings with no defaults for the
  PostgreSQL store: should each store declare its timings (the in-memory store declaring leases that
  never expire, with no renewal or polling), should the runtime take the three as settings required
  for every store, or should the runtime take them as settings with defaults?
- **Why it matters:**
  - Blocks FR-81, FR-83, FR-85, the `TaskRuntime` and `TaskStore` signatures, and TDS-002 FR-4.
  - The in-memory store is the default (D-66) and cannot outlive its process, so a lease there only
    guards against an event loop stalled beyond the lease, after which the same process would run
    the task twice.
  - D-66 asks the in-memory store to take the same paths as any other.
- **Options:**
  1. **Store declares timings** — `TaskStore` gains a timings value; the PostgreSQL store requires
     the three (TDS-002 D-3); the in-memory store declares leases without expiry and wakes runtimes
     on every change, so the runtime skips renewal and polling for it: one path differs by store.
  1. **Runtime settings, required** — `TaskRuntime(lease=..., lease_renewal=..., polling=...)` for
     every store, the in-memory default included; same paths everywhere; every block states three
     numbers.
  1. **Runtime settings, defaulted** — the same settings with defaults (for example 30/10/1 s); the
     defaults replace TDS-002 D-3's "no defaults".
- **Recommendation:** none.

### OQ-38 — Where the restored form sits in the outcome union (resolved)

Resolved by [D-62](#decisions).

- **Header:** Restored
- **Question:** D-52 adds a restored variant for outcomes read back from storage; since a time-limit
  failure (D-48) and a cancellation carry nothing that storage loses, should the restored form
  replace only the exception of a raised or preparation failure, be one outcome-level variant for
  restored failures, or be one outcome-level variant wrapping every restored outcome?
- **Why it matters:**
  - Blocks FR-20, FR-64, TDS-002 FR-11 and the `TaskOutcome` type.
  - With the shared work queue (TDS-002 D-1), "read back" covers every outcome of a task run by
    another runner, not only outcomes from before a restart: a handle in the spawning process can
    receive a restored outcome while that process keeps running.
  - What a stored failure holds is settled: its kind and the exception's qualified type name, or the
    time limit (D-52, FR-65); a completed value is restored with `from_json` (FR-76).
- **Options:**
  1. **Restored failure record** — `TaskFailure` gains `TaskFailureRecord(kind, exception_type)` for
     raised and preparation failures read back; `TaskCompleted`, `TaskCancelled` and
     `TaskTimeLimitExceeded` have one shape whether live or restored; code matching `TaskRaised`
     also handles the record.
  1. **Restored failed outcome** — `TaskOutcome` gains
     `TaskFailedRestored(failure_kind, exception_type or time limit, attempts)` for every failure
     read back; completed and cancelled outcomes have one shape.
  1. **Restored outcome wrapper** — `TaskOutcome` gains `TaskRestored` holding the stored outcome
     (completed value, failure record, or cancelled); code tells a live outcome from a stored one at
     the top, for every variant.
- **Recommendation:** none.

### OQ-39 — Classes of stored spawn state (resolved)

Resolved by [D-63](#decisions).

- **Header:** State types
- **Question:** D-53 stores the spawn state of a registered task as JSON and re-applies it; since a
  stored element needs its `State` class to be decoded and State classes have no names, should a
  registered task declare the spawn state types it accepts when it is registered, or should
  applications register `State` classes under stable names shared by all tasks?
- **Why it matters:**
  - Blocks FR-62, FR-63 and TDS-002 FR-9.
  - A State is identified only by its class; there is no name registry of States (per CodeExplorer,
    from a search of `attributes/`; no single line shows an absence), and D-46 rules out import
    paths for task identity.
  - Arguments need no such answer: their classes are the parameters' annotations (FR-62).
- **Options:**
  1. **Declared per task** — `managed_task(name=..., state=(A, B))`; spawn rejects other types with
     `TypeError`; stored elements are keyed by their position among the declared types; one type at
     most once per task.
  1. **Named State registry** — applications register State classes under stable names (for example
     a decorator); stored elements carry that name; spawn rejects unregistered types; names are
     unique per process and, with TDS-002, per database.
- **Recommendation:** option 1. The registered task already reads its argument types from its own
  declaration (FR-62), and D-46 rejects identities that live outside the task's registration, such
  as import paths; option 1 keeps the stored data decodable from the registered task alone.

### OQ-28 — Task kinds (resolved)

Resolved by [D-51](#decisions).

- **Header:** Task kinds
- **Question:** Should ManagedTasks accept only registered task functions (a stable registered name,
  `State` arguments, resumable by a persistent implementation), or also ad-hoc coroutine functions
  with arbitrary arguments that are never stored or resumed?
- **Why it matters:**
  - Blocks FR-4, FR-16, FR-59, FR-60, FR-62, FR-63, FR-67 and the interfaces.
  - `ctx.spawn_background` today takes any coroutine function with any arguments, or a coroutine
    object, and returns `asyncio.Task[Result]` (`access.py:407-460`); D-43 keeps it as a proxy, so
    the answer fixes its signature. Under either option it returns a task handle instead of an
    `asyncio.Task`.
  - Retries (D-47) run a function again, so a bare coroutine object, which runs once, cannot be
    spawned under either option.
- **Options:**
  1. **Registered and ad-hoc** — two spawn forms: a registered task (stored and resumed by
     persistent implementations) and an ad-hoc coroutine function with any arguments (in-process
     only under every implementation, never resumed).
     `ctx.spawn_background(function, *args, **kwargs)` keeps its argument shape and spawns an ad-hoc
     task.
  1. **Registered only** — every managed task is a registered task with `State` arguments, including
     through `ctx.spawn_background(task, *arguments)`; every call site of `ctx.spawn_background`
     registers its function first.
  1. **Implementation decides** — the protocol accepts ad-hoc tasks; a persistent implementation
     rejects them with `TypeError` at spawn; code written for the in-memory implementation can fail
     when the implementation is swapped.
- **Recommendation:** option 1. D-46 speaks of "resumable task identity", which presupposes tasks
  that are not resumable, and option 1 keeps the argument shape of `ctx.spawn_background`
  (`access.py:414-427`) that D-43 keeps as a proxy.

### OQ-29 — Outcome data across a process boundary (resolved)

Resolved by [D-52](#decisions).

- **Header:** Outcome data
- **Question:** What should a task's outcome carry so that a persistent implementation can return it
  after a restart: must a registered task's result be a `State` stored as JSON, and is a failure
  kept as the exception type and message, as the type only, or as the live exception in process with
  a stored record after a restart?
- **Why it matters:**
  - Blocks FR-20, FR-47, FR-59, FR-64, FR-65, NFR-4, the outcome and failure types, and the TDS-002
    schema.
  - v0.5's `TaskFailed.exception: BaseException` and `TaskCompleted.value: Result` cannot be read
    back from storage. `State` refuses pickling (`state.py:1651-1668`) and has a JSON form
    (`state.py:1344-1404`, `:1480-1532`).
  - Exception messages can carry secrets into the database; AGENTS.md forbids logging secrets.
  - It also decides the kind of the outcome types (`State` or frozen class), a defect of v0.5.
- **Options:**
  1. **`State` result, type and message** — a registered task returns a `State` or `None`; a failure
     is a record of the exception's qualified type name and its message, the same in process and
     after a restart; the live exception is only logged. Messages reach storage.
  1. **`State` result, type only** — as option 1, but the record keeps only the qualified type name;
     diagnosis goes through the logs, which carry the trace link.
  1. **Live, record after restart** — in process the outcome carries the live exception and any
     result; a task read back from storage after a restart carries a stored record (type name) and a
     `State` result; the outcome union has a restored variant next to the live ones.
- **Recommendation:** none.

### OQ-30 — Spawn state and disposables of resumed and retried tasks (resolved)

Resolved by [D-53](#decisions).

- **Header:** Spawn state
- **Question:** D-40 lets a spawn pass explicit `State`s and disposables: for a task that is retried
  or resumed after a restart, should those be accepted only for ad-hoc tasks, entered once and held
  across all attempts (and absent after a restart), stored as JSON and re-applied, or given as
  factories called for every attempt?
- **Why it matters:**
  - Blocks FR-4, FR-9, FR-63, FR-64 and the spawn signature.
  - A haiway disposable is an async context manager; one built with `asynccontextmanager` can be
    entered once, so it cannot be entered again for a second attempt.
  - Nothing that is not stored can be restored after a restart, and States holding clients or
    functions have no JSON form.
- **Options:**
  1. **Ad-hoc tasks only** — explicit state and disposables are spawn arguments of ad-hoc tasks
     only; a registered task's state is the root snapshot plus presets, and its data travel as its
     `State` arguments.
  1. **Entered once, not restored** — accepted for every task; disposables are entered before the
     first attempt and released after the last, held during retry delays; a task resumed after a
     restart runs without them.
  1. **State stored, disposables ad-hoc** — explicit `State`s are stored as JSON with the task and
     applied to every attempt and after a restart; explicit disposables are accepted for ad-hoc
     tasks only.
  1. **Disposable factories** — spawn takes functions producing disposables, called for every
     attempt; explicit `State`s are applied to every attempt; neither is restored after a restart.
- **Recommendation:** none.

### OQ-31 — Retry policy shape (resolved)

Resolved by [D-54](#decisions).

- **Header:** Retry scope
- **Question:** Where can the retry policy that D-47 gives the implementation be set: on the
  implementation only, as an implementation default overridden per registered task, or as an
  implementation default overridden at each spawn?
- **Why it matters:**
  - Blocks FR-19, FR-27, FR-59, FR-66 and the signatures of the registration and spawn.
  - Settled regardless of the answer: a policy is a maximum number of attempts, a delay before each
    retry, and a predicate on the failure, the same parts as `retry`
    (`src/haiway/helpers/retries.py:35-74`); a time-limit failure is retryable (D-48); a task
    cancelled through its handle is never retried, since retrying would undo the cancellation the
    caller asked for; what a close does to retries is OQ-33.
- **Options:**
  1. **Implementation only** — one policy for every task of an implementation; tasks that need
     different policies use different implementations.
  1. **Per registered task** — the implementation's policy is the default; a registration can carry
     its own, stored with the task, so it holds after a restart; ad-hoc tasks use the default.
  1. **Per spawn** — the implementation's policy is the default; each spawn can pass its own, stored
     with the task.
- **Recommendation:** none.

### OQ-32 — Retry defaults and retryable failures (resolved)

Resolved by [D-55](#decisions).

- **Header:** Retry dflt
- **Question:** What should the default retry policy be, and is a preparation failure (a preset or
  spawn disposable failing to enter) retried like a failure of the body?
- **Why it matters:** Blocks FR-45, FR-47, FR-66. HM-3 needs preparation failures distinguishable
  (D-25), not whether they are retried.
- **Options:**
  1. **No retries** — one attempt by default; retries only when configured; preparation failures
     retried like any other failure once configured.
  1. **No retries, preparation never** — one attempt by default; a preparation failure is never
     retried.
  1. **Three attempts, backoff** — three attempts by default, delays of 1 s then 2 s; preparation
     failures retried.
- **Recommendation:** none.

### OQ-33 — Stored running tasks at close (resolved)

Resolved by [D-56](#decisions).

- **Header:** Stored close
- **Question:** When a persistent implementation's ManagedTasks block closes, what should happen to
  its stored tasks that are running, queued or waiting for a retry: stay resumable for the next open
  (here or in another process), be recorded as an interrupted attempt and retried under the retry
  policy, or be recorded as cancelled?
- **Why it matters:** Blocks FR-64, FR-72, the TDS-002 status model and its resume rules. D-50 says
  closing closes the tasks; the in-memory reading (cancel) leaves nothing to resume, which would
  void D-45's resuming for a planned shutdown.
- **Options:**
  1. **Left resumable** — running work is cancelled in process; the stored task returns to `queued`
     with the attempt not counted; the next open resumes it.
  1. **Interrupted attempt** — the attempt is recorded as failed (interrupted) and the retry policy
     decides; with no retries left the task fails.
  1. **Cancelled** — the stored task is finished as cancelled; only a crash leaves work to resume.
- **Recommendation:** none.

### OQ-34 — Completion notification for draive (resolved)

Resolved by [D-57](#decisions). draive TDS-003 FR-31.

- **Header:** Done signal
- **Question:** With completion callbacks removed (D-42), how should draive learn that a task
  finished, so that it can persist the outcome and wake the realtime session: by awaiting each task
  handle in a watcher task of its own, through a stream of finished tasks offered by ManagedTasks,
  or through a wait on many tasks offered by ManagedTasks?
- **Why it matters:**
  - draive HM-7, HM-11 and HM-12 rest on callbacks (draive TDS-004 lines 1153, 1157, 1158), and
    draive TDS-004 FR-38 and draive TDS-003 FR-31 need outcomes of tasks cancelled at close to be
    applied before close returns.
  - Under every option HM-12 and HM-13 as written are not met by haiway: the block's close returns
    without running draive code (see [Traceability](#traceability)).
- **Options:**
  1. **Await the handle** — no haiway API; draive awaits `outcome()` of each task in a managed task
     or a scope task of its own; draive owns the latency and the ordering at close.
  1. **Finished stream** — ManagedTasks offers an async iterator of finished task identifiers for
     the block; it ends when the block closes, after the tasks cancelled by the close are reported.
  1. **Wait on many** — ManagedTasks offers waiting until any of given task identifiers finishes,
     returning those finished.
- **Recommendation:** none.

### OQ-35 — `ctx.spawn` outside a scope (resolved)

Resolved by [D-58](#decisions).

- **Header:** Spawn fallbk
- **Question:** With the background task group removed (D-43), what should `ctx.spawn` (and
  `ContextTaskGroup.run`) do when called outside any scope, where it falls back to a background task
  today: spawn through the ManagedTasks proxy, or raise?
- **Why it matters:** Blocks FR-69. Today the fallback of `ctx.spawn` is logged at DEBUG
  (`tasks.py:270-284`), and that of `ContextTaskGroup.run` is not logged (`tasks.py:453-459`;
  corrected in v1.4). Outside any scope a ManagedTasks is in context only after
  `ctx.managed_tasks(...)` or `ctx.updating(...)` placed it there.
- **Options:**
  1. **Proxy** — the fallback calls the `ctx.spawn_background` proxy, so it raises
     `ContextStateMissing` when no ManagedTasks is in context.
  1. **Raise** — `ctx.spawn` outside any scope raises `ContextMissing`, closing a given coroutine.
- **Recommendation:** none.

### OQ-36 — Retention bound of the in-memory implementation (resolved)

Resolved by [D-59](#decisions).

- **Header:** Keep bound
- **Question:** Should the in-memory implementation bound the number of finished tasks it keeps for
  lookup, in addition to the 3600 s retention period (D-11, D-20)?
- **Why it matters:** Blocks FR-32 and FR-45. With retention by time only, memory grows with the
  number of tasks finished within one period, without bound.
- **Options:**
  1. **Count bound** — a setting for the most finished tasks kept, 10,000 by default; the oldest
     finished task is dropped first when it is exceeded.
  1. **Time only** — no count bound; the documentation states the growth.
- **Recommendation:** none.

### OQ-37 — Presets of a task (resolved)

Resolved by [D-60](#decisions).

- **Header:** Task preset
- **Question:** With the presets registry now part of the root snapshot (D-39), should a spawn still
  accept presets as an argument (D-23), or should a task's preset be only the registry entry named
  like its task scope?
- **Why it matters:** Blocks FR-9 and the spawn signature. A scope resolves a directly given preset,
  or else the registry entry named like itself (`scope.py:84-89`); a registered task's scope is
  named after its registered name (FR-16), so a registry entry of that name applies to it.
- **Options:**
  1. **Registry only** — no presets argument; the task scope resolves the snapshot registry's entry
     of its name; nested scopes resolve the same registry.
  1. **Argument and registry** — a presets argument (D-23) is used for the task scope instead of the
     registry entry, as a direct preset is in `ctx.scope`; nested scopes resolve the registry.
- **Recommendation:** none.

### OQ-1 — Disposable state in the task (resolved)

Resolved by [D-1](#decisions); superseded by D-22, under which nothing is captured, so the question
is moot for managed tasks.

- **Header:** Disposed
- **Question:** When a managed task reads, through `ctx.state`, a State produced by a disposable of
  a scope that has exited (a request-scoped client, for example), what should it get: a defined
  error raised at the time of use, an error because such state is left out of the capture from the
  start, or plain absence under the usual default rules?
- **Why it matters:** Blocked FR-6, FR-7, FR-8. HM-3 allows "absent" or "raises a defined error"; no
  provenance existed; `DisposableState.of(...)` also wraps plain configuration state.
- **Options:**
  1. **Raise when used after exit** — `ctx.state(T)` returns it while its scope is open and raises
     `ContextStateDisposed` once it has exited.
  1. **Left out, raising** — left out at the start; reading raises `ContextStateDisposed`.
  1. **Left out, default rules** — `ctx.state(T)` constructs `T()` when it can.
- **Recommendation:** none.

### OQ-2 — Link to the starting trace (resolved)

Resolved by [D-2](#decisions).

- **Header:** Trace link
- **Question:** Should a managed task's records join the starting scope's trace as a child of the
  starting scope, or start a new trace that carries a link back to the starting scope?
- **Why it matters:** Blocked FR-12 to FR-15 and the protocol change.
- **Options:**
  1. **Same trace, child** — request traces grow for as long as tasks run.
  1. **New trace, link** — request traces end with the request.
- **Recommendation:** none.

### OQ-3 — Completion delivery and persistence (resolved)

Resolved by [D-3](#decisions).

- **Header:** Completion
- **Question:** Beyond awaiting a task handle, how should haiway deliver each task's outcome: a
  completion callback given when the task starts, a manager-wide observer called for every task, or
  a haiway store protocol that the manager writes outcomes to?
- **Why it matters:** Blocked FR-22, FR-38, FR-39, NFR-9.
- **Options:**
  1. **Per-task callback** — exit waits for it; failures are logged.
  1. **Manager observer** — one observer for all tasks.
  1. **Store protocol** — a second copy next to draive's records.
- **Recommendation:** option 1 — draive feeds its `DeferredResultStore` from a per-task callback.

### OQ-4 — How code starts a task (resolved)

Resolved by [D-4](#decisions).

- **Header:** Entry point
- **Question:** Should code start and check managed tasks through class methods of the task manager
  State, or through new `ctx` functions next to `ctx.spawn`?
- **Why it matters:** Blocked the interfaces.
- **Options:**
  1. **Manager class methods** — `ctx` unchanged.
  1. **`ctx` functions** — `ctx` grows by two functions.
- **Recommendation:** option 1 — the brief's preference puts public type-specific helpers on the
  class.

### OQ-5 — No task manager present (resolved)

Resolved by [D-5](#decisions).

- **Header:** No manager
- **Question:** What should starting a managed task do where no task manager was entered?
- **Why it matters:** Blocked FR-5.
- **Options:**
  1. **Raise** — `ContextStateMissing`; nothing starts.
  1. **Fall back to `ctx.spawn`** — HM-1 silently fails.
  1. **Unowned run** — nothing cancels it at shutdown.
- **Recommendation:** option 1 — HM-9 lets callers decide the fallback.

### OQ-6 — Presets in the task scope (resolved)

Resolved by [D-6](#decisions); D-22 made it the primary source of task state.

- **Header:** Presets
- **Question:** Should the task scope resolve context presets again (entering fresh preset
  disposables for the task), or receive only the captured state?
- **Why it matters:** Blocked FR-9.
- **Options:**
  1. **Captured state only.**
  1. **Re-resolve by name** — fresh preset resources per task.
- **Recommendation:** none.

### OQ-7 — Task scope name and kind (resolved)

Resolved by [D-7](#decisions).

- **Header:** Scope name
- **Question:** How should the task scope be named and which scope kind should it have?
- **Why it matters:** Blocked FR-16.
- **Options:**
  1. **Required name, internal.**
  1. **Function name, internal.**
  1. **Required name, kind argument.**
- **Recommendation:** none.

### OQ-8 — Task identifier (resolved)

Resolved by [D-8](#decisions).

- **Header:** Task id
- **Question:** Should a task handle carry an identifier, and if so, should it be a non-secret UUID
  or an unguessable token that can serve as a credential?
- **Why it matters:** Blocked FR-18, NFR-4.
- **Options:**
  1. **UUID, not a credential.**
  1. **256-bit token.**
  1. **No identifier.**
- **Recommendation:** none.

### OQ-9 — Default time limit (resolved)

Resolved by [D-9](#decisions).

- **Header:** Def. limit
- **Question:** Besides the per-task time limit HM-8 requires, should the manager have a default
  time limit for tasks started without one?
- **Why it matters:** Blocked FR-28.
- **Options:**
  1. **Per-task only.**
  1. **Manager default.**
- **Recommendation:** none.

### OQ-10 — Grace period at manager exit (resolved)

Resolved by [D-10](#decisions).

- **Header:** Grace period
- **Question:** On manager exit, should running tasks be cancelled at once, or given a grace period
  to finish before being cancelled?
- **Why it matters:** Blocked FR-30.
- **Options:**
  1. **Cancel at once.**
  1. **Grace period** — a setting, 0 s by default.
- **Recommendation:** option 1 — HM-6 as written.

### OQ-11 — Finished tasks and lookup (resolved)

Resolved by [D-11](#decisions).

- **Header:** Retention
- **Question:** Should the manager let callers look tasks up by identifier, and keep finished tasks
  whose outcome nobody collected?
- **Why it matters:** Blocked FR-32.
- **Options:**
  1. **Handles only.**
  1. **Lookup while running.**
  1. **Lookup with retention.**
- **Recommendation:** none.

### OQ-12 — Concurrency limit (resolved)

Resolved by [D-12](#decisions).

- **Header:** Task limit
- **Question:** Should the manager limit how many managed tasks run at once, and what happens beyond
  the limit?
- **Why it matters:** Blocked FR-33.
- **Options:**
  1. **No limit.**
  1. **Limit, reject.**
  1. **Limit, queue.**
- **Recommendation:** none.

### OQ-13 — `ServerContext` integration (resolved)

Resolved by [D-13](#decisions).

- **Header:** Server owner
- **Question:** Should a Starlette `ServerContext` enter a task manager on its own, or only when the
  application lists the manager's disposable?
- **Why it matters:** Blocked FR-35.
- **Options:**
  1. **Listed by the application.**
  1. **Built in.**
- **Recommendation:** none.

### OQ-14 — `ctx.spawn_background` (resolved)

Resolved by [D-14](#decisions).

- **Header:** Background
- **Question:** With managed tasks available, should `ctx.spawn_background` stay as is, be removed,
  or stay but have its documentation point to managed tasks?
- **Why it matters:** Blocked FR-36.
- **Options:**
  1. **Keep unchanged.**
  1. **Remove.**
  1. **Keep, redirect docs.**
- **Recommendation:** none.

### OQ-15 — Starting during disposable preparation (resolved)

Resolved by [D-15](#decisions).

- **Header:** Preparing
- **Question:** Should starting a managed task be allowed while a scope's disposables are being
  prepared or disposed, where `ctx.spawn` raises `RuntimeError`?
- **Why it matters:** Blocked FR-37.
- **Options:**
  1. **Allowed.**
  1. **Rejected.**
- **Recommendation:** none.

### OQ-16 — Context of the completion callback (resolved)

Resolved by [D-16](#decisions).

- **Header:** Callback ctx
- **Question:** In which context should the async completion callback run: inside the task scope
  (with the task's captured state and trace link), or in a scope of its own built from the manager
  owner's state?
- **Why it matters:** Blocked FR-38.
- **Options:**
  1. **Task scope.**
  1. **Owner scope** — a new root scope whose state holds only the manager.
- **Recommendation:** none.

### OQ-17 — Context variables of the task scope (resolved)

Resolved by [D-17](#decisions).

- **Header:** Context vars
- **Question:** Should the task scope start from an empty `contextvars.Context` (only haiway's own
  state passed in), or from a copy of the starting context with haiway's variables replaced by the
  new root scope?
- **Why it matters:** Blocked FR-10, FR-17.
- **Options:**
  1. **Empty context.**
  1. **Copied context.**
- **Recommendation:** none.

### OQ-18 — Manager exit against sibling disposables (resolved)

Resolved by [D-18](#decisions); its mechanism is D-27 (OQ-19).

- **Header:** Exit order
- **Question:** Since disposables listed together exit concurrently, how should the task manager's
  exit be ordered against the disposal of the resources those tasks and callbacks use?
- **Why it matters:** Blocked FR-29, FR-35.
- **Options:**
  1. **Documented constraint.**
  1. **Manager exits first.**
- **Recommendation:** none.

### OQ-19 — Mechanism of the supervisor stage (resolved)

Resolved by [D-27](#decisions).

- **Header:** Stage hook
- **Question:** How should a disposable be marked as one that `Disposables`, `ctx.scope`,
  `ctx.disposables` and `ServerContext` enter after all others and exit before them: a new public
  protocol any disposable can implement, an explicit separate parameter on each owner, or
  recognition of the task manager's disposable type only?
- **Why it matters:**
  - Blocks FR-43 and FR-44, the interfaces of `Disposables`, `ctx.scope`, `ctx.disposables` and
    `ServerContext`, and the shutdown path of HM-5 and HM-6 under draive TDS-003 FR-31.
  - Today one `Disposables` gathers every enter and exit concurrently (`disposables.py:116-123`,
    `:200-217`), and `ctx.scope` runs its preset and scope groups in sequence (`scope.py:152-182`).
- **Options:**
  1. **Stage protocol** — a new runtime-checkable `Disposable` sub-protocol declares the supervisor
     stage. `TaskManager.disposable()` implements it, and so can any application disposable. The
     owners detect it with `isinstance` across all their groups. The application lists the manager
     as any other disposable.
  1. **Separate parameter** — `Disposables.of(..., supervisors=...)`,
     `ctx.scope(..., supervisors=...)`, `ctx.disposables(..., supervisors=...)` and
     `ServerContext(..., supervisors=...)`. Nothing is detected at run time. A manager listed in
     `disposables` instead still races its resources.
  1. **Manager type only** — the owners check for the task manager's disposable type. No new public
     interface, but the ordering serves the manager alone.
- **Recommendation:** option 1. The caller asked for "an ordering hook on disposables" rather than a
  special case, and AGENTS.md's Golden Rule asks for "small, focused feature interfaces that are
  easily composable".

### OQ-20 — Links of manager scopes (resolved)

Resolved by [D-29](#decisions).

- **Header:** Cb link
- **Question:** Should the manager scope in which a completion callback runs (and the one recording
  a queued task's end) carry a link to the task's origin, the same way the task scope does?
- **Why it matters:**
  - Blocks part of FR-38 and FR-41.
  - D-16 fixes the callback scope's state and backend, not its links.
  - Without a link, a callback's records share no identifier with the request trace that started the
    task, except the task identifier logged by FR-42.
- **Options:**
  1. **Link to the origin** — the manager scope starts a new trace with one link to the origin
     (FR-12), recorded as in FR-14 and FR-15.
  1. **No link** — the manager scope is a plain new root; correlation goes through the task
     identifier only.
- **Recommendation:** none.

### OQ-21 — Disposed state with `default=` and `contains_state` (withdrawn)

Withdrawn before it was asked: it depended on `ContextStateDisposed` (FR-7), which D-22 makes
unnecessary.

### OQ-22 — Presets of a task (resolved)

Resolved by [D-23](#decisions): spawn arguments, as a sequence, following draive TDS-004 HM-2.

- **Header:** Task preset
- **Question:** How should the scope presets that build a managed task's state be chosen: by an
  argument at each spawn, by a default preset on the manager that a spawn argument can override, by
  presets registered on the manager and matched by the task's name, or by the `ctx.presets(...)`
  registry current at the spawn call?
- **Why it matters:**
  - Blocks FR-9, FR-10 and the parameters of `spawn` and `disposable`.
  - D-22 makes presets the only source of task state.
  - The registry is a context variable (`presets.py:265-306`) and is absent from the empty context
    of D-17 unless carried in.
  - A scope resolves the preset named like itself (`scope.py:84-89`), and D-7 names the task scope
    after the coroutine's qualified name.
- **Options:**
  1. **Spawn argument** — `spawn(..., preset: ContextPresets | None = None)`; no preset when absent;
     every call site states what the task gets; the registry is not needed.
  1. **Manager default, spawn override** — `TaskManager.disposable(preset=...)` applies to every
     task, and `spawn(..., preset=...)` replaces it for one task.
  1. **Manager registry by name** — `TaskManager.disposable(presets=...)`; the task scope uses the
     preset named like the coroutine's qualified name; no spawn argument; renaming a function
     changes its state.
  1. **Registry at spawn by name** — the registry current at the spawn call is carried into the
     empty context and matched by the task's name; the same preset can resolve differently depending
     on where the spawn happens.
- **Recommendation:** none.

### OQ-23 — Explicit state at spawn (resolved)

Resolved by [D-24](#decisions), following draive TDS-004 HM-2.

- **Header:** Spawn state
- **Question:** Should `TaskManager.spawn` accept explicit `State` instances for the task scope (for
  example a small immutable delivery key), or should such values reach a task only as coroutine
  arguments?
- **Why it matters:**
  - Blocks FR-9 and the signature of `spawn`.
  - draive needs at least small immutable values such as a delivery key (caller's note).
  - Explicit state ranks above preset state (`scope.py:176-182`).
  - An instance that a request-scoped disposable produced would reach the task untouched and stay
    usable after its disposal: the risk D-22 removed.
- **Options:**
  1. **Explicit state allowed** — `spawn(coroutine, *state: State, ...)`; highest priority in the
     task state; documentation warns against passing request-bound resources.
  1. **Arguments only** — no state parameter; `ctx.state` in a task holds only the manager and
     preset state.
- **Recommendation:** none.

### OQ-24 — Backend of the task scope (resolved)

Resolved by [D-26](#decisions).

- **Header:** Task backend
- **Question:** Which observability backend should a task scope record through: the starting scope's
  backend instance, the manager's backend, or a backend given at spawn with the manager's as the
  default?
- **Why it matters:**
  - Blocks FR-11.
  - Presets carry no backend (`presets.py:67`, `:172`).
  - Under Starlette the starting scope's backend is the per-request adapter
    (`middleware.py:184-190`).
  - The manager backend already serves callbacks (D-16).
  - The origin link applies under every option (FR-12, D-2), but its `trace_context` comes from the
    starting scope's backend. Under options 2 and 3 the task can record through another backend
    type. A `LoggerObservability` origin yields `{}`, so an OpenTelemetry task backend creates no
    span link and logs a WARNING (FR-15). An OpenTelemetry origin under a logger task backend still
    links, through the ids (FR-14). HM-4 then holds or degrades depending on how the application
    pairs the two backends.
- **Options:**
  1. **Starting scope's backend** — task records go wherever the request's go, through the same
     instance, which lives on while tasks run.
  1. **Manager backend** — one backend for all tasks and callbacks, configured once on
     `TaskManager.disposable`.
  1. **Spawn argument, manager default** — `spawn(..., observability=...)`; the manager backend when
     absent.
- **Recommendation:** none.

### OQ-25 — Missing preset (resolved)

Resolved by [D-25](#decisions). Its options below were written before D-25; with presets passed as
objects (D-23), no preset can be missing; preparation failure is an outcome (FR-47), following
draive TDS-004 HM-3.

- **Header:** No preset
- **Question:** When the preset chosen for a task does not exist, should `TaskManager.spawn` raise,
  should the task end as failed, or should the task run without it?
- **Why it matters:**
  - Blocks the missing-preset row of the error table and part of FR-9. Ask after OQ-22.
  - A preset whose disposables fail to enter can only fail the task: entry happens inside the task
    after `spawn` has returned (FR-4), so its outcome is `TaskFailed` (FR-20) under every option.
  - Today a scope with no matching preset resolves none, silently (`scope.py:84-89`,
    `presets.py:267-277`).
- **Options:**
  1. **Raise at spawn** — `ValueError` naming the preset; nothing starts; coroutine closed.
  1. **Task failed** — `spawn` returns; the outcome is `TaskFailed` with a lookup error; the
     callback is called.
  1. **Run without it** — as `ctx.scope` does; the task can meet `ContextStateMissing` or default
     state later.
- **Recommendation:** none.

### OQ-26 — Manager in the task state (resolved)

Resolved by [D-28](#decisions).

- **Header:** Task manager
- **Question:** Should the task scope also hold the `TaskManager` it was started through, so that a
  task can start further managed tasks, or should its state be exactly the given presets and
  explicit state?
- **Why it matters:**
  - Blocks part of FR-9 and FR-17.
  - Draive TDS-004 HM-2 says the task state is "exactly" the presets' state plus the explicit state.
  - The callback's manager scope holds the manager (D-16).
- **Options:**
  1. **Included** — the manager sits below presets and explicit state; nested spawns work without
     wiring; HM-2's "exactly" gains one type.
  1. **Excluded** — the state is exactly as HM-2 says; a task that starts tasks needs the manager
     passed as explicit state.
- **Recommendation:** none.

### OQ-27 — Release failure after a successful body (resolved)

Resolved by [D-30](#decisions).

- **Header:** Release fail
- **Question:** When a task's coroutine returns normally but releasing its task scope then fails
  (for example a preset disposable raising in `__aexit__`), what should the outcome be: a failure
  like a failing body, a completion with the release error only logged, or a separate
  release-failure outcome?
- **Why it matters:**
  - Blocks part of FR-20.
  - Draive TDS-004 HM-3 separates preparation failures from body failures, but says nothing about
    release.
  - A scope exit raises disposal errors (`scope.py:224-244`).
- **Options:**
  1. **Body failure** — `TaskFailed` with the release error; the returned value is lost; draive
     cannot tell release from body failures.
  1. **Completed, logged** — `TaskCompleted` with the value; the release error is logged at ERROR
     only.
  1. **Release variant** — a sixth outcome `TaskReleaseFailed`, carrying the value and the error.
- **Recommendation:** none.

## Unverified

- v1.6: of TDS-002 FR-23 and FR-24, only the items TDS-002 Unverified lists were left unrun; the
  rest was verified on PostgreSQL 16.16 and 18.7 (TDS-002 D-30).
- v1.6: that a `State` holds `Set[Literal[...]]` and `datetime` fields of the filter types and
  checks them in its own `__init__` was not run; D-106's probe covered `StoredTask` only.
- v1.6: Python documentation facts (PEP 3155, `time.monotonic()`, `datetime.UTC`) and PostgreSQL
  facts (`OFFSET`, index ordering, `timestamptz` resolution) are from WebExplorer, 2026-11-27, not
  opened by me; the `__name__`, `__qualname__` and naive-datetime behaviors were run by me on
  CPython 3.12.4, not on 3.15.
- Facts marked "per CodeExplorer", not opened by me: the State JSON methods and their exceptions
  (`state.py:1288-1532`), the float-key spelling of `tests/test_state_json.py:199-231`, the absence
  of a State name registry, the trace id sources of both backends, the OpenTelemetry scope-exit
  recording, the background-task mentions outside `src/`, `PostgresConnectionPool`'s second-entry
  error, and Starlette mount and lifespan dispatch.
- That PostgreSQL `JSONB` rejects `\u0000` is from WebExplorer, as relayed by the review (PostgreSQL
  18, `datatype-json.html`); I did not open it.
- That haiway's union decoding takes the first alternative that validates comes from the review's
  probe; the `Literal` tags make the design independent of it.
- That a `State` can hold `Literal[...] | TaskFailure` in `StoredAttempt.result` and specialized
  unions of States was not run.
- That pyright strict loses a method-level type parameter through `statemethod` was not checked.
- Facts from WebExplorer (v0.5), not opened by me: OpenTelemetry specification v1.63.0,
  opentelemetry-python 1.45.2, W3C Trace Context Level 1.
- The asyncio behavior for `KeyboardInterrupt` and `SystemExit` (FR-40) is taken from the caller's
  reading of CPython `Lib/asyncio/tasks.py`.
- W3C Trace Context limits (FR-87) are from WebExplorer (2026-11-26, Level 1 Recommendation and
  Level 2 draft); the 16 KiB total is inferred, and the Level 1 key length rule was confirmed only
  through the Level 2 text.
- NFR-2 and NFR-3 bounds are HM test values; their plausibility on CI hardware was not measured.
- Review 2's findings S6 (pool starvation), S7 and S8 are taken as the review states them; S8's
  `ignore_extra` behavior matches the docstring I opened, the others were not reproduced.
- The type check of D-80 ran on a stand-in of the signatures (plain classes with the same members),
  not on an implementation.
- draive specification facts: TDS-004 lines 953 and 1138-1159 and TDS-003 lines 688-733 were opened
  by me (2026-11-27) in draive's working tree, where `specs/` is untracked and so not pinned by
  5080cbc; the rest come from a DocumentsExplorer report of 2026-11-26 and, for TDS-004 lines 460,
  461, 469, 680, 960, 968 and TDS-003 lines 694-696, from review 1, not opened by me.

## Traceability

### Consumer requirements

Source: draive TDS-004, section "Required from haiway managed tasks"
(`draive/specs/design/TDS-004-deferred-tools.md:1138-1159`, v0.8, per DocumentsExplorer, 2026-11-26;
lines 1138-1159 and FR-33 at line 953 re-opened by me, 2026-11-27, in draive's working tree, where
`specs/` is untracked). This table is the authoritative mapping of those items (D-34). draive
TDS-004 FR-33 spawns "a coroutine of the task body that holds the tool object and its validated
arguments", an in-process callable that only ad-hoc tasks served; with D-77 every item below that
draive needs for its deferred tool bodies is met only for registered tasks, whose sole input is one
`State` argument.

| Consumer item        | v1.4 status                                                                                                                                                                                                                                                                                                                                                                                                                             | Requirements                        | Decisions                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------------------------- | ---------------------------- |
| draive TDS-004 HM-1  | Met for registered tasks; not met for draive's deferred tool bodies, which are in-process callables (D-77).                                                                                                                                                                                                                                                                                                                             | FR-4, FR-54, FR-74, NFR-2           | D-32, D-40, D-77             |
| draive TDS-004 HM-2  | Changed: the task state is the root snapshot, the owner and the registry preset only; no explicit state, and presets come from the registry, not the spawn. Not met for draive's deferred tool bodies (D-77).                                                                                                                                                                                                                           | FR-9, FR-10, FR-56, FR-58           | D-60, D-68, D-77             |
| draive TDS-004 HM-3  | Met per attempt whose preparation completed, for registered tasks only (D-77); a preparation failure is a `TaskFailureRecord` of kind `preparation`.                                                                                                                                                                                                                                                                                    | FR-20, FR-47, FR-48                 | D-25, D-55, D-70, D-77       |
| draive TDS-004 HM-4  | Met, in whichever runtime runs the attempt (D-72), for registered tasks only (D-77); under `ServerContext` a runtime without a backend records through the server's (D-93).                                                                                                                                                                                                                                                             | FR-11 to FR-15, FR-71, FR-74, FR-87 | D-2, D-41, D-72, D-77, D-93  |
| draive TDS-004 HM-5  | Changed: the owner is a block entered with a `TaskRuntime`; `ServerContext(tasks=...)` enters it for a Starlette app.                                                                                                                                                                                                                                                                                                                   | FR-35, FR-55                        | D-39, D-66                   |
| draive TDS-004 HM-6  | Partly met, for registered tasks only (D-77): cancel by handle holds, answered by the store with `"cancelled"`, `"pending"` or `"finished"` instead of a `bool` (D-82, D-91); with the in-memory store a close cancels running tasks and their handles return `TaskCancelled`; with TDS-002 it releases them to the store, where they stay resumable, and their `outcome()` raises `RuntimeError` in the closing runtime (FR-77, D-88). | FR-25, FR-26, FR-29, FR-40          | D-57, D-77, D-82, D-88, D-91 |
| draive TDS-004 HM-7  | Not met as written (see [Conflicts](#conflicts)).                                                                                                                                                                                                                                                                                                                                                                                       | FR-20, FR-21, FR-23                 | D-42, D-57, D-70             |
| draive TDS-004 HM-8  | Changed: exceeding the limit is a failure, per attempt, for registered tasks only (D-77), counted from the claim, not the start call (D-89).                                                                                                                                                                                                                                                                                            | FR-27, FR-41, NFR-3                 | D-48, D-77, D-89             |
| draive TDS-004 HM-9  | Withdrawn in draive; `available()` (a coroutine) and the errors of FR-5 and FR-31 remain.                                                                                                                                                                                                                                                                                                                                               | FR-3, FR-5, FR-31                   | D-4, D-5, D-64               |
| draive TDS-004 HM-10 | Met for registered tasks only (D-77).                                                                                                                                                                                                                                                                                                                                                                                                   | FR-34, NFR-1                        | D-77                         |
| draive TDS-004 HM-11 | Not met (see [Conflicts](#conflicts)).                                                                                                                                                                                                                                                                                                                                                                                                  | FR-21                               | D-42, D-57                   |
| draive TDS-004 HM-12 | Not met (see [Conflicts](#conflicts)).                                                                                                                                                                                                                                                                                                                                                                                                  | FR-29                               | D-42, D-57                   |
| draive TDS-004 HM-13 | Not met as written; the ordering it protects holds (see [Conflicts](#conflicts)).                                                                                                                                                                                                                                                                                                                                                       | FR-35, FR-57                        | D-39, D-57                   |

### Requirements to sources

Acceptance criteria are each requirement's own numbered list. Each requirement's source is its
`**Source:**` block, listed here.

| Requirement | Source                                                                                                                                        |
| ----------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| FR-1        | withdrawn, D-39                                                                                                                               |
| FR-2        | derived from D-5 and `state.py:50-58`: D-5's error holds only if reading never creates one                                                    |
| FR-3        | D-4, D-64                                                                                                                                     |
| FR-4        | HM-1; D-40, D-46, D-66, D-77, D-92, D-138                                                                                                     |
| FR-5        | D-5, D-78, D-109                                                                                                                              |
| FR-6        | withdrawn, D-22                                                                                                                               |
| FR-7        | withdrawn, D-22, which makes D-1 moot; `ContextStateDisposed` is not added                                                                    |
| FR-8        | withdrawn, FR-7 (D-22)                                                                                                                        |
| FR-9        | D-60, D-68, D-77, D-95; derived from `access.py:170-177`; D-28 restated                                                                       |
| FR-10       | D-17, D-39, D-68                                                                                                                              |
| FR-11       | D-26, D-66                                                                                                                                    |
| FR-12       | D-2, D-41, D-72, D-77; HM-4                                                                                                                   |
| FR-13       | derived from D-41 and `observability.py:205-216`: a link cannot reach a backend otherwise                                                     |
| FR-14       | D-2, D-41; HM-4                                                                                                                               |
| FR-15       | D-2, D-41; HM-4                                                                                                                               |
| FR-16       | D-7 (in part), D-46, D-60, D-77                                                                                                               |
| FR-17       | derived from FR-9, FR-74 and `scope.py:103-118`                                                                                               |
| FR-18       | D-8                                                                                                                                           |
| FR-19       | D-42, D-47                                                                                                                                    |
| FR-20       | D-25, D-30, D-48, D-65, D-70, D-85, D-110, D-111; HM-3, HM-7, HM-8                                                                            |
| FR-21       | D-42, D-57, D-70, D-81, D-83, D-88; HM-7                                                                                                      |
| FR-22       | withdrawn, D-42                                                                                                                               |
| FR-23       | D-49                                                                                                                                          |
| FR-24       | derived from AGENTS.md Logging and Security; D-47, D-70, D-110                                                                                |
| FR-25       | HM-6; D-91                                                                                                                                    |
| FR-26       | D-37, D-48, D-82, D-91                                                                                                                        |
| FR-27       | D-9, D-48, D-89                                                                                                                               |
| FR-28       | withdrawn, D-9                                                                                                                                |
| FR-29       | D-10, D-44, D-50, D-56, D-66, D-74, D-77, D-88, D-99, D-100, D-103, D-104, D-109, D-110, D-128, D-136, D-137, D-139                           |
| FR-30       | D-10                                                                                                                                          |
| FR-31       | derived from FR-29 and `tasks.py:183-185`                                                                                                     |
| FR-32       | D-11, D-20, D-59, D-70, D-75, D-84, D-96                                                                                                      |
| FR-33       | D-12, D-21, D-90; HM-1, HM-8                                                                                                                  |
| FR-34       | HM-10, D-109                                                                                                                                  |
| FR-35       | D-13, D-39, D-66, D-86, D-93, D-102                                                                                                           |
| FR-36       | withdrawn, D-43                                                                                                                               |
| FR-37       | D-15, D-22, D-78                                                                                                                              |
| FR-38       | withdrawn, D-42                                                                                                                               |
| FR-39       | withdrawn, D-42                                                                                                                               |
| FR-40       | derived from HM-6, HM-8; D-48, D-65, D-66, D-82, D-85, D-144                                                                                  |
| FR-41       | D-12, D-48, D-66, D-77, D-89                                                                                                                  |
| FR-42       | derived from AGENTS.md Logging, Security; D-110                                                                                               |
| FR-43       | withdrawn, D-39                                                                                                                               |
| FR-44       | withdrawn, D-39, with FR-43                                                                                                                   |
| FR-45       | derived from FR-30, FR-32, FR-33, FR-66; D-88, D-108, D-110                                                                                   |
| FR-46       | derived from D-66 and `client.py:335-339`                                                                                                     |
| FR-47       | D-25, D-55, D-70; draive TDS-004 HM-3; D-131                                                                                                  |
| FR-48       | draive TDS-004 HM-3                                                                                                                           |
| FR-49       | withdrawn, D-42                                                                                                                               |
| FR-50       | withdrawn, D-39                                                                                                                               |
| FR-51       | withdrawn, D-39, with FR-50                                                                                                                   |
| FR-52       | derived from D-26, D-33; `observability.py:426-450`; D-93                                                                                     |
| FR-53       | withdrawn, D-39, with FR-50                                                                                                                   |
| FR-54       | D-32, D-78; `scope.py:103-118`, `tasks.py:349-368`                                                                                            |
| FR-55       | D-39, D-66, D-86, D-110                                                                                                                       |
| FR-56       | D-39                                                                                                                                          |
| FR-57       | derived from D-39, D-68 and `scope.py:207-245`, `:279-313`                                                                                    |
| FR-58       | derived from D-39, D-40                                                                                                                       |
| FR-59       | withdrawn, D-66                                                                                                                               |
| FR-60       | withdrawn, D-66                                                                                                                               |
| FR-61       | D-44, D-66, D-87, D-128                                                                                                                       |
| FR-62       | D-46, D-54, D-67, D-80, D-108, D-117, D-120                                                                                                   |
| FR-63       | D-46, D-66, D-67, D-80, D-94; TDS-002 D-16                                                                                                    |
| FR-64       | withdrawn, D-66                                                                                                                               |
| FR-65       | D-47, D-56, D-65, D-67, D-70, D-84, D-110                                                                                                     |
| FR-66       | D-47, D-48, D-54, D-55, D-65, D-67, D-74, D-84, D-110                                                                                         |
| FR-67       | withdrawn, D-78                                                                                                                               |
| FR-68       | D-78                                                                                                                                          |
| FR-69       | D-58, D-78                                                                                                                                    |
| FR-70       | D-42, D-11, D-75, D-96, D-118                                                                                                                 |
| FR-71       | derived from `access.py:46-62`                                                                                                                |
| FR-72       | withdrawn, D-66                                                                                                                               |
| FR-73       | withdrawn, D-77                                                                                                                               |
| FR-74       | derived from D-41, D-55, D-60, D-68, D-86; D-131, D-135                                                                                       |
| FR-75       | withdrawn, D-68                                                                                                                               |
| FR-76       | D-46, D-67, D-70, D-84                                                                                                                        |
| FR-77       | derived from D-50, D-56, D-66, D-77; D-88                                                                                                     |
| FR-78       | D-56, D-66, D-67, D-72, D-81, D-83, D-84, D-88, D-91, D-98, D-103, D-104, D-105, D-109, D-110                                                 |
| FR-79       | D-66, D-88, D-100, D-109                                                                                                                      |
| FR-80       | D-66, D-75, D-76, D-88, D-97; TDS-002 D-1                                                                                                     |
| FR-81       | D-66, D-77, D-88, D-110                                                                                                                       |
| FR-82       | derived from AGENTS.md, D-65; D-81, D-99, D-109, D-110, D-127, D-143                                                                          |
| FR-83       | derived from D-66, TDS-002 D-1; D-81, D-83, D-88, D-127, D-132, D-133, D-140, D-141                                                           |
| FR-84       | D-65, D-66, D-81, D-88, D-134, D-141                                                                                                          |
| FR-85       | D-66, D-88, D-103, D-105; derived from FR-80 for the wake on due moments and the round at open                                                |
| FR-86       | D-82, D-91; FR-25, FR-26                                                                                                                      |
| FR-87       | D-72; W3C Trace Context Level 1, sections 3.2.2, 3.3.1 (per WebExplorer)                                                                      |
| FR-88       | derived from D-26 and `observability.py:862-875`; D-87, D-110                                                                                 |
| FR-89       | derived from D-78 and `tasks.py:286-293`                                                                                                      |
| FR-90       | derived from D-84, D-98, D-106, D-110, FR-66, FR-78 (review 2 S9)                                                                             |
| FR-91       | D-108; derived from FR-62 for the `TypeError`                                                                                                 |
| FR-92       | D-110                                                                                                                                         |
| FR-93       | D-110, D-111, D-116; derived from FR-24, FR-82, FR-85, FR-88                                                                                  |
| FR-94       | D-96, D-109, D-112, D-113, D-115, D-118; derived from FR-5, FR-34, FR-77, and from D-113 with AGENTS.md Golden Rule for rejecting `arguments` |
| FR-95       | D-106, D-109; derived from AGENTS.md Golden Rule                                                                                              |
| FR-96       | D-109; derived from FR-78 (timestamps are store bookkeeping) and `time.monotonic()`                                                           |
| FR-97       | D-109, D-113, D-114, D-115                                                                                                                    |
| FR-98       | D-106, D-112; derived from FR-90 and AGENTS.md Golden Rule                                                                                    |
| FR-99       | D-80, D-108, D-117, D-120                                                                                                                     |
| NFR-1       | HM-10                                                                                                                                         |
| NFR-2       | HM-1                                                                                                                                          |
| NFR-3       | HM-8, D-48, D-89                                                                                                                              |
| NFR-4       | AGENTS.md Security                                                                                                                            |
| NFR-5       | AGENTS.md Testing; D-88, D-97                                                                                                                 |
| NFR-6       | AGENTS.md Documentation; D-108, D-109, D-110, D-145                                                                                           |
| NFR-7       | AGENTS.md; D-109, D-110                                                                                                                       |
| NFR-8       | brief (user preference)                                                                                                                       |
| NFR-9       | withdrawn, D-42                                                                                                                               |

### Conflicts

- **D-110's example and the Golden Rule.** The brief's example
  `TaskUnsupported(kind="unsupported", name=...)` repeats the registered name, which `NewTask.name`
  and `ManagedTask.name` hold; D-111 drops the field.

- **D-67 and listing timestamps.** D-67 limits what a stored task holds; FR-78 already allowed
  creation and finish timestamps as store bookkeeping, and FR-96 uses them for listing; listed
  summaries show them (D-112, FR-98).

- **D-46 and D-108.** D-46 asked for a stable registered name; the default name changes with a
  rename, and FR-91 documents the explicit `name` as the stable form.

- **Gap found in v1.5:** what `ManagedTasks.task` does once its runtime is `closed` was not stated;
  D-118 settles it (FR-70).

- **D-113 and the Golden Rule.** `Pagination.arguments` is a loose bag; listing rejects a non-empty
  one instead of reading filters from it (FR-94), and `has_next_page`'s heuristic is met by giving a
  token exactly for full pages (FR-97).

- **draive HM-7, HM-11, HM-12, HM-13 (D-42, D-57).** No callback receives the outcome, no 50 ms
  bound exists, and the close does not wait for draive code; draive awaits each handle in a task of
  its own, and draive TDS-004 FR-38 and draive TDS-003 FR-31 now have to specify that watcher's
  writes before the block closes. The ordering HM-13 protects holds (FR-57, FR-35).

- **draive TDS-004 FR-33 and D-77.** draive's deferred tools spawn a coroutine holding the tool
  object and its validated arguments, the ad-hoc path D-77 removes; HM-1, HM-2, HM-3, HM-4, HM-6,
  HM-8 and HM-10 are met only for registered tasks, so draive has to register each deferred tool
  body under a registered name with one `State` argument, or run it outside managed tasks. Ad-hoc
  tasks are not reintroduced (D-79). draive TDS-003 FR-30 starts every MCP task through that path
  (`draive/specs/design/TDS-003-mcp-tasks.md:688-712`, depending on TDS-004 FR-33), so it inherits
  the same gap; draive TDS-003 FR-31 relies on HM-6, HM-12 and HM-13, unchanged by D-77.

- **draive HM-2 (D-68, D-60).** draive's explicit spawn state (a delivery key) has to travel inside
  the registered task's argument State. draive TDS-003 FR-30 relies on presets selected at spawn
  (draive `TDS-003-mcp-tasks.md:694-696`, per the review), which D-60 removed: the preset is the
  registry entry named like the task.

- **draive citations of changed requirements.** draive TDS-004 also cites haiway FR-38 (withdrawn;
  lines 460, 469), FR-52 (the backend now belongs to `TaskRuntime`; lines 461, 469), FR-9 with
  D-28's lowest priority (FR-9 now puts the owner above the snapshot; lines 960, 968), and FR-27
  counted from the spawn (now from each attempt's claim, D-89; line 680), per the review.

- **D-88 for draive.** draive states lease timings only when it uses TDS-002's store, on the store;
  `TaskRuntime` takes none.

- **draive HM-5 (D-39, D-66).** `DeferredTasks.disposable` cannot enter the block; draive enters
  `ctx.managed_tasks(TaskRuntime(...))` or uses `ServerContext(tasks=...)`.

- **draive HM-9 and D-64.** `ManagedTasks.available()` and every spawn are coroutines.

- **draive HM-8 (D-48).** A time limit is a failure, retried under the policy.

- **draive citations.** draive TDS-004 cites haiway FR-3, FR-5, FR-9, FR-26, FR-27, FR-29, FR-31,
  FR-33, FR-38, FR-43, FR-49, FR-50, FR-52 and FR-53, and draive TDS-003 cites FR-29, FR-35, FR-43
  and FR-49 (by `rg` in draive's working tree, 2026-11-27): FR-38, FR-43, FR-49, FR-50 and FR-53 are
  withdrawn, and FR-3, FR-5, FR-9, FR-26, FR-27, FR-29, FR-31, FR-33, FR-35 and FR-52 changed.

- **D-41 against D-67.** Resolved by D-72: the origin is stored as telemetry metadata, so every
  attempt links back (FR-12, FR-87).

- **D-44 and D-66.** D-44 set ERROR records for the in-memory implementation's close; with one
  runtime the same records apply to every store (FR-61), replacing v0.8's WARNING for persistent
  closes.

- **D-59 and D-70.** Retained failures no longer hold exceptions or frames (FR-32).

- **D-43 and downstream code (D-78).** D-43's claim that nothing released depends on
  `ctx.spawn_background` held only for haiway's `src/`; draive and draive-platform call it
  (Compatibility and migration).

- **Review 2 against recorded decisions.** Put to the user as OQ-41 to OQ-52: S1 superseded D-12
  (D-89), S2's result type D-31 (D-91), S7 superseded D-71 and narrowed D-66 (D-88); S5 (D-90), S11
  (D-95) and S13 (D-97) were declined, keeping D-21, D-68 and D-76.

- **draive HM-6 and D-91.** HM-6 asks that "the cancel call reports whether the task was still
  running", which draive TDS-004 FR-15 and draive TDS-003 FR-22 need; the report is now
  `"cancelled"`, `"pending"` or `"finished"` rather than a `bool`.

- **HM-4 with `LoggerObservability`.** The logger writes the link once, at INFO (FR-14); a logger
  set above INFO drops it.

- **D-30 and D-37.** A release failure after a successful attempt is a `raised` failure, and a
  `cancel()` landing during release gives `TaskCancelled`; the value is lost in both.

## References

- haiway [TDS-002](TDS-002-postgres-managed-tasks.md), PostgreSQL task store.
- draive TDS-004 "Deferred tool results", `draive/specs/design/TDS-004-deferred-tools.md` (section
  "Required from haiway managed tasks").
- draive TDS-003 "MCP Tasks extension", `draive/specs/design/TDS-003-mcp-tasks.md`.
- OpenTelemetry Trace API, specification v1.63.0:
  <https://github.com/open-telemetry/opentelemetry-specification/blob/v1.63.0/specification/trace/api.md>
  (per WebExplorer, 2026-11-26).
- opentelemetry-python 1.45.2: <https://pypi.org/project/opentelemetry-api/1.45.2/> (per
  WebExplorer, 2026-11-26).
- W3C Trace Context Level 1, Recommendation 2021-11-23: <https://www.w3.org/TR/trace-context/>.
- RFC 2119, RFC 8174 (BCP 14).
