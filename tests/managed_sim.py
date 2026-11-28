"""
A seeded simulator of runtimes around the coordinator, with a store model and faults.

Two coordinators share one store. Every I/O the coordinators ask for is a pending step
which the simulator runs in a random order: commit, then reply, a reply possibly lost after
the commit. Time is virtual and jumps to the next timer. The invariants checked along the way
are the ones of the design: no slot beyond the limit, no attempt held in the store which no
run, job or in-flight claim accounts for, no impossible state, no park ended on a lease the
runtime holds, and a close which ends with nothing left over.
"""

import heapq
import random
from collections.abc import Callable, Mapping, Sequence, Set
from contextvars import copy_context
from typing import Any, Literal, final
from uuid import UUID

from haiway.context.managed import (
    AttemptCancelled,
    AttemptCompleted,
    AttemptEnd,
    AttemptFailed,
    AttemptRetried,
    ClaimedAttempt,
    ClaimedInterrupted,
    ClaimedTask,
    LeaseRenewal,
    LeaseState,
    LeaseTimings,
    NewTask,
    StoredAttempt,
    StoredCancelled,
    StoredCompleted,
    StoredFailed,
    StoredFinished,
    StoredQueued,
    StoredRetryPending,
    StoredRunning,
    StoredStatus,
    StoredTask,
    TaskCancellation,
    TaskCancelled,
    TaskCompleted,
    TaskFailed,
    TaskFailure,
    TaskRetryPolicy,
)
from haiway.context.managed.attempt import (
    AttemptRaised,
    AttemptReturned,
    AttemptTerminated,
)
from haiway.context.managed.coordinator import (
    AttemptEnded,
    Attempting,
    CancelAsked,
    CancelReplied,
    CancelTask,
    ChangedReplied,
    ChangesCancelled,
    Claim,
    ClaimReplied,
    Closed,
    CloseRequested,
    Coordinator,
    Discarding,
    Effect,
    Entering,
    Event,
    ExitHost,
    Fail,
    Insert,
    InsertReplied,
    InterruptionRecorded,
    Log,
    Open,
    Opened,
    OutcomeAsked,
    Parked,
    Read,
    ReadReplied,
    Record,
    Recording,
    RecordInterruption,
    RecordReplied,
    Release,
    ReleaseReplied,
    Releasing,
    Renew,
    RenewReplied,
    Resolve,
    RunAttempt,
    ScopeEntered,
    ScopeExited,
    ScopeFailed,
    SpawnAsked,
    StartChanges,
    StartHost,
    StartTimer,
    StopChanges,
    StopTimer,
    StopTimers,
    StoreCallAsked,
    StoreCallEffect,
    StoreCallReplied,
    StoreFailure,
)
from haiway.context.managed.types import TaskFailureRecord
from tests.managed_helpers import coordination_of

LEASE: float = 1.0
RENEWAL: float = 0.3
POLLING: float = 0.1
TIMINGS: LeaseTimings = LeaseTimings(lease=LEASE, renewal=RENEWAL, polling=POLLING)


async def store_call() -> object:
    return 1


class Violation(Exception):
    pass


@final
class Entry:
    def __init__(
        self,
        task: NewTask,
        now: float,
    ) -> None:
        self.task: NewTask = task
        self.status: Literal["queued", "running", "retry_pending", "finished"] = "queued"
        self.attempts: list[StoredAttempt] = []
        self.owner: str | None = None
        self.expiry: float = 0.0
        self.due: float = now
        self.cancel: bool = False
        self.outcome: StoredCompleted | StoredFailed | StoredCancelled | None = None

    def stored(self) -> StoredTask:
        status: StoredStatus
        match self.status:
            case "queued":
                status = StoredQueued()

            case "running":
                status = StoredRunning(cancel_requested=self.cancel)

            case "retry_pending":
                status = StoredRetryPending()

            case "finished":
                assert self.outcome is not None
                status = StoredFinished(outcome=self.outcome)

        return StoredTask(task=self.task, status=status, attempts=tuple(self.attempts))

    def finish(
        self,
        outcome: StoredCompleted | StoredFailed | StoredCancelled,
        result: Literal["completed", "cancelled"] | TaskFailure,
    ) -> StoredStatus:
        self.attempts.append(StoredAttempt(result=result))
        self.status = "finished"
        self.outcome = outcome
        return StoredFinished(outcome=outcome)


