# Managed Tasks

`ctx.spawn(...)` ties a task to the scope it was spawned in: the scope waits for it, and cancelling
the scope cancels it. Work which has to outlive the scope needs another owner. Managed tasks are
that owner. A managed task is a registered coroutine function with one `State` argument, stored in a
task store and run by a runtime you enter with `ctx.managed_tasks(...)`. It has its own identifier,
status and outcome, retries, a time limit, and - with a database store - it survives a restart of
the process and can be run by any process sharing the database.

## When to Use Managed Tasks

| need                                                      | use                                  |
| --------------------------------------------------------- | ------------------------------------ |
| work belonging to the current scope, joined when it exits | `ctx.spawn(...)`                     |
| fan-out within a scope                                    | `concurrently`, `gather` and friends |
| work which outlives the spawning scope                    | managed tasks                        |
| work which survives a restart, or is picked up elsewhere  | managed tasks with a database store  |
| work to retry, time-limit, cancel or inspect by id        | managed tasks                        |

Haiway has no detached spawn: fire-and-forget work which is not a managed task runs in loop tasks
the caller owns. See [Concurrent Processing](concurrent.md) for the scope-bound tools.

## Quick Start

```python
from haiway import ManagedTasks, State, TaskCompleted, TaskRuntime, ctx, managed_task


class ReportRequest(State):
    month: str


class Report(State):
    url: str


@managed_task()
async def build_report(request: ReportRequest) -> Report:
    ctx.log_info("Building the report of %s", request.month)
    return Report(url=f"https://example.com/reports/{request.month}.pdf")


async def main() -> None:
    async with ctx.scope("app"):
        async with ctx.managed_tasks(TaskRuntime(tasks=(build_report,))):
            task = await ManagedTasks.spawn(build_report(ReportRequest(month="2026-11")))
            outcome = await task.outcome()
            if isinstance(outcome, TaskCompleted):
                ctx.log_info("Report ready: %s", outcome.value.url)
```