@final
class SimStore:
    """The store: leases which expire, attempt-fenced record and release, cancel requests."""

    def __init__(
        self,
        expiring: bool,
        local: bool,
    ) -> None:
        self.now: float = 0.0
        self.expiring: bool = expiring
        # a store whose runtimes wake on its changes, whose release finishes a task
        self.local: bool = local
        self.version: int = 0
        self.entries: dict[UUID, Entry] = {}

    def steal(
        self,
        identifier: UUID,
    ) -> None:
        # another runtime held the lease and ended the attempt as a failure to retry
        entry: Entry = self.entries[identifier]
        if entry.status == "running":
            entry.owner = "X"
            entry.attempts.append(
                StoredAttempt(result=TaskFailureRecord(kind="raised", exception_type="x"))
            )
            entry.status = "retry_pending"
            entry.due = self.now
            self.version += 1

    def insert(
        self,
        task: NewTask,
    ) -> None:
        self.entries[task.identifier] = Entry(task, self.now)
        self.version += 1

    def claim(
        self,
        runner: str,
        limit: int | None,
    ) -> Sequence[ClaimedTask]:
        claimed: list[ClaimedTask] = []
        if self.expiring:
            for entry in self.entries.values():
                if entry.status == "running" and entry.expiry <= self.now:
                    entry.owner = runner
                    entry.expiry = self.now + LEASE
                    claimed.append(
                        ClaimedInterrupted(
                            task=entry.task,
                            attempt=len(entry.attempts) + 1,
                            cancel_requested=entry.cancel,
                        )
                    )

        due: list[tuple[float, str, Entry]] = sorted(
            (entry.due, str(identifier), entry)
            for identifier, entry in self.entries.items()
            if entry.status in ("queued", "retry_pending") and entry.due <= self.now
        )
        for _, _, entry in due[: len(due) if limit is None else limit]:
            entry.status = "running"
            entry.owner = runner
            entry.expiry = self.now + LEASE
            entry.cancel = False
            claimed.append(ClaimedAttempt(task=entry.task, attempt=len(entry.attempts) + 1))

        if claimed:
            self.version += 1

        return claimed

    def _holds(
        self,
        runner: str,
        identifier: UUID,
        attempt: int,
    ) -> Entry | None:
        entry: Entry | None = self.entries.get(identifier)
        if (
            entry is None
            or entry.status != "running"
            or entry.owner != runner
            or len(entry.attempts) + 1 != attempt
        ):
            return None

        return entry

    def renew(
        self,
        runner: str,
        identifiers: Set[UUID],
    ) -> LeaseRenewal:
        leases: dict[UUID, LeaseState] = {}
        for identifier in identifiers:
            entry: Entry | None = self.entries.get(identifier)
            if entry is not None and entry.status == "running" and entry.owner == runner:
                entry.expiry = self.now + LEASE
                leases[identifier] = "cancel_requested" if entry.cancel else "held"

        return LeaseRenewal(leases=leases)

    def record(
        self,
        runner: str,
        identifier: UUID,
        attempt: int,
        end: AttemptEnd,
    ) -> StoredStatus | None:
        entry: Entry | None = self._holds(runner, identifier, attempt)
        self.version += 1
        if entry is None:
            return None

        if entry.cancel:
            return entry.finish(StoredCancelled(), "cancelled")

        match end:
            case AttemptCompleted(result=result):
                return entry.finish(StoredCompleted(result=result), "completed")

            case AttemptCancelled():
                return entry.finish(StoredCancelled(), "cancelled")

            case AttemptFailed(failure=failure):
                return entry.finish(StoredFailed(), failure)

            case AttemptRetried(failure=failure, delay=delay):
                entry.attempts.append(StoredAttempt(result=failure))
                entry.status = "retry_pending"
                entry.due = self.now if self.local else self.now + delay
                return StoredRetryPending()

    def release(
        self,
        runner: str,
        identifier: UUID,
        attempt: int,
    ) -> StoredStatus | None:
        entry: Entry | None = self._holds(runner, identifier, attempt)
        self.version += 1
        if entry is None:
            return None

        if entry.cancel or self.local:
            entry.status = "finished"
            entry.outcome = StoredCancelled()
            return StoredFinished(outcome=entry.outcome)

        entry.status = "queued"
        entry.due = self.now
        return StoredQueued()

    def cancel(
        self,
        identifier: UUID,
    ) -> TaskCancellation | None:
        entry: Entry | None = self.entries.get(identifier)
        self.version += 1
        if entry is None:
            return None

        if entry.status in ("queued", "retry_pending"):
            entry.status = "finished"
            entry.outcome = StoredCancelled()
            return "cancelled"

        if entry.status == "running":
            entry.cancel = True
            return "pending"

        return "finished"

    def read(
        self,
        identifiers: Set[UUID],
    ) -> Mapping[UUID, StoredTask]:
        return {
            identifier: self.entries[identifier].stored()
            for identifier in identifiers
            if identifier in self.entries
        }


@final
class Reply:
    def __init__(self) -> None:
        self.value: object = None
        self.exception: Exception | None = None
        self.done: bool = False

    def resolve(
        self,
        value: object,
        /,
    ) -> None:
        self.value = value
        self.done = True

    def fail(
        self,
        exception: Exception,
        /,
    ) -> None:
        self.exception = exception
        self.done = True


@final
class Step:
    def __init__(
        self,
        runtime: Runtime,
        label: str,
        action: Callable[[], None],
        enabled: Callable[[], bool] | None = None,
    ) -> None:
        self.runtime: Runtime = runtime
        self.label: str = label
        self.action: Callable[[], None] = action
        self.enabled: Callable[[], bool] | None = enabled


@final
class Host:
    def __init__(
        self,
        identifier: UUID,
    ) -> None:
        self.identifier: UUID = identifier
        self.entered: bool = False


@final
class Runtime:
    def __init__(
        self,
        name: str,
        limit: int | None,
        grace: float,
        local: bool,
    ) -> None:
        self.name: str = name
        self.coordinator: Coordinator = Coordinator(
            results={"t": type(None)},
            limit=limit,
            grace_period=grace,
            coordination=coordination_of(None if local else TIMINGS),
            unsupported="leave",
        )
        self.changes: Step | None = None
        self.limit: int | None = limit
        self.hosts: dict[int, Host] = {}
        self.timers: dict[tuple[object, ...], int] = {}
        self.lost: set[tuple[UUID, int]] = set()
        self.abandoned: set[tuple[UUID, int]] = set()
        self.own_unparks: list[tuple[UUID, int]] = []
        self.awaited: list[tuple[Reply, UUID]] = []
        self.scopes: int = 0