`ctx.managed_tasks(runtime)` is an async context manager yielding the `ManagedTasks` of the runtime.
Entering it opens the runtime; leaving it closes the runtime, see [Closing](#closing). Inside the
block, `ManagedTasks.spawn(...)`, `ManagedTasks.task(...)` and `ManagedTasks.tasks(...)` work from
any scope at any depth, and from inside a task. Outside a block they raise `ContextStateMissing`,
and `await ManagedTasks.available()` returns `False`.

## Registering Tasks

`@managed_task()` registers a coroutine function. The function takes exactly one positional
parameter without a default, annotated with a `State` class, and returns a `State` class or `None`.
The argument and the result are stored as JSON, so they have to survive `to_json` and `from_json`.
The argument is the only data a task receives from the code which spawned it. Keys of a stored
argument that the class no longer defines are skipped when it is decoded.

```python
from haiway import State, managed_task


class CleanupRequest(State):
    older_than_days: int


@managed_task(name="maintenance.cleanup")
async def cleanup(request: CleanupRequest) -> None: ...
```

Calling the registered task with an argument does not run anything. It validates the argument,
encodes it and returns a call which `ManagedTasks.spawn(...)` stores. Passing an argument of another
class raises `TypeError`, an argument which cannot be encoded raises `ValueError`.

A runtime spawns and runs only the tasks listed in `TaskRuntime(tasks=...)`. Spawning a call of a
task which the runtime does not list raises `ValueError` and stores nothing. Two tasks of one
registered name cannot be listed in one runtime.

### Names and Identity

The registered name is the identity of a task in the store. It is `name` when given, and otherwise
the `__name__` of the function - not its qualified name or module path. A task stored under a name
is run by whichever runtime lists that name.

Renaming the function therefore changes its registered name: stored tasks of the old name have an
unlisted name in every runtime of the new code. They wait there with `unsupported="leave"`, and are
failed by a runtime with `unsupported="fail"`. Give a task an explicit `name` when it is stored
somewhere durable, so that the identity stays stable across renames and moves.

Two functions with one `__name__` in one runtime are rejected. Runtimes sharing a store share the
registered names too: two applications registering the same name on one database run each other's
tasks of that name.

The registered name also names the task scope and its attempt scopes, which prefixes their records,
and selects a [context preset](../features/context-presets.md) of the same name for each attempt.

## The Runtime

`TaskRuntime` holds the settings of one `ctx.managed_tasks` block. Its arguments are keyword-only:

| setting             | default                   | meaning                                                              |
| ------------------- | ------------------------- | -------------------------------------------------------------------- |
| `store`             | new `InMemoryTaskStore()` | where tasks are kept, see [Stores](#stores)                          |
| `tasks`             | `()`                      | the registered tasks this runtime spawns and runs                    |
| `retry`             | `TaskRetryPolicy()`       | the retry policy of tasks which bring none, no retry                 |
| `grace_period`      | `0.0`                     | seconds running attempts get to finish when the block closes         |
| `concurrency_limit` | `None`                    | the most attempts run at once, unlimited when absent                 |
| `unsupported`       | `"leave"`                 | what to do with due stored tasks of a name it does not list          |
| `observability`     | `None`                    | the backend recording the tasks, see [Observability](#observability) |

A runtime object is entered once: entering it again raises `RuntimeError`. Everything it serves runs
on the event loop its block was entered on.

```python
from haiway import TaskRetryPolicy, TaskRuntime

runtime = TaskRuntime(
    tasks=(build_report, cleanup),
    retry=TaskRetryPolicy(delays=(1.0, 10.0)),
    grace_period=5.0,
    concurrency_limit=8,
)
```

### Concurrency Limit

Without `concurrency_limit`, every attempt starts as soon as it is due. With a store shared by
several runtimes, the first runtime to claim from it takes every due task, so set a limit for
runtimes sharing a store, and for any application which spawns without bound. Due tasks beyond the
limit wait in the store as `queued` or `retry_pending`, in the order they became due, and start as
slots free up.

### Unsupported Tasks

With the default `unsupported="leave"`, a stored task of a name the runtime does not list is never
claimed by it and stays as it is. With `unsupported="fail"`, every claim round of the open runtime
also finishes every due `queued` or `retry_pending` task of an unlisted name as failed with
`TaskUnsupported`. It is never retried, a cancel requested before still wins, and each failed task
is logged at ERROR. A `running` task is never taken.

`unsupported="fail"` is a warning, not a convenience. The runtime fails every due stored task of a
name it does not list - also one that another runtime on the same store lists and would run:

- runtimes of different applications or worker kinds sharing one database fail each other's tasks
- during a rolling deploy, older replicas fail tasks of names only newer ones list
- a runtime given `tasks=()` fails every due task
- tasks of a renamed function are failed, see [Names and Identity](#names-and-identity)

Enable it only when one kind of runtime owns the whole store.

## The Block

```python
async with ctx.scope("app", ServiceConfig(), disposables=(HTTPXClient(),)):
    async with ctx.managed_tasks(runtime) as tasks:
        ...
```

**Entering starts executing stored tasks.** The due tasks of the store whose names the runtime lists
start running at once, concurrently with the body and without any spawn - so a task can start before
the first statement of the body. The body does not wait for them. With `unsupported="fail"`, the due
tasks of unlisted names are failed at once.

**The root snapshot.** The block takes a snapshot of the `State` instances and the presets registry
of the context it is entered in, once, at entry. Every attempt builds its context from that
snapshot, plus the `ManagedTasks` of its runtime, plus the preset named like the task. State merged
and presets entered in the body of the block never reach a task, and nothing of the spawning scope
does but the argument. A task reads `ctx.state(ServiceConfig)` set up around the block, and raises
`ContextStateMissing` for state which was only set in the scope that spawned it.

**Lifetime of snapshotted resources.** The block closes its runtime, ending every attempt, before it
returns, so that the state taken from the scopes and disposables around it stays usable for every
attempt. This holds only when the block is exited before the scopes and disposables around it, as
nesting `async with` blocks ensures.

**One shared connection.** Every task shares the instances of the snapshot. A snapshot holding a
single connection, for example a `PostgresConnection`, hands that one connection to every task at
once, which most connections do not support. Keep a single connection out of the state the block
snapshots.

A block entered inside another block, or inside a task, is a separate block with its own runtime:
its tasks have its `ManagedTasks`, and closing it does not affect the tasks of the outer one.

## Spawning

```python
task = await ManagedTasks.spawn(build_report(ReportRequest(month="2026-11")), time_limit=300.0)
```

`spawn` returns a `ManagedTask` once the store has recorded the task. It ties the task to no scope:
the spawning scope exit neither waits for it, cancels it, nor fails it. A queued task does not delay
the spawning scope either.

A task can spawn further tasks through `ManagedTasks.spawn(...)`. Each forms a separate task tree,
unaffected by the end of the spawning task.

`spawn` raises `ValueError` for a `time_limit` which is not a finite number above 0, and for a call
of a task the runtime does not list. It raises `RuntimeError` while the runtime is closing or
closed, and on another event loop than the one the block was entered on.

**Spawn after commit.** The store records the task through its own connections, never through the
connection of the caller. A task spawned inside an application transaction is therefore stored at
once, can start before that transaction commits, and survives its rollback. Spawn after the
transaction has committed.

**A cancelled spawn.** When the caller of `spawn` is cancelled while the store records the task, the
insert is allowed to finish: the task can be stored, and run, although the caller got no handle.

## The Task Handle

`spawn` and `ManagedTasks.task(identifier)` return a `ManagedTask`. It owns nothing: its status and
outcome are read from the store, so every handle of one task, in any runtime on the same store, sees
the same. Awaiting or dropping a handle never affects the task.

```python
task = await ManagedTasks.spawn(build_report(ReportRequest(month="2026-11")))

task.identifier  # UUID of the task
task.name  # "build_report"

await task.status()  # "queued", "running", "retry_pending" or "finished"
outcome = await task.outcome()
```

The identifier is an identifier, not a credential: it comes from a generator which is not
cryptographic, and anyone able to look tasks up in a runtime or read its store can use it. Code
exposing tasks to remote callers has to map them to identifiers of its own.

`ManagedTasks.task(identifier)` returns `None` only when the store does not hold the task. Otherwise
it returns a handle, also for a task of a name the runtime does not list: for such a task `status()`
works, and `outcome()` raises `ValueError`, as no result class is known.

When the store no longer holds a task, its handle raises `LookupError` from `status()`, `outcome()`
and `cancel()`.

### Outcome

`outcome()` waits until the outcome is fixed and returns one of:

- `TaskCompleted` with `value` - the result decoded from the store
- `TaskFailed` with `failure` and `attempts` - the failure of the last attempt and the number of
  counted attempts
- `TaskCancelled`

It never raises the exception of the task: that is reported as `TaskFailed`. Cancelling the awaiting
coroutine does not affect the task, and every awaiter receives an equal outcome.

```python
match await task.outcome():
    case TaskCompleted(value=report):
        ctx.log_info("Report ready: %s", report.url)

    case TaskFailed(failure=failure, attempts=attempts):
        ctx.log_error("Report failed after %d attempts: %s", attempts, failure)

    case TaskCancelled():
        ctx.log_info("Report cancelled")
```

### Cancelling

`await task.cancel()` goes to the store and returns what it answered:

- `"cancelled"` - the task was `queued` or `retry_pending`, and finished as `TaskCancelled` at once;
  its function was not called
- `"pending"` - the task was `running`: the request is kept and delivered to the attempt, and the
  task finishes as `TaskCancelled` when the attempt ends
- `"finished"` - the task already had its outcome, nothing changed

A cancel the store accepted wins over whatever the attempt did: a task whose function already
returned but whose outcome was not recorded yet still ends as `TaskCancelled`, and its lost value is
logged at WARNING without the value. A `"finished"` answer is the other side of that race - the
outcome was fixed before the cancel arrived, and `outcome()` returns it. No retry follows a cancel.

## Retries and Time Limits

A retry policy is the list of delays before each retry; its length is the number of retries.

```python
from haiway import TaskRetryPolicy, managed_task


@managed_task(retry=TaskRetryPolicy(delays=(1.0, 5.0, 30.0)))
async def deliver(request: DeliveryRequest) -> None: ...
```

The policy of a task is the one of its `managed_task(retry=...)` when given, and otherwise
`TaskRuntime(retry=...)`; it is stored with the task at spawn. After a failed attempt the task goes
to `retry_pending` and its next attempt becomes due after the matching delay. Every kind of attempt
failure is retried - an exception, a preparation failure, a time limit, an interruption. A task
whose retries are used up finishes as `TaskFailed`. A cancel is never retried, and neither is a task
failed as unsupported.

`time_limit` of `spawn` bounds each attempt, in seconds, counted from the moment the runtime claims
the attempt: time spent queued, waiting for a retry, or released by a close does not count. When it
elapses the attempt is cancelled and fails with `TaskTimeLimitExceeded`, which the retry policy may
retry.

When an attempt fails and a retry follows, the runtime logs one WARNING in the task scope; when the
task ends as `TaskFailed`, one ERROR. These records of the runtime carry neither the message nor the
traceback of the exception; the backend of the scope logs the exception of each raised attempt
itself, at ERROR, as it leaves the attempt scope.

## Failures

`TaskFailed.failure` is one of:

| failure                 | meaning                                                                      |
| ----------------------- | ---------------------------------------------------------------------------- |
| `TaskFailureRecord`     | `kind="raised"`: the function, or the exit of its attempt scope, raised      |
| `TaskFailureRecord`     | `kind="preparation"`: preparing the attempt, its presets for example, raised |
| `TaskTimeLimitExceeded` | the time limit elapsed first, `time_limit` holds it                          |
| `TaskInterrupted`       | the runtime which ran the attempt stopped without closing                    |
| `TaskUnsupported`       | a runtime with `unsupported="fail"` found the task due and did not list it   |

`TaskFailureRecord.exception_type` holds the `__module__` and `__qualname__` of the exception joined
by `.`, for example `builtins.ValueError`. Failures are stored as that name alone: the message and
the traceback are not stored, and are recorded only by the observability backend as the exception
leaves the attempt scope.

`TaskInterrupted` needs a store whose leases expire, see [PostgreSQL Store](#postgresql-store): when
a process dies while running a task, another runtime records the attempt as interrupted once its
lease expires, and the retry policy decides what follows.

## Closing

Leaving the block closes the runtime, in this order:

1. new spawns are rejected with `RuntimeError` and no new attempt starts
1. running attempts get `grace_period` seconds to finish
1. attempts still running are cancelled and released to the store
1. the close waits until every attempt and every store call in progress has ended

What a released task becomes depends on the store. A store with leases, like the PostgreSQL one,
leaves it resumable - with the cancelled attempt not counted - so that another runtime, or the next
start, runs it again. The in-memory store finishes it as `TaskCancelled`, also when shared: no other
process could reach a released task. Tasks this runtime was not running stay as they are in the
store. A cancellation of the exit itself does not abandon these steps; it is re-raised after the
last.

After the block has exited, `status()`, `outcome()` and `cancel()` of a handle raise `RuntimeError`.
The exception is a task which the close finished as `TaskCancelled`: its handle still returns it.

**Grace period.** Recording an attempt's end in the store can fail and is retried. A `record`
failing when a close begins is retried only until the grace period has elapsed from the start of the
close. With the default `grace_period=0.0` it is therefore abandoned at once, and the task, still
`running` in the store, is recovered as `TaskInterrupted` once its lease expires. Set a grace period
long enough for a store outage you want to ride out during a deploy, and for attempts you want to
see finish.

## Listing Tasks

`ManagedTasks.tasks(...)` lists the tasks the store holds a page at a time, whether or not the
runtime lists their names. It returns a `Paginated[TaskSummary]`.

```python
from haiway import ManagedTasks, Pagination, TaskFilter, TasksFinished

pagination = Pagination.of(limit=100)
while True:
    page = await ManagedTasks.tasks(
        pagination,
        matching=TaskFilter(
            names={"build_report"},
            status=TasksFinished(outcomes={"failed"}),
        ),
    )
    for summary in page:
        ctx.log_info("%s %s attempts: %d", summary.identifier, summary.name, summary.attempts)

    if page.pagination.token is None:
        break

    pagination = page.pagination
```

- Tasks come oldest first, ordered by creation time and then identifier.
- `limit` is required and is an integer from 1 to 1,000; any other value raises `ValueError`.
- The token of the returned `pagination` is set only for a full page. A page with fewer tasks than
  the limit ends the traversal, while a full last page leads to one empty page.
- Tokens are opaque: pass back only what a listing returned, a malformed token raises `ValueError`.
  `arguments` of the `Pagination` have to be empty.
- A summary holds the identifier, name, status, number of counted attempts and creation time, and
  the finish time and outcome kind when finished - never the argument, result or failure.

`TaskFilter` combines, all at once, `names` (a set of registered names), `status` -
`TasksUnfinished` with a set of statuses, or `TasksFinished` with a set of outcomes and an optional
finish time range - and `created`, a creation time range. The ranges are `TaskTimeFrom`,
`TaskTimeBefore` and `TaskTimeBetween`, with timezone-aware datetimes. An empty set matches no task.

Creation and finish times are the clock of the store; for the database store see
[Postgres](../features/postgres.md#listing).

## Observability

Every task is a new trace, linked to the scope which spawned it. The task scope and each attempt
scope are named by the registered name, so records read `[build_report] ...`. The link holds the
trace and scope identifiers of the spawning scope, and is absent for a task spawned outside any
scope. It is stored with the task, so a task run by another process links back too.

- `LoggerObservability` logs a `Linked to: trace ... scope ...` line when the task scope starts.
- The OpenTelemetry backend starts the task as a new trace, with a span link to the spawning span
  when the spawning scope carried a W3C trace context.

The backend recording the tasks is the first of: `TaskRuntime(observability=...)` - a backend, or a
`Logger` which is wrapped; the `observability` given to `ctx.managed_tasks(...)`; a
`LoggerObservability` created for the block. It is not the backend of the spawning scope, so a task
keeps recording after that scope has ended.

## Starlette and FastAPI

`ServerContext` takes a runtime, and its lifespan opens it:

```python
from haiway import ManagedTasks, TaskRuntime
from haiway.postgres import PostgresConnectionPool
from haiway.starlette import ServerContext

context = ServerContext(
    ServiceConfig(),
    disposables=(PostgresConnectionPool(),),
    tasks=TaskRuntime(tasks=(build_report,), concurrency_limit=8),
)
```

Requests then spawn through `ManagedTasks.spawn(...)`. See
[Starlette](../features/starlette.md#managed-tasks) for what the lifespan does, which applies to
[FastAPI](../features/fastapi.md) unchanged.

## Stores

A store holds the tasks. Every runtime opening one store is a worker of one shared queue, claiming
any due task whose registered name it lists. The runtime owns scheduling, concurrency, retries, time
limits and the handle API; the store only keeps the tasks.

### In-Memory Store

`InMemoryTaskStore` is the default: a runtime given no `store` uses a new one.

```python
from haiway import InMemoryTaskStore, TaskRuntime

runtime = TaskRuntime(store=InMemoryTaskStore(retention=600.0), tasks=(build_report,))
```

- Tasks and their state are lost with the process, and nothing resumes them.
- A finished task is kept `retention` seconds after it finished - `3600.0` by default, finite and at
  least 0 - and dropped afterwards. Nothing else bounds the store, so memory grows with the number
  of tasks finished within one retention period. After that, `ManagedTasks.task(...)` returns `None`
  and a handle raises `LookupError`.
- It binds to the event loop of its first opening for good: a store object cannot be reused across
  separate `asyncio.run` calls.
- Its leases never expire, so no attempt is ever recovered as interrupted.

### PostgreSQL Store

`PostgresTaskStore` keeps tasks in PostgreSQL, so they survive a restart, and any runtime on the
database can run them. See [Postgres](../features/postgres.md#task-store) for the setup: the schema,
the pool, the timings and the housekeeping.

### Custom Stores

A store implements the `TaskStore` protocol: `open()` returns an async context manager yielding a
`TaskStoreSession`, a bundle of operations - `insert`, `claim`, `renew`, `record`, `release`,
`cancel`, `read`, `fail_unsupported` and `query` - and a `coordination` telling runtimes how to
wait: `LocalCoordination` for runtimes in one process, or `LeasedCoordination` with lease timings.
The runtime decides retries and failures. The store has to keep this contract:

- a cancel request wins: the next `record` or `release` of that task finishes it as cancelled
- `release` leaves the attempt uncounted
- `record` and `release` apply only to the attempt the runner holds, and return `None` otherwise
- `insert` of an identifier the store holds raises, and so does a negative limit of `claim` or
  `query`

Read the docstrings of `TaskStoreSession` and its operation protocols before writing one.