class Sim:
    def __init__(
        self,
        seed: int,
    ) -> None:
        self.random: random.Random = random.Random(seed)
        self.local: bool = self.random.random() < 0.25
        self.expiring: bool = not self.local and self.random.random() < 0.6
        self.store: SimStore = SimStore(self.expiring, self.local)
        self.runtimes: list[Runtime] = [
            Runtime(
                "R1",
                self.random.choice([None, 1, 2]),
                self.random.choice([0.0, 0.5]),
                self.local,
            ),
            Runtime(
                "R2",
                self.random.choice([None, 1]),
                self.random.choice([0.0, 0.5]),
                self.local,
            ),
        ]
        self.pending: list[Step] = []
        self.timers: list[tuple[float, int, Runtime, tuple[object, ...], Event]] = []
        self.counter: int = 0
        self.faults: bool = True
        self.tasks: int = 0
        for runtime in self.runtimes:
            self.feed(runtime, Opened())

    # --- feeding the coordinators ------------------------------------------------------

    def feed(
        self,
        runtime: Runtime,
        event: Event,
    ) -> None:
        parked: dict[UUID, int] = self._parked(runtime)
        effects: Sequence[Effect] = runtime.coordinator.handle(event)
        if isinstance(event, ReadReplied):
            self._own_unparks(runtime, event, parked)

        limit: int | None = runtime.limit
        if limit is not None and runtime.coordinator.slots > limit:
            raise Violation(f"{runtime.name} slots {runtime.coordinator.slots} > {limit}")

        for effect in effects:
            self.effect(runtime, effect)

    def _parked(
        self,
        runtime: Runtime,
    ) -> dict[UUID, int]:
        parked: dict[UUID, int] = {}
        for identifier in self.store.entries:
            phase = runtime.coordinator.phase(identifier)
            if isinstance(phase, Parked):
                parked[identifier] = phase.attempts

        return parked

    def _own_unparks(
        self,
        runtime: Runtime,
        event: ReadReplied,
        parked: Mapping[UUID, int],
    ) -> None:
        # a park ended while the store shows the attempt running under this very runtime
        if isinstance(event.result, StoreFailure):
            return

        for identifier, attempts in parked.items():
            item: StoredTask | None = event.result.get(identifier)
            if (
                item is not None
                and runtime.coordinator.phase(identifier) is None
                and item.status.status == "running"
                and len(item.attempts) == attempts
                and self.store.entries[identifier].owner == runtime.name
            ):
                runtime.own_unparks.append((identifier, attempts + 1))

    def timer(
        self,
        runtime: Runtime,
        key: tuple[object, ...],
        delay: float,
        event: Event,
    ) -> None:
        self.counter += 1
        runtime.timers[key] = self.counter
        heapq.heappush(self.timers, (self.store.now + delay, self.counter, runtime, key, event))

    def io(
        self,
        runtime: Runtime,
        label: str,
        commit: Callable[[], Any],
        reply: Callable[[Any], Event],
        lost: Callable[[Any], None] | None = None,
    ) -> None:
        def do_commit() -> None:
            outcome: Any
            roll: float = self.random.random() if self.faults else 1.0
            if roll < 0.05 and (self.expiring or label in ("read", "renew", "claim")):
                outcome = StoreFailure(OSError("lost before the commit"))

            elif roll < 0.10 and self.expiring:
                committed: Any = commit()
                if lost is not None:
                    lost(committed)

                outcome = StoreFailure(OSError("reply lost"))

            else:
                outcome = commit()

            self.pending.append(
                Step(runtime, label + ".reply", lambda: self.feed(runtime, reply(outcome)))
            )

        self.pending.append(Step(runtime, label + ".commit", do_commit))

    # --- effects -------------------------------------------------------------------------

    def effect(
        self,
        runtime: Runtime,
        effect: Effect,
    ) -> None:
        match effect:
            case Claim() | Read() | Renew():
                self._query_effect(runtime, effect)

            case Record() | RecordInterruption() | Release():
                self._write_effect(runtime, effect)

            case Insert(task=task, reply=reply):
                self._insert_effect(runtime, task, reply)

            case StoreCallEffect(reply=reply):
                self.io(runtime, "call", lambda: 1, lambda result: StoreCallReplied(reply, result))

            case StartChanges() | StopChanges():
                self._changes_effect(runtime, effect)

            case StartHost() | RunAttempt() | ExitHost():
                self._host_effect(runtime, effect)

            case StartTimer() | StopTimer() | StopTimers():
                self._timer_effect(runtime, effect)

            case Log():
                self._log_effect(runtime, effect)

            case Resolve() | Fail() | CancelTask():
                pass

            case _:
                raise Violation(f"unexpected effect {effect}")

    def _insert_effect(
        self,
        runtime: Runtime,
        task: NewTask,
        reply: Any,
    ) -> None:
        replied: Event
        if self.faults and self.random.random() < 0.05:
            replied = InsertReplied(reply, StoreFailure(OSError("insert failed")))

        else:
            self.store.insert(task)
            replied = InsertReplied(reply, None)

        self.pending.append(Step(runtime, "insert.reply", lambda: self.feed(runtime, replied)))

    def _changes_effect(
        self,
        runtime: Runtime,
        effect: StartChanges | StopChanges,
    ) -> None:
        match effect:
            case StartChanges(seen=seen):
                step = Step(
                    runtime,
                    "changes.reply",
                    lambda: self.feed(runtime, ChangedReplied(self.store.version)),
                    lambda: self.store.version != seen,
                )
                runtime.changes = step
                self.pending.append(step)

            case StopChanges():
                if runtime.changes is not None and runtime.changes in self.pending:
                    self.pending.remove(runtime.changes)

                runtime.changes = None
                self.pending.append(
                    Step(
                        runtime,
                        "changes.reply",
                        lambda: self.feed(runtime, ChangedReplied(ChangesCancelled())),
                    )
                )

    def _timer_effect(
        self,
        runtime: Runtime,
        effect: StartTimer | StopTimer | StopTimers,
    ) -> None:
        match effect:
            case StartTimer(key=key, delay=delay, event=event):
                self.timer(runtime, key, delay, event)

            case StopTimer(key=key):
                runtime.timers.pop(key, None)

            case StopTimers(kind=kind):
                for key in [key for key in runtime.timers if key[0] == kind]:
                    del runtime.timers[key]

    def _log_effect(
        self,
        runtime: Runtime,
        effect: Log,
    ) -> None:
        if "impossible state" in effect.message:
            raise Violation(f"{runtime.name}: {effect.message % effect.arguments}")

        if "abandoned" in effect.message:
            self._left(runtime, effect.arguments[2], effect.arguments[1])

        elif "could not be released" in effect.message:
            self._left(runtime, effect.arguments[1], effect.arguments[0])

    def _left(
        self,
        runtime: Runtime,
        identifier: object,
        attempt: object,
    ) -> None:
        # a lease left to expire
        assert isinstance(identifier, UUID) and isinstance(attempt, int)
        runtime.abandoned.add((identifier, attempt))

    def _query_effect(
        self,
        runtime: Runtime,
        effect: Claim | Read | Renew,
    ) -> None:
        store: SimStore = self.store
        match effect:
            case Claim(limit=limit):
                self.io(
                    runtime,
                    "claim",
                    lambda: store.claim(runtime.name, limit),
                    ClaimReplied,
                    lambda claimed: runtime.lost.update(
                        (item.task.identifier, item.attempt) for item in claimed
                    ),
                )

            case Read(identifiers=identifiers, parks=parks, epoch=epoch):
                self.io(
                    runtime,
                    "read",
                    lambda: store.read(identifiers),
                    lambda result: ReadReplied(identifiers, parks, epoch, result),
                )

            case Renew(asked=asked, issued=issued):
                self._renewing(runtime, asked)
                self.io(
                    runtime,
                    "renew",
                    lambda: store.renew(runtime.name, frozenset(asked)),
                    lambda result: RenewReplied(asked, issued, result),
                )

    def _renewing(
        self,
        runtime: Runtime,
        asked: Mapping[UUID, int],
    ) -> None:
        # every lease which a run, or the attempt waiting behind a discarded one, holds
        for identifier in self.store.entries:
            phase = runtime.coordinator.phase(identifier)
            held: int | None = None
            if isinstance(phase, Entering | Attempting | Recording):
                held = phase.held.attempt

            elif isinstance(phase, Discarding) and phase.successor is not None:
                held = phase.successor.attempt

            if held is not None and asked.get(identifier) != held:
                raise Violation(f"{runtime.name} holds {identifier}#{held} but does not renew it")

    def _write_effect(
        self,
        runtime: Runtime,
        effect: Record | RecordInterruption | Release,
    ) -> None:
        store: SimStore = self.store
        match effect:
            case Record(identifier=identifier, serial=serial, attempt=attempt, end=end):
                self.io(
                    runtime,
                    "record",
                    lambda: store.record(runtime.name, identifier, attempt, end),
                    lambda result: RecordReplied(identifier, serial, attempt, result),
                )

            case RecordInterruption(identifier=identifier, attempt=attempt, end=end):
                self.io(
                    runtime,
                    "record",
                    lambda: store.record(runtime.name, identifier, attempt, end),
                    lambda result: InterruptionRecorded(identifier, attempt, result),
                )

            case Release(identifier=identifier, serial=serial, attempt=attempt):
                self.io(
                    runtime,
                    "release",
                    lambda: store.release(runtime.name, identifier, attempt),
                    lambda result: ReleaseReplied(identifier, serial, attempt, result),
                )

    def _host_effect(
        self,
        runtime: Runtime,
        effect: StartHost | RunAttempt | ExitHost,
    ) -> None:
        match effect:
            case StartHost(task=task, serial=serial):
                runtime.scopes += 1
                host: Host = Host(task.identifier)
                runtime.hosts[serial] = host
                self.pending.append(
                    Step(runtime, "host.enter", lambda: self._enter(runtime, host, serial))
                )

            case RunAttempt(serial=serial, claimed=claimed, termination=termination):
                self._running(runtime, claimed.task.identifier, claimed.attempt)
                self.pending.append(
                    Step(
                        runtime,
                        "attempt.finish",
                        lambda: self._finish(
                            runtime,
                            claimed.task.identifier,
                            serial,
                            claimed.attempt,
                            termination,
                        ),
                    )
                )

            case ExitHost(serial=serial):
                exiting: Host = runtime.hosts[serial]
                self.pending.append(
                    Step(
                        runtime,
                        "host.exit",
                        lambda: self.feed(
                            runtime,
                            ScopeExited(exiting.identifier, serial, None),
                        ),
                        lambda: exiting.entered,
                    )
                )

    def _running(
        self,
        runtime: Runtime,
        identifier: UUID,
        attempt: int,
    ) -> None:
        # with leases which never expire, an attempt is run only while the store has it running
        # under this runtime
        if self.expiring:
            return

        entry: Entry = self.store.entries[identifier]
        if (
            entry.status != "running"
            or entry.owner != runtime.name
            or len(entry.attempts) + 1 != attempt
        ):
            raise Violation(f"{runtime.name} runs {identifier}#{attempt} which it does not hold")

    def _enter(
        self,
        runtime: Runtime,
        host: Host,
        serial: int,
    ) -> None:
        host.entered = True
        if self.faults and self.random.random() < 0.05:
            self.feed(runtime, ScopeFailed(host.identifier, serial, OSError("scope")))

        else:
            self.feed(runtime, ScopeEntered(host.identifier, serial, copy_context()))

    def _finish(
        self,
        runtime: Runtime,
        identifier: UUID,
        serial: int,
        attempt: int,
        termination: Any,
    ) -> None:
        result: AttemptReturned | AttemptRaised | AttemptTerminated
        if self.faults and self.random.random() < 0.03:
            # the host of the scope fails, the attempt with it
            self.pending.append(
                Step(
                    runtime,
                    "attempt.deliver",
                    lambda: self.feed(runtime, ScopeExited(identifier, serial, RuntimeError())),
                )
            )
            return

        if termination.reason is not None:
            result = AttemptTerminated(termination.reason, None)

        elif self.random.random() < 0.4:
            result = AttemptReturned("null")

        else:
            result = AttemptRaised(TaskFailureRecord(kind="raised", exception_type="x"))

        self.pending.append(
            Step(
                runtime,
                "attempt.deliver",
                lambda: self.feed(runtime, AttemptEnded(identifier, serial, attempt, result)),
            )
        )

    # --- stepping ------------------------------------------------------------------------

    def step(
        self,
        externals: bool,
    ) -> bool:
        enabled: list[Step] = [
            step for step in self.pending if step.enabled is None or step.enabled()
        ]
        choices: list[str] = ["p"] * len(enabled)
        weights: list[float] = [0.08 if step.label == "attempt.finish" else 1.0 for step in enabled]
        if self.timers:
            choices.append("t")
            weights.append(0.3 if enabled else 1.0)

        if externals:
            choices.append("x")
            weights.append(0.4)

        if not choices:
            return False

        index: int = self.random.choices(range(len(choices)), weights)[0]
        if choices[index] == "p":
            step: Step = enabled[index]
            self.pending.remove(step)
            step.action()

        elif choices[index] == "t":
            self.fire_timer()

        else:
            self.external()

        return True

    def fire_timer(self) -> None:
        when, order, runtime, key, event = heapq.heappop(self.timers)
        if runtime.timers.get(key) == order:
            self.store.now = max(self.store.now, when)
            self.feed(runtime, event)

    def external(self) -> None:
        roll: float = self.random.random()
        runtime: Runtime = self.random.choice(self.runtimes)
        known: list[UUID] = list(self.store.entries)
        if roll < 0.35 and self.tasks < 4 and isinstance(runtime.coordinator.lifecycle, Open):
            self.spawn(runtime)

        elif roll < 0.5 and known and not isinstance(runtime.coordinator.lifecycle, Closed):
            self.cancel(runtime, self.random.choice(known))

        elif roll < 0.75 and known and not isinstance(runtime.coordinator.lifecycle, Closed):
            reply: Reply = Reply()
            identifier = self.random.choice(known)
            runtime.awaited.append((reply, identifier))
            self.feed(runtime, OutcomeAsked(identifier, reply))

        elif roll < 0.78 and not isinstance(runtime.coordinator.lifecycle, Closed):
            self.feed(runtime, StoreCallAsked(store_call, Reply(), copy_context()))

        elif roll < 0.80 and self.expiring:
            self.store.now += LEASE * 1.5

        elif roll < 0.88 and self.expiring and known:
            self.store.steal(self.random.choice(known))

        elif roll < 0.90:
            self.feed(self.runtimes[1], CloseRequested())

    def spawn(
        self,
        runtime: Runtime,
    ) -> None:
        self.tasks += 1
        delays: tuple[float, ...] = tuple(
            self.random.choice([0.0, 0.05, 0.2]) for _ in range(self.random.choice([0, 1, 2, 2]))
        )
        task: NewTask = NewTask(
            identifier=UUID(int=self.tasks),
            name="t",
            argument="{}",
            retry=TaskRetryPolicy(delays=delays),
            time_limit=self.random.choice([None, None, 0.15]),
            origin=None,
        )
        self.feed(runtime, SpawnAsked(task, Reply(), copy_context()))

    def cancel(
        self,
        runtime: Runtime,
        identifier: UUID,
    ) -> None:
        store: SimStore = self.store
        reply: Reply = Reply()
        self.feed(runtime, CancelAsked(identifier, reply, copy_context()))

        def replied() -> None:
            if self.faults and self.random.random() < 0.05:
                self.feed(runtime, CancelReplied(identifier, reply, StoreFailure(OSError())))

            else:
                self.feed(runtime, CancelReplied(identifier, reply, store.cancel(identifier)))

        # the store answers without faults, a cancel is not retried
        self.pending.append(Step(runtime, "cancel.reply", replied))

    # --- invariants ----------------------------------------------------------------------

    def accounting(
        self,
        runtime: Runtime,
    ) -> None:
        if any(step.runtime is runtime for step in self.pending):
            return

        for identifier, entry in self.store.entries.items():
            if entry.status == "running" and entry.owner == runtime.name:
                attempt: int = len(entry.attempts) + 1
                if not self._accounted(runtime, identifier, attempt):
                    raise Violation(
                        f"{runtime.name} holds {identifier}#{attempt} in the store"
                        f" but owns no run or job for it: {runtime.coordinator.phase(identifier)}"
                    )

    def _accounted(
        self,
        runtime: Runtime,
        identifier: UUID,
        attempt: int,
    ) -> bool:
        key: tuple[UUID, int] = (identifier, attempt)
        if (
            key in runtime.coordinator.interruptions
            or key in runtime.lost
            or key in runtime.abandoned
        ):
            return True

        phase = runtime.coordinator.phase(identifier)
        if isinstance(phase, Entering | Attempting | Recording | Releasing):
            return phase.held.attempt == attempt

        if isinstance(phase, Discarding):
            successor = phase.successor
            return phase.attempt == attempt or (
                successor is not None and successor.attempt == attempt
            )

        return False

    def check(self) -> None:
        for runtime in self.runtimes:
            self.accounting(runtime)

    def unfinished(self) -> list[UUID]:
        return [
            identifier
            for identifier, entry in self.store.entries.items()
            if entry.status != "finished"
        ]

    # --- a run ---------------------------------------------------------------------------

    def run(self) -> None:
        for _ in range(400):
            self.step(True)
            self.check()

        self.faults = False
        self.settle()
        for runtime in self.runtimes:
            self.feed(runtime, CloseRequested())

        self.close()
        self.leftovers()

    def settle(self) -> None:
        steps: int = 0
        while self.unfinished():
            steps += 1
            if steps > 30000 or not self.step(False):
                raise Violation(f"liveness: {self.unfinished()} at {self.store.now}")

            self.check()

    def closed(
        self,
        runtime: Runtime,
    ) -> bool:
        return isinstance(runtime.coordinator.lifecycle, Closed)

    def close(self) -> None:
        steps: int = 0
        while not all(self.closed(runtime) for runtime in self.runtimes):
            steps += 1
            if steps > 30000 or not self.step(False):
                raise Violation("a close does not end")

    def outcomes(self) -> None:
        # what an awaiter was given is what the store fixed
        expected = {
            StoredCompleted: TaskCompleted,
            StoredFailed: TaskFailed,
            StoredCancelled: TaskCancelled,
        }
        for runtime in self.runtimes:
            for reply, identifier in runtime.awaited:
                if reply.exception is not None:
                    if not isinstance(reply.exception, RuntimeError):
                        raise Violation(f"an awaiter failed with {reply.exception!r}")

                elif reply.done:
                    outcome = self.store.entries[identifier].outcome
                    if outcome is None or not isinstance(reply.value, expected[type(outcome)]):
                        raise Violation(f"an awaiter of {identifier} was given {reply.value!r}")

    def leftovers(self) -> None:
        self.outcomes()
        for runtime in self.runtimes:
            coordinator: Coordinator = runtime.coordinator
            if coordinator.runs or coordinator.pending or coordinator.hosts:
                raise Violation(f"{runtime.name} closed with leftovers")

            if coordinator.interruptions:
                raise Violation(f"{runtime.name} closed with interruption jobs")

            late: list[str] = [
                step.label
                for step in self.pending
                if step.runtime is runtime and not step.label.startswith(("host", "attempt"))
            ]
            if late:
                raise Violation(f"{runtime.name} has I/O pending after the close: {late}")

            for identifier, attempt in runtime.own_unparks:
                if (identifier, attempt) not in runtime.lost:
                    raise Violation(f"a park ended on the lease held for {identifier}#{attempt}")
